from pathlib import Path
import random
from datetime import date, timedelta
import duckdb

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
DB = DATA / 'ecommerce.duckdb'
rng = random.Random(42)

con = duckdb.connect(str(DB))
for name in ('order_items', 'orders', 'products', 'customers'):
    con.execute(f'DROP TABLE IF EXISTS {name}')

con.execute('CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, customer_name VARCHAR, signup_date DATE, region VARCHAR)')
con.execute('CREATE TABLE products (product_id INTEGER PRIMARY KEY, product_name VARCHAR, category VARCHAR, unit_price DECIMAL(10,2))')
con.execute('CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, order_date DATE, order_status VARCHAR)')
con.execute('CREATE TABLE order_items (order_item_id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price DECIMAL(10,2))')

regions = ['North', 'South', 'East', 'West']
customers = [(i, f'Customer {i:03d}', date(2024, 1, 1) + timedelta(days=rng.randrange(550)), rng.choice(regions)) for i in range(1,201)]
con.executemany('INSERT INTO customers VALUES (?, ?, ?, ?)', customers)

categories = ['Electronics', 'Home', 'Fashion', 'Beauty', 'Sports']
products = []
for i in range(1, 21):
    cat = categories[(i-1)//4]
    products.append((i, f'{cat} Product {((i-1)%4)+1}', cat, round(rng.uniform(8, 250), 2)))
con.executemany('INSERT INTO products VALUES (?, ?, ?, ?)', products)

statuses = ['completed', 'cancelled', 'refunded', 'pending']
orders = []
items = []
item_id = 1
for oid in range(1, 1001):
    cid = rng.randint(1,200)
    day = date(2025,1,1) + timedelta(days=rng.randrange(365))
    status = rng.choices(statuses, weights=[70, 12, 10, 8], k=1)[0]
    orders.append((oid, cid, day, status))
    for pid in rng.sample(range(1,21), k=rng.randint(1,4)):
        qty = rng.randint(1,3)
        items.append((item_id, oid, pid, qty, products[pid-1][3]))
        item_id += 1
con.executemany('INSERT INTO orders VALUES (?, ?, ?, ?)', orders)
con.executemany('INSERT INTO order_items VALUES (?, ?, ?, ?, ?)', items)

print('Database:', DB)
for name in ('customers', 'products', 'orders', 'order_items'):
    count = con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
    print(f'{name}: {count:,} rows')
revenue = con.execute('''
    SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2)
    FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
    WHERE o.order_status = 'completed'
''').fetchone()[0]
print('Completed order revenue:', revenue)
con.close()
