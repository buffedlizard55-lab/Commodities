#!/usr/bin/env python3
"""Forward-desk strategy roster.

Every entry is a deterministic, falsifiable rule evaluated ONLY on values returned by official
sources at decision time (Kalshi market/quote/book fields, the archived NWS forecast).  A rule
returns a *signal* (side + reason) or None; sizing, fills, fees and settlement are handled by
forward_desk.py against the fresh order book.  Strategies aim for the highest return - there is
no risk budget - but a fill is never created without verified displayed liquidity.

Provenance of each rule is recorded in `source` (MasterSite project, community post, literature
or exchange mechanics) and rendered on the site with the review link.
"""
from __future__ import annotations

from paper_engine import fnum

DAY = 86_400
HOUR = 3_600

# Tracked series (verified against GET /series/{ticker}; the universe job records the proof).
SERIES_ECON = ["KXFED", "KXCPI", "KXCPIYOY", "KXFEDDECISION"]
SERIES_CRYPTO = ["KXBTC", "KXBTC15M", "KXETH15M"]
SERIES_GOLD = ["KXGOLD15M", "KXGOLDH"]
SERIES_WEATHER = ["KXHIGHNY"]
SERIES_SPORTS = ["KXNFLGAME", "KXNBAGAME", "KXNCAAFGAME", "KXMLBGAME", "KXNHLGAME", "KXWNBAGAME"]
SELECTOR_CEO = "tag:CEOs&contains:CEO"   # Companies-category series tagged CEOs whose ticker names a CEO market
SELECTOR_FDA = "prefix:KXFDA&tag:Medicine"  # FDA drug-decision series (excludes FDA-politics series)
# Every Kalshi daily-high temperature series (verified in data/universe/series-catalog.json:
# tag "Daily temperature", e.g. KXHIGHLAX/CHI/MIA/AUS/DEN/PHIL/SFO/PHX/SEA/ATL/BOS/DAL/DC/LV/HOU/...).
SELECTOR_WEATHER = "tag:Daily temperature&prefix:KXHIGH"

TRACKED_SERIES = SERIES_ECON + SERIES_CRYPTO + SERIES_GOLD + SERIES_WEATHER + SERIES_SPORTS
TRACKED_SELECTORS = [SELECTOR_CEO, SELECTOR_FDA, SELECTOR_WEATHER]

# Minimum live lead before ScorePulse buys the leading side, per sport.  These are rule
# parameters, not risk limits: the desk always sizes 50% of free cash.  Units are the sport's own
# scoring unit (points for football/basketball, runs for baseball, goals for hockey).
# KXNHLGAME/KXWNBAGAME added 2026-09-21 with the ESPN adapter expansion (IRR-35): a 2-goal NHL
# lead with time remaining is a strong favourite state; WNBA uses the same 10-point lead as the NBA.
LIVE_SCORE_THRESHOLDS = {"KXNFLGAME": 8.0, "KXNCAAFGAME": 8.0, "KXNBAGAME": 10.0, "KXMLBGAME": 3.0,
                         "KXNHLGAME": 2.0, "KXWNBAGAME": 10.0}


def _cheaper_side(m, maximum, minimum=0.0):
    """Cheapest executable side whose ask is within [minimum, maximum]."""
    candidates = []
    for side in ("yes", "no"):
        ask = m.get(f"{side}_ask")
        if ask is not None and minimum <= ask <= maximum and 0 < ask < 1:
            candidates.append((ask, side))
    if not candidates:
        return None
    ask, side = min(candidates)
    return side, ask


MAX_FAVOURITE_SPREAD = 0.05


def _favourite_side(m, low, high):
    """The side whose ask lies in the favourite band [low, high] AND whose displayed spread is at
    most 5c (bid present).  A lone ask on a wide quote is not a priced favourite (IRR-25)."""
    for side in ("yes", "no"):
        ask, bid = m.get(f"{side}_ask"), m.get(f"{side}_bid")
        if ask is None or bid is None or bid <= 0:
            continue
        if low <= ask <= high and (ask - bid) <= MAX_FAVOURITE_SPREAD + 1e-9:
            return side, ask
    return None


def _spread(m, side):
    bid, ask = m.get(f"{side}_bid"), m.get(f"{side}_ask")
    return None if bid is None or ask is None else round(ask - bid, 4)


def _hours_to_close(m, now_ts):
    """Hours until expected resolution: expected_expiration_time when Kalshi provides it (game
    markets keep close_time days after the game), else close_time."""
    ref = m.get("expected_expiration_ts") or m.get("close_ts")
    return None if ref is None else (ref - now_ts) / HOUR


def _minutes_to_close(m, now_ts):
    """Minutes left to TRADE (close_time), used by the 15-minute market rules."""
    close_ts = m.get("close_ts")
    return None if close_ts is None else (close_ts - now_ts) / 60


def _move(m, now_ts=None):
    """last trade minus the last trade a day ago (official previous_price_dollars semantics).
    Undefined for markets younger than 24h (no day-ago trade exists) or without a prior trade."""
    last, prev = m.get("last"), m.get("previous")
    if last is None or prev is None or prev <= 0 or last <= 0:
        return None
    if now_ts is not None and (m.get("open_ts") is None or now_ts - m["open_ts"] < DAY):
        return None
    return round(last - prev, 4)


# ----------------------------------------------------------------------------- entry rules
def entry_book_edge(m, ctx):
    pick = _cheaper_side(m, 0.45)
    if not pick:
        return None
    side, ask = pick
    spread = _spread(m, side)
    if spread is None or spread < 0.02:
        return None
    return {"side": side, "price": ask, "limit": 0.45, "reason": f"{side.upper()} ask {ask:.2f} <= 0.45 with displayed spread {spread:.2f} >= 0.02"}


