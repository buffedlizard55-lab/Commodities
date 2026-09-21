#!/usr/bin/env python3
"""Backtest over the collector's growing archive of verified candlesticks.

The forward desk archives the official candlesticks of every market it traded once that market
settles (`data/season-<year>/forward/candles/`, indexed in `candles/index.jsonl` with the response
URL, SHA-256, official result and settlement timestamp).  Every bar in those files is an official
Kalshi response, so the archive is a *verified* tape: this script replays deterministic rules over
it and writes a second committed competition memory next to the hand-collected 2026 backtest.

Truth rules (identical to scripts/build_competition.py, no exceptions):
  * Entry fill = the bar's verified `yes_ask_close` (buying YES) or `1 - yes_bid_close` (buying NO).
  * Exit fill  = a later bar's verified `yes_bid_close` (selling YES) / `1 - yes_ask_close` (NO).
  * Settlement = the official `result` from candles/index.jsonl (the desk read it from
    GET /markets/{ticker} and archived it); payout $1.00 / $0.00, no fee (official settlement doc).
  * Fee        = 0.07 * q * p * (1-p) * series fee_multiplier, rounded UP to $0.0001.  Series whose
    fee_type is not quadratic are skipped entirely (flagged, never guessed).
  * Sizing     = 50% of current equity, capped at 25% of that bar's verified volume.  No volume on
    the bar -> no entry (no verified liquidity, no trade).
  * Slippage   = half-spread paid, side-aware, from the entry/exit bar's own bid and ask.
  * No look-ahead: a rule sees bars[0..i] only and fills are timestamped at bars[i].end_period_ts.

Usage:
  python3 scripts/backtest_archive.py                 # rebuild data/season-2026/backtest-archive/
  python3 scripts/backtest_archive.py --season 2027 --min-bars 8 --dry-run
  python3 scripts/backtest_archive.py --series KXNFLGAME,KXNCAAFGAME --dry-run   # ad-hoc subset
  python3 scripts/backtest_archive.py --folds 3       # walk-forward windows over the replay horizon
The committed run always replays the WHOLE archive; a --series run prints and refuses to overwrite.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
STARTING_CASH = 10_000.0
EQUITY_FRACTION = 0.50
VOLUME_CAP_FRACTION = 0.25
FEE_GRID = 10_000
QUADRATIC_FEES = {"quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}


def iso(ts=None) -> str:
    return datetime.fromtimestamp(ts or int(__import__("time").time()), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fee(price: float, qty: float, multiplier: float = 1.0) -> float:
    if qty <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = 0.07 * qty * price * (1.0 - price) * (multiplier or 1.0)
    return math.ceil((raw - 1e-12) * FEE_GRID) / FEE_GRID


def read_bars(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for row in rows:
        def num(key):
            value = (row.get(key) or "").strip()
            return float(value) if value else None
        out.append({"ts": int(row["end_period_ts"]), "open": num("open"), "high": num("high"), "low": num("low"),
                    "close": num("close"), "bid": num("yes_bid_close"), "ask": num("yes_ask_close"),
                    "volume": num("volume") or 0.0, "open_interest": num("open_interest")})
    return out


def load_archive(season_dir: str, min_bars: int) -> list[dict]:
    """Every archived market with a verified result, its bars and its series fee settings."""
    index_path = os.path.join(season_dir, "forward", "candles", "index.jsonl")
    if not os.path.exists(index_path):
        return []
    catalog = {}
    catalog_path = os.path.join(DATA_DIR, "universe", "series-catalog.json")
    if os.path.exists(catalog_path):
        with open(catalog_path) as fh:
            catalog = json.load(fh).get("series") or {}
    index_path_dir = os.path.join(DATA_DIR, "universe", "series-index.json")
    series_index = {}
    if os.path.exists(index_path_dir):
        with open(index_path_dir) as fh:
            series_index = json.load(fh).get("series") or {}
    seen: dict[str, dict] = {}
    with open(index_path) as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            ticker = row.get("ticker")
            if not ticker or row.get("result") not in ("yes", "no"):
                continue  # a market without an official result is never backtested
            csv_path = os.path.join(season_dir, "forward", row["file"])
            if not os.path.exists(csv_path):
                continue
            bars = read_bars(csv_path)
            if len(bars) < min_bars:
                continue
            series_ticker = row.get("series") or ticker.split("-")[0]
            record = series_index.get(series_ticker) or {}
            fee_type = record.get("fee_type") or (catalog.get(series_ticker) or {}).get("f")
            multiplier = record.get("fee_multiplier")
            if multiplier is None:
                multiplier = (catalog.get(series_ticker) or {}).get("m")
            if fee_type not in QUADRATIC_FEES:
                continue  # fee model unknown -> not traded (flagged in the output)
            seen[ticker] = {"ticker": ticker, "series": series_ticker, "file": row["file"], "bars": bars,
                            "barCount": len(bars), "period": row.get("period"), "result": row["result"],
                            "settlementTs": row.get("settlementTs"), "settlementTsNum": _ts(row.get("settlementTs")),
                            "openTs": row.get("openTs"), "closeTs": row.get("closeTs"), "url": row.get("url"),
                            "sha256": row.get("sha256"), "cycle": row.get("cycle"),
                            "feeType": fee_type, "feeMultiplier": float(multiplier or 1)}
    return sorted(seen.values(), key=lambda m: m["ticker"])


def _ts(value):
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


# ----------------------------------------------------------------------------- replayable rules
def _mid(bar, side):
    bid, ask = bar.get("bid"), bar.get("ask")
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2
    return mid if side == "yes" else round(1 - mid, 6)


def _entry_price(bar, side):
    if side == "yes":
        return bar.get("ask")
    bid = bar.get("bid")
    return None if bid is None else round(1 - bid, 6)


def _exit_price(bar, side):
    if side == "yes":
        return bar.get("bid")
    ask = bar.get("ask")
    return None if ask is None else round(1 - ask, 6)


def _spread(bar):
    bid, ask = bar.get("bid"), bar.get("ask")
    if bid is None or ask is None:
        return None
    return round(ask - bid, 6)


def _bars_per_day(period):
    return {1440: 1, 60: 24, 1: 1440}.get(int(period or 1440), 1)


def rule_favourite_band(bar, bars, i, market):
    """Favourite band: verified ask in [0.85, 0.97] with a displayed spread <= 5c."""
    ask, spread = bar.get("ask"), _spread(bar)
    if ask is None or spread is None or not (0.85 <= ask <= 0.97) or spread > 0.05:
        return None
    return {"side": "yes", "reason": f"verified ask {ask:.4f} in [0.85, 0.97] with spread {spread:.4f}"}


def rule_longshot_sweep(bar, bars, i, market):
    ask = bar.get("ask")
    if ask is None or not (0.05 <= ask <= 0.20):
        return None
    return {"side": "yes", "reason": f"verified ask {ask:.4f} in [0.05, 0.20] (longshot convexity)"}


def rule_fade_longshot(bar, bars, i, market):
    """Buy NO when YES is a 5-20c longshot on a bar with a tight quoted spread."""
    ask, spread = bar.get("ask"), _spread(bar)
    if ask is None or spread is None or not (0.05 <= ask <= 0.20) or spread > 0.05:
        return None
    return {"side": "no", "reason": f"fade the {ask:.4f} YES longshot (spread {spread:.4f}); buy NO"}


def rule_sma_cross(bar, bars, i, market):
    closes = [b["close"] for b in bars[:i + 1] if b.get("close") is not None]
    if len(closes) < 10 or bar.get("close") is None:
        return None
    fast, slow = sum(closes[-5:]) / 5, sum(closes[-10:]) / 10
    if not (closes[-1] > slow and fast > slow):
        return None
    return {"side": "yes", "reason": f"SMA5 {fast:.4f} > SMA10 {slow:.4f} and close {closes[-1]:.4f} > SMA10",
            "exit": "sma"}


def rule_momentum(bar, bars, i, market):
    """Move versus the bar one day earlier (period-aware) on the verified closes."""
    step = _bars_per_day(market.get("period"))
    if i - step < 0:
        return None
    now, then = bars[i].get("close"), bars[i - step].get("close")
    if now is None or then is None or abs(now - then) < 0.03:
        return None
    side = "yes" if now > then else "no"
    return {"side": side, "reason": f"verified close {now:.4f} vs {then:.4f} a day earlier ({now - then:+.4f})",
            "exit": "profit"}


def rule_panic_fade(bar, bars, i, market):
    """1-minute bars: fade a >= 15c dump versus the prior five minutes' high."""
    if int(market.get("period") or 0) != 1 or i < 6:
        return None
    window = [b["close"] for b in bars[i - 5:i] if b.get("close") is not None]
    close = bar.get("close")
    if not window or close is None or max(window) - close < 0.15:
        return None
    return {"side": "yes", "reason": f"1-minute close {close:.4f} is {max(window) - close:.4f} below the prior 5-minute high",
            "exit": "profit"}


