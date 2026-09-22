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


def settlement_ts_equal(api_ts, ledger_ts) -> bool:
    """Second-precision settlement-timestamp comparison (IRR-39).

    The desk stores a settlement's exitAt truncated to whole seconds (iso(parse_ts(...))),
    while Kalshi's API returns fractional seconds (e.g. '2026-09-20T03:33:45.191125Z' in the
    committed evidence projections).  A strict string comparison therefore reports a mismatch
    on every real settlement - which froze the ledger by failing verification on the runner.
    Both sides are parsed to epoch seconds instead; values that do not parse fall back to
    strict string equality, so a missing timestamp stays a finding, never a match.
    """
    parsed_api, parsed_ledger = parse_ts(api_ts), parse_ts(ledger_ts)
    if parsed_api is not None and parsed_ledger is not None:
        return parsed_api == parsed_ledger
    return api_ts == ledger_ts and api_ts is not None


def taker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = 0.07 * contracts * price * (1.0 - price) * (multiplier or 1.0)
    return math.ceil(raw * FEE_GRID - 1e-9) / FEE_GRID


# ------------------------------------------------------------------------------- maker fees
# Primary source: Kalshi's published fee schedule ("Fee Schedule for July 2026 - 7.7.26 Update",
# https://kalshi.com/docs/kalshi-fee-schedule.pdf):
#     maker fees = round up(M x 0.0175 x C x P x (1-P)),  M defaults to 0 unless otherwise indicated
# so a resting order pays nothing on a series without a maker multiplier, and one quarter of the
# taker rate when the series carries one.  The Trade API exposes a series' fee_type
# (quadratic | quadratic_with_maker_fees | quadratic_with_combo_maker_fees | flat - see
# https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes) and a single
# fee_multiplier; the fee schedule's Non-Standard Fees table lists maker multipliers per series
# (e.g. KXCPI maker 1 / taker 1), which is what that multiplier is used for here.
MAKER_FEE_COEFFICIENT = 0.0175
MAKER_FEE_TYPES = {"quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}


def maker_multiplier(fee_type: str | None, fee_multiplier: float | None = 1.0) -> float:
    """Maker-side multiplier for a series record (0 when the series has no maker fees).

    Conservative by construction: a fee type that carries maker fees uses the series' recorded
    multiplier (the fee schedule lists 1 for every market series observed so far), and every other
    fee type is modelled at the documented default of 0.
    """
    if (fee_type or "") not in MAKER_FEE_TYPES:
        return 0.0
    value = fnum(fee_multiplier)
    return float(value) if value else 0.0


def maker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    """Kalshi maker fee for a resting order that ultimately executes (0 when multiplier is 0)."""
    if contracts <= 0 or price <= 0 or price >= 1 or not multiplier:
        return 0.0
    raw = MAKER_FEE_COEFFICIENT * contracts * price * (1.0 - price) * multiplier
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


def execute(book: dict, side: str, action: str, requested: float, limit: float | None = None) -> dict:
    """Consume the ladder as a taker (immediate-or-cancel limit order).

    buy YES <- NO bids (price 1-p); sell YES -> YES bids.  Levels priced worse than `limit`
    (above it for buys, below it for sells) are never touched; the remainder stays unfilled.
    """
    if side not in ("yes", "no") or action not in ("buy", "sell") or requested is None or requested <= 0:
        return {"fills": [], "requested": requested or 0, "filled": 0.0, "notional": 0.0, "vwap": None,
                "unfilled": requested or 0, "touch": None, "slippage_per_contract": None, "limit": limit}
    source_side = side if action == "sell" else ("no" if side == "yes" else "yes")
    ladder = sorted(book.get(source_side) or [], key=lambda level: -level[0])  # best first
    touch = None if not ladder else (ladder[0][0] if action == "sell" else round(1.0 - ladder[0][0], 4))
    remaining, notional, fills = float(requested), 0.0, []
    for price, qty in ladder:
        if remaining <= 1e-9:
            break
        exec_price = price if action == "sell" else round(1.0 - price, 4)
        if limit is not None and ((action == "buy" and exec_price > limit + 1e-9) or (action == "sell" and exec_price < limit - 1e-9)):
            break
        take = min(remaining, qty)
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
            "touch": touch, "slippage_per_contract": None if slip is None else round(slip, 6), "limit": limit}


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
                  limit: float | None = None, max_contracts: float | None = None) -> dict | None:
    """Entry execution for a taker buy sized at `fraction` of cash.

    The order is an immediate-or-cancel limit at `limit` (defaults to the rule's own price bound,
    or the touch when none is given): it consumes displayed levels up to the limit, and the
    quantity is the largest one whose notional + exact fee fits the budget (binary search over the
    ladder).  Depth beyond the budget or the limit is reported as `unfilled`.
    """
    quotes = book_quotes(book)
    ask = quotes["yes_ask"] if side == "yes" else quotes["no_ask"]
    if ask is None or ask <= 0 or ask >= 1:
        return None
    if limit is None:
        limit = ask
    if ask > limit + 1e-9:
        return None
    budget = cash * fraction
    if budget <= 0:
        return None
    ceiling = affordable_contracts(budget, ask, multiplier)  # upper bound: everything at the touch
    if max_contracts is not None:
        ceiling = min(ceiling, int(max_contracts))
    if ceiling <= 0:
        return None

    def cost(q):
        ex = execute(book, side, "buy", q, limit)
        if ex["filled"] <= 0 or ex["vwap"] is None:
            return None, ex
        return ex["notional"] + taker_fee(ex["vwap"], ex["filled"], multiplier), ex

    total, execution = cost(ceiling)
    if total is None:
        return None
    if total > budget + 1e-9:
        low, high = 0, ceiling  # cost is monotonic in q; find the largest affordable q
        while high - low > 1:
            mid = (low + high) // 2
            mid_total, _ = cost(mid)
            if mid_total is not None and mid_total <= budget + 1e-9:
                low = mid
            else:
                high = mid
        if low <= 0:
            return None
        total, execution = cost(low)
        if total is None or total > budget + 1e-9:
            return None
    execution["fee"] = taker_fee(execution["vwap"], execution["filled"], multiplier)
    execution["side"] = side
    return execution


def liquidation_value(book_or_quotes: dict, side: str, contracts: float) -> tuple[float | None, float | None]:
    """Conservative mark: the current best bid for the held side (None if no bid)."""
    bid = book_or_quotes.get(f"{side}_bid")
    if bid is None:
        return None, None
    return bid, round(bid * contracts, 6)
