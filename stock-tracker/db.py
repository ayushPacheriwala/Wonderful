"""Lightweight SQLite access layer for the stock-tracker app.

No ORM — just thin helpers around the stdlib ``sqlite3`` module. Rows come back
as ``sqlite3.Row`` objects, which behave like dicts (``row["ticker"]``).
"""

import os
import sqlite3

DB_PATH = os.environ.get(
    "STOCK_TRACKER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.db"),
)
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SNAPSHOT_MIGRATIONS = [
    ("gross_margin", "REAL"),
    ("operating_margin", "REAL"),
    ("earnings_growth", "REAL"),
    ("recommendation_key", "TEXT"),
    ("recommendation_mean", "REAL"),
    ("num_analysts", "INTEGER"),
    ("target_mean_price", "REAL"),
    ("target_high_price", "REAL"),
    ("target_low_price", "REAL"),
    ("peg_ratio", "REAL"),
    ("price_to_book", "REAL"),
    ("return_on_equity", "REAL"),
    ("return_on_assets", "REAL"),
    ("debt_to_equity", "REAL"),
    ("current_ratio", "REAL"),
    ("free_cashflow", "REAL"),
    ("enterprise_value", "REAL"),
    ("price_to_sales", "REAL"),
    ("ev_to_ebitda", "REAL"),
    ("book_value", "REAL"),
]


def _migrate(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(metric_snapshots)")}
    for col, col_type in _SNAPSHOT_MIGRATIONS:
        if col not in existing:
            conn.execute(f"ALTER TABLE metric_snapshots ADD COLUMN {col} {col_type}")


def init_db():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema = fh.read()
    conn = get_connection()
    try:
        conn.executescript(schema)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def query(sql, params=(), one=False):
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        conn.close()


def execute(sql, params=()):
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