def rule_late_favourite(bar, bars, i, market):
    """Favourite >= 0.90 inside the last 20% of the archived tape."""
    if len(bars) < 5 or i < int(len(bars) * 0.8):
        return None
    ask, spread = bar.get("ask"), _spread(bar)
    if ask is None or spread is None or ask < 0.90 or spread > 0.05:
        return None
    return {"side": "yes", "reason": f"late favourite: verified ask {ask:.4f} >= 0.90 on bar {i + 1}/{len(bars)}"}


def rule_gap_fade(bar, bars, i, market):
    """Fade an opening gap of >= 10c against the previous bar's verified close.

    Mean reversion is the archetype the r/PredictionsMarkets 5,000-strategy KXBTC15M backtest
    reported as the only profitable family (the same source as the desk's PanicFader - discovery
    only, no number from that post is used here).  Deterministic: a gap up is faded by buying NO,
    a gap down by buying YES, with the same +5c verified-bid take-profit as the momentum rules.
    """
    if i < 1 or bar.get("open") is None or bars[i - 1].get("close") is None:
        return None
    prev_close = bars[i - 1]["close"]
    gap = round(bar["open"] - prev_close, 6)
    if abs(gap) < 0.10:
        return None
    side = "no" if gap > 0 else "yes"
    return {"side": side, "reason": f"open {bar['open']:.4f} gapped {gap:+.4f} vs prior close {prev_close:.4f}; "
                                    f"fade toward the prior close (verified {side} side only)",
            "exit": "profit"}


