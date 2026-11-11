"""Held-out evaluation of Mistral SQL and the frozen semantic validator.

Run from the project folder after placing this file beside run_experiment.py.
Does not modify the original benchmark or semantic_validator.py.
"""
from __future__ import annotations

import csv
import json
import time
from decimal import Decimal
from pathlib import Path

import duckdb

from run_experiment import DB, OUT, generate, scalar
from semantic_validator import validate_sql

# New questions, not used in the initial ten-question development benchmark.
# Every reference query returns one numeric scalar.
TESTS = [
    ("h01", "What was our sales revenue in January 2025?", """
        SELECT SUM(oi.quantity * oi.unit_price)
        FROM orders o JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-01-01'
          AND o.order_date < DATE '2025-02-01'
    """),
    ("h02", "How many orders were fulfilled in June 2025?", """
        SELECT COUNT(*) FROM orders
        WHERE order_status = 'completed'
          AND order_date >= DATE '2025-06-01'
          AND order_date < DATE '2025-07-01'
    """),
    ("h03", "What was our sales revenue in Q2 2025?", """
        SELECT SUM(oi.quantity * oi.unit_price)
        FROM orders o JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-04-01'
          AND o.order_date < DATE '2025-07-01'
    """),
    ("h04", "How many units did we sell in September 2025?", """
        SELECT SUM(oi.quantity)
        FROM orders o JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-09-01'
          AND o.order_date < DATE '2025-10-01'
    """),
    ("h05", "What was our Clothing category revenue?", """
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM orders o
        JOIN order_items oi USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status = 'completed'
        AND p.category = 'Clothing'
    """),
    ("h06", "How much revenue came from customers in the South region?", """
        SELECT SUM(oi.quantity * oi.unit_price)
        FROM orders o JOIN order_items oi USING (order_id)
        JOIN customers c USING (customer_id)
        WHERE o.order_status = 'completed' AND c.region = 'South'
    """),
    ("h07", "How many customers purchased from us in Q3 2025?", """
        SELECT COUNT(DISTINCT o.customer_id)
        FROM orders o
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-07-01'
          AND o.order_date < DATE '2025-10-01'
    """),
    ("h08", "What was the average order value in Q1 2025?", """
        SELECT SUM(oi.quantity * oi.unit_price) / COUNT(DISTINCT o.order_id)
        FROM orders o JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-01-01'
          AND o.order_date < DATE '2025-04-01'
    """),
    ("h09", "How many distinct products did we sell in Q4 2025?", """
        SELECT COUNT(DISTINCT oi.product_id)
        FROM orders o JOIN order_items oi USING (order_id)
        WHERE o.order_status = 'completed'
          AND o.order_date >= DATE '2025-10-01'
          AND o.order_date < DATE '2026-01-01'
    """),
    ("h10", "How many completed orders included Electronics products?", """
        SELECT COUNT(DISTINCT o.order_id)
        FROM orders o JOIN order_items oi USING (order_id)
        JOIN products p USING (product_id)
        WHERE o.order_status = 'completed' AND p.category = 'Electronics'
    """),
]

FIELDS = ["id", "question", "condition", "expected", "actual", "correct",
          "executed", "validator_passed", "approved", "seconds", "sql", "error", "issues"]


def save_rows(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict]) -> dict:
    by_condition = {}
    for label in ("baseline", "semantic"):
        subset = [r for r in rows if r["condition"] == label]
        by_condition[label] = {
            "total": len(subset),
            "executed": sum(r["executed"] for r in subset),
            "numerically_correct": sum(r["correct"] for r in subset),
            "validator_passed": sum(r["validator_passed"] for r in subset),
            "approved": sum(r["approved"] for r in subset),
            "incorrect_approved": sum(r["approved"] and not r["correct"] for r in subset),
            "correct_rejected": sum(r["correct"] and not r["approved"] for r in subset),
        }
    return by_condition


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"Database not found: {DB}")
    OUT.mkdir(exist_ok=True)
    destination = OUT / "independent_test.csv"
    rows = []
    con = duckdb.connect(str(DB), read_only=True)
    try:
        for test_id, question, reference_sql in TESTS:
            expected = scalar(con, reference_sql)
            for label, with_semantics in (("baseline", False), ("semantic", True)):
                row = {"id": test_id, "question": question, "condition": label,
                       "expected": str(expected), "actual": "", "correct": False,
                       "executed": False, "validator_passed": False,
                       "approved": False, "seconds": "", "sql": "", "error": "", "issues": ""}
                started = time.monotonic()
                try:
                    sql = generate(question, with_semantics)
                    row["sql"] = sql
                    verdict = validate_sql(question, sql)
                    row["validator_passed"] = bool(verdict["passed"])
                    row["issues"] = " | ".join(verdict["issues"])
                    # Benchmark execution only, using a read-only synthetic database.
                    # Validation is recorded separately, even if the query fails it.
                    actual = scalar(con, sql)
                    row["actual"] = str(actual)
                    row["executed"] = True
                    row["correct"] = abs(actual - expected) <= Decimal("0.01")
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["approved"] = row["validator_passed"] and row["executed"]
                row["seconds"] = round(time.monotonic() - started, 2)
                rows.append(row)
                save_rows(rows, destination)
                print(f"{test_id} {label:8} correct={row['correct']} "
                      f"executed={row['executed']} approved={row['approved']} "
                      f"({row['seconds']}s)", flush=True)
    finally:
        con.close()

    summary = summarize(rows)
    (OUT / "independent_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nINDEPENDENT TEST SUMMARY")
    print(json.dumps(summary, indent=2))
    print("Saved:", destination)
    print("Note: numerical correctness is distinct from semantic rule compliance.")
    print("This is a small held-out test on the same synthetic database and model.")


if __name__ == "__main__":
    main()
