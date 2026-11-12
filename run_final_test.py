
"""Final evaluation of Mistral SQL using the frozen semantic validator."""

import json
from pathlib import Path

import duckdb

from final_test_questions import FINAL_QUESTIONS
from run_experiment import DB, OUT, generate, scalar
from run_independent_test import save_rows, summarize
from semantic_validator import validate_sql

import time
from decimal import Decimal


REFERENCE_SQL = {
    "f01": """
        SELECT COUNT(DISTINCT customer_id)
        FROM orders
        WHERE order_status = 'completed'
    """,
    "f02": """
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-02-01'
          AND o.order_date < DATE '2025-03-01'
    """,
    "f03": """
        SELECT COUNT(*)
        FROM orders
        WHERE order_status = 'completed'
          AND order_date >= DATE '2025-07-01'
          AND order_date < DATE '2025-10-01'
    """,
    "f04": """
        SELECT COALESCE(SUM(oi.quantity), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status = 'completed'
          AND p.category = 'Beauty'
    """,
    "f05": """
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        JOIN customers c USING (customer_id)
        WHERE o.order_status = 'completed'
          AND c.region = 'North'
    """,
    "f06": """
        SELECT COUNT(DISTINCT o.customer_id)
        FROM orders o
        JOIN order_items oi USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status = 'completed'
          AND p.category = 'Electronics'
    """,
    "f07": """
        SELECT SUM(oi.quantity * oi.unit_price)
               / NULLIF(COUNT(DISTINCT o.order_id), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
    """,
    "f08": """
        SELECT COUNT(DISTINCT o.order_id)
        FROM orders o
        JOIN order_items oi USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status = 'completed'
          AND p.category = 'Sports'
    """,
    "f09": """
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-12-01'
          AND o.order_date < DATE '2026-01-01'
    """,
    "f10": """
        SELECT COUNT(DISTINCT oi.product_id)
        FROM orders o
        JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
    """,
}


def main():
    if not DB.exists():
        raise SystemExit(f"Database not found: {DB}")

    OUT.mkdir(exist_ok=True)

    destination = OUT / "final_test.csv"
    summary_path = OUT / "final_summary.json"

    rows = []
    con = duckdb.connect(str(DB), read_only=True)

    try:
        for test_id, question in FINAL_QUESTIONS:
            expected = scalar(con, REFERENCE_SQL[test_id])

            for label, with_semantics in (
                ("baseline", False),
                ("semantic", True),
            ):
                row = {
                    "id": test_id,
                    "question": question,
                    "condition": label,
                    "expected": str(expected),
                    "actual": "",
                    "correct": False,
                    "executed": False,
                    "validator_passed": False,
                    "approved": False,
                    "seconds": "",
                    "sql": "",
                    "error": "",
                    "issues": "",
                }

                started = time.monotonic()

                try:
                    sql = generate(question, with_semantics)
                    row["sql"] = sql

                    verdict = validate_sql(question, sql)
                    row["validator_passed"] = bool(verdict["passed"])
                    row["issues"] = " | ".join(verdict["issues"])

                    actual = scalar(con, sql)
                    row["actual"] = str(actual)
                    row["executed"] = True

                    row["correct"] = (
                        abs(actual - expected) <= Decimal("0.01")
                    )

                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"

                row["approved"] = (
                    row["validator_passed"] and row["executed"]
                )

                row["seconds"] = round(
                    time.monotonic() - started, 2
                )

                rows.append(row)
                save_rows(rows, destination)

                print(
                    f"{test_id} {label:8} "
                    f"correct={row['correct']} "
                    f"executed={row['executed']} "
                    f"approved={row['approved']} "
                    f"({row['seconds']}s)",
                    flush=True,
                )

    finally:
        con.close()

    summary = summarize(rows)

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print("\nFINAL TEST SUMMARY")
    print(json.dumps(summary, indent=2))
    print("Saved:", destination)


if __name__ == "__main__":
    main()