def rule_favourite_stop(bar, bars, i, market):
    """ArchiveFavourite's entry with a 10c protective exit added - measures what the stop is worth."""
    ask, spread = bar.get("ask"), _spread(bar)
    if ask is None or spread is None or not (0.85 <= ask <= 0.97) or spread > 0.05:
        return None
    return {"side": "yes", "reason": f"verified ask {ask:.4f} in [0.85, 0.97] with spread {spread:.4f}; "
                                     f"stop if the verified bid drops 10c under entry",
            "exit": "stop", "stopLevel": round(ask - 0.10, 6)}


STRATEGIES = [
    {"id": "arch-favourite", "username": "ArchiveFavourite", "name": "Archive Favourite Band",
     "rule": rule_favourite_band, "exit": "hold",
     "text": "Enter at the verified ask when it is 85-97c with a spread <= 5c; hold to the official result.",
     "why": "The forward desk's favourite personas, replayed on settled tapes: favourites are usually right, "
            "and the tape shows exactly what the band paid in fees and lost on the exceptions."},
    {"id": "arch-longshot", "username": "ArchiveLongshot", "name": "Archive Longshot Sweep",
     "rule": rule_longshot_sweep, "exit": "hold",
     "text": "Enter at the verified ask when it is 5-20c; hold to the official result.",
     "why": "Measures the cost of longshot convexity on real settled markets (the favourite-longshot bias "
            "predicts a bleed; this quantifies it per series)."},
    {"id": "arch-fade-longshot", "username": "ArchiveFader", "name": "Archive Longshot Fader",
     "rule": rule_fade_longshot, "exit": "hold",
     "text": "When YES is 5-20c on a bar with a spread <= 5c, buy NO at 1 - yes_bid_close; hold to settlement.",
     "why": "The mirror of the sweep: selling the longshot collects the premium but pays the full stake on upsets."},
    {"id": "arch-sma", "username": "ArchiveSMA", "name": "Archive SMA Cross",
     "rule": rule_sma_cross, "exit": "sma",
     "text": "Enter YES on a verified SMA5 > SMA10 cross; exit at the verified bid when the close falls below SMA10, "
             "else hold to settlement.",
     "why": "A Pine-style trend rule on official bars only - the same idea PinePilotX forward-tests, so the two can be "
            "compared on identical prices."},
    {"id": "arch-momentum", "username": "ArchiveMomentum", "name": "Archive Day-over-Day Momentum",
     "rule": rule_momentum, "exit": "profit",
     "text": "Enter the direction of a >= 3c move versus the bar one day earlier; exit at the verified bid 5c above "
             "entry, else hold to settlement.",
     "why": "Momentum on the verified tape; the exit rule is mechanical so the replay has no discretion."},
    {"id": "arch-panic-fade", "username": "ArchivePanicFade", "name": "Archive Panic Fade",
     "rule": rule_panic_fade, "exit": "profit",
     "text": "On 1-minute bars, enter YES when the close is >= 15c below the prior five-minute high; exit at the "
             "verified bid 5c above entry, else hold to settlement.",
     "why": "Volatility reversion inside micro markets, replayed on the archived 1-minute candles of settled "
            "15-minute markets."},
    {"id": "arch-late-favourite", "username": "ArchiveCloser", "name": "Archive Late Favourite",
     "rule": rule_late_favourite, "exit": "hold",
     "text": "Inside the last 20% of the archived tape, enter a favourite quoted at or above 90c; hold to settlement.",
     "why": "Late certainty: the tape shows what the last few cents of a near-decided market actually paid."},
    {"id": "arch-gap-fade", "username": "ArchiveGapFade", "name": "Archive Opening-Gap Fade",
     "rule": rule_gap_fade, "exit": "profit",
     "text": "When a bar opens >= 10c away from the previous bar's close, fade the gap (buy the cheap side) and "
             "exit at the verified bid 5c above entry, else hold to settlement.",
     "why": "The mean-reversion archetype the r/PredictionsMarkets 5,000-strategy KXBTC15M backtest reported as "
            "the only profitable family (discovery only; measured here on official bars). Distinguishes a true "
            "overreaction from ordinary noise on each market's own tape."},
    {"id": "arch-favourite-stop", "username": "ArchiveFavStop", "name": "Archive Favourite with 10c Stop",
     "rule": rule_favourite_stop, "exit": "stop",
     "text": "Same entry as ArchiveFavourite (verified ask 85-97c with a spread <= 5c) but sell at the first "
             "verified bid 10c under the entry price; otherwise hold to settlement.",
     "why": "Isolates one design choice against its own twin: what cutting losers at 10c does to a hold-to-"
            "settlement favourite strategy on the same markets, same fees, same bars."},
]


