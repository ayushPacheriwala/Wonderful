"""Flask web app for systematic stock tracking.

Routes cover three v1 pillars: themes & tagging, metrics-over-time, and a
decision journal. Metrics are auto-fetched via yfinance (see market_data.py).
"""

import json

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

import db
import signals
import valuation
from market_data import SNAPSHOT_COLS, QuoteError, fetch_quote

app = Flask(__name__)
app.secret_key = "dev-stock-tracker"  # only used for flash messages

# Allowed journal entry types (kept in sync with the form + schema comment).
ENTRY_TYPES = ["thesis", "mgmt_decision", "note", "buy", "sell", "risk"]


# --- Snapshot helper -----------------------------------------------------

def _insert_snapshot(stock_id, data):
    """Insert one metric_snapshots row from a fetch_quote() dict."""
    cols = ", ".join(["stock_id"] + SNAPSHOT_COLS)
    placeholders = ", ".join(["?"] * (len(SNAPSHOT_COLS) + 1))
    values = [stock_id] + [data.get(c) for c in SNAPSHOT_COLS]
    db.execute(f"INSERT INTO metric_snapshots ({cols}) VALUES ({placeholders})", values)


def refresh_stock(stock_id, ticker):
    """Fetch fresh data for a stock and store a new snapshot.

    Returns the fetched dict. Raises QuoteError on failure (caller flashes it).
    """
    data = fetch_quote(ticker)
    _insert_snapshot(stock_id, data)
    return data


def latest_snapshot(stock_id):
    """Return the most recent metric snapshot for a stock (or None)."""
    return db.query(
        "SELECT * FROM metric_snapshots WHERE stock_id = ? "
        "ORDER BY captured_at DESC LIMIT 1",
        (stock_id,), one=True,
    )


def theme_peer_snapshots(stock_id):
    """Latest snapshots (as dicts) of stocks sharing a theme with this one."""
    rows = db.query(
        "SELECT DISTINCT st2.stock_id AS id FROM stock_themes st1 "
        "JOIN stock_themes st2 ON st2.theme_id = st1.theme_id "
        "WHERE st1.stock_id = ? AND st2.stock_id != ?",
        (stock_id, stock_id),
    )
    snaps = (latest_snapshot(r["id"]) for r in rows)
    return [dict(s) for s in snaps if s is not None]


# --- Dashboard -----------------------------------------------------------

@app.route("/")
def dashboard():
    themes = db.query(
        """
        SELECT t.*, COUNT(st.stock_id) AS stock_count
        FROM themes t
        LEFT JOIN stock_themes st ON st.theme_id = t.id
        GROUP BY t.id
        ORDER BY t.name
        """
    )
    recent = db.query(
        """
        SELECT s.*, ms.price, ms.captured_at
        FROM stocks s
        LEFT JOIN metric_snapshots ms ON ms.id = (
            SELECT id FROM metric_snapshots
            WHERE stock_id = s.id ORDER BY captured_at DESC LIMIT 1
        )
        ORDER BY s.created_at DESC
        LIMIT 8
        """
    )
    totals = {
        "stocks": db.query("SELECT COUNT(*) AS c FROM stocks", one=True)["c"],
        "themes": db.query("SELECT COUNT(*) AS c FROM themes", one=True)["c"],
        "entries": db.query("SELECT COUNT(*) AS c FROM journal_entries", one=True)["c"],
    }
    return render_template("dashboard.html", themes=themes, recent=recent, totals=totals)


# --- Stocks list ---------------------------------------------------------

