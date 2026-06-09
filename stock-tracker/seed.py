"""Seed the database with a few example themes and stocks.

Run once to avoid an empty UI: python seed.py
"""

import db
from market_data import SNAPSHOT_COLS, QuoteError, fetch_quote

THEMES = [
    ("AI infrastructure", "Compute, chips and platforms powering AI", "#6366f1"),
    ("Energy transition", "Clean energy, grid and electrification", "#22c55e"),
    ("Consumer staples", "Defensive, durable demand", "#f59e0b"),
]

STOCKS = {
    "NVDA": ["AI infrastructure"],
    "MSFT": ["AI infrastructure"],
    "NEE": ["Energy transition"],
    "KO": ["Consumer staples"],
}


def _insert_snapshot(stock_id, data):
    cols = ", ".join(["stock_id"] + SNAPSHOT_COLS)
    placeholders = ", ".join(["?"] * (len(SNAPSHOT_COLS) + 1))
    db.execute(
        f"INSERT INTO metric_snapshots ({cols}) VALUES ({placeholders})",
        [stock_id] + [data.get(c) for c in SNAPSHOT_COLS],
    )


def main():
    db.init_db()

    theme_ids = {}
    for name, desc, color in THEMES:
        existing = db.query("SELECT id FROM themes WHERE name = ?", (name,), one=True)
        if existing:
            theme_ids[name] = existing["id"]
        else:
            theme_ids[name] = db.execute(
                "INSERT INTO themes (name, description, color) VALUES (?, ?, ?)",
                (name, desc, color),
            )
            print(f"theme: {name}")

    for ticker, themes in STOCKS.items():
        if db.query("SELECT id FROM stocks WHERE ticker = ?", (ticker,), one=True):
            print(f"skip {ticker} (already present)")
            continue
        data = None
        try:
            data = fetch_quote(ticker)
        except QuoteError as err:
            print(f"warn {ticker}: {err}")
        stock_id = db.execute(
            "INSERT INTO stocks (ticker, name, exchange, currency, sector, industry) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, (data or {}).get("name", ticker), (data or {}).get("exchange"),
             (data or {}).get("currency"), (data or {}).get("sector"), (data or {}).get("industry")),
        )
        if data:
            _insert_snapshot(stock_id, data)
        for tname in themes:
            db.execute(
                "INSERT OR IGNORE INTO stock_themes (stock_id, theme_id) VALUES (?, ?)",
                (stock_id, theme_ids[tname]),
            )
        print(f"stock: {ticker} ({'metrics' if data else 'no metrics'})")

    print("Done. Run `python app.py` and open http://localhost:5000")


if __name__ == "__main__":
    main()