def replay(strategy: dict, markets: list[dict]) -> tuple[list[dict], dict]:
    """Run one rule over every archived market; returns (trades, per-strategy totals)."""
    trades = []
    totals = {"fills": 0, "wins": 0, "losses": 0, "realized": 0.0, "fees": 0.0, "slippage": 0.0,
              "marketsTraded": 0, "notional": 0.0, "contracts": 0.0, "skippedNoLiquidity": 0}
    cash = STARTING_CASH
    for market in markets:
        bars = market["bars"]
        multiplier = market["feeMultiplier"]
        position = None
        for i, bar in enumerate(bars):
            if position is None:
                signal = strategy["rule"](bar, bars, i, market)
                if not signal:
                    continue
                price = _entry_price(bar, signal["side"])
                volume = bar.get("volume") or 0.0
                if price is None or not (0 < price < 1) or volume <= 0:
                    totals["skippedNoLiquidity"] += 1
                    continue
                qty = int(min(cash * EQUITY_FRACTION / max(price, 1e-9), volume * VOLUME_CAP_FRACTION))
                if qty <= 0:
                    totals["skippedNoLiquidity"] += 1
                    continue
                while qty > 0 and qty * price + fee(price, qty, multiplier) > cash:
                    qty -= 1
                if qty <= 0:
                    totals["skippedNoLiquidity"] += 1
                    continue
                cost = qty * price
                entry_fee = fee(price, qty, multiplier)
                mid = _mid(bar, signal["side"])
                slip = None if mid is None else round((price - mid) * qty, 6)
                cash = round(cash - cost - entry_fee, 6)
                position = {"side": signal["side"], "contracts": qty, "entryPrice": price, "entryTs": bar["ts"],
                            "entryBar": {"end_period_ts": bar["ts"], "yes_bid_close": bar.get("bid"),
                                         "yes_ask_close": bar.get("ask"), "volume": volume},
                            "entryFee": entry_fee, "entryNotional": round(cost, 6), "slippageEntry": slip,
                            "reason": signal["reason"], "exitMode": signal.get("exit") or strategy["exit"],
                            "exitTarget": None if (signal.get("exit") or strategy["exit"]) != "profit" else round(price + 0.05, 6),
                            "stopLevel": signal.get("stopLevel")}
                continue
            # exits: only on a bar with a verified bid for the held side
            exit_price = _exit_price(bar, position["side"])
            reason = None
            if position["exitMode"] == "profit" and position["exitTarget"] and exit_price is not None \
                    and exit_price >= position["exitTarget"]:
                reason = f"verified bid {exit_price:.4f} >= take-profit {position['exitTarget']:.4f}"
            elif position["exitMode"] == "stop" and exit_price is not None \
                    and position.get("stopLevel") is not None and exit_price <= position["stopLevel"] + 1e-9:
                reason = f"verified bid {exit_price:.4f} at/below protective stop {position['stopLevel']:.4f}"
            elif position["exitMode"] == "sma" and exit_price is not None:
                closes = [b["close"] for b in bars[:i + 1] if b.get("close") is not None]
                if len(closes) >= 10 and closes[-1] is not None and closes[-1] < sum(closes[-10:]) / 10:
                    reason = f"verified close {closes[-1]:.4f} < SMA10 {sum(closes[-10:]) / 10:.4f}"
            if reason:
                closed = _close(position, bar, exit_price, reason, market, multiplier)
                cash = round(cash + closed["exitNotional"] - closed["exitFee"], 6)
                trades.append(closed)
                _tally(totals, closed)
                position = None
        if position is not None:  # official settlement from candles/index.jsonl
            payout_per = 1.0 if market["result"] == position["side"] else 0.0
            reason = f"official settlement {market['result']} at {market['settlementTs']}"
            closed = _finish(position, market, payout_per, reason, multiplier, exit_ts=market["settlementTsNum"],
                             exit_type="settlement", exit_fee=0.0, slippage_exit=0.0)
            cash = round(cash + payout_per * position["contracts"], 6)
            trades.append(closed)
            _tally(totals, closed)
            position = None
        if any(t["ticker"] == market["ticker"] for t in trades):
            totals["marketsTraded"] += 1
    totals["cash"] = round(cash, 6)
    totals["realized"] = round(sum(t["pnl"] for t in trades), 6)
    return trades, totals


