#!/usr/bin/env python3
"""Pure paper-trading mechanics shared by the forward desk and the tests.

Everything here is arithmetic over values returned by Kalshi's public API.  Nothing in this
module fetches data or invents a price.

Conventions (identical to src/engine.js so the browser desk and the collector agree):
  * Kalshi's binary order book returns YES bids and NO bids only.  A YES *ask* is derived as
    1 - best NO bid and a NO ask as 1 - best YES bid (documented reciprocal rule).
  * A taker BUY of side S consumes the opposite side's bid ladder from the best level down,
    producing a VWAP; a taker SELL of side S consumes side S's own bid ladder.
  * Fee: Kalshi's published quadratic taker formula 0.07 * q * p * (1 - p) * multiplier,
    rounded UP to $0.0001 (documented direct-member balance grid; IRR-11).  Series with a
    non-quadratic fee_type are never traded by this simulator (flagged, not guessed).
  * Settlement pays $1.00 to the winning side and $0.00 to the other, with no fee for simple
    yes/no determinations (official market_settlement doc).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

STARTING_CASH = 10_000.0
FEE_GRID = 10_000  # $0.0001


def fnum(value):
    """Parse Kalshi fixed-point strings ("0.6900", "123.45") or numbers; None if absent."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_ts(value) -> int | None:
    """ISO-8601 (with Z / fractional seconds) or epoch seconds -> epoch seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value if value < 1e12 else value / 1000)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def taker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = 0.07 * contracts * price * (1.0 - price) * (multiplier or 1.0)
    return math.ceil(raw * FEE_GRID - 1e-9) / FEE_GRID


def normalize_market(raw: dict) -> dict:
    """Flatten the fields the strategies use.  Raw is preserved by the caller if needed."""
    yes_bid = fnum(raw.get("yes_bid_dollars"))
    no_bid = fnum(raw.get("no_bid_dollars"))
    yes_ask = fnum(raw.get("yes_ask_dollars"))
    no_ask = fnum(raw.get("no_ask_dollars"))
    if yes_ask is None and no_bid is not None:
        yes_ask = round(1.0 - no_bid, 4)
    if no_ask is None and yes_bid is not None:
        no_ask = round(1.0 - yes_bid, 4)
    close_ts = parse_ts(raw.get("close_time"))
    return {
        "ticker": raw.get("ticker", ""),
        "event_ticker": raw.get("event_ticker", ""),
        "series_ticker": series_of(raw),
        "title": raw.get("title", ""),
        "yes_sub_title": raw.get("yes_sub_title", ""),
        "status": raw.get("status", ""),
        "result": (raw.get("result") or "").lower(),
        "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask,
        "yes_bid_size": fnum(raw.get("yes_bid_size_fp")), "yes_ask_size": fnum(raw.get("yes_ask_size_fp")),
        "last": fnum(raw.get("last_price_dollars")), "previous": fnum(raw.get("previous_price_dollars")),
        "volume": fnum(raw.get("volume_fp")) or 0.0, "volume_24h": fnum(raw.get("volume_24h_fp")) or 0.0,
        "open_interest": fnum(raw.get("open_interest_fp")) or 0.0,
        "liquidity": fnum(raw.get("liquidity_dollars")),
        "open_ts": parse_ts(raw.get("open_time")), "close_ts": close_ts,
        "expected_expiration_ts": parse_ts(raw.get("expected_expiration_time")),
        "settlement_ts": parse_ts(raw.get("settlement_ts")),
        "settlement_value": fnum(raw.get("settlement_value_dollars")),
        "strike_type": raw.get("strike_type"), "floor_strike": fnum(raw.get("floor_strike")),
        "cap_strike": fnum(raw.get("cap_strike")),
        "exchange_index": int(raw.get("exchange_index") or 0),
        "price_level_structure": raw.get("price_level_structure", ""),
        "rules_primary": raw.get("rules_primary", ""),
    }


def series_of(raw: dict) -> str:
    """Kalshi tickers are SERIES-EVENT-MARKET; the event ticker's first segment is the series."""
    event = raw.get("event_ticker") or raw.get("ticker") or ""
    return event.split("-")[0]


def parse_book(payload: dict) -> dict:
    """{'yes': [(price, qty)...best last], 'no': [...]} from an orderbook response (fp or legacy)."""
    source = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    out = {}
    for side in ("yes", "no"):
        levels = source.get(f"{side}_dollars")
        scale = 1.0
        if levels is None:
            levels = source.get(side) or []
            scale = 0.01  # legacy integer-cent levels
        parsed = []
        for level in levels or []:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price, qty = fnum(level[0]), fnum(level[1])
            elif isinstance(level, dict):
                price, qty = fnum(level.get("price_dollars", level.get("price"))), fnum(level.get("quantity_fp", level.get("quantity")))
            else:
                continue
            if price is None or qty is None or qty <= 0:
                continue
            parsed.append((round(price * scale, 4), qty))
        parsed.sort(key=lambda level: level[0])
        out[side] = parsed
    return out


