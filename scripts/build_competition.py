#!/usr/bin/env python3
"""Season 2026 competition builder.

Reads ONLY the verified data stored in data/season-2026 (raw API responses captured
2026-09-19, see MANIFEST.md), runs the deterministic strategy rules over the verified
candlesticks, and writes the competition memory files:

  data/season-2026/competition.json        - season metadata
  data/season-2026/trades.json             - every placed backtest trade (verified prices/dates)
  data/season-2026/leaderboard.json        - computed standings
  data/season-2026/intents.json            - upcoming (proposed) forward trades from the live snapshot
  data/season-2026/explanations.json       - per-strategy result analysis

Accounting conventions (all documented, none invented):
- Entry fill price  = the bar's verified yes_ask_close (taker buying YES) or
  1 - yes_bid_close (taker buying NO), exactly as the candlesticks returned them.
- Exit fill price   = the bar's verified yes_bid_close (taker selling).
- Settlement exit   = official market result (1.00 / 0.00) at the official settlement_ts.
- Fee               = Kalshi standard quadratic taker fee 0.07 * q * p * (1-p),
  rounded UP to the nearest $0.0001 (documented fee-rounding rule). No fee at settlement
  (flagged assumption IRR-11).
- Sizing            = 50% of current equity, capped at 25% of that bar's verified volume
  (liquidity constraint). No bar volume -> no entry (no verified liquidity).
- Slippage reported = half-spread paid: entry_slip = (fill - mid)*q, exit_slip = (mid - fill)*q,
  mid = (yes_bid_close + yes_ask_close)/2 from the same verified bar.
- No look-ahead: a strategy decides using only bars up to and including bar i, and fills
  are timestamped at bar i's end_period_ts.
"""
import csv
import io
import json
import math
import os
from datetime import datetime, timezone

BASE = os.path.join(os.path.dirname(__file__), "..", "data", "season-2026")
STARTING_CASH = 10_000.0
EQUITY_FRACTION = 0.50
VOLUME_CAP_FRACTION = 0.25


def fee(price, qty):
    if qty <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw = 0.07 * qty * price * (1.0 - price)
    return math.ceil((raw - 1e-12) * 10_000) / 10_000


def read_candles(path):
    rows = []
    with open(os.path.join(BASE, path), newline="") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            rows.append(next(csv.reader(io.StringIO(line))))
    header = rows[0]
    out = []
    for r in rows[1:]:
        d = dict(zip(header, r))
        g = lambda k: (float(d[k]) if d[k].strip() else None)
        out.append({
            "ts": int(d["end_period_ts"]),
            "po": g("price_open"), "ph": g("price_high"), "pl": g("price_low"),
            "pc": g("price_close"), "pm": g("price_mean"), "pp": g("price_previous"),
            "vol": g("volume") or 0.0, "oi": g("open_interest"),
            "bid": g("yes_bid_close"), "ask": g("yes_ask_close"),
        })
    return out


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


MARKETS = {
    "KXCPI-26AUG-T0.8": {
        "csv": "candles-KXCPI-26AUG-T0.8-daily.csv",
        "title": "Will CPI rise more than 0.8% in August 2026?",
        "result": "no",
        "settlement_ts": "2026-09-11T13:28:53.706257Z",
        "close_time": "2026-09-11T12:25:00Z",
        "settlement_ts_num": 1789235055,  # 2026-09-11T13:24:15? -> recomputed below from string
        "frequency": "daily",
        "fast": 5, "slow": 10,
    },
    "KXFED-26SEP-T4.75": {
        "csv": "candles-KXFED-26SEP-T4.75-daily.csv",
        "title": "Fed rate above 4.75% after Sep 16, 2026 meeting?",
        "result": "no",
        "settlement_ts": "2026-09-16T18:20:58.459351Z",
        "close_time": "2026-09-16T17:55:00Z",
        "frequency": "daily",
        "fast": 5, "slow": 10,
    },
    "KXNFLGAME-26SEP17DETBUF-BUF": {
        "csv": "candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv",
        "title": "Buffalo wins (DET Lions @ BUF Bills, Sep 17, 2026)",
        "result": "yes",
        "settlement_ts": "2026-09-18T03:34:55.133992Z",
        "close_time": "2026-09-18T03:29:53Z",
        "frequency": "hourly",
        "fast": 3, "slow": 6,
    },
}
for m in MARKETS.values():
    m["settlement_ts_num"] = int(datetime.fromisoformat(
        m["settlement_ts"].replace("Z", "+00:00")).timestamp())