def _close(position, bar, exit_price, reason, market, multiplier):
    """Rule-based exit at a later bar's verified bid for the held side."""
    qty = position["contracts"]
    proceeds = round(exit_price * qty, 6)
    exit_fee = fee(exit_price, qty, multiplier)
    mid = _mid(bar, position["side"])
    slip = None if mid is None else round((mid - exit_price) * qty, 6)
    pnl = round(proceeds - exit_fee - position["entryNotional"] - position["entryFee"], 6)
    return _trade_dict(position, market, exit_price, reason, multiplier, exit_ts=bar["ts"], exit_type="bid_exit",
                       exit_fee=exit_fee, slippage_exit=slip, pnl=pnl,
                       exit_bar={"end_period_ts": bar["ts"], "yes_bid_close": bar.get("bid"),
                                 "yes_ask_close": bar.get("ask"), "volume": bar.get("volume")})


def _finish(position, market, payout_per, reason, multiplier, exit_ts, exit_type, exit_fee, slippage_exit):
    qty = position["contracts"]
    pnl = round(payout_per * qty - exit_fee - position["entryNotional"] - position["entryFee"], 6)
    return _trade_dict(position, market, payout_per, reason, multiplier, exit_ts=exit_ts, exit_type=exit_type,
                       exit_fee=exit_fee, slippage_exit=slippage_exit, pnl=pnl, exit_bar=None)


