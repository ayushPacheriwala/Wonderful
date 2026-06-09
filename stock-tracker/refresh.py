"""CLI to snapshot metrics for every tracked stock.

Run manually or on a schedule (cron) to build up metric history:

    python refresh.py
"""

import db
from market_data import SNAPSHOT_COLS, QuoteError, fetch_quote


def main():
    db.init_db()
    stocks = db.query("SELECT id, ticker FROM stocks ORDER BY ticker")
    if not stocks:
        print("No stocks to refresh.")
        return

    ok, failed = 0, []
    for s in stocks:
        try:
            data = fetch_quote(s["ticker"])
            cols = ", ".join(["stock_id"] + SNAPSHOT_COLS)
            placeholders = ", ".join(["?"] * (len(SNAPSHOT_COLS) + 1))
            db.execute(
                f"INSERT INTO metric_snapshots ({cols}) VALUES ({placeholders})",
                [s["id"]] + [data.get(c) for c in SNAPSHOT_COLS],
            )
            ok += 1
            print(f"ok   {s['ticker']}  price={data.get('price')}")
        except QuoteError as err:
            failed.append(s["ticker"])
            print(f"fail {s['ticker']}: {err}")

    print(f"\nRefreshed {ok}/{len(stocks)}." + (f" Failed: {', '.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
