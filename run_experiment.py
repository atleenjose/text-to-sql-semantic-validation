"""Evaluate local Mistral SQL with and without business metric definitions."""
import csv
import json
import re
import time
from decimal import Decimal
from pathlib import Path

import duckdb
import requests
from sqlglot import parse, exp

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'ecommerce.duckdb'
OUT = ROOT / 'results'
OUT.mkdir(exist_ok=True)
MODEL = 'mistral:latest'

SCHEMA = '''
customers(customer_id INTEGER, customer_name VARCHAR, signup_date DATE, region VARCHAR)
products(product_id INTEGER, product_name VARCHAR, category VARCHAR, unit_price DECIMAL)
orders(order_id INTEGER, customer_id INTEGER, order_date DATE, order_status VARCHAR)
order_items(order_item_id INTEGER, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price DECIMAL)
'''
SEMANTICS = '''
Business definitions:
* Recognised sales/revenue includes ONLY orders with order_status = 'completed'.
* Sales value for a line item is order_items.quantity * order_items.unit_price.
* For revenue, use historical order_items.unit_price, NOT products.unit_price.
* Average order value = total recognised sales / number of completed orders.
* A purchasing customer is a DISTINCT customer_id with at least one completed order.
* Units sold counts order_items.quantity on completed orders only.
* A completed order is counted once regardless of number of items.
* Product/category sales and rankings exclude cancelled, pending and refunded orders.
* All dates are order_date in the orders table; 2025 data only.
* Join orders to order_items by order_id; orders to customers by customer_id;
  order_items to products by product_id.
'''

# Independently specified reference queries. All questions request one numeric scalar.
TESTS = [
 ('q01', 'What is our total sales revenue?', '''SELECT SUM(oi.quantity * oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' '''),
 ('q02', 'What is the average order value?', '''SELECT SUM(oi.quantity * oi.unit_price)/COUNT(DISTINCT o.order_id) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' '''),
 ('q03', 'How many orders did we fulfil?', '''SELECT COUNT(*) FROM orders WHERE order_status='completed' '''),
 ('q04', 'How many customers purchased from us?', '''SELECT COUNT(DISTINCT customer_id) FROM orders WHERE order_status='completed' '''),
 ('q05', 'How many units did we sell?', '''SELECT SUM(oi.quantity) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' '''),
 ('q06', 'What was our Electronics category revenue?', '''SELECT SUM(oi.quantity*oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) JOIN products p USING(product_id) WHERE o.order_status='completed' AND p.category='Electronics' '''),
 ('q07', 'What was our revenue from customers in the North region?', '''SELECT SUM(oi.quantity*oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) JOIN customers c USING(customer_id) WHERE o.order_status='completed' AND c.region='North' '''),
 ('q08', 'What was our sales revenue in March 2025?', '''SELECT SUM(oi.quantity*oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' AND o.order_date >= DATE '2025-03-01' AND o.order_date < DATE '2025-04-01' '''),
 ('q09', 'What was our sales revenue in Q4 2025?', '''SELECT SUM(oi.quantity*oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' AND o.order_date >= DATE '2025-10-01' AND o.order_date < DATE '2026-01-01' '''),
 ('q10', 'How many distinct products did we sell?', '''SELECT COUNT(DISTINCT oi.product_id) FROM orders o JOIN order_items oi USING(order_id) WHERE o.order_status='completed' '''),
]


def clean_sql(text):
    text = text.strip()
    match = re.search(r'```(?:sql)?\s*(.*?)```', text, flags=re.I | re.S)
    if match:
        text = match.group(1).strip()
    return text.strip().rstrip(';').strip()


def validate_read_only(sql):
    statements = parse(sql, read='duckdb')
    if len(statements) != 1 or statements[0] is None:
        raise ValueError('Expected exactly one SQL statement')
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise ValueError('Only SELECT queries are permitted')
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                 exp.Alter, exp.Command, exp.Copy, exp.Merge)
    if any(tree.find(node) for node in forbidden):
        raise ValueError('Query contains a forbidden operation')
    return sql


def generate(question, with_semantics):
    prompt = f'''You are a DuckDB SQL analyst. Return exactly one SELECT query, no markdown or commentary.
Schema:\n{SCHEMA}
{('Business definitions:\n' + SEMANTICS) if with_semantics else ''}
Question: {question}
Return a single numeric value in one row and one column.'''
    response = requests.post('http://localhost:11434/api/generate', json={
        'model': MODEL, 'prompt': prompt, 'stream': False,
        'options': {'temperature': 0, 'num_predict': 450}
    }, timeout=300)
    response.raise_for_status()
    return clean_sql(response.json()['response'])


def scalar(connection, sql):
    result = connection.execute(validate_read_only(sql)).fetchall()
    if len(result) != 1 or len(result[0]) != 1 or result[0][0] is None:
        raise ValueError('Expected one non-null scalar result')
    return Decimal(str(result[0][0]))


def main():
    if not DB.exists():
        raise FileNotFoundError(f'Database not found: {DB}')
    rows = []
    con = duckdb.connect(str(DB), read_only=True)
    try:
        for test_id, question, reference_sql in TESTS:
            expected = scalar(con, reference_sql)
            for label, context in [('baseline', False), ('semantic', True)]:
                row = {'id': test_id, 'question': question, 'condition': label,
                       'expected': str(expected), 'actual': '', 'correct': False,
                       'executed': False, 'seconds': '', 'sql': '', 'error': ''}
                started = time.monotonic()
                try:
                    sql = generate(question, context)
                    row['sql'] = sql
                    actual = scalar(con, sql)
                    row['actual'] = str(actual)
                    row['executed'] = True
                    row['correct'] = abs(actual - expected) <= Decimal('0.01')
                except Exception as exc:
                    row['error'] = f'{type(exc).__name__}: {exc}'
                row['seconds'] = round(time.monotonic() - started, 2)
                rows.append(row)
                print(f"{test_id} {label:8} correct={row['correct']} executed={row['executed']} ({row['seconds']}s)", flush=True)
                with (OUT / 'results.csv').open('w', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
    finally:
        con.close()
    summary = {}
    for label in ('baseline', 'semantic'):
        subset = [r for r in rows if r['condition'] == label]
        summary[label] = {'correct': sum(r['correct'] for r in subset),
                          'executed': sum(r['executed'] for r in subset),
                          'total': len(subset)}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('\nSUMMARY:', json.dumps(summary, indent=2))
    print('Saved:', OUT / 'results.csv')


if __name__ == '__main__':
    main()