def _trade_dict(position, market, exit_price, reason, multiplier, exit_ts, exit_type, exit_fee, slippage_exit, pnl, exit_bar):
    qty = position["contracts"]
    return {"id": f"{market['ticker']}-{position['entryTs']}", "ticker": market["ticker"], "series": market["series"],
            "file": market["file"], "sha256": market["sha256"], "url": market["url"], "period": market["period"],
            "side": position["side"], "contracts": qty, "entryPrice": position["entryPrice"], "entryTs": position["entryTs"],
            "entryAt": iso(position["entryTs"]), "entryBar": position["entryBar"], "entryFee": position["entryFee"],
            "entryNotional": position["entryNotional"], "entryReason": position["reason"],
            "slippageEntry": position["slippageEntry"],
            "exitPrice": exit_price, "exitTs": exit_ts, "exitAt": iso(exit_ts) if exit_ts else None,
            "exitType": exit_type, "exitReason": reason, "exitFee": exit_fee, "exitBar": exit_bar,
            "exitNotional": round(exit_price * qty, 6), "slippageExit": slippage_exit,
            "feesTotal": round(position["entryFee"] + exit_fee, 6), "pnl": pnl,
            "result": market["result"], "settlementTs": market["settlementTs"], "feeMultiplier": multiplier}


def _tally(totals, trade):
    totals["fills"] += 1
    totals["wins" if trade["pnl"] > 0 else "losses"] += 1
    totals["fees"] = round(totals["fees"] + trade["feesTotal"], 6)
    totals["slippage"] = round(totals["slippage"] + (trade["slippageEntry"] or 0) + (trade["slippageExit"] or 0), 6)
    totals["notional"] = round(totals["notional"] + trade["entryNotional"], 6)
    totals["contracts"] = round(totals["contracts"] + trade["contracts"], 2)


def analysis(strategy, totals, markets) -> str:
    if not totals["fills"]:
        return (f"{strategy['username']} never triggered on the {len(markets)} archived markets: the rule needs a price "
                f"band or pattern that this sample does not contain yet. No return is claimed.")
    win_rate = totals["wins"] / totals["fills"] * 100
    avg = totals["notional"] / totals["fills"]
    parts = [f"{totals['fills']} trade(s) on {totals['marketsTraded']} of {len(markets)} archived market(s), "
             f"{totals['wins']} win / {totals['losses']} loss ({win_rate:.0f}% win rate), average entry notional "
             f"${avg:,.2f}."]
    parts.append(f"Realized ${totals['realized']:,.2f} ({totals['realized'] / STARTING_CASH * 100:+.3f}% of the "
                 f"${STARTING_CASH:,.0f} start) after ${totals['fees']:,.2f} of quadratic taker fees and "
                 f"${totals['slippage']:,.2f} of half-spread slippage measured on the same bars.")
    if totals["skippedNoLiquidity"]:
        parts.append(f"{totals['skippedNoLiquidity']} signal(s) produced no trade because the bar had no verified "
                     f"volume or the 25% volume cap left nothing affordable.")
    parts.append(strategy["why"])
    return " ".join(parts)


def curves_from_trades(trades: list[dict], strategies: list[dict]) -> dict:
    """Cumulative realized PnL timeline per rule set, one point per closed trade.

    Honest label: positions are carried at cost until they close (no unrealized per-bar mark), so
    the curve steps at each exit/settlement timestamp.  Equity = starting cash + cumulative
    realized PnL; the final point is exactly the leaderboard's return (verified by verify_data).
    """
    by_strategy: dict[str, list[dict]] = {}
    for trade in trades:
        by_strategy.setdefault(trade["strategyId"], []).append(trade)
    curves = {"schemaVersion": 1, "generatedAt": iso(),
              "method": "cumulative realized PnL at each trade's exit/settlement timestamp; entries marked at "
                        "cost until closed (no unrealized mark); equity = 10000 + cumulative PnL",
              "startingCash": STARTING_CASH, "strategies": {}}
    for strategy in strategies:
        rows = sorted(by_strategy.get(strategy["id"], []), key=lambda t: (t["exitTs"], t["id"]))
        if not rows:
            curves["strategies"][strategy["id"]] = {"username": strategy["username"], "startTs": None, "points": []}
            continue
        cum = 0.0
        points = []
        for trade in rows:
            cum = round(cum + trade["pnl"], 6)
            points.append([trade["exitTs"], cum])
        curves["strategies"][strategy["id"]] = {"username": strategy["username"], "startTs": rows[0]["entryTs"],
                                                "points": points}
    return curves