def entry_tick_momentum(m, ctx):
    move = _move(m, ctx["now_ts"])
    if move is None or abs(move) < 0.03:
        return None
    side = "yes" if move > 0 else "no"
    ask = m.get(f"{side}_ask")
    if ask is None or not 0 < ask < 1:
        return None
    return {"side": side, "price": ask, "limit": round(min(0.99, ask + 0.02), 4),
            "reason": f"last trade {m['last']:.2f} vs a day ago {m['previous']:.2f} = {move:+.2f} (>= 3c) -> buy {side.upper()} at {ask:.2f} (limit +2c)"}


def entry_expiry_tail(m, ctx):
    hours = _hours_to_close(m, ctx["now_ts"])
    if hours is None or hours < 0 or hours > 48:
        return None
    pick = _cheaper_side(m, 0.15)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.15, "reason": f"expected to resolve in {hours:.1f}h and {side.upper()} ask {ask:.2f} <= 0.15"}


def entry_depth_imbalance(m, ctx):
    book = ctx.get("book")
    if not book:
        return None  # needs the fresh ladder; evaluated again at the book stage
    yes_depth = sum(q for _, q in book.get("yes") or [])
    no_depth = sum(q for _, q in book.get("no") or [])
    if yes_depth <= 0 or no_depth <= 0:
        return None
    imbalance = (yes_depth - no_depth) / (yes_depth + no_depth)
    if abs(imbalance) < 0.35:
        return None
    side = "yes" if imbalance > 0 else "no"
    ask = m.get(f"{side}_ask")
    if ask is None or not 0 < ask < 1:
        return None
    return {"side": side, "price": ask, "limit": round(min(0.99, ask + 0.02), 4), "reason": f"displayed depth imbalance {imbalance * 100:+.1f}% toward {side.upper()} (YES {yes_depth:,.0f} / NO {no_depth:,.0f}); limit +2c"}


def entry_dip_hunter(m, ctx):
    if (m.get("volume") or 0) <= 0:
        return None
    pick = _cheaper_side(m, 0.05, 0.01)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.05, "reason": f"{side.upper()} ask {ask:.2f} <= 0.05 on a market with volume {m['volume']:,.0f}"}


def entry_spike_surfer(m, ctx):
    move = _move(m, ctx["now_ts"])
    if move is None or abs(move) < 0.20 or (m.get("volume_24h") or 0) < 1_000:
        return None
    side = "yes" if move > 0 else "no"
    ask = m.get(f"{side}_ask")
    if ask is None or not 0 < ask < 1:
        return None
    return {"side": side, "price": ask, "limit": round(min(0.99, ask + 0.02), 4),
            "reason": f"24h move {move:+.2f} (>= 20c; last {m['last']:.2f} vs a day ago {m['previous']:.2f}) with 24h volume {m['volume_24h']:,.0f} -> buy {side.upper()} (limit +2c)"}


def entry_sure_thing(m, ctx):
    if (m.get("volume") or 0) < 100_000:
        return None
    pick = _favourite_side(m, 0.90, 0.97)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.97, "reason": f"{side.upper()} ask {ask:.2f} in [0.90, 0.97] with volume {m['volume']:,.0f} >= 100k"}


def entry_yield_sniper(m, ctx):
    if (m.get("volume") or 0) <= 0:
        return None
    hours = _hours_to_close(m, ctx["now_ts"])
    if hours is None or hours < 0 or hours > 21 * 24:
        return None
    pick = _favourite_side(m, 0.97, 0.99)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.99, "reason": f"{side.upper()} ask {ask:.2f} in [0.97, 0.99], expected to resolve in {hours / 24:.1f} days (<= 21)"}


def entry_ceo_fav(m, ctx):
    pick = _favourite_side(m, 0.80, 0.96)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.96, "reason": f"CEO market: favourite {side.upper()} ask {ask:.2f} in [0.80, 0.96]"}


def entry_fda_premium(m, ctx):
    pick = _favourite_side(m, 0.85, 0.97)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.97, "reason": f"FDA drug-decision market: favourite {side.upper()} ask {ask:.2f} in [0.85, 0.97]"}


def entry_game_favourite(m, ctx):
    hours = _hours_to_close(m, ctx["now_ts"])
    if hours is None or hours < 0 or hours > 12 or (m.get("volume") or 0) < 10_000:
        return None
    pick = _favourite_side(m, 0.80, 0.95)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.95, "reason": f"game favourite {side.upper()} ask {ask:.2f} in [0.80, 0.95], expected resolution in {hours:.1f}h, volume {m['volume']:,.0f}"}


def entry_underdog_sweep(m, ctx):
    hours = _hours_to_close(m, ctx["now_ts"])
    if hours is None or hours < 0 or hours > 12 or (m.get("volume") or 0) < 10_000:
        return None
    pick = _cheaper_side(m, 0.20, 0.02)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.20, "reason": f"underdog {side.upper()} ask {ask:.2f} <= 0.20, expected resolution in {hours:.1f}h, volume {m['volume']:,.0f}"}


def entry_gold_leader(m, ctx):
    minutes = _minutes_to_close(m, ctx["now_ts"])
    if minutes is None or minutes < 5 or minutes > 12:
        return None
    pick = _favourite_side(m, 0.60, 0.80)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.80, "reason": f"gold 15-minute early leader {side.upper()} ask {ask:.3f} in [0.60, 0.80] with {minutes:.1f} min left"}


def entry_micro_tail(m, ctx):
    minutes = _minutes_to_close(m, ctx["now_ts"])
    if minutes is None or minutes < 3 or minutes > 15:
        return None
    pick = _cheaper_side(m, 0.10, 0.001)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.10, "reason": f"15-minute tail {side.upper()} ask {ask:.3f} <= 0.10 with {minutes:.1f} min left"}