STRATEGIES = [
    {"id": "cross-chaser", "username": "CrossChaser", "name": "Momentum Cross (Pine-style)",
     "rule": "Long when last price > slow SMA and fast SMA > slow SMA (daily 5/10, hourly 3/6); "
             "exit when last price < slow SMA at the verified bid; else hold to settlement.",
     "why": "Classic trend-following (PinePilot-style) on a verified tape. Buys continuation after "
            "breakouts; pays the spread both ways."},
    {"id": "dip-hunter", "username": "DipHunter", "name": "Cheap Dip Hunter",
     "rule": "Buy YES at the verified ask when ask <= 0.05 on a bar with volume; exit at the first "
             "verified bid >= 2x entry, else hold to settlement.",
     "why": "Return-seeking lottery-ticket play: pay pennies, need a 100% payoff or a double to quit. "
            "Honest exposure to one-sided books where the YES bid sits at 0."},
    {"id": "spike-surfer", "username": "SpikeSurfer", "name": "Release Spike Surfer",
     "rule": "Buy in the direction of a bar whose |last - previous last| >= 0.20 AND volume >= 3x the "
             "average of the prior 10 volume bars; exit at the first verified bid above entry, else "
             "hold to settlement.",
     "why": "Chases information releases (CPI, score, Fed) when the verified tape shows both a price "
            "jump and heavy official volume. Loses big when the spike is a trap."},
    {"id": "sure-thing", "username": "SureThing", "name": "Favorite Holder",
     "rule": "Buy YES at the verified ask when bar close >= 0.90 and bar volume >= 100,000 contracts; "
             "hold to official settlement.",
     "why": "Favorite-longshot-bias play: the literature says heavy favorites on event markets are "
            "slightly under-priced. Only enters when the verified tape proves a liquid 90+ cent favorite."},
    {"id": "yield-sniper", "username": "YieldSniper", "name": "Certainty Carry",
     "rule": "Buy NO at the verified NO ask (1 - YES bid) when NO ask is in [0.97, 0.99] (i.e. YES bid "
             "0.01-0.03), the bar has volume, and official settlement is within 21 days; hold to "
             "settlement. NO asks below 0.97 are lottery tickets, not carry, and are rejected.",
     "why": "Earns the residual on near-certain outcomes with a hard time horizon. Small, steady, "
            "documented; dies instantly if the favorite collapses."},
]


