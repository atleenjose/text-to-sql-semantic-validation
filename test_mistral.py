
import requests
import duckdb
from pathlib import Path

DB_PATH = Path.home() / "Projects/text-to-sql-experiment/data/ecommerce.duckdb"

schema = """
customers(customer_id, customer_name, signup_date, region)
products(product_id, product_name, category, unit_price)
orders(order_id, customer_id, order_date, order_status)
order_items(order_item_id, order_id, product_id, quantity, unit_price)
"""

question = "What is the total revenue generated from completed orders?"

prompt = f"""
You are an expert DuckDB SQL analyst.

Database schema:
{schema}

Generate a single read-only SELECT query to answer:
{question}

Return only SQL. Do not include explanations or markdown.
"""

print("Asking Mistral to generate SQL...")

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

sql = sql.replace("```sql", "").replace("```", "").strip()
sql = sql.rstrip(";")

print("\nGenerated SQL:\n", sql)

if not sql.lower().startswith("select"):
    raise ValueError("Model did not return a SELECT query")

if ";" in sql:
    raise ValueError("Multiple SQL statements are not allowed")

con = duckdb.connect(str(DB_PATH), read_only=True)

try:
    result = con.execute(sql).fetchall()
    print("\nAI query result:", result)

    expected = con.execute("""
        SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2)
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        WHERE o.order_status = 'completed'
    """).fetchone()[0]

    print("Verified revenue:", expected)

    actual = result[0][0] if len(result) == 1 and len(result[0]) == 1 else None
    print("Correct answer:", actual is not None and abs(float(actual) - float(expected)) < 0.01)
finally:
    con.close()