def walk_forward(trades: list[dict], strategies: list[dict], folds: int) -> dict:
    """Split the replay horizon into equal windows by ENTRY time; a fold holds the trades entered in it.

    Exits and settlements always use the real later bars, so a fold is out-of-sample for its own
    entries: no bar is re-simulated, moved or invented.  Each fold restarts from $10,000 so folds
    are comparable.  With one month of archived bars the folds are short - the output says so
    instead of pretending to be robust (IRR-30).
    """
    entries = [t["entryTs"] for t in trades]
    exits = [t["exitTs"] for t in trades]
    result = {"schemaVersion": 1, "generatedAt": iso(), "folds": folds,
              "method": "trades assigned to equal-duration windows by entry time; exits/settlements on the real "
                        "later bars; each fold resets to $10,000",
              "windows": [], "rows": []}
    if not entries:
        return result
    tmin, tmax = min(entries), max(max(exits), max(entries))
    folds = max(1, min(folds, 6))
    span = max(tmax - tmin, 1)
    edges = [tmin + span * k / folds for k in range(folds)] + [tmax + 1]
    for k in range(folds):
        result["windows"].append({"fold": k + 1, "startTs": int(edges[k]), "endTs": int(min(edges[k + 1], tmax)),
                                  "startAt": iso(int(edges[k])), "endAt": iso(int(min(edges[k + 1], tmax)))})

    def fold_of(ts):
        for k in range(folds):
            if edges[k] <= ts < edges[k + 1]:
                return k + 1
        return folds

    for strategy in strategies:
        rows = [t for t in trades if t["strategyId"] == strategy["id"]]
        per_fold = {k: [] for k in range(1, folds + 1)}
        for trade in rows:
            per_fold[fold_of(trade["entryTs"])].append(trade)
        for k, fold_rows in per_fold.items():
            if not fold_rows:
                continue
            wins = sum(1 for t in fold_rows if t["pnl"] > 0)
            realized = round(sum(t["pnl"] for t in fold_rows), 6)
            result["rows"].append({"strategyId": strategy["id"], "username": strategy["username"], "fold": k,
                                   "trades": len(fold_rows), "wins": wins, "losses": len(fold_rows) - wins,
                                   "realizedPnl": realized, "returnPct": round(realized / STARTING_CASH * 100, 4)})
    result["caveat"] = ("Folds are windows over ONE continuous replay of the same bars; the rules have no fitted "
                        "parameters, so this measures stability across time, not a train/test split. Short windows "
                        "are noise - do not promote a rule set on a single fold (IRR-30).")
    return result


