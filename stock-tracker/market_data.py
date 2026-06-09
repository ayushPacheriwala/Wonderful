"""Market-data fetching via yfinance.

yfinance is free and key-less but can be flaky (rate limits, sparse data for
some tickers). Every public function here degrades gracefully: it returns
``None`` rather than raising so callers/routes can show a friendly message.
"""

import json

SNAPSHOT_COLS = [
    "price", "market_cap", "enterprise_value", "trailing_pe", "forward_pe",
    "peg_ratio", "price_to_book", "price_to_sales", "ev_to_ebitda",
    "book_value", "dividend_yield", "eps",
    "profit_margin", "gross_margin", "operating_margin",
    "return_on_equity", "return_on_assets",
    "revenue_growth", "earnings_growth",
    "debt_to_equity", "current_ratio", "free_cashflow",
    "recommendation_key", "recommendation_mean", "num_analysts",
    "target_mean_price", "target_high_price", "target_low_price",
    "fifty_two_week_high", "fifty_two_week_low", "beta", "raw_json",
]


class QuoteError(Exception):
    """Raised when a ticker can't be resolved or data can't be fetched."""


def _safe(info, *keys):
    for key in keys:
        val = info.get(key)
        if val is not None:
            return val
    return None


def fetch_quote(ticker):
    """Fetch a metrics snapshot for ``ticker``.

    Returns a dict with profile fields (name/sector/...) and metric fields
    matching the ``metric_snapshots`` columns, plus ``raw_json``. Raises
    :class:`QuoteError` if the ticker can't be resolved.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise QuoteError("Empty ticker.")

    try:
        import yfinance as yf
    except ImportError as exc:
        raise QuoteError("yfinance is not installed.") from exc

    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        raise QuoteError(f"Could not fetch data for {ticker}: {exc}") from exc

    name = _safe(info, "longName", "shortName", "displayName")
    price = _safe(info, "currentPrice", "regularMarketPrice", "previousClose")
    if not name and price is None:
        raise QuoteError(f"'{ticker}' did not resolve to a known instrument.")

    div_yield = _safe(info, "dividendYield")
    if div_yield is not None and div_yield < 1:
        div_yield *= 100

    return {
        "ticker": ticker,
        "name": name or ticker,
        "exchange": _safe(info, "exchange", "fullExchangeName"),
        "currency": _safe(info, "currency"),
        "sector": _safe(info, "sector"),
        "industry": _safe(info, "industry"),
        "price": price,
        "market_cap": _safe(info, "marketCap"),
        "enterprise_value": _safe(info, "enterpriseValue"),
        "trailing_pe": _safe(info, "trailingPE"),
        "forward_pe": _safe(info, "forwardPE"),
        "peg_ratio": _safe(info, "trailingPegRatio", "pegRatio"),
        "price_to_book": _safe(info, "priceToBook"),
        "price_to_sales": _safe(info, "priceToSalesTrailing12Months"),
        "ev_to_ebitda": _safe(info, "enterpriseToEbitda"),
        "book_value": _safe(info, "bookValue"),
        "dividend_yield": div_yield,
        "eps": _safe(info, "trailingEps", "epsTrailingTwelveMonths"),
        "profit_margin": _safe(info, "profitMargins"),
        "gross_margin": _safe(info, "grossMargins"),
        "operating_margin": _safe(info, "operatingMargins"),
        "return_on_equity": _safe(info, "returnOnEquity"),
        "return_on_assets": _safe(info, "returnOnAssets"),
        "revenue_growth": _safe(info, "revenueGrowth"),
        "earnings_growth": _safe(info, "earningsGrowth", "earningsQuarterlyGrowth"),
        "debt_to_equity": _safe(info, "debtToEquity"),
        "current_ratio": _safe(info, "currentRatio"),
        "free_cashflow": _safe(info, "freeCashflow"),
        "recommendation_key": _safe(info, "recommendationKey"),
        "recommendation_mean": _safe(info, "recommendationMean"),
        "num_analysts": _safe(info, "numberOfAnalystOpinions"),
        "target_mean_price": _safe(info, "targetMeanPrice"),
        "target_high_price": _safe(info, "targetHighPrice"),
        "target_low_price": _safe(info, "targetLowPrice"),
        "fifty_two_week_high": _safe(info, "fiftyTwoWeekHigh"),
        "fifty_two_week_low": _safe(info, "fiftyTwoWeekLow"),
        "beta": _safe(info, "beta"),
        "raw_json": json.dumps(info),
    }


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    try:
        data = fetch_quote(symbol)
        printable = {k: v for k, v in data.items() if k != "raw_json"}
        print(json.dumps(printable, indent=2))
    except QuoteError as err:
        print(f"ERROR: {err}")
