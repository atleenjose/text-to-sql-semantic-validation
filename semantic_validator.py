"""Rule based checks for generated ecommerce analytics SQL.

Experimental validator, not a SQL security sandbox.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

SALES_TERMS = (
    "revenue",
    "sales",
    "sold",
    "sell",
    "purchased",
    "fulfil",
    "order value",
    "completed orders",
)

def validate_sql(question: str, sql: str) -> dict:
    issues = []
    if not isinstance(sql, str) or not sql.strip():
        return {"passed": False, "issues": ["SQL is empty"]}

    try:
        statements = sqlglot.parse(sql, read="duckdb", error_level="RAISE")
    except Exception as exc:
        return {"passed": False, "issues": [f"SQL parsing failed: {type(exc).__name__}"]}

    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return {"passed": False, "issues": ["Expected one SELECT statement"]}

    tree = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command)
    if any(tree.find(kind) is not None for kind in forbidden):
        issues.append("Statement contains a disallowed operation")

    tables = {table.name.lower() for table in tree.find_all(exp.Table)}
    q = question.lower()

    known_tables = {"orders", "order_items", "customers", "products"}
    unknown = sorted(tables - known_tables)
    if unknown:
        issues.append("Unknown table: " + ", ".join(unknown))

    needs_completed = any(term in q for term in SALES_TERMS)
    if needs_completed:
        if "orders" not in tables:
            issues.append("Sales metric needs the orders table")
        if not _completed_filter(tree):
            issues.append("Missing completed order status filter")

    if "revenue" in q or "sales" in q or "order value" in q:
        if "order_items" not in tables:
            issues.append("Revenue metric needs order_items")
        if not _has_revenue_multiplication(tree):
            issues.append("Revenue should use order item quantity multiplied by its unit price")

    if "orders" not in tables:
        for col in tree.find_all(exp.Column):
            if col.name.lower() in {"order_status", "order_date"}:
                issues.append(f"Column {col.name} requires orders table")

    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    month_match = re.search(r"\b(" + "|".join(months) + r")\s+(20\d{2})\b", q)
    if month_match:
        month = months.index(month_match.group(1)) + 1
        year = int(month_match.group(2))
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1)
        if not _covers_date_interval(tree, start, end):
            issues.append("Date filters do not clearly cover the full requested month")

    quarter_match = re.search(r"\bq([1-4])\s+(20\d{2})\b", q)
    if quarter_match:
        quarter, year = int(quarter_match.group(1)), int(quarter_match.group(2))
        start_month = 1 + (quarter - 1) * 3
        start = date(year, start_month, 1)
        end = date(
            year + (quarter == 4),
            1 if quarter == 4 else start_month + 3,
            1
        )

        if not _covers_date_interval(tree, start, end):
            issues.append(
                "Date filters do not clearly cover the full requested quarter"
            )

    order_count_question = bool(
        re.search(
            r"\b(?:how many|number of|count of|total number of)\b"
            r".{0,100}\borders\b",
            question.lower(),
        )
    )

    if order_count_question and _has_order_count_grain_issue(tree):
        issues.append(
            "Order count may be inflated by duplicate order items"
        )

    return {
        "passed": not issues,
        "issues": list(dict.fromkeys(issues))
    }



def _completed_filter(tree: exp.Expression) -> bool:
    where = tree.args.get("where")
    if where is None:
        return False
    for eq in where.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if ((isinstance(left, exp.Column) and left.name.lower() == "order_status" and isinstance(right, exp.Literal) and right.is_string and right.this == "completed")
                or (isinstance(right, exp.Column) and right.name.lower() == "order_status" and isinstance(left, exp.Literal) and left.is_string and left.this == "completed")):
            return True
    return False

def _has_order_count_grain_issue(tree: exp.Expression) -> bool:
    """Detect unsafe order counting after a direct order items join."""

    if not isinstance(tree, exp.Select):
        return False

    from_clause = tree.args.get("from_")
    if from_clause is None:
        return False

    source = from_clause.this

    if not isinstance(source, exp.Table):
        return False

    outer_tables = {source.name.lower()}

    for join in tree.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            outer_tables.add(join.this.name.lower())

    if not {"orders", "order_items"}.issubset(outer_tables):
        return False

    for expression in tree.expressions:
        for count in expression.find_all(exp.Count):

            if isinstance(count.this, exp.Star):
                return True

            if isinstance(count.this, exp.Column):
                if count.this.name.lower() == "order_id":
                    if not isinstance(count.this, exp.Distinct):
                        return True

    return False

def _has_revenue_multiplication(tree: exp.Expression) -> bool:
    for mul in tree.find_all(exp.Mul):
        columns = list(mul.find_all(exp.Column))
        if {c.name.lower() for c in columns} >= {"quantity", "unit_price"}:
            price_cols = [c for c in columns if c.name.lower() == "unit_price"]
            if all(c.table.lower() not in {"products", "p"} for c in price_cols):
                return True
    return False


def _covers_date_interval(tree: exp.Expression, start: date, end: date) -> bool:
    """Conservative check for simple ANDed lower and upper date comparisons."""
    where = tree.args.get("where")
    if where is None:
        return False
    for between in where.find_all(exp.Between):
        column = between.this

        if not isinstance(column, exp.Column):
            continue

        if column.name.lower() != "order_date":
            continue

        low = between.args.get("low")
        high = between.args.get("high")

        if not isinstance(low, exp.Literal) or not isinstance(high, exp.Literal):
            continue

        try:
            low_date = date.fromisoformat(low.this[:10])
            high_date = date.fromisoformat(high.this[:10])
        except (ValueError, TypeError):
            continue

        if low_date <= start and high_date >= end - timedelta(days=1):
            return True
        
    lower_ok = upper_ok = False
    for node in where.walk():
        if not isinstance(node, (exp.GTE, exp.GT, exp.LTE, exp.LT, exp.EQ)):
            continue
        if not isinstance(node.left, exp.Column) or node.left.name.lower() != "order_date":
            continue
        rhs = node.right
        if not isinstance(rhs, exp.Literal) or not rhs.is_string:
            continue
        try:
            d = date.fromisoformat(rhs.this[:10])
        except ValueError:
            continue
        if isinstance(node, exp.GTE) and d <= start:
            lower_ok = True
        if isinstance(node, exp.GT) and d < start:
            lower_ok = True
        if isinstance(node, exp.LT) and d >= end:
            upper_ok = True
        if isinstance(node, exp.LTE) and d >= end - timedelta(days=1):
            upper_ok = True
        if isinstance(node, exp.EQ):
            return False
    return lower_ok and upper_ok


def main() -> None:
    source = Path(__file__).resolve().parent / "results" / "results.csv"
    if not source.exists():
        raise SystemExit(f"Missing benchmark file: {source}")
    df = pd.read_csv(source)
    records = []
    for _, row in df.iterrows():
        verdict = validate_sql(str(row["question"]), row["sql"])
        executed = str(row["executed"]).strip().lower() == "true"
        correct = str(row["correct"]).strip().lower() == "true"
        approved = verdict["passed"] and executed
        issues = list(verdict["issues"])
        if not executed:
            issues.append("SQL execution failed")
        records.append({
            "id": row["id"],
            "condition": row["condition"],
            "correct": correct,
            "executed": executed,
            "validator_passed": verdict["passed"],
            "approved": approved,
            "issues": " | ".join(issues),
        })
    output = pd.DataFrame(records)
    destination = source.parent / "validation_results.csv"
    output.to_csv(destination, index=False)
    print(output.to_string(index=False, max_colwidth=110))
    incorrect = ~output["correct"]
    correct = output["correct"]
    print("\nVALIDATION SUMMARY")
    print("Flagged incorrect queries by rules:", int((~output["validator_passed"] & incorrect).sum()), "/", int(incorrect.sum()))
    print("Rejected incorrect queries after execution check:", int((~output["approved"] & incorrect).sum()), "/", int(incorrect.sum()))
    print("Rejected correct queries:", int((~output["approved"] & correct).sum()), "/", int(correct.sum()))
    print("Incorrect queries still approved:", int((output["approved"] & incorrect).sum()))
    print("Saved:", destination)


if __name__ == "__main__":
    main()