def entry_scalp_8095(m, ctx):
    minutes = _minutes_to_close(m, ctx["now_ts"])
    if minutes is None or minutes < 2:
        return None
    pick = _favourite_side(m, 0.75, 0.80)
    if not pick:
        return None
    side, ask = pick
    return {"side": side, "price": ask, "limit": 0.80, "reason": f"{side.upper()} ask {ask:.3f} in the 75-80c entry band (take profit at a 0.95 bid)"}


def entry_panic_fade(m, ctx):
    """Fade a >= 15c dump/pump inside the micro market, read from its official 1-minute candles."""
    bars = (ctx.get("candles1m") or {}).get(m.get("ticker"))
    if not bars or len(bars) < 3:
        return None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 3:
        return None
    recent = closes[-1]
    window = closes[-6:-1]
    hi, lo = max(window), min(window)
    minutes = _minutes_to_close(m, ctx["now_ts"])
    if minutes is None or minutes < 2:
        return None
    if hi - recent >= 0.15:
        side, ask = "yes", m.get("yes_ask")
        move = recent - hi
    elif recent - lo >= 0.15:
        side, ask = "no", m.get("no_ask")
        move = recent - lo
    else:
        return None
    if ask is None or not 0 < ask < 1:
        return None
    return {"side": side, "price": ask, "limit": round(min(0.99, ask + 0.02), 4),
            "reason": f"panic fade: 1-minute close moved {move:+.2f} vs the prior 5 minutes (|move| >= 15c) -> buy the dumped side {side.upper()} at {ask:.3f} (limit +2c), {minutes:.1f} min left"}


def entry_longshot_fader(m, ctx):
    if (m.get("volume") or 0) < 5_000:
        return None
    yes_ask = m.get("yes_ask")
    no_ask = m.get("no_ask")
    # Fade a 5-20c YES longshot by buying NO (NO ask 0.80-0.95), or the mirror image.
    tight_no = _spread(m, "no") is not None and _spread(m, "no") <= MAX_FAVOURITE_SPREAD
    tight_yes = _spread(m, "yes") is not None and _spread(m, "yes") <= MAX_FAVOURITE_SPREAD
    if yes_ask is not None and 0.05 <= yes_ask <= 0.20 and no_ask is not None and 0.80 <= no_ask <= 0.95 and tight_no:
        return {"side": "no", "price": no_ask, "limit": 0.95, "reason": f"fade YES longshot at {yes_ask:.2f}: buy NO at {no_ask:.2f} (favourite-longshot bias; spread <= 5c)"}
    if no_ask is not None and 0.05 <= no_ask <= 0.20 and yes_ask is not None and 0.80 <= yes_ask <= 0.95 and tight_yes:
        return {"side": "yes", "price": yes_ask, "limit": 0.95, "reason": f"fade NO longshot at {no_ask:.2f}: buy YES at {yes_ask:.2f} (favourite-longshot bias; spread <= 5c)"}
    return None


def _bracket_contains(m, value):
    kind = m.get("strike_type")
    lo, hi = m.get("floor_strike"), m.get("cap_strike")
    if value is None or kind is None:
        return None
    if kind == "between":
        return lo is not None and hi is not None and lo <= value <= hi
    if kind == "greater":
        return lo is not None and value > lo
    if kind == "greater_or_equal":
        return lo is not None and value >= lo
    if kind == "less":
        return hi is not None and value < hi
    if kind == "less_or_equal":
        return hi is not None and value <= hi
    return None


def _bracket_distance(m, value):
    kind = m.get("strike_type")
    lo, hi = m.get("floor_strike"), m.get("cap_strike")
    if value is None:
        return None
    if kind == "between" and lo is not None and hi is not None:
        return 0.0 if lo <= value <= hi else min(abs(value - lo), abs(value - hi))
    if kind in ("greater", "greater_or_equal") and lo is not None:
        return 0.0 if value > lo else lo - value
    if kind in ("less", "less_or_equal") and hi is not None:
        return 0.0 if value < hi else value - hi
    return None


def entry_weather_bracket(m, ctx):
    forecast = (ctx.get("nws") or {}).get(m.get("event_ticker"))
    if not forecast:
        return None
    contains = _bracket_contains(m, forecast["high_f"])
    if not contains:
        return None
    ask = m.get("yes_ask")
    if ask is None or not 0 < ask <= 0.70:
        return None
    return {"side": "yes", "price": ask, "limit": 0.70,
            "reason": f"NWS forecast high {forecast['high_f']}F ({forecast['period']}, issued {forecast['updated']}) falls in this bracket; YES ask {ask:.2f} <= 0.70"}


def entry_weather_fade(m, ctx):
    forecast = (ctx.get("nws") or {}).get(m.get("event_ticker"))
    if not forecast:
        return None
    distance = _bracket_distance(m, forecast["high_f"])
    if distance is None or distance < 4:
        return None
    ask = m.get("no_ask")
    if ask is None or not 0 < ask <= 0.92:
        return None
    return {"side": "no", "price": ask, "limit": 0.92,
            "reason": f"bracket is {distance:.0f}F away from the NWS forecast high {forecast['high_f']}F (issued {forecast['updated']}); NO ask {ask:.2f} <= 0.92"}


HEAT_CONFIRM_FORECAST_F = 77.0   # the community post's gate: "forecast high above 77 degrees"
HEAT_CONFIRM_MAX_ASK = 0.42      # "... a YES price below 42 cents"
HEAT_CONFIRM_MAX_SPREAD = 0.08   # "... and a spread < 8c"


def _bracket_contains_strikes(strikes, value):
    """Same containment logic as _bracket_contains, on the strike fields a position stored at entry."""
    kind, lo, hi = strikes.get("strikeType"), strikes.get("floorStrike"), strikes.get("capStrike")
    if value is None or kind is None:
        return None
    if kind == "between":
        return lo is not None and hi is not None and lo <= value <= hi
    if kind == "greater":
        return lo is not None and value > lo
    if kind == "greater_or_equal":
        return lo is not None and value >= lo
    if kind == "less":
        return hi is not None and value < hi
    if kind == "less_or_equal":
        return hi is not None and value <= hi
    return None