def run_strategy(sid, market, bars):
    trades = []
    cash = STARTING_CASH
    pos = None
    last = None
    last_history = []
    trade_no = 0

    def close_position(bar, exit_price, exit_fee, exit_type, note=""):
        nonlocal cash, pos, trade_no
        payout = exit_price * pos["contracts"] - exit_fee
        pnl = payout - (pos["cost_notional"] + pos["entry_fee"])
        mid = (pos["entry_bid"] + pos["entry_ask"]) / 2 if pos["entry_bid"] is not None and pos["entry_ask"] is not None else None
        exit_mid = (bar["bid"] + bar["ask"]) / 2 if bar["bid"] is not None and bar["ask"] is not None else None
        slip_in = (pos["entry_price"] - mid) * pos["contracts"] if mid is not None else None
        slip_out = (exit_mid - exit_price) * pos["contracts"] if exit_mid is not None else None
        cash += payout
        trades.append({
            "id": f"{sid}-{market}-t{trade_no}",
            "strategyId": sid,
            "market": market,
            "marketTitle": MARKETS[market]["title"],
            "result": MARKETS[market]["result"],
            "side": pos["side"],
            "contracts": pos["contracts"],
            "entryPrice": pos["entry_price"],
            "entryFee": pos["entry_fee"],
            "entryAt": iso(pos["entry_ts"]),
            "entryBar": {"end_period_ts": pos["entry_ts"], "yes_bid_close": pos["entry_bid"],
                         "yes_ask_close": pos["entry_ask"], "volume": bar0vol(pos), "open_interest": None},
            "exitPrice": exit_price,
            "exitAt": bar["ts_iso"] if exit_type != "settlement" else MARKETS[market]["settlement_ts"],
            "exitType": exit_type,
            "exitFee": exit_fee,
            "pnl": round(pnl, 6),
            "feesTotal": round(pos["entry_fee"] + exit_fee, 6),
            "slippageEntry": round(slip_in, 6) if slip_in is not None else None,
            "slippageExit": round(slip_out, 6) if slip_out is not None else None,
            "note": note,
        })
        trade_no += 1
        pos = None

    def bar0vol(p):
        return p.get("entry_vol")

    for bar in bars:
        if bar["pc"] is not None:
            last = bar["pc"]
        if last is None:
            continue
        last_history.append(last)
        fast = sma(last_history, MARKETS[market]["fast"])
        slow = sma(last_history, MARKETS[market]["slow"])

        # --- exits first (uses this bar's verified quotes) ---
        if pos is not None and pos["side"] == "yes":
            if sid == "cross-chaser" and slow is not None and last < slow and bar["bid"] is not None and bar["bid"] > 0:
                q = pos["contracts"]
                close_position({**bar, "ts_iso": iso(bar["ts"])}, bar["bid"], fee(bar["bid"], q), "bid_exit",
                               "last < slow SMA; exited at verified bid")
            elif sid == "dip-hunter" and bar["bid"] is not None and bar["bid"] >= 2 * pos["entry_price"] and bar["bid"] > 0:
                q = pos["contracts"]
                close_position({**bar, "ts_iso": iso(bar["ts"])}, bar["bid"], fee(bar["bid"], q), "bid_exit",
                               "bid >= 2x entry; exited at verified bid")
            elif sid == "spike-surfer" and bar["bid"] is not None and bar["bid"] > pos["entry_price"]:
                q = pos["contracts"]
                close_position({**bar, "ts_iso": iso(bar["ts"])}, bar["bid"], fee(bar["bid"], q), "bid_exit",
                               "first verified bid above entry; exited at verified bid")

        # --- entries (uses this bar's verified quotes; fills at bar close) ---
        if pos is None and bar["vol"] > 0:
            entry_side = "yes"
            entry_price = bar["ask"]
            signal = None
            if entry_price is not None and 0 < entry_price < 1:
                if sid == "cross-chaser":
                    if fast is not None and slow is not None and last > slow and fast > slow:
                        signal = "last > slow SMA and fast SMA > slow SMA"
                elif sid == "dip-hunter":
                    if entry_price <= 0.05:
                        signal = f"verified YES ask {entry_price:.2f} <= 0.05 on a volume bar"
                elif sid == "spike-surfer":
                    if len(last_history) >= 2:
                        prev_last = last_history[-2]
                        vol_bars = [b["vol"] for b in bars if b["ts"] < bar["ts"] and b["vol"] > 0][-10:]
                        if len(vol_bars) >= 3:
                            avg = sum(vol_bars) / len(vol_bars)
                            if abs(last - prev_last) >= 0.20 and bar["vol"] >= 3 * avg:
                                signal = (f"move {last - prev_last:+.2f} with volume {bar['vol']:,.2f} "
                                          f">= 3x avg {avg:,.2f}")
                elif sid == "sure-thing":
                    if bar["pc"] is not None and bar["pc"] >= 0.90 and bar["vol"] >= 100_000:
                        signal = f"close {bar['pc']:.2f} >= 0.90 with volume {bar['vol']:,.2f} >= 100k"
                elif sid == "yield-sniper":
                    if bar["bid"] is not None and bar["bid"] >= 0.01:
                        no_ask = round(1.0 - bar["bid"], 4)
                        if 0.97 <= no_ask <= 0.99 and MARKETS[market]["settlement_ts_num"] - bar["ts"] <= 21 * 86400:
                            entry_side = "no"
                            entry_price = no_ask
                            signal = f"NO ask {no_ask:.2f} in [0.97,0.99] within 21d of official settlement"
            if signal:
                equity = cash
                max_contracts = int(equity * EQUITY_FRACTION / entry_price)
                cap = int(bar["vol"] * VOLUME_CAP_FRACTION)
                qty = min(max_contracts, cap)
                if qty > 0:
                    f = fee(entry_price, qty)
                    if entry_price * qty + f <= equity:
                        pos = {"side": entry_side, "contracts": qty, "entry_price": entry_price,
                               "entry_fee": f, "entry_ts": bar["ts"], "cost_notional": entry_price * qty,
                               "entry_bid": bar["bid"], "entry_ask": bar["ask"], "entry_vol": bar["vol"]}
                        cash -= entry_price * qty + f

    # --- settle any open position at the official result ---
    if pos is not None:
        won = (MARKETS[market]["result"] == "yes") == (pos["side"] == "yes")
        payout_per = 1.0 if won else 0.0
        bar = bars[-1]
        mid = (bar["bid"] + bar["ask"]) / 2 if bar["bid"] is not None and bar["ask"] is not None else None
        slip_in = (pos["entry_price"] - mid) * pos["contracts"] if mid is not None else None
        slip_out = (mid - payout_per) * pos["contracts"] if mid is not None else None
        pnl = payout_per * pos["contracts"] - (pos["cost_notional"] + pos["entry_fee"])
        cash += payout_per * pos["contracts"]
        trades.append({
            "id": f"{sid}-{market}-t{trade_no}",
            "strategyId": sid,
            "market": market,
            "marketTitle": MARKETS[market]["title"],
            "result": MARKETS[market]["result"],
            "side": pos["side"],
            "contracts": pos["contracts"],
            "entryPrice": pos["entry_price"],
            "entryFee": pos["entry_fee"],
            "entryAt": iso(pos["entry_ts"]),
            "entryBar": {"end_period_ts": pos["entry_ts"], "yes_bid_close": pos["entry_bid"],
                         "yes_ask_close": pos["entry_ask"], "volume": pos["entry_vol"], "open_interest": None},
            "exitPrice": payout_per,
            "exitAt": MARKETS[market]["settlement_ts"],
            "exitType": "settlement",
            "exitFee": 0.0,
            "pnl": round(pnl, 6),
            "feesTotal": round(pos["entry_fee"], 6),
            "slippageEntry": round(slip_in, 6) if slip_in is not None else None,
            "slippageExit": round(slip_out, 6) if slip_out is not None else None,
            "note": f"held to official settlement (result={MARKETS[market]['result']}); no fee at settlement (assumption IRR-11)",
        })

    return cash, trades


