"""The database LIFEBOAT protects: a small online shop that changes every day.

`seed()` creates it once (the "genesis" backup). `business_day()` simulates one day of traffic:
new customers, orders, cancellations and stock changes. So every backup is different and the
drill proves that yesterday's real data comes back, not a static fixture.
"""

from __future__ import annotations

import random

SCHEMA = """
CREATE TABLE customers (
  id INT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  city VARCHAR(60) NOT NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE products (
  id INT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  price_rappen INT NOT NULL,
  stock INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  status ENUM('paid','shipped','cancelled') NOT NULL,
  total_rappen INT NOT NULL,
  created_at DATETIME NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  product_id INT NOT NULL,
  qty INT NOT NULL,
  price_rappen INT NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB;

CREATE VIEW revenue_by_day AS
  SELECT DATE(created_at) AS day, COUNT(*) AS orders, SUM(total_rappen) / 100 AS chf
  FROM orders WHERE status <> 'cancelled' GROUP BY DATE(created_at);
"""

FIRST = ["Lena", "Noah", "Mia", "Luca", "Emma", "Elias", "Lara", "Leon", "Sofia", "Matteo", "Nina", "Jonas", "Alina", "Levin", "Elena", "Nico"]
LAST = ["Meier", "Müller", "Keller", "Huber", "Weber", "Schmid", "Frei", "Baumann", "Steiner", "Fischer", "Gerber", "Brunner", "Moser", "Zürcher"]
CITIES = ["Brugg", "Windisch", "Baden", "Aarau", "Basel", "Zürich", "Bern", "Luzern", "Olten", "Lenzburg"]
PRODUCTS = ["Kaffeebohnen 500 g", "Thermosflasche", "Notizbuch A5", "USB-C Kabel", "Rucksack", "Velolicht", "Kopfhörer",
            "Tastatur", "Maus", "Laptopständer", "Trinkflasche", "Regenjacke", "Powerbank", "Schreibset", "Brotdose"]


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def _insert(table: str, rows: list[tuple]) -> str:
    out = []
    for i in range(0, len(rows), 500):
        vals = ",".join("(" + ",".join(_q(v) if isinstance(v, str) else str(v) for v in r) + ")" for r in rows[i:i + 500])
        out.append(f"INSERT INTO {table} VALUES {vals};")
    return "\n".join(out)


def seed(db, start="2026-10-01 08:00:00") -> dict:
    rng = random.Random(42)
    db.recreate()
    customers = []
    for cid in range(1, 401):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        customers.append((cid, f"{first} {last}", f"{first}.{last}.{cid}@example.ch".lower(), rng.choice(CITIES), start))
    products = [(pid, f"{name}", rng.randint(9, 189) * 100 - 10, rng.randint(20, 400)) for pid, name in enumerate(PRODUCTS, 1)]
    db.script(SCHEMA + _insert("customers", customers) + _insert("products", products))
    stats = business_day(db, day="2026-10-01", seed_value=1, orders=600)
    return {"customers": len(customers), "products": len(products), **stats}


def business_day(db, day: str, seed_value: int, orders: int | None = None) -> dict:
    """One simulated day of shop traffic."""
    rng = random.Random(seed_value)
    next_customer = int(db.sql("SELECT COALESCE(MAX(id),0)+1 FROM customers").strip())
    next_order = int(db.sql("SELECT COALESCE(MAX(id),0)+1 FROM orders").strip())
    next_item = int(db.sql("SELECT COALESCE(MAX(id),0)+1 FROM order_items").strip())
    prices = {int(p): int(v) for p, v in (line.split("\t") for line in db.sql("SELECT id, price_rappen FROM products").strip().split("\n"))}
    max_customer = next_customer - 1

    new_customers = []
    for i in range(rng.randint(8, 25)):
        cid = next_customer + i
        first, last = rng.choice(FIRST), rng.choice(LAST)
        new_customers.append((cid, f"{first} {last}", f"{first}.{last}.{cid}@example.ch".lower(), rng.choice(CITIES), f"{day} 07:00:00"))
    max_customer += len(new_customers)

    order_rows, item_rows, sold = [], [], {}
    for n in range(orders if orders is not None else rng.randint(60, 140)):
        oid = next_order + n
        total = 0
        for _ in range(rng.randint(1, 4)):
            pid, qty = rng.choice(list(prices)), rng.randint(1, 3)
            item_rows.append((next_item, oid, pid, qty, prices[pid]))
            next_item += 1
            total += qty * prices[pid]
            sold[pid] = sold.get(pid, 0) + qty
        status = rng.choices(["paid", "shipped", "cancelled"], [5, 4, 1])[0]
        at = f"{day} {rng.randint(6, 22):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        order_rows.append((oid, rng.randint(1, max_customer), status, total, at))

    stock = "\n".join(f"UPDATE products SET stock = GREATEST(stock - {q}, 0) + {rng.randint(0, 30)} WHERE id = {p};" for p, q in sold.items())
    ship = "UPDATE orders SET status = 'shipped' WHERE status = 'paid' AND created_at < DATE_SUB(" + _q(day) + ", INTERVAL 2 DAY);"
    db.script("START TRANSACTION;\n" + _insert("customers", new_customers) + "\n" + _insert("orders", order_rows) + "\n"
              + _insert("order_items", item_rows) + "\n" + stock + "\n" + ship + "\nCOMMIT;")
    return {"new_customers": len(new_customers), "new_orders": len(order_rows), "new_items": len(item_rows)}