def entry_heat_confirm(m, ctx):
    """The top rule from a 500-bot community weather backtest, recreated mechanically.

    r/PredictionsMarkets "I backtested 500 Weather Kalshi Bots" (2026-05-22): the best bot "waited
    for a forecast high above 77 degrees, a YES price below 42 cents, and a reasonably tight
    spread" and "if the forecast cooled, it got out"; the median of all 500 was -41.61%, so the
    post's own evidence says most weather bots lose - this persona measures the *survivor* rule on
    official prices.  The forecast is the desk's point-in-time NWS capture (context); the bracket,
    price and liquidity are Kalshi's.
    """
    forecast = (ctx.get("nws") or {}).get(m.get("event_ticker"))
    if not forecast:
        return None
    high = forecast.get("high_f")
    if high is None or high <= HEAT_CONFIRM_FORECAST_F:
        return None
    if not _bracket_contains(m, high):
        return None
    ask, bid = m.get("yes_ask"), m.get("yes_bid")
    if ask is None or bid is None or not 0 < ask < HEAT_CONFIRM_MAX_ASK or (ask - bid) > HEAT_CONFIRM_MAX_SPREAD:
        return None
    return {"side": "yes", "price": ask, "limit": HEAT_CONFIRM_MAX_ASK,
            "reason": f"NWS forecast high {high}F > {HEAT_CONFIRM_FORECAST_F:.0f}F and falls in this bracket; "
                      f"YES ask {ask:.2f} < {HEAT_CONFIRM_MAX_ASK:.2f} with spread {ask - bid:.2f} "
                      f"(500-bot top rule, forward recreation)",
            "meta": {"forecastHighF": high, "forecastDate": forecast.get("date"),
                     "forecastUpdated": forecast.get("updated"), "forecastSource": forecast.get("source"),
                     "strikeType": m.get("strike_type"), "floorStrike": m.get("floor_strike"),
                     "capStrike": m.get("cap_strike")}}


def exit_heat_forecast_cools(position, quotes, ctx):
    """Exit when the CURRENT NWS forecast no longer falls inside the bracket that was bought."""
    meta = position.get("signalMeta") or {}
    if meta.get("forecastHighF") is None:
        return None
    forecast = (ctx.get("nws") or {}).get(position.get("eventTicker"))
    if not forecast or forecast.get("high_f") is None:
        return None  # no fresh forecast -> hold; never sell on an adapter gap
    if _bracket_contains_strikes(meta, forecast["high_f"]):
        return None
    return (f"NWS forecast for {meta.get('forecastDate')} moved {meta['forecastHighF']}F -> "
            f"{forecast['high_f']}F and no longer falls in the entered bracket")


def entry_live_score(m, ctx):
    """Buy the side of the team ESPN's official scoreboard shows leading, late in the game.

    Signal source: the public ESPN scoreboard JSON (ESPN is a listed settlement source for
    KXNCAAFGAME / KXMLBGAME / KXNBAGAME / KXNHLGAME / KXWNBAGAME per the settlement_sources field
    in data/universe/series-catalog.json).  The mapping is accepted only when exactly one event
    matches the team names + scheduled date in the Kalshi market's own rules_primary; otherwise
    ctx["espn"] has no entry and the rule abstains.  The price is always Kalshi's.
    """
    signal = (ctx.get("espn") or {}).get(m.get("ticker"))
    if not signal or signal.get("state") != "in":
        return None
    diff = signal.get("scoreDiff")
    if diff is None:
        return None
    threshold = LIVE_SCORE_THRESHOLDS.get(m.get("series_ticker"), 8)
    if diff < threshold:
        return None
    side = "yes"  # a Kalshi game market's YES side is the team named in yes_sub_title
    ask, bid = m.get("yes_ask"), m.get("yes_bid")
    if ask is None or bid is None or not 0 < ask <= 0.85 or (ask - bid) > MAX_FAVOURITE_SPREAD + 1e-9:
        return None
    return {"side": side, "price": ask, "limit": 0.85,
            "reason": f"ESPN scoreboard ({signal['detail']}, event {signal['espnEventId']}): {signal['away']} "
                      f"{_fmt_score(signal.get('awayScore'))} @ {signal['home']} {_fmt_score(signal.get('homeScore'))} "
                      f"-> market side '{signal['marketSide']}' leads by {_fmt_score(diff)} (>= {threshold}); "
                      f"YES ask {ask:.2f} <= 0.85 with spread {ask - bid:.2f}"}


def _fmt_score(value):
    if value is None:
        return "?"
    return f"{value:g}"


def entry_fda_record(m, ctx):
    """Buy YES on an FDA drug-decision market when openFDA Drugs@FDA already lists an approval.

    Signal source: openFDA Drugs@FDA (https://api.fda.gov/drug/drugsfda.json), the drug name taken
    from the market's own official title.  Only positive record evidence is traded: when the lookup
    finds no application the rule abstains (absence is not proof of a future decision, and Drugs@FDA
    publishes no PDUFA target date - IRR-27).
    """
    signal = (ctx.get("fda") or {}).get(m.get("ticker"))
    if not signal or not signal.get("approvedRecord"):
        return None
    ask, bid = m.get("yes_ask"), m.get("yes_bid")
    if ask is None or bid is None or not 0 < ask <= 0.97 or (ask - bid) > MAX_FAVOURITE_SPREAD + 1e-9:
        return None
    application = (signal.get("applications") or [{}])[0]
    return {"side": "yes", "price": ask, "limit": 0.97,
            "reason": f"openFDA Drugs@FDA lists {application.get('application_number') or 'an application'} for "
                      f"{signal['drug']} (first ORIG approval {application.get('first_orig_approved') or 'n/a'}); "
                      f"YES ask {ask:.2f} <= 0.97 with spread {ask - bid:.2f}"}