def main():
    all_trades = []
    standings = []
    verified_bars = {}
    for market in MARKETS:
        verified_bars[market] = len(read_candles(MARKETS[market]["csv"]))
    for strat in STRATEGIES:
        sid = strat["id"]
        strat_trades = []
        for market in MARKETS:
            bars = read_candles(MARKETS[market]["csv"])
            _cash, trades = run_strategy(sid, market, bars)
            strat_trades.extend(trades)
        equity = STARTING_CASH + sum(t["pnl"] for t in strat_trades)
        wins = sum(1 for t in strat_trades if t["pnl"] > 0)
        standings.append({
            "strategyId": sid,
            "username": strat["username"],
            "name": strat["name"],
            "rule": strat["rule"],
            "why": strat["why"],
            "startingCash": STARTING_CASH,
            "equity": round(equity, 2),
            "returnPct": round((equity / STARTING_CASH - 1) * 100, 4),
            "trades": len(strat_trades),
            "wins": wins,
            "losses": len(strat_trades) - wins,
            "bestTradePnl": round(max((t["pnl"] for t in strat_trades), default=0.0), 2),
            "worstTradePnl": round(min((t["pnl"] for t in strat_trades), default=0.0), 2),
            "realizedPnl": round(sum(t["pnl"] for t in strat_trades), 2),
            "feesPaid": round(sum(t["feesTotal"] for t in strat_trades), 4),
            "slippagePaid": round(sum((t["slippageEntry"] or 0) + (t["slippageExit"] or 0)
                                       for t in strat_trades), 4),
            "marketsTraded": sorted({t["market"] for t in strat_trades}),
            "evidenceState": "verified backtest fills" if strat_trades else "no verified signal in stored window",
        })
        all_trades.extend(strat_trades)

    standings.sort(key=lambda s: (-s["returnPct"], s["username"]))
    for i, s in enumerate(standings):
        s["rank"] = i + 1

    competition = {
        "schemaVersion": 1,
        "season": "2026",
        "description": "Return-seeking paper-trading competition. Each username is a deterministic "
                       "strategy. Backtest trades were filled ONLY against verified Kalshi candlestick "
                       "prices and the official settlement records stored in this directory "
                       "(see MANIFEST.md). Starting capital $10,000 per account.",
        "seasonWindow": {"start": "2026-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"},
        "dataCollectedAt": "2026-09-19 (UTC) session window 23:42-00:20Z",
        "startingCashPerAccount": STARTING_CASH,
        "settlementFeeAssumption": "no fee at settlement (IRR-11; to be verified against Kalshi settlement documentation in a future pass)",
        "takerFeeModel": "0.07 * q * p * (1-p), rounded up to nearest $0.0001 (Kalshi published formula + fee-rounding docs)",
        "sizingRule": "50% of equity per entry, capped at 25% of the bar's verified volume",
        "markets": {k: {"title": v["title"], "result": v["result"], "settlementTs": v["settlement_ts"],
                        "frequency": v["frequency"], "bars": verified_bars[k]} for k, v in MARKETS.items()},
        "verifiedBars": sum(verified_bars.values()),
        "coldOpenContextBars": 27,
        "participants": len(standings),
    }

    intents = [
        {
            "id": "intent-bookrover-B90625",
            "strategyId": "book-edge", "username": "BookRocket", "strategyName": "Book Edge Sweep",
            "ticker": "KXBTC-26SEP2017-B90625",
            "title": "Bitcoin $90,500-90,749.99 at 5 PM EDT Sep 20, 2026 (YES)",
            "side": "yes",
            "proposedPrice": 0.02,
            "maxDisplayDepth": 180.0,
            "spread": 0.02,
            "reason": "Verified YES ask 0.02 <= 0.45 with a 2-cent displayed spread (YES bid 0.00 / ask 0.02, size 180).",
            "observedAt": "2026-09-19 (UTC) session window 23:42-00:20Z",
            "evidence": ["market-records.json#KXBTC-26SEP2017-B90625",
                          "GET /trade-api/v2/markets?series_ticker=KXBTC&status=open&limit=3"],
            "status": "proposed",
            "note": "Upcoming trade the strategy wants to place. A fill is only recorded after a fresh verified order book is consumed (live desk, browser).",
        },
        {
            "id": "intent-tailsprint-T90749.99",
            "strategyId": "tail-sprint", "username": "TailSprint", "strategyName": "Expiry Tail Sprint",
            "ticker": "KXBTC-26SEP2017-T90749.99",
            "title": "Bitcoin >= $90,750 at 5 PM EDT Sep 20, 2026 (YES)",
            "side": "yes",
            "proposedPrice": 0.01,
            "maxDisplayDepth": 5999.0,
            "spread": 0.01,
            "reason": "Market closes 2026-09-20T21:00:00Z (within 48h of collection); verified YES ask 0.01 <= 0.15 with 5,999 contracts of displayed depth (NO bid 0.99).",
            "observedAt": "2026-09-19 (UTC) session window 23:42-00:20Z",
            "evidence": ["market-records.json#KXBTC-26SEP2017-T90749.99",
                          "orderbook-KXBTC-26SEP2017-T90749.99.csv"],
            "status": "proposed",
            "note": "Cheapest executable side of a near-expiry binary. Pure return-seeking tail exposure; settlement source is CF Benchmarks BRTI.",
        },
        {
            "id": "intent-diphunter-T90749.99",
            "strategyId": "dip-hunter", "username": "DipHunter", "strategyName": "Cheap Dip Hunter",
            "ticker": "KXBTC-26SEP2017-T90749.99",
            "title": "Bitcoin >= $90,750 at 5 PM EDT Sep 20, 2026 (YES)",
            "side": "yes",
            "proposedPrice": 0.01,
            "maxDisplayDepth": 5999.0,
            "spread": 0.01,
            "reason": "Live rule of the backtested DipHunter: ask 0.01 <= 0.05 at a verified depth of 5,999 contracts.",
            "observedAt": "2026-09-19 (UTC) session window 23:42-00:20Z",
            "evidence": ["market-records.json#KXBTC-26SEP2017-T90749.99",
                          "orderbook-KXBTC-26SEP2017-T90749.99.csv"],
            "status": "proposed",
            "note": "Same market as TailSprint; two different return-seeking theses on one verified book.",
        },
    ]

    explanations = []
    for s in standings:
        st = [t for t in all_trades if t["strategyId"] == s["strategyId"]]
        if not st:
            text = (f"{s['username']} placed no trades in the stored verified window: the rule never "
                    f"fired on the KXCPI (Jul 24-Sep 12, 2026), KXFED (Jun 20-Sep 16, 2026) or NFL "
                    f"game-day (Sep 17-18, 2026) tapes. No return claim is made for an untested rule.")
        else:
            wins = [t for t in st if t["pnl"] > 0]
            losses = [t for t in st if t["pnl"] <= 0]
            parts = [f"{s['username']} placed {len(st)} verified trade(s): "
                     f"{len(wins)} winner(s), {len(losses)} loser(s); net PnL ${s['realizedPnl']:.2f} "
                     f"(${s['feesPaid']:.2f} fees + ${s['slippagePaid']:.2f} spread/slippage cost). "]
            for t in st:
                parts.append(f"{t['market']} {t['side']} x{t['contracts']} @ ${t['entryPrice']:.4f} "
                             f"on {t['entryAt']} -> ${t['exitPrice']:.2f} on {t['exitAt']} "
                             f"({t['exitType']}) = ${t['pnl']:.2f}. ")
            parts.append(f"Result cause: {s['why']}")
            if s["realizedPnl"] > 0:
                parts.append(" The positive return came from entries that verified against the official "
                             "settlement (or a verified bid exit), net of modeled taker fees.")
            elif s["realizedPnl"] < 0:
                parts.append(" The negative return is the documented failure mode: the verified tape did "
                             "not move in the strategy's favor and fees/spread were paid both ways.")
            text = "".join(parts)
        explanations.append({"strategyId": s["strategyId"], "username": s["username"], "explanation": text})

    def dump(name, obj):
        with open(os.path.join(BASE, name), "w") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")
        print(f"WROTE {name}")

    dump("competition.json", competition)
    dump("trades.json", all_trades)
    dump("leaderboard.json", standings)
    dump("intents.json", intents)
    dump("explanations.json", explanations)

    print("\nLEADERBOARD")
    for s in standings:
        print(f"  #{s['rank']} {s['username']:<12} equity ${s['equity']:<10} ret {s['returnPct']:>8.4f}% "
              f"trades {s['trades']} pnl {s['realizedPnl']:>10.2f}")


if __name__ == "__main__":
    main()