@app.route("/stocks")
def stocks_list():
    theme_id = request.args.get("theme", type=int)
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "name")

    sort_map = {
        "name": "s.name COLLATE NOCASE",
        "ticker": "s.ticker",
        "price": "price DESC",
        "market_cap": "market_cap DESC",
        "added": "s.created_at DESC",
    }
    order_by = sort_map.get(sort, sort_map["name"])

    sql = [
        """
        SELECT s.*, ms.price, ms.market_cap, ms.trailing_pe, ms.captured_at
        FROM stocks s
        LEFT JOIN metric_snapshots ms ON ms.id = (
            SELECT id FROM metric_snapshots
            WHERE stock_id = s.id ORDER BY captured_at DESC LIMIT 1
        )
        """
    ]
    params = []
    if theme_id:
        sql.append("JOIN stock_themes st ON st.stock_id = s.id AND st.theme_id = ?")
        params.append(theme_id)
    if q:
        sql.append("WHERE (s.ticker LIKE ? OR s.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    sql.append(f"ORDER BY {order_by}")

    stocks = db.query("\n".join(sql), params)
    ratings, valuations = {}, {}
    for s in stocks:
        snap = latest_snapshot(s["id"])
        ratings[s["id"]] = signals.evaluate(snap)
        valuations[s["id"]] = valuation.assess(
            dict(snap) if snap else None, theme_peer_snapshots(s["id"]), []
        )["overall"]
    themes = db.query("SELECT * FROM themes ORDER BY name")
    active_theme = db.query("SELECT * FROM themes WHERE id = ?", (theme_id,), one=True) if theme_id else None
    return render_template(
        "stocks.html", stocks=stocks, themes=themes, ratings=ratings,
        valuations=valuations, active_theme=active_theme, q=q, sort=sort,
    )


@app.route("/stocks", methods=["POST"])
def add_stock():
    ticker = (request.form.get("ticker") or "").strip().upper()
    if not ticker:
        flash("Please enter a ticker.", "error")
        return redirect(url_for("stocks_list"))

    existing = db.query("SELECT id FROM stocks WHERE ticker = ?", (ticker,), one=True)
    if existing:
        flash(f"{ticker} is already tracked.", "error")
        return redirect(url_for("stock_detail", stock_id=existing["id"]))

    data = None
    try:
        data = fetch_quote(ticker)
    except QuoteError as err:
        flash(f"Added {ticker}, but couldn't fetch metrics ({err}). Try Refresh later.", "error")

    stock_id = db.execute(
        "INSERT INTO stocks (ticker, name, exchange, currency, sector, industry) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ticker,
            (data or {}).get("name", ticker),
            (data or {}).get("exchange"),
            (data or {}).get("currency"),
            (data or {}).get("sector"),
            (data or {}).get("industry"),
        ),
    )
    if data:
        _insert_snapshot(stock_id, data)
        flash(f"Added {ticker} with the latest metrics.", "success")

    for theme_id in request.form.getlist("theme_ids", type=int):
        db.execute(
            "INSERT OR IGNORE INTO stock_themes (stock_id, theme_id) VALUES (?, ?)",
            (stock_id, theme_id),
        )
    return redirect(url_for("stock_detail", stock_id=stock_id))


# --- Stock detail --------------------------------------------------------

@app.route("/stocks/<int:stock_id>")
def stock_detail(stock_id):
    stock = db.query("SELECT * FROM stocks WHERE id = ?", (stock_id,), one=True)
    if not stock:
        abort(404)

    latest = latest_snapshot(stock_id)
    evaluation = signals.evaluate(latest)
    history = db.query(
        "SELECT * FROM metric_snapshots WHERE stock_id = ? ORDER BY captured_at ASC",
        (stock_id,),
    )
    valuation_assessment = valuation.assess(
        dict(latest) if latest else None,
        theme_peer_snapshots(stock_id),
        [dict(h) for h in history],
    )
    stock_themes = db.query(
        """
        SELECT t.* FROM themes t
        JOIN stock_themes st ON st.theme_id = t.id
        WHERE st.stock_id = ? ORDER BY t.name
        """,
        (stock_id,),
    )
    all_themes = db.query("SELECT * FROM themes ORDER BY name")
    journal = db.query(
        "SELECT * FROM journal_entries WHERE stock_id = ? "
        "ORDER BY entry_date DESC, id DESC",
        (stock_id,),
    )
    snapshot_count = db.query(
        "SELECT COUNT(*) AS c FROM metric_snapshots WHERE stock_id = ?",
        (stock_id,), one=True,
    )["c"]
    return render_template(
        "stock_detail.html", stock=stock, latest=latest, evaluation=evaluation,
        valuation=valuation_assessment, stock_themes=stock_themes,
        all_themes=all_themes, journal=journal, entry_types=ENTRY_TYPES,
        snapshot_count=snapshot_count,
    )


@app.route("/stocks/<int:stock_id>/refresh", methods=["POST"])
def refresh_stock_route(stock_id):
    stock = db.query("SELECT * FROM stocks WHERE id = ?", (stock_id,), one=True)
    if not stock:
        abort(404)
    try:
        refresh_stock(stock_id, stock["ticker"])
        flash(f"Refreshed metrics for {stock['ticker']}.", "success")
    except QuoteError as err:
        flash(f"Couldn't refresh {stock['ticker']}: {err}", "error")
    return redirect(url_for("stock_detail", stock_id=stock_id))


@app.route("/stocks/<int:stock_id>/delete", methods=["POST"])
def delete_stock(stock_id):
    db.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
    flash("Stock removed.", "success")
    return redirect(url_for("stocks_list"))


@app.route("/stocks/<int:stock_id>/themes", methods=["POST"])
def update_stock_themes(stock_id):
    if not db.query("SELECT id FROM stocks WHERE id = ?", (stock_id,), one=True):
        abort(404)
    selected = set(request.form.getlist("theme_ids", type=int))
    db.execute("DELETE FROM stock_themes WHERE stock_id = ?", (stock_id,))
    for theme_id in selected:
        db.execute(
            "INSERT OR IGNORE INTO stock_themes (stock_id, theme_id) VALUES (?, ?)",
            (stock_id, theme_id),
        )
    flash("Themes updated.", "success")
    return redirect(url_for("stock_detail", stock_id=stock_id))


