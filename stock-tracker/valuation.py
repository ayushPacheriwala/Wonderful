"""Is the current price reasonable? Four complementary valuation lenses.

1. Peer multiples   - compare each multiple to the median of theme peers.
2. Fair-value anchors - peer-implied prices + Graham number + analyst target.
3. Own-history bands - where today's multiple sits within the stock's own range.
4. Yield / reverse-DCF - earnings yield vs risk-free rate, implied FCF growth.
"""

import os

from signals import BUY, HOLD, SELL, NA

MULTIPLES = [
    ("trailing_pe", "P/E"),
    ("forward_pe", "Fwd P/E"),
    ("peg_ratio", "PEG"),
    ("price_to_book", "P/B"),
    ("price_to_sales", "P/S"),
    ("ev_to_ebitda", "EV/EBITDA"),
]

RISK_FREE_PCT = float(os.environ.get("RISK_FREE_RATE_PCT", "4.3"))
DISCOUNT_RATE = float(os.environ.get("DISCOUNT_RATE", "0.09"))
TERMINAL_GROWTH = float(os.environ.get("TERMINAL_GROWTH", "0.025"))

_CHEAP, _RICH = -10.0, 10.0
_UNDER, _OVER = 15.0, -15.0


def _median(values):
    vals = sorted(v for v in values if v is not None and v > 0)
    if not vals:
        return None
    n, mid = len(vals), len(vals) // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _verdict_from_upside(upside):
    if upside is None:
        return NA, "Unclear"
    if upside >= _UNDER:
        return BUY, "Undervalued"
    if upside <= _OVER:
        return SELL, "Overvalued"
    return HOLD, "Fair"


def peer_comparison(target, peers):
    rows = []
    for key, label in MULTIPLES:
        tv = target.get(key)
        pm = _median([p.get(key) for p in peers])
        n = len([p for p in peers if p.get(key) and p.get(key) > 0])
        premium = signal = None
        if tv and tv > 0 and pm:
            premium = (tv - pm) / pm * 100
            signal = BUY if premium <= _CHEAP else SELL if premium >= _RICH else HOLD
        else:
            signal = NA
        rows.append({
            "key": key, "label": label, "value": tv,
            "peer_median": pm, "n": n, "premium": premium, "signal": signal,
        })
    return rows


def fair_value(target, peers):
    eps = target.get("eps")
    price = target.get("price")
    mcap = target.get("market_cap")
    shares = (mcap / price) if (mcap and price) else None
    anchors = []

    pm_pe = _median([p.get("trailing_pe") for p in peers])
    if pm_pe and eps and eps > 0:
        anchors.append(("Peer P/E", pm_pe * eps))

    pm_ps = _median([p.get("price_to_sales") for p in peers])
    ps = target.get("price_to_sales")
    if pm_ps and ps and ps > 0 and price:
        sales_per_share = price / ps
        anchors.append(("Peer P/S", pm_ps * sales_per_share))

    pm_ev = _median([p.get("ev_to_ebitda") for p in peers])
    ev = target.get("enterprise_value")
    evebitda = target.get("ev_to_ebitda")
    if pm_ev and ev and evebitda and evebitda > 0 and shares and mcap:
        ebitda = ev / evebitda
        net_debt = ev - mcap
        implied_equity = pm_ev * ebitda - net_debt
        if implied_equity > 0:
            anchors.append(("Peer EV/EBITDA", implied_equity / shares))

    bv = target.get("book_value")
    if eps and eps > 0 and bv and bv > 0:
        anchors.append(("Graham number", (22.5 * eps * bv) ** 0.5))

    target_price = target.get("target_mean_price")
    if target_price:
        anchors.append(("Analyst target", target_price))

    prices = [p for _, p in anchors]
    blend = sum(prices) / len(prices) if prices else None
    upside = ((blend - price) / price * 100) if (blend and price) else None
    signal, verdict = _verdict_from_upside(upside)

    return {
        "anchors": [
            {"label": lbl, "price": p,
             "upside": ((p - price) / price * 100) if price else None}
            for lbl, p in anchors
        ],
        "blend": blend, "upside": upside, "signal": signal, "verdict": verdict,
    }


