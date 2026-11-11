
import requests
import duckdb
import re
from pathlib import Path

DB_PATH = Path.home() / "Projects/text-to-sql-experiment/data/ecommerce.duckdb"

SCHEMA = """
customers(customer_id, customer_name, signup_date, region)
products(product_id, product_name, category, unit_price)
orders(order_id, customer_id, order_date, order_status)
order_items(order_item_id, order_id, product_id, quantity, unit_price)
"""

SEMANTIC_CONTEXT = """
Business metric definitions:

Sales revenue:
SUM(order_items.quantity * order_items.unit_price)
for orders where order_status = 'completed'.

Cancelled and refunded orders must be excluded.

Order values must be calculated using order_items.unit_price,
not products.unit_price, because prices can change.

Always join orders and order_items using order_id.
"""

QUESTION = "What is our total sales revenue?"


def generate_sql(use_semantics):
    context = SEMANTIC_CONTEXT if use_semantics else ""

    prompt = f"""
You are an expert DuckDB SQL analyst.

Database schema:
{SCHEMA}

{context}

Question:
{QUESTION}

Generate one read-only SELECT query.
Return only SQL without explanations or markdown.
"""

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "mistral:latest",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0}
        },
        timeout=180
    )

    response.raise_for_status()

    sql = response.json()["response"].strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.I)
    sql = re.sub(r"\s*```$", "", sql).strip()
    return sql.rstrip(";").strip()


def evaluate_sql(sql, expected):
    if not re.match(r"^SELECT\b", sql, flags=re.I):
        return None, False, "Not a SELECT query"

    if ";" in sql:
        return None, False, "Multiple statements not allowed"

    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)

        try:
            result = con.execute(sql).fetchall()
        finally:
            con.close()

        if len(result) != 1 or len(result[0]) != 1:
            return result, False, "Unexpected result shape"

        actual = result[0][0]

        if actual is None:
            return actual, False, "Null result"

        correct = abs(float(actual) - expected) < 0.01

        return actual, correct, None

    except Exception as error:
        return None, False, str(error)


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        expected = float(con.execute("""
            SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2)
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            WHERE o.order_status = 'completed'
        """).fetchone()[0])
    finally:
        con.close()

    print(f"\nVerified sales revenue: ${expected:,.2f}")

    for label, use_semantics in [
        ("A: WITHOUT SEMANTIC CONTEXT", False),
        ("B: WITH SEMANTIC CONTEXT", True)
    ]:
        print(f"\n{'=' * 55}")
        print(label)
        print("=" * 55)

        try:
            sql = generate_sql(use_semantics)
            print("\nGenerated SQL:\n", sql)

            actual, correct, error = evaluate_sql(sql, expected)

            print("\nResult:", actual)
            print("Correct:", correct)

            if error:
                print("Error:", error)

        except Exception as error:
            print("Generation failed:", error)


if __name__ == "__main__":
    main()