@app.route("/stocks/<int:stock_id>/journal", methods=["POST"])
def add_journal_entry(stock_id):
    if not db.query("SELECT id FROM stocks WHERE id = ?", (stock_id,), one=True):
        abort(404)
    entry_type = request.form.get("entry_type", "note")
    if entry_type not in ENTRY_TYPES:
        entry_type = "note"
    conviction = request.form.get("conviction", type=int)
    entry_date = (request.form.get("entry_date") or "").strip() or None

    db.execute(
        "INSERT INTO journal_entries (stock_id, entry_date, entry_type, title, body, conviction) "
        "VALUES (?, COALESCE(?, date('now')), ?, ?, ?, ?)",
        (
            stock_id, entry_date, entry_type,
            (request.form.get("title") or "").strip(),
            (request.form.get("body") or "").strip(),
            conviction,
        ),
    )
    flash("Journal entry added.", "success")
    return redirect(url_for("stock_detail", stock_id=stock_id))


@app.route("/journal/<int:entry_id>/delete", methods=["POST"])
def delete_journal_entry(entry_id):
    entry = db.query("SELECT stock_id FROM journal_entries WHERE id = ?", (entry_id,), one=True)
    if entry:
        db.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
        flash("Entry deleted.", "success")
        return redirect(url_for("stock_detail", stock_id=entry["stock_id"]))
    return redirect(url_for("dashboard"))


@app.route("/themes")
def themes_list():
    themes = db.query(
        """
        SELECT t.*, COUNT(st.stock_id) AS stock_count
        FROM themes t
        LEFT JOIN stock_themes st ON st.theme_id = t.id
        GROUP BY t.id ORDER BY t.name
        """
    )
    return render_template("themes.html", themes=themes)


@app.route("/themes", methods=["POST"])
def add_theme():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Theme name is required.", "error")
        return redirect(url_for("themes_list"))
    try:
        db.execute(
            "INSERT INTO themes (name, description, color) VALUES (?, ?, ?)",
            (name, (request.form.get("description") or "").strip(),
             request.form.get("color") or "#6366f1"),
        )
        flash(f"Theme '{name}' created.", "success")
    except Exception:
        flash(f"Theme '{name}' already exists.", "error")
    return redirect(url_for("themes_list"))


@app.route("/themes/<int:theme_id>")
def theme_detail(theme_id):
    theme = db.query("SELECT * FROM themes WHERE id = ?", (theme_id,), one=True)
    if not theme:
        abort(404)
    stocks = db.query(
        """
        SELECT s.*, ms.price, ms.market_cap, ms.captured_at
        FROM stocks s
        JOIN stock_themes st ON st.stock_id = s.id AND st.theme_id = ?
        LEFT JOIN metric_snapshots ms ON ms.id = (
            SELECT id FROM metric_snapshots
            WHERE stock_id = s.id ORDER BY captured_at DESC LIMIT 1
        )
        ORDER BY s.name COLLATE NOCASE
        """,
        (theme_id,),
    )
    return render_template("theme.html", theme=theme, stocks=stocks)


@app.route("/themes/<int:theme_id>/delete", methods=["POST"])
def delete_theme(theme_id):
    db.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
    flash("Theme deleted.", "success")
    return redirect(url_for("themes_list"))


@app.route("/api/stocks/<int:stock_id>/metrics")
def metrics_api(stock_id):
    rows = db.query(
        "SELECT captured_at, price, trailing_pe, dividend_yield "
        "FROM metric_snapshots WHERE stock_id = ? ORDER BY captured_at ASC",
        (stock_id,),
    )
    return {
        "labels": [r["captured_at"] for r in rows],
        "price": [r["price"] for r in rows],
        "trailing_pe": [r["trailing_pe"] for r in rows],
        "dividend_yield": [r["dividend_yield"] for r in rows],
    }


@app.route("/refresh-all", methods=["POST"])
def refresh_all_route():
    stocks = db.query("SELECT id, ticker FROM stocks")
    ok, failed = 0, []
    for s in stocks:
        try:
            refresh_stock(s["id"], s["ticker"])
            ok += 1
        except QuoteError:
            failed.append(s["ticker"])
    msg = f"Refreshed {ok} stock(s)."
    if failed:
        msg += f" Failed: {', '.join(failed)}."
    flash(msg, "success" if not failed else "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.template_filter("human_num")
def human_num(value):
    """Format large numbers (market cap) as 1.2T / 3.4B / 5.6M."""
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return f"{value / size:.2f}{unit}"
    return f"{value:,.2f}"


@app.template_filter("pct")
def pct(value):
    """Format a fraction (0.23) as a percentage (23.0%)."""
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


@app.template_filter("num")
def num(value, digits=2):
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


@app.template_filter("title_key")
def title_key(value):
    """Format a key like 'strong_buy' as 'Strong Buy'."""
    if not value:
        return "—"
    return str(value).replace("_", " ").title()


db.init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
