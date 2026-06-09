-- Schema for the stock-tracker app.
-- Safe to run repeatedly: every statement uses IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS stocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL UNIQUE,
    name        TEXT,
    exchange    TEXT,
    currency    TEXT,
    sector      TEXT,
    industry    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS themes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    color       TEXT DEFAULT '#6366f1'
);

CREATE TABLE IF NOT EXISTS stock_themes (
    stock_id    INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    theme_id    INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    PRIMARY KEY (stock_id, theme_id)
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id            INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    captured_at         TEXT NOT NULL DEFAULT (datetime('now')),
    price               REAL,
    market_cap          REAL,
    enterprise_value    REAL,
    trailing_pe         REAL,
    forward_pe          REAL,
    peg_ratio           REAL,
    price_to_book       REAL,
    price_to_sales      REAL,
    ev_to_ebitda        REAL,
    book_value          REAL,
    dividend_yield      REAL,
    eps                 REAL,
    profit_margin       REAL,
    gross_margin        REAL,
    operating_margin    REAL,
    return_on_equity    REAL,
    return_on_assets    REAL,
    revenue_growth      REAL,
    earnings_growth     REAL,
    debt_to_equity      REAL,
    current_ratio       REAL,
    free_cashflow       REAL,
    recommendation_key  TEXT,
    recommendation_mean REAL,
    num_analysts        INTEGER,
    target_mean_price   REAL,
    target_high_price   REAL,
    target_low_price    REAL,
    fifty_two_week_high REAL,
    fifty_two_week_low  REAL,
    beta                REAL,
    raw_json            TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_stock_time
    ON metric_snapshots (stock_id, captured_at);

CREATE TABLE IF NOT EXISTS journal_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id    INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    entry_date  TEXT NOT NULL DEFAULT (date('now')),
    entry_type  TEXT NOT NULL DEFAULT 'note',
    title       TEXT,
    body        TEXT,
    conviction  INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_stock
    ON journal_entries (stock_id, entry_date);