def entry_sma_cross(m, ctx):
    bars = (ctx.get("candles") or {}).get(m.get("ticker"))
    if not bars or len(bars) < 10:
        return None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 10:
        return None
    fast = sum(closes[-5:]) / 5
    slow = sum(closes[-10:]) / 10
    last = closes[-1]
    if not (last > slow and fast > slow):
        return None
    ask = m.get("yes_ask")
    if ask is None or not 0 < ask < 1 or (m.get("volume_24h") or 0) <= 0:
        return None
    return {"side": "yes", "price": ask, "limit": round(min(0.99, ask + 0.02), 4), "reason": f"daily SMA5 {fast:.3f} > SMA10 {slow:.3f} and last {last:.2f} > SMA10 on verified candles ({len(closes)} bars); YES ask {ask:.2f} (limit +2c)"}


# ----------------------------------------------------------------------------- exit rules
def exit_hold(position, quotes, ctx):
    return None


def exit_take_profit(multiple=None, add=None, target=None):
    def rule(position, quotes, ctx):
        bid = quotes.get(f"{position['side']}_bid")
        if bid is None or bid <= 0:
            return None
        entry = position["entryPrice"]
        if multiple is not None and bid >= entry * multiple:
            return f"bid {bid:.3f} >= {multiple}x entry {entry:.3f}"
        if add is not None and bid >= entry + add:
            return f"bid {bid:.3f} >= entry {entry:.3f} + {add:.2f}"
        if target is not None and bid >= target:
            return f"bid {bid:.3f} >= take-profit {target:.2f}"
        return None
    return rule


def exit_sma_cross(position, quotes, ctx):
    bars = (ctx.get("candles") or {}).get(position["ticker"])
    if not bars:
        return None
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 10:
        return None
    slow = sum(closes[-10:]) / 10
    if closes[-1] < slow and (quotes.get("yes_bid") or 0) > 0:
        return f"last {closes[-1]:.2f} < SMA10 {slow:.3f}"
    return None