def best_bid(book: dict, side: str) -> float | None:
    levels = book.get(side) or []
    return levels[-1][0] if levels else None


def book_quotes(book: dict) -> dict:
    yes_bid, no_bid = best_bid(book, "yes"), best_bid(book, "no")
    return {
        "yes_bid": yes_bid, "no_bid": no_bid,
        "yes_ask": None if no_bid is None else round(1.0 - no_bid, 4),
        "no_ask": None if yes_bid is None else round(1.0 - yes_bid, 4),
    }


def depth(book: dict, side: str) -> float:
    return sum(qty for _, qty in book.get(side) or [])


def execute(book: dict, side: str, action: str, requested: float) -> dict:
    """Consume the ladder as a taker.  buy YES <- NO bids (price 1-p); sell YES -> YES bids."""
    if side not in ("yes", "no") or action not in ("buy", "sell") or requested is None or requested <= 0:
        return {"fills": [], "requested": requested or 0, "filled": 0.0, "notional": 0.0, "vwap": None,
                "unfilled": requested or 0, "touch": None, "slippage_per_contract": None}
    source_side = side if action == "sell" else ("no" if side == "yes" else "yes")
    ladder = sorted(book.get(source_side) or [], key=lambda level: -level[0])  # best first
    touch = None if not ladder else (ladder[0][0] if action == "sell" else round(1.0 - ladder[0][0], 4))
    remaining, notional, fills = float(requested), 0.0, []
    for price, qty in ladder:
        if remaining <= 1e-9:
            break
        take = min(remaining, qty)
        exec_price = price if action == "sell" else round(1.0 - price, 4)
        fills.append({"level_price": price, "price": exec_price, "contracts": round(take, 2)})
        remaining -= take
        notional += take * exec_price
    filled = float(requested) - remaining
    vwap = notional / filled if filled > 0 else None
    slip = None
    if vwap is not None and touch is not None:
        slip = (vwap - touch) if action == "buy" else (touch - vwap)
    return {"fills": fills, "requested": float(requested), "filled": round(filled, 2), "notional": round(notional, 6),
            "vwap": None if vwap is None else round(vwap, 6), "unfilled": round(max(0.0, remaining), 2),
            "touch": touch, "slippage_per_contract": None if slip is None else round(slip, 6)}


def affordable_contracts(cash: float, price: float, multiplier: float = 1.0) -> int:
    """Largest integer q with q*price + fee(q) <= cash (binary search, exact fee)."""
    if cash <= 0 or price is None or price <= 0:
        return 0
    low, high = 0, int(cash / price) + 1
    while high - low > 1:
        mid = (low + high) // 2
        if mid * price + taker_fee(price, mid, multiplier) <= cash + 1e-9:
            low = mid
        else:
            high = mid
    return low


def size_and_fill(book: dict, side: str, cash: float, fraction: float, multiplier: float,
                  max_contracts: float | None = None) -> dict | None:
    """Return the entry execution for a taker buy sized at `fraction` of cash, bounded by depth
    (and an optional external cap).  The whole ladder can be consumed; slippage is recorded."""
    quotes = book_quotes(book)
    ask = quotes["yes_ask"] if side == "yes" else quotes["no_ask"]
    if ask is None or ask <= 0 or ask >= 1:
        return None
    budget = cash * fraction
    wanted = affordable_contracts(budget, ask, multiplier)
    if max_contracts is not None:
        wanted = min(wanted, int(max_contracts))
    if wanted <= 0:
        return None
    execution = execute(book, side, "buy", wanted)
    if execution["filled"] <= 0 or execution["vwap"] is None:
        return None
    # Re-check affordability at the realized VWAP (worse than the touch when depth is thin):
    # shrink to what the budget buys at that VWAP and repeat until stable (a few iterations).
    fee = taker_fee(execution["vwap"], execution["filled"], multiplier)
    guard = 0
    while execution["notional"] + fee > budget + 1e-9 and guard < 50:
        guard += 1
        smaller = min(math.floor(execution["filled"]) - 1, affordable_contracts(budget, execution["vwap"], multiplier))
        if smaller <= 0:
            return None
        execution = execute(book, side, "buy", smaller)
        if execution["filled"] <= 0 or execution["vwap"] is None:
            return None
        fee = taker_fee(execution["vwap"], execution["filled"], multiplier)
    if execution["notional"] + fee > cash + 1e-9:
        return None
    execution["fee"] = fee
    execution["side"] = side
    return execution


def liquidation_value(book_or_quotes: dict, side: str, contracts: float) -> tuple[float | None, float | None]:
    """Conservative mark: the current best bid for the held side (None if no bid)."""
    bid = book_or_quotes.get(f"{side}_bid")
    if bid is None:
        return None, None
    return bid, round(bid * contracts, 6)
