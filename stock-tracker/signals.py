"""Investor-style buy / hold / sell signals.

Each indicator is graded against thresholds drawn from well-known value &
quality investing heuristics (Buffett, Graham, Lynch) and labelled:

    buy  (green)  - clearly favourable
    hold (yellow) - neutral / fair
    sell (red)    - clearly unfavourable
    na   (grey)   - missing or not a material signal

Thresholds are deliberately transparent and conservative - tune INDICATORS
to taste.
"""

from collections import Counter

BUY, HOLD, SELL, NA = "buy", "hold", "sell", "na"
_SCORE = {BUY: 1, HOLD: 0, SELL: -1}

_BUY_AT = 0.20
_SELL_AT = -0.20
_MIN_SIGNALS = 4


def _higher_better(v, buy, sell):
    if v >= buy:
        return BUY
    if v < sell:
        return SELL
    return HOLD


def _lower_better(v, buy, sell):
    if v <= buy:
        return BUY
    if v > sell:
        return SELL
    return HOLD


def _dividend(v):
    if not v:
        return NA
    if 2.5 <= v <= 6.0:
        return BUY
    return HOLD


INDICATORS = [
    # Quality / moat (Buffett)
    dict(key="return_on_equity", label="ROE", group="Quality", weight=2.0, fmt="pct", dir="higher", buy=0.15, sell=0.08),
    dict(key="return_on_assets", label="ROA", group="Quality", weight=1.0, fmt="pct", dir="higher", buy=0.08, sell=0.03),
    dict(key="gross_margin", label="Gross margin", group="Quality", weight=1.5, fmt="pct", dir="higher", buy=0.40, sell=0.20),
    dict(key="operating_margin", label="Operating margin", group="Quality", weight=1.5, fmt="pct", dir="higher", buy=0.20, sell=0.10),
    dict(key="profit_margin", label="Profit margin", group="Quality", weight=1.0, fmt="pct", dir="higher", buy=0.15, sell=0.05),
    # Valuation (Graham / Lynch)
    dict(key="trailing_pe", label="Actual P/E", group="Valuation", weight=1.5, fmt="num", dir="lower", buy=15, sell=25, positive=True),
    dict(key="forward_pe", label="Forward P/E", group="Valuation", weight=1.0, fmt="num", dir="lower", buy=15, sell=25, positive=True),
    dict(key="peg_ratio", label="PEG", group="Valuation", weight=1.5, fmt="num", dir="lower", buy=1.0, sell=2.0, positive=True),
    dict(key="price_to_book", label="P/B", group="Valuation", weight=0.5, fmt="num", dir="lower", buy=1.5, sell=5.0, positive=True),
    dict(key="fcf_yield", label="FCF yield", group="Valuation", weight=1.5, fmt="pctraw", dir="higher", buy=5.0, sell=2.0),
    dict(key="upside_to_target", label="Upside to target", group="Valuation", weight=1.0, fmt="pctraw", dir="higher", buy=15.0, sell=0.0),
    # Growth
    dict(key="revenue_growth", label="Revenue growth", group="Growth", weight=1.0, fmt="pct", dir="higher", buy=0.10, sell=0.03),
    dict(key="earnings_growth", label="Earnings growth", group="Growth", weight=1.0, fmt="pct", dir="higher", buy=0.10, sell=0.0),
    # Financial health
    dict(key="debt_to_equity", label="Debt / Equity", group="Financial health", weight=1.5, fmt="de", dir="lower", buy=50, sell=150),
    dict(key="current_ratio", label="Current ratio", group="Financial health", weight=1.0, fmt="num", dir="higher", buy=1.5, sell=1.0),
    # Income
    dict(key="dividend_yield", label="Dividend yield", group="Income", weight=0.5, fmt="pctraw", dir="dividend"),
    # Sentiment
    dict(key="recommendation_mean", label="Analyst consensus", group="Sentiment", weight=1.0, fmt="rec", dir="lower", buy=2.0, sell=3.5, positive=True),
]


def _classify(spec, v):
    if spec["dir"] == "dividend":
        return _dividend(v)
    if v is None:
        return NA
    if spec.get("positive") and v <= 0:
        return NA
    if spec["dir"] == "higher":
        return _higher_better(v, spec["buy"], spec["sell"])
    return _lower_better(v, spec["buy"], spec["sell"])


def _format(spec, v):
    if v is None:
        return "—"
    fmt = spec["fmt"]
    if fmt == "pct":
        return f"{v * 100:.1f}%"
    if fmt == "pctraw":
        return f"{v:.1f}%"
    if fmt == "de":
        return f"{v / 100:.2f}x"
    if fmt == "rec":
        return f"{v:.2f} / 5"
    return f"{v:.2f}"


def _derive(snapshot):
    values = dict(snapshot)
    fcf, mcap = values.get("free_cashflow"), values.get("market_cap")
    values["fcf_yield"] = (fcf / mcap * 100) if (fcf and mcap) else None
    target, price = values.get("target_mean_price"), values.get("price")
    values["upside_to_target"] = ((target - price) / price * 100) if (target and price) else None
    return values


def evaluate(snapshot):
    """Grade a metric snapshot.

    Returns results, by_key, grouped, and overall rating.
    snapshot may be None (no metrics yet) -> UNCLEAR.
    """
    results = []
    if snapshot is not None:
        values = _derive(snapshot)
        for spec in INDICATORS:
            v = values.get(spec["key"])
            signal = _classify(spec, v)
            results.append({
                "key": spec["key"],
                "label": spec["label"],
                "group": spec["group"],
                "value": v,
                "display": _format(spec, v),
                "signal": signal,
            })

    grouped = []
    for r in results:
        if not grouped or grouped[-1][0] != r["group"]:
            grouped.append((r["group"], []))
        grouped[-1][1].append(r)

    counts = Counter(r["signal"] for r in results)
    material = [(r, spec) for r, spec in zip(results, INDICATORS) if r["signal"] != NA]

    if len(material) < _MIN_SIGNALS:
        rating, signal, score = "UNCLEAR", NA, 0.0
    else:
        total_w = sum(spec["weight"] for _, spec in material)
        score = sum(spec["weight"] * _SCORE[r["signal"]] for r, spec in material) / total_w
        if score >= _BUY_AT:
            rating, signal = "BUY", BUY
        elif score <= _SELL_AT:
            rating, signal = "SELL", SELL
        else:
            rating, signal = "HOLD", HOLD

    return {
        "results": results,
        "grouped": grouped,
        "by_key": {r["key"]: r for r in results},
        "overall": {
            "rating": rating,
            "signal": signal,
            "score": round(score, 3),
            "counts": {"buy": counts[BUY], "hold": counts[HOLD], "sell": counts[SELL], "na": counts[NA]},
        },
    }