# ----------------------------------------------------------------------------- roster
STRATEGIES = [
    # --- exchange-mechanics personas (prior roster, now forward-tested automatically) ---
    {"id": "book-edge", "username": "BookRocket", "name": "Book Edge Sweep", "group": "microstructure",
     "source": {"kind": "exchange mechanics", "label": "Kalshi order-book reciprocal rule", "url": "https://docs.kalshi.com/getting_started/orderbook_responses"},
     "universe": "tracked", "entry": entry_book_edge, "exit": exit_take_profit(multiple=1.5), "fraction": 0.5,
     "rule": "Buy the cheaper executable side when its ask is <= 45c and the displayed spread is >= 2c; sell at a bid >= 1.5x entry, else hold to settlement.",
     "why": "Price-dislocation hypothesis using only the live book. Pays the spread on entry; wins only if the cheap side re-rates or settles in the money."},
    {"id": "tick-chaser", "username": "TickChaser", "name": "24h Momentum", "group": "momentum",
     "source": {"kind": "exchange mechanics", "label": "Kalshi market last/previous price fields", "url": "https://docs.kalshi.com/api-reference/market/get-market"},
     "universe": "tracked", "entry": entry_tick_momentum, "exit": exit_take_profit(add=0.05), "fraction": 0.5,
     "rule": "When the last trade is >= 3c away from the last trade a day ago (official previous_price field; market must be >= 24h old), buy that direction at the ask with a +2c limit; sell at a bid 5c above entry, else hold to settlement.",
     "why": "24-hour momentum on the official tape. Fails when the last trade is stale or the move was a one-off print."},
    {"id": "tail-sprint", "username": "TailSprint", "name": "Expiry Tail Sprint", "group": "expiry",
     "source": {"kind": "exchange mechanics", "label": "Kalshi close_time semantics", "url": "https://docs.kalshi.com/getting_started/market_lifecycle"},
     "universe": "tracked", "entry": entry_expiry_tail, "exit": exit_hold, "fraction": 0.5,
     "rule": "Inside 48h of expected resolution (expected_expiration_time, else close_time), buy any side quoted at or below 15c and hold to official settlement.",
     "why": "Pure return-seeking tail exposure: many small losses, occasional 6-100x payoffs. Judged only against official results."},
    {"id": "depth-diver", "username": "DepthDiver", "name": "Depth Imbalance", "group": "liquidity",
     "source": {"kind": "exchange mechanics", "label": "Kalshi order-book depth", "url": "https://docs.kalshi.com/api-reference/market/get-market-orderbook"},
     "universe": "tracked", "entry": entry_depth_imbalance, "exit": exit_hold, "fraction": 0.5, "needs_book": True,
     "rule": "When displayed YES vs NO depth imbalance exceeds 35% (both sides present), buy the heavier side and hold to settlement.",
     "why": "Tests whether visible resting depth predicts the outcome. Displayed liquidity can vanish, so the book is logged with every fill."},
    # --- forward twins of the committed backtest personas ---
    {"id": "dip-hunter", "username": "DipHunter", "name": "Cheap Dip Hunter", "group": "lottery",
     "source": {"kind": "committed backtest", "label": "Season 2026 backtest persona", "url": "data/season-2026/trades.json"},
     "universe": "tracked", "entry": entry_dip_hunter, "exit": exit_take_profit(multiple=2.0), "fraction": 0.5,
     "rule": "Buy any side quoted between 1c and 5c on a market with traded volume; sell at a bid >= 2x entry, else hold to settlement.",
     "why": "Lottery tickets. The backtest showed one-sided books keep the bid at zero, so most tickets ride to a zero settlement."},
    {"id": "spike-surfer", "username": "SpikeSurfer", "name": "Release Spike Surfer", "group": "event",
     "source": {"kind": "committed backtest", "label": "Season 2026 backtest persona", "url": "data/season-2026/trades.json"},
     "universe": "tracked", "entry": entry_spike_surfer, "exit": exit_take_profit(add=0.01), "fraction": 0.5,
     "rule": "When the 24h move (last trade vs the last trade a day ago) is >= 20c and 24h volume >= 1,000 on a market >= 24h old, buy the direction of the move with a +2c limit; sell at the first bid above entry, else hold to settlement.",
     "why": "Chases information releases on the official tape. Wins when the print is real (NFL final drive); loses when the spike is a trap."},
    {"id": "sure-thing", "username": "SureThing", "name": "Favorite Holder", "group": "favorite",
     "source": {"kind": "literature", "label": "CEPR favourite-longshot analysis of 300k+ Kalshi contracts", "url": "https://cepr.org/voxeu/columns/economics-kalshi-prediction-market"},
     "universe": "tracked", "entry": entry_sure_thing, "exit": exit_hold, "fraction": 0.5,
     "rule": "Buy a 90-97c favourite (displayed spread <= 5c) on a market with >= 100,000 contracts of volume and hold to settlement.",
     "why": "Favourite-longshot bias: heavy favourites on liquid event markets have historically been slightly under-priced."},
    {"id": "yield-sniper", "username": "YieldSniper", "name": "Certainty Carry", "group": "carry",
     "source": {"kind": "literature", "label": "Prediction-market carry (buying near-certain outcomes)", "url": "https://medium.com/@FrenzyCapital/trading-strategies-for-prediction-markets-4025a050e2e2"},
     "universe": "tracked", "entry": entry_yield_sniper, "exit": exit_hold, "fraction": 0.5,
     "rule": "Buy a 97-99c side (displayed spread <= 5c) on a market with volume whose expected resolution is within 21 days; hold to settlement.",
     "why": "Earns the last cents on near-certain outcomes with a hard horizon; loses everything if the favourite collapses."},
    {"id": "pinepilot", "username": "PinePilotX", "name": "Pine SMA Cross Replay", "group": "technical",
     "source": {"kind": "MasterSite project", "label": "PinePilot - TradingView Pine Script Strategy Lab", "url": "https://buffedlizard55-lab.github.io/Tradingview-pinescript-editor/"},
     "universe": "technical", "entry": entry_sma_cross, "exit": exit_sma_cross, "fraction": 0.5, "needs_candles": True,
     "rule": "On verified daily candlesticks: buy YES when SMA5 > SMA10 and last > SMA10 (24h volume > 0); exit at the bid when last < SMA10, else hold to settlement.",
     "why": "A Pine-style trend rule replayed on official candles with real fees. The committed backtest showed it pays the spread on 1-tick range crosses."},
    # --- MasterSite-sourced personas ---
    {"id": "ceo-fav", "username": "CEOExitFav", "name": "CEO-Change Favourite", "group": "companies",
     "source": {"kind": "MasterSite negative + exchange series", "label": "No CEO project exists in MasterSite (verified); Kalshi's CEO-change series (tag CEOs) are traded instead", "url": "https://buffedlizard55-lab.github.io/MasterSite/"},
     "universe": SELECTOR_CEO, "entry": entry_ceo_fav, "exit": exit_hold, "fraction": 0.5,
     "rule": "On Kalshi CEO markets (Companies series tagged CEOs whose ticker names a CEO market), buy the favourite side when its ask is 80-96c with a displayed spread <= 5c; hold to settlement.",
     "why": "Executive departures are rare, dated events; the favourite (usually NO) tends to carry. Loses the full stake on a surprise exit."},
    {"id": "weather-bracket", "username": "WeatherCatalyst", "name": "NWS Forecast Bracket", "group": "weather",
     "source": {"kind": "MasterSite project + official feed", "label": "SFWeather (NWS pipeline) -> NWS gridpoint forecast for Central Park (OKX/34,45) -> KXHIGHNY", "url": "https://buffedlizard55-lab.github.io/SFWeather/"},
     "universe": SELECTOR_WEATHER, "entry": entry_weather_bracket, "exit": exit_hold, "fraction": 0.5, "needs_nws": True,
     "rule": "Buy YES on the daily-high bracket (any tracked KXHIGH* city) that contains the point-in-time NWS forecast high for that city when its ask is <= 70c; hold to settlement.",
     "why": "Tests whether the official NWS forecast beats the market's bracket pricing across cities. The forecast is archived at decision time so the signal is auditable; the settlement value comes from the station named in each market's rules_primary (The Weather Company for most cities - IRR-26), so the NWS forecast is a signal, not the settlement source."},
    {"id": "weather-fade", "username": "WeatherFader", "name": "Forecast-Distance Fader", "group": "weather",
     "source": {"kind": "MasterSite project + official feed", "label": "SFWeather (NWS pipeline) -> NWS gridpoint forecast -> KXHIGHNY", "url": "https://buffedlizard55-lab.github.io/SFWeather/"},
     "universe": SELECTOR_WEATHER, "entry": entry_weather_fade, "exit": exit_hold, "fraction": 0.5, "needs_nws": True,
     "rule": "Buy NO on daily-high brackets (any tracked KXHIGH* city) at least 4F away from that city's NWS forecast high when the NO ask is <= 92c; hold to settlement.",
     "why": "Sells far-from-forecast tails. Small steady gains unless the forecast busts by 4F+."},
    {"id": "heat-confirm", "username": "HeatConfirm", "name": "Weather Heat Confirm (500-bot top rule)",
     "group": "weather",
     "source": {"kind": "community backtest",
                "label": "r/PredictionsMarkets 'I backtested 500 Weather Kalshi Bots' (2026-05-22): the "
                         "best bot bought forecast-confirmed heat while YES was still cheap; median ROI "
                         "of the 500 was -41.61% (discovery only; prices and fills here are Kalshi's)",
                "url": "https://www.reddit.com/r/PredictionsMarkets/comments/1tko1iw/i_backtested_500_weather_kalshi_bots_the_best_bot/"},
     "universe": SELECTOR_WEATHER, "entry": entry_heat_confirm, "exit": exit_heat_forecast_cools,
     "fraction": 0.5, "needs_nws": True,
     "rule": "Buy YES (<= 42c ask, displayed spread <= 8c) on a daily-high bracket that the city's current NWS "
             "point forecast puts at > 77F and inside the bracket; exit when the latest forecast no longer falls "
             "in the bracket, else hold to settlement.",
     "why": "Confirmation, not argument: the post's losers all fought the market on one bearish variable; the "
            "winner waited for the forecast to corroborate a cheap YES. Forward-only test - no verified weather "
            "candle archive exists to replay this rule, so it earns or bleeds on the live tape."},
    {"id": "fda-premium", "username": "FDAReaction", "name": "FDA Decision Premium", "group": "biotech",
     "source": {"kind": "MasterSite project", "label": "DrugAnalysis - FDA Decisions & Biotech Reactions -> Kalshi KXFDA* series", "url": "https://buffedlizard55-lab.github.io/DrugAnalysis/"},
     "universe": SELECTOR_FDA, "entry": entry_fda_premium, "exit": exit_hold, "fraction": 0.5,
     "rule": "On Kalshi FDA drug-decision markets (KXFDA* series tagged Medicine), buy the favourite side when its ask is 85-97c with a displayed spread <= 5c; hold to settlement.",
     "why": "PDUFA outcomes are heavily favoured one way; the premium is the residual. An openFDA point-in-time adapter is the next step."},
    {"id": "game-favourite", "username": "GridironPulse", "name": "Game Favourite (NFL/NBA/NCAA/MLB)", "group": "sports",
     "source": {"kind": "MasterSite projects", "label": "NFL-scoreboard, NFLInjuryReport, NBAInjuryReport, Ncaa-football-alerts, MLB-Live-PBP -> Kalshi game series", "url": "https://buffedlizard55-lab.github.io/NFL-scoreboard/"},
     "universe": SERIES_SPORTS, "entry": entry_game_favourite, "exit": exit_hold, "fraction": 0.5,
     "rule": "Within 12h of a game market's expected resolution (expected_expiration_time), buy the 80-95c favourite (displayed spread <= 5c) when volume >= 10,000 and hold to settlement.",
     "why": "Favourites on liquid game markets; the injury/scoreboard feeds are review links, the exchange price is the trade."},
    {"id": "underdog-sweep", "username": "SportsPredLab", "name": "Underdog Convexity Sweep", "group": "sports",
     "source": {"kind": "MasterSite project", "label": "SportsPred - 22-Sport Scoreboard & Prediction Hub -> Kalshi game series", "url": "https://buffedlizard55-lab.github.io/SportsPred/"},
     "universe": SERIES_SPORTS, "entry": entry_underdog_sweep, "exit": exit_take_profit(multiple=2.0), "fraction": 0.5,
     "rule": "Within 12h of expected resolution, buy a 2-20c underdog side on a game market with volume >= 10,000; sell at a bid >= 2x entry, else hold to settlement.",
     "why": "Convexity on upsets. The favourite-longshot literature predicts this bleeds; it is here to measure exactly how much."},
    {"id": "score-pulse", "username": "ScorePulse", "name": "Live Scoreboard Leader", "group": "sports",
     "source": {"kind": "official feed + exchange series", "label": "ESPN scoreboard API (public JSON) -> Kalshi KX*GAME series; ESPN is a listed settlement source for KXNCAAFGAME/KXMLBGAME/KXNBAGAME", "url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"},
     "universe": SERIES_SPORTS, "entry": entry_live_score, "exit": exit_hold, "fraction": 0.5, "needs_espn": True,
     "rule": "While a game market is live, buy the side of the team the ESPN scoreboard shows leading by at least 8 points (football), 10 (basketball) or 3 runs (baseball) when YES is quoted at or below 85c with a displayed spread <= 5c; hold to settlement.",
     "why": "In-game score is public information the market may price slowly; the exchange price is the only execution evidence. The ESPN event is matched to the market by the team names and scheduled date in the market's own rules_primary, and a non-unique match makes the rule abstain rather than guess."},
    {"id": "fda-record", "username": "FdaRecordCheck", "name": "Drugs@FDA Record Check", "group": "biotech",
     "source": {"kind": "official feed + exchange series", "label": "openFDA Drugs@FDA (public, no key) -> Kalshi KXFDA* drug-decision series", "url": "https://api.fda.gov/drug/drugsfda.json"},
     "universe": SELECTOR_FDA, "entry": entry_fda_record, "exit": exit_hold, "fraction": 0.5, "needs_fda": True,
     "rule": "On an FDA drug-decision market whose title names a drug, buy YES at or below 97c (displayed spread <= 5c) when openFDA Drugs@FDA lists an approved application for that drug; abstain when the lookup finds nothing.",
     "why": "A Drugs@FDA application with an approved ORIG submission is primary evidence that the approval already happened. Drugs@FDA publishes no PDUFA target action date (IRR-27), so absence of a record is never traded as evidence of a future decision."},
    {"id": "gold-leader", "username": "MetalMomentum", "name": "Gold 15-Minute Early Leader", "group": "gold",
     "source": {"kind": "MasterSite negative + exchange series", "label": "GOLD is a ring-buyer directory (not a price signal); Kalshi's liquid gold series KXGOLD15M is traded instead (Pyth-settled)", "url": "https://buffedlizard55-lab.github.io/GOLD/"},
     "universe": SERIES_GOLD, "entry": entry_gold_leader, "exit": exit_hold, "fraction": 0.5,
     "rule": "With 5-12 minutes left in a gold 15-minute market, buy the 60-80c leading side (displayed spread <= 5c) and hold to settlement.",
     "why": "Momentum persistence inside a 15-minute window on the Pyth gold feed. Fees on 60-80c contracts are near the maximum of the quadratic curve."},
    {"id": "micro-tail", "username": "TailSprint15", "name": "15-Minute Cheap Tail", "group": "crypto",
     "source": {"kind": "community post", "label": "r/KalshiBTCUporDown15 community (discovery only; the sub exists and discusses 15-minute BTC entries)", "url": "https://www.reddit.com/r/KalshiBTCUporDown15/"},
     "universe": SERIES_CRYPTO + SERIES_GOLD, "entry": entry_micro_tail, "exit": exit_hold, "fraction": 0.5,
     "rule": "With 3-15 minutes left in a 15-minute crypto/gold market, buy any side at or below 10c and hold to settlement.",
     "why": "Cheap tails on sub-cent-priced micro markets. Most expire worthless; one reversal pays 10x."},
    {"id": "scalp-8095", "username": "HighProbScalp", "name": "75-80c Entry / 95c Take-Profit", "group": "crypto",
     "source": {"kind": "community post", "label": "r/KalshiBTCUporDown15 'limit buy at 75-80c, take-profit at 95c' post (recreated mechanically; the sibling KalshiPaperSim measured it at -65.03% on its history)", "url": "https://www.reddit.com/r/KalshiBTCUporDown15/comments/1ribidl/my_full_strategy_for_btc_up_to_down_15/"},
     "universe": SERIES_CRYPTO + SERIES_GOLD, "entry": entry_scalp_8095, "exit": exit_take_profit(target=0.95), "fraction": 0.5,
     "rule": "Buy a side quoted 75-80c (displayed spread <= 5c) with >= 2 minutes left; sell when the bid reaches 95c, else hold to settlement.",
     "why": "The social claim is a high win rate; the arithmetic says a 75c entry needs > 79% wins to break even after fees."},
    {"id": "panic-fade", "username": "PanicFader", "name": "Panic Fade (volatility reversion)", "group": "crypto",
     "source": {"kind": "community post", "label": "r/PredictionsMarkets 5,000-strategy KXBTC15M run: volatility reversion was the only profitable archetype (discovery only)", "url": "https://www.reddit.com/r/PredictionsMarkets/comments/1szxy8h/backtested_5000_strategies_on_kalshi_15min_btc/"},
     "universe": SERIES_CRYPTO + SERIES_GOLD, "entry": entry_panic_fade, "exit": exit_take_profit(add=0.05), "fraction": 0.5, "needs_candles1m": True,
     "rule": "On a 15-minute market's official 1-minute candles, when the latest close is >= 15c below (above) the prior five minutes' high (low), buy the dumped side at the ask with a +2c limit and >= 2 minutes left; sell at a bid 5c above entry, else hold to settlement.",
     "why": "Fades over-reactions inside micro markets. The community backtest is unverified; this forward test measures it on official prices."},
    {"id": "longshot-fader", "username": "LongshotFader", "name": "Favourite-Longshot Fader", "group": "favorite",
     "source": {"kind": "literature", "label": "Favorite-longshot bias evidence (CEPR / Polymarket paper) - longshots at 5-20c underperform", "url": "https://cepr.org/voxeu/columns/economics-kalshi-prediction-market"},
     "universe": "tracked", "entry": entry_longshot_fader, "exit": exit_hold, "fraction": 0.5,
     "rule": "When one side is a 5-20c longshot on a market with volume >= 5,000, buy the opposite 80-95c side (displayed spread <= 5c) and hold to settlement.",
     "why": "The documented bias: longshots are over-bought. Loses the full stake on the occasional upset."},
]