def history_bands(history, min_points=3):
    rows = []
    for key, label in MULTIPLES:
        series = [h.get(key) for h in history if h.get(key) and h.get(key) > 0]
        if len(series) < min_points:
            continue
        cur, lo, hi = series[-1], min(series), max(series)
        pos = ((cur - lo) / (hi - lo) * 100) if hi > lo else 50.0
        signal = SELL if pos >= 70 else BUY if pos <= 30 else HOLD
        rows.append({
            "key": key, "label": label, "current": cur,
            "low": lo, "high": hi, "position": pos, "signal": signal,
        })
    return rows


def yield_checks(target, risk_free=RISK_FREE_PCT):
    pe = target.get("trailing_pe")
    earnings_yield = (1 / pe * 100) if (pe and pe > 0) else None
    spread = (earnings_yield - risk_free) if earnings_yield is not None else None
    if earnings_yield is None:
        ey_signal = NA
    elif earnings_yield >= risk_free + 3:
        ey_signal = BUY
    elif earnings_yield < risk_free:
        ey_signal = SELL
    else:
        ey_signal = HOLD

    fcf, mcap = target.get("free_cashflow"), target.get("market_cap")
    fcf_yield = (fcf / mcap * 100) if (fcf and mcap) else None
    return {
        "earnings_yield": earnings_yield, "risk_free": risk_free,
        "spread": spread, "ey_signal": ey_signal, "fcf_yield": fcf_yield,
    }


def reverse_dcf(market_cap, fcf, r=DISCOUNT_RATE, gt=TERMINAL_GROWTH, years=5):
    """Solve for the high-growth rate the current price implies (2-stage DCF)."""
    if not (market_cap and fcf and fcf > 0) or r <= gt:
        return None

    def pv(g):
        total, cf = 0.0, fcf
        for t in range(1, years + 1):
            cf = fcf * (1 + g) ** t
            total += cf / ((1 + r) ** t)
        terminal = cf * (1 + gt) / (r - gt)
        return total + terminal / ((1 + r) ** years)

    lo, hi = -0.5, 1.0
    if pv(lo) > market_cap:
        return lo * 100
    if pv(hi) < market_cap:
        return hi * 100
    for _ in range(60):
        mid = (lo + hi) / 2
        if pv(mid) < market_cap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2 * 100


def assess(target, peers, history,
           risk_free=RISK_FREE_PCT, disc_rate=DISCOUNT_RATE, term_growth=TERMINAL_GROWTH):
    """Run all lenses. target may be None -> not available."""
    if target is None:
        return {"available": False, "overall": {"signal": NA, "verdict": "Unclear"}}

    fv = fair_value(target, peers)
    peers_cmp = peer_comparison(target, peers)

    overall = {"signal": fv["signal"], "verdict": fv["verdict"]}
    if fv["signal"] == NA:
        prems = [r["premium"] for r in peers_cmp if r["premium"] is not None]
        if prems:
            avg = sum(prems) / len(prems)
            sig, verd = _verdict_from_upside(-avg)
            overall = {"signal": sig, "verdict": verd}

    implied_growth = reverse_dcf(
        target.get("market_cap"), target.get("free_cashflow"), disc_rate, term_growth)

    return {
        "available": True,
        "peer_count": len(peers),
        "peers": peers_cmp,
        "fair_value": fv,
        "bands": history_bands(history),
        "yields": yield_checks(target, risk_free),
        "reverse_dcf": {
            "implied_growth": implied_growth,
            "disc_rate": disc_rate * 100,
            "terminal_growth": term_growth * 100,
            "earnings_growth": target.get("earnings_growth"),
        },
        "overall": overall,
    }