def build(season_dir: str, min_bars: int, series_filter: list[str] | None = None, folds: int = 2) -> dict:
    markets = load_archive(season_dir, min_bars)
    if series_filter:
        wanted = {s.upper() for s in series_filter}
        markets = [m for m in markets if m["series"].upper() in wanted]
    board, explanations, all_trades = [], [], []
    for strategy in STRATEGIES:
        trades, totals = replay(strategy, markets)
        for trade in trades:
            trade["strategyId"] = strategy["id"]
            trade["username"] = strategy["username"]
        all_trades.extend(trades)
        cash = round(STARTING_CASH + totals["realized"], 6)
        board.append({"strategyId": strategy["id"], "username": strategy["username"], "name": strategy["name"],
                      "rule": strategy["text"], "why": strategy["why"], "startingCash": STARTING_CASH,
                      "cash": cash, "equity": cash, "returnPct": round((cash / STARTING_CASH - 1) * 100, 4),
                      "realizedPnl": totals["realized"], "feesPaid": totals["fees"], "slippagePaid": totals["slippage"],
                      "trades": totals["fills"], "wins": totals["wins"], "losses": totals["losses"],
                      "marketsTraded": totals["marketsTraded"], "contracts": totals["contracts"],
                      "notional": totals["notional"], "skippedNoLiquidity": totals["skippedNoLiquidity"],
                      "evidenceState": ("verified archived candles + official results" if totals["fills"]
                                        else "no trigger in the archived sample")})
        explanations.append({"strategyId": strategy["id"], "username": strategy["username"], "name": strategy["name"],
                             "explanation": analysis(strategy, totals, markets)})
    board.sort(key=lambda row: (-row["returnPct"], row["username"]))
    for i, row in enumerate(board):
        row["rank"] = i + 1
    competition = {
        "schemaVersion": 1, "generatedAt": iso(), "season": os.path.basename(season_dir).replace("season-", ""),
        "source": "data/season-*/forward/candles/ archived by scripts/forward_desk.py from "
                  "GET /series/{series}/markets/{ticker}/candlesticks (each file is bound to the response URL and "
                  "SHA-256 in candles/index.jsonl)",
        "marketCount": len(markets), "verifiedBars": sum(m["barCount"] for m in markets),
        "minBars": min_bars,
        "sizingRule": f"{int(EQUITY_FRACTION * 100)}% of cash, capped at {int(VOLUME_CAP_FRACTION * 100)}% of the "
                      f"bar's verified volume; no entry without bar volume",
        "feeRule": "0.07 * q * p * (1-p) * series fee_multiplier, rounded up to $0.0001; no fee on settlement",
        "exitRule": "take-profit/SMA exits fill at the verified bid of a later bar; otherwise the position settles at "
                    "the official result (1/0) at the archived settlement_ts",
        "series": sorted({m["series"] for m in markets}),
        "seriesFilter": sorted({s.upper() for s in series_filter}) if series_filter else None,
        "periods": sorted({int(m["period"] or 0) for m in markets}),
        "markets": {m["ticker"]: {"series": m["series"], "file": m["file"], "bars": m["barCount"], "period": m["period"],
                                  "result": m["result"], "settlementTs": m["settlementTs"], "openTs": m["openTs"],
                                  "closeTs": m["closeTs"], "url": m["url"], "sha256": m["sha256"],
                                  "archivedByCycle": m["cycle"], "feeType": m["feeType"],
                                  "feeMultiplier": m["feeMultiplier"]} for m in markets},
    }
    return {"competition": competition, "trades": all_trades, "board": board, "explanations": explanations,
            "curves": curves_from_trades(all_trades, STRATEGIES),
            "walkForward": walk_forward(all_trades, STRATEGIES, folds)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", help="season year (default: the newest data/season-* directory)")
    parser.add_argument("--min-bars", type=int, default=8, help="skip archived markets with fewer bars")
    parser.add_argument("--series", help="comma-separated series filter, e.g. KXBTC15M,KXGOLD15M")
    parser.add_argument("--folds", type=int, default=2, help="walk-forward windows over the replay horizon")
    parser.add_argument("--dry-run", action="store_true", help="compute and print, do not write")
    args = parser.parse_args(argv)
    season = args.season
    if not season:
        seasons = sorted(d.replace("season-", "") for d in os.listdir(DATA_DIR)
                         if d.startswith("season-") and os.path.isdir(os.path.join(DATA_DIR, d)))
        if not seasons:
            print("no data/season-* directory")
            return 1
        season = seasons[-1]
    season_dir = os.path.join(DATA_DIR, f"season-{season}")
    out = build(season_dir, args.min_bars,
                series_filter=[s.strip() for s in args.series.split(",") if s.strip()] if args.series else None,
                folds=args.folds)
    if args.dry_run:
        print(json.dumps({"season": season, "markets": out["competition"]["marketCount"],
                          "bars": out["competition"]["verifiedBars"], "trades": len(out["trades"]),
                          "seriesFilter": out["competition"]["seriesFilter"],
                          "board": [{k: r[k] for k in ("rank", "username", "returnPct", "trades", "wins", "losses")}
                                    for r in out["board"]]}, indent=1))
        return 0
    if out["competition"].get("seriesFilter"):
        print("--series is an analysis filter; refusing to overwrite the committed full-archive outputs")
        return 2
    target = os.path.join(season_dir, "backtest-archive")
    os.makedirs(target, exist_ok=True)
    for name, payload in (("competition.json", out["competition"]), ("trades.json", out["trades"]),
                          ("leaderboard.json", out["board"]), ("explanations.json", out["explanations"]),
                          ("curves.json", out["curves"]), ("walkforward.json", out["walkForward"])):
        with open(os.path.join(target, name), "w") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
            fh.write("\n")
    print(json.dumps({"season": season, "markets": out["competition"]["marketCount"],
                      "verifiedBars": out["competition"]["verifiedBars"], "trades": len(out["trades"]),
                      "written": [os.path.relpath(os.path.join(target, n), ROOT) for n in
                                  ("competition.json", "trades.json", "leaderboard.json", "explanations.json",
                                   "curves.json", "walkforward.json")]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