GATED = [
    {"id": "sec-insider", "username": "Form4Flash", "name": "SEC Form 4 Catalyst", "group": "insider",
     "source": {"kind": "MasterSite project", "label": "Insider-trades - SEC Form 4 Dashboard", "url": "https://buffedlizard55-lab.github.io/Insider-trades/"},
     "blocker": "No Kalshi contract settles on a Form 4 filing, and SEC EDGAR hosts were measured unreachable from shared GitHub runner IPs by the sibling StockPaperSim (its IR-76/IR-77). Needs a company-event mapping plus a reachable point-in-time filing archive."},
    {"id": "leap-rotation", "username": "LeapMapper", "name": "The Leap Research Map", "group": "contest",
     "source": {"kind": "MasterSite project", "label": "TradingViewTheLeap - contest research layer", "url": "https://buffedlizard55-lab.github.io/TradingViewTheLeap/"},
     "blocker": "The Leap universe is futures (AMP); the Kalshi analogues are index/commodity range series that the universe job must first enumerate and verify (fee type, tick grid) before a rule can be stated."},
    {"id": "spread-smith", "username": "SpreadSmith", "name": "Market-Making Quote Plan", "group": "maker",
     "source": {"kind": "academic", "label": "Optimal market making in prediction markets (stochastic control)", "url": "https://pith.science/paper/2607.17991"},
     "blocker": "A REST snapshot cannot prove queue position or a resting-order fill; maker fills would be invented. Only taker fills against displayed depth are simulated."},
    {"id": "nba-injury-gate", "username": "TipoffTriage", "name": "NBA Official Report Gate", "group": "nba",
     "source": {"kind": "MasterSite project", "label": "NBA Injury Watch - 30-Team Injury Monitor", "url": "https://buffedlizard55-lab.github.io/NBAInjuryReport/"},
     "blocker": "No machine-readable official NBA injury feed exists (the project's own finding); ESPN rows are not official confirmation. GridironPulse trades the KXNBAGAME price instead."},
]

STRATEGY_BY_ID = {s["id"]: s for s in STRATEGIES}
