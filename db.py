"""SQLite schema, migrations and row helpers.

Tables:
  batches         one photo shoot / upload session
  product_images  every photo; product_id NULL = not yet grouped into an item
  products        one eBay listing ("item")
  jobs            background work (grouping, identify, pricing, publishing)
"""
import json
import os
import sqlite3

import config

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products.db")

JSON_FIELDS = ("aspects", "condition_descriptors", "ai", "comps")

CARD_CATEGORIES = {"183454", "261328", "183455", "183050"}

# Old TCG-style condition keys -> eBay "Ungraded" + card-condition descriptor.
_LEGACY_CARD_CONDITIONS = {"near_mint", "lightly_played", "moderately_played", "damaged"}
_LEGACY_SHIPPING_TO_PACKAGE = {
    "envelope": "card",
    "flat rate under 1 lb": "small",
    "flat rate 1-5 lbs": "medium",
    "flat rate 5-10 lbs": "large",
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            category_id TEXT,
            price REAL,
            condition_id TEXT DEFAULT '',
            upc TEXT DEFAULT 'Does not apply',
            description TEXT,
            sku TEXT UNIQUE,
            cost REAL,
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS product_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            file_name TEXT,
            local_path TEXT,
            hosted_url TEXT,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ebay_cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            fetched_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            label TEXT,
            batch_id INTEGER,
            status TEXT DEFAULT 'running',
            total INTEGER DEFAULT 0,
            done INTEGER DEFAULT 0,
            log TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        );
    """)

    _add_columns(conn, "products", {
        "brand": "TEXT DEFAULT ''",
        "quantity": "INTEGER DEFAULT 1",
        "cert_number": "TEXT DEFAULT ''",
        "grade": "TEXT DEFAULT ''",
        "grader": "TEXT DEFAULT ''",
        "title_locked": "INTEGER DEFAULT 0",
        "exported_at": "TIMESTAMP",
        "notes": "TEXT DEFAULT ''",
        "shipping_profile": "TEXT DEFAULT ''",
        "batch_id": "INTEGER",
        "category_name": "TEXT DEFAULT ''",
        "aspects": "TEXT DEFAULT '{}'",
        "condition_descriptors": "TEXT DEFAULT '{}'",
        "package": "TEXT DEFAULT ''",
        "ai": "TEXT DEFAULT '{}'",
        "comps": "TEXT DEFAULT '{}'",
        "ebay_offer_id": "TEXT DEFAULT ''",
        "ebay_listing_id": "TEXT DEFAULT ''",
        "listed_at": "TIMESTAMP",
        "publish_error": "TEXT DEFAULT ''",
        "updated_at": "TIMESTAMP",
    })
    _add_columns(conn, "product_images", {
        "batch_id": "INTEGER",
        "taken_at": "TEXT DEFAULT ''",
        "thumb_path": "TEXT DEFAULT ''",
    })
    _migrate_legacy_rows(conn)
    conn.execute("UPDATE jobs SET status = 'error', log = log || '\nInterrupted (app restarted)' WHERE status = 'running'")
    conn.commit()
    conn.close()


def _add_columns(conn, table, columns):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _migrate_legacy_rows(conn):
    """One-time upgrade of v1 rows: TCG condition strings -> numeric + descriptor,
    shipping profile names -> package size."""
    rows = conn.execute(
        "SELECT id, condition_id, shipping_profile, package FROM products "
        "WHERE condition_id IN ('near_mint','lightly_played','moderately_played','damaged') "
        "OR (COALESCE(package,'') = '' AND COALESCE(shipping_profile,'') != '')"
    ).fetchall()
    for r in rows:
        fields = {}
        if r["condition_id"] in _LEGACY_CARD_CONDITIONS:
            fields["condition_id"] = "4000"
            fields["condition_descriptors"] = json.dumps({"40001": "400010"})
        if not r["package"] and r["shipping_profile"] in _LEGACY_SHIPPING_TO_PACKAGE:
            fields["package"] = _LEGACY_SHIPPING_TO_PACKAGE[r["shipping_profile"]]
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE products SET {sets} WHERE id = ?", [*fields.values(), r["id"]])


# ---------------------------------------------------------------- helpers

def row_to_item(row, images=None):
    item = dict(row)
    for f in JSON_FIELDS:
        try:
            item[f] = json.loads(item.get(f) or "{}")
        except (TypeError, ValueError):
            item[f] = {}
    if images is not None:
        item["images"] = images
    return item


def image_dict(row):
    d = dict(row)
    d["url"] = "/photos/" + d["local_path"] if d.get("local_path") else ""
    d["thumb_url"] = f"/thumb/{d['id']}"
    return d


def images_for(conn, product_ids):
    """Batch-load images for many products -> {product_id: [image, ...]}."""
    out = {pid: [] for pid in product_ids}
    if not product_ids:
        return out
    marks = ",".join("?" * len(product_ids))
    for r in conn.execute(
        f"SELECT * FROM product_images WHERE product_id IN ({marks}) ORDER BY product_id, sort_order, id",
        list(product_ids),
    ):
        out[r["product_id"]].append(image_dict(r))
    return out


def get_item(conn, item_id):
    row = conn.execute("SELECT * FROM products WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return None
    return row_to_item(row, images_for(conn, [item_id])[item_id])


def get_items(conn, where="1=1", params=()):
    rows = conn.execute(f"SELECT * FROM products WHERE {where} ORDER BY id DESC", params).fetchall()
    imgs = images_for(conn, [r["id"] for r in rows])
    return [row_to_item(r, imgs[r["id"]]) for r in rows]


def update_item(conn, item_id, fields):
    if not fields:
        return
    fields = dict(fields)
    for f in JSON_FIELDS:
        if f in fields and not isinstance(fields[f], str):
            fields[f] = json.dumps(fields[f])
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE products SET {sets} WHERE id = ?", [*fields.values(), item_id])
    if "price" in fields or "package" in fields:
        # eBay Standard Envelope caps item value at $20 — move pricier items to a tracked mailer.
        conn.execute("UPDATE products SET package = 'small' WHERE id = ? AND package = 'card' AND price > ?",
                     (item_id, config.ENVELOPE_MAX_PRICE))
    conn.commit()


def next_sku(conn):
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(sku, 12) AS INTEGER)) AS n FROM products WHERE sku LIKE 'LOCAL-ITEM_%'"
    ).fetchone()
    n = (row["n"] or 0) + 1
    return f"LOCAL-ITEM_{n:03d}"


def item_kind(item):
    """Coarse type for UI badges: graded / card / item."""
    if item.get("cert_number") or item.get("grade"):
        return "graded"
    if str(item.get("category_id") or "") in CARD_CATEGORIES:
        return "card"
    return "item"


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


now = _now
