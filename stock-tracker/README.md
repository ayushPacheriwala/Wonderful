# Stock Tracker

A small personal web app for **systematic investing**: track a watchlist of
stocks, organise them by **themes**, watch key **metrics evolve over time**, and
keep a dated **decision journal** (thesis, management decisions, risks,
buy/sell rationale, conviction).

Built with Flask + SQLite. Metrics are auto-fetched from Yahoo Finance via
[`yfinance`](https://github.com/ranaroussi/yfinance) - no API key required.

## Features

- **Themes & tagging** - group stocks (e.g. "AI infrastructure", "Energy
  transition"), multi-tag, and filter the watchlist by theme.
- **Metrics tracking** - price, market cap, actual (TTM) & forward P/E, dividend
  yield, EPS, profit/gross/operating margins, revenue & earnings growth, 52-week
  range and beta. Each refresh stores a snapshot, so you build a **history**
  that's charted on the stock page.
- **Analyst consensus** - recommendation (e.g. Strong Buy) and score, number of
  covering analysts, mean/high/low target price, and computed upside to target.
- **Investor signals & ratings** - every indicator is graded **green (buy) /
  yellow (hold) / red (sell) / grey (unclear)** using value & quality thresholds
  (Buffett/Graham/Lynch), and rolled up into an **overall BUY / HOLD / SELL**
  rating shown on each stock and in the watchlist's Rating column.

### Indicators behind the signals

| Group | Indicators | Lens |
|---|---|---|
| Quality | ROE, ROA, gross/operating/profit margin | Buffett - capital efficiency & moat |
| Valuation | Actual & forward P/E, PEG, P/B, FCF yield, upside to target | Graham/Lynch - price vs worth |
| Growth | Revenue growth, earnings growth | - |
| Financial health | Debt/Equity, current ratio | Balance-sheet strength |
| Income | Dividend yield | - |
| Sentiment | Analyst consensus score | Street view |

Thresholds live in `signals.py` (`INDICATORS`) and are easy to tune.

### Is the price reasonable? (valuation)

Each stock has a **Valuation** panel (and a **Value** column in the watchlist)
that triangulates fair value four ways - see `valuation.py`:

1. **Peer multiples** - P/E, fwd P/E, PEG, P/B, P/S, EV/EBITDA vs the **median
   of peers sharing a theme**, with premium/discount %.
2. **Fair-value anchors** - peer-implied prices + **Graham number** + analyst
   target, blended into one fair value & implied up/downside.
3. **Own-history bands** - where today's multiple sits within the stock's own
   tracked range (mean reversion).
4. **Yield & reverse-DCF** - earnings yield vs the risk-free rate, FCF yield,
   and the 5-yr FCF growth the current price implies.

Assumptions tunable via env vars: `RISK_FREE_RATE_PCT` (default 4.3),
`DISCOUNT_RATE` (0.09), `TERMINAL_GROWTH` (0.025).

- **Decision journal** - dated entries typed as thesis / management decision /
  note / buy / sell / risk, each with an optional 1-5 conviction rating.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python seed.py        # optional: example themes + stocks
python app.py         # http://localhost:5000
```

## Building metric history

Every "Refresh" appends a new snapshot. Schedule the CLI to capture history:

```bash
python refresh.py
# cron example - every weekday at 22:00:
# 0 22 * * 1-5  cd /path/to/stock-tracker && .venv/bin/python refresh.py
```

## Data model

| Table | Purpose |
|---|---|
| `stocks` | one row per ticker |
| `themes` / `stock_themes` | themes and their many-to-many links to stocks |
| `metric_snapshots` | one row per refresh - metric history over time |
| `journal_entries` | dated decision-journal notes with type + conviction |

The SQLite file (`stocks.db`) is created automatically on first run and is
git-ignored. Override its location with the `STOCK_TRACKER_DB` env var.

## Notes & limitations

- `yfinance` scrapes Yahoo Finance and can occasionally rate-limit or return
  sparse data. The app degrades gracefully: a stock is still added if the fetch
  fails, and you can Refresh later.
- v1 is a **watchlist / research journal** - holdings with cost basis & P&L,
  price-target alerts, and auth are intentionally out of scope.
