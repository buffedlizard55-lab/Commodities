#!/usr/bin/env python3
"""Execution realism: compare the desk's paper fills with the official Kalshi trade tape.

The forward desk fills immediate-or-cancel limit orders against *displayed* depth captured by a REST
read (IRR-22).  Queue position, latency and market impact are not facts in that model, so the fills
may be optimistic.  This script measures how optimistic by asking the exchange what actually traded
on the same contract in the same seconds:

    GET /markets/trades?ticker=<t>&min_ts=<fill-120s>&max_ts=<fill+120s>&limit=1000

For every paper fill it records (all values straight from the two official responses):
  tapeTrades / tapeContracts   prints and volume in the window on the same outcome side
  tapeVwap                     volume-weighted price of those prints
  tapeBest                     best (cheapest for a buy) print price in the window
  centsDiff                    desk VWAP - tape VWAP, in cents (positive = the desk paid more)
  coveredByTape                tape volume in the window >= the desk's filled contracts
  withinOneCent                at least one real print within 1c of the desk's VWAP
  tapeSha256 / tapeUrl         hash + URL of the verbatim tape response (audit trail)

Nothing here changes the ledger: it only writes `forward/execution/<day>.jsonl` and a rolled-up
`forward/execution/summary.json`.  A window with no prints is reported as `no_tape_in_window`
(usually a quiet contract), never treated as a fill.

Usage:
  python3 scripts/execution_realism.py --live                # official API (GitHub runner)
  python3 scripts/execution_realism.py --fixtures DIR        # offline replay for tests
  python3 scripts/execution_realism.py --window 120 --limit 60
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from kalshi_client import KalshiClient, FixtureClient, KalshiError  # noqa: E402
from paper_engine import parse_ts, iso  # noqa: E402
from forward_desk import FORWARD_DIR as DEFAULT_FORWARD_DIR, set_paths  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read_events(forward_dir: str) -> list[dict]:
    path = os.path.join(forward_dir, "trades.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def price_for(trade: dict, side: str) -> float | None:
    """Tape price of the outcome side the desk traded (official yes/no price fields)."""
    key = "yes_price_dollars" if side == "yes" else "no_price_dollars"
    value = trade.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_tape(client, ticker: str, min_ts: int, max_ts: int, exchange_index=None) -> tuple[list[dict], bytes, str]:
    """Official per-market trade tape for a window (falls back to the unsharded call on error)."""
    try:
        payload, raw, url = client.trades(ticker=ticker, limit=1000, min_ts=min_ts, max_ts=max_ts)
        return payload.get("trades") or [], raw, url
    except KalshiError as error:
        if not exchange_index:
            raise
        payload, raw, url = client.trades(ticker=ticker, limit=1000, min_ts=min_ts, max_ts=max_ts)
        return payload.get("trades") or [], raw, url


def compare(event: dict, client, window: int) -> dict:
    """One paper fill vs the official tape around it."""
    side = event.get("side")
    at = parse_ts(event.get("entryAt") or event.get("exitAt"))
    desk_price = event.get("entryPrice") if event.get("kind") == "fill" else event.get("exitPrice")
    contracts = float(event.get("contracts") or 0)
    row = {"kind": "execution-comparison", "at": iso(int(time.time())), "eventKind": event.get("kind"),
           "strategyId": event.get("strategyId"), "username": event.get("username"), "ticker": event.get("ticker"),
           "series": event.get("series"), "side": side, "contracts": contracts, "deskPrice": desk_price,
           "deskTouch": event.get("entryTouch") or event.get("exitTouch"), "deskAt": event.get("entryAt") or event.get("exitAt"),
           "windowSeconds": window, "limitPrice": event.get("limitPrice"), "positionId": event.get("positionId")}
    if at is None or desk_price is None:
        row["status"] = "skipped_missing_timestamp_or_price"
        return row
    try:
        trades, raw, url = fetch_tape(client, event["ticker"], at - window, at + window,
                                      event.get("exchangeIndex"))
    except KalshiError as error:
        row["status"] = "tape_fetch_failed"
        row["error"] = str(error)[:200]
        return row
    import hashlib
    row["tapeUrl"] = url
    row["tapeSha256"] = hashlib.sha256(raw).hexdigest()
    mine = [t for t in trades if price_for(t, side) is not None]
    row["tapeTrades"] = len(mine)
    if not mine:
        row["status"] = "no_tape_in_window"
        row["tapeTradesAll"] = len(trades)
        return row
    counts = [float(t.get("count_fp") or 0) for t in mine]
    prices = [price_for(t, side) for t in mine]
    total = sum(counts)
    vwap = sum(p * c for p, c in zip(prices, counts)) / total if total else None
    action = "buy" if event.get("kind") == "fill" else "sell"
    best = min(prices) if action == "buy" else max(prices)
    row.update({
        "status": "compared", "tapeContracts": round(total, 2), "tapeVwap": round(vwap, 6) if vwap else None,
        "tapeBest": best, "tapeMin": min(prices), "tapeMax": max(prices),
        "centsDiff": round((desk_price - vwap) * 100, 4) if vwap else None,
        "coveredByTape": bool(total + 1e-9 >= contracts),
        "tapeCoverageRatio": round(total / contracts, 4) if contracts else None,
        "withinOneCent": bool(min(abs(p - desk_price) for p in prices) <= 0.01),
        "deskPriceInsideTapeRange": bool(min(prices) - 1e-9 <= desk_price <= max(prices) + 1e-9),
        "note": "desk fill vs GET /markets/trades in a +-%ds window; both responses are official" % window,
    })
    return row


def summarize(rows: list[dict], generated_at: str) -> dict:
    compared = [r for r in rows if r.get("status") == "compared"]
    diffs = [abs(r["centsDiff"]) for r in compared if r.get("centsDiff") is not None]
    signed = [r["centsDiff"] for r in compared if r.get("centsDiff") is not None]
    covered = [r for r in compared if r.get("coveredByTape")]
    within = [r for r in compared if r.get("withinOneCent")]
    inside = [r for r in compared if r.get("deskPriceInsideTapeRange")]
    no_tape = [r for r in rows if r.get("status") == "no_tape_in_window"]
    failed = [r for r in rows if r.get("status") == "tape_fetch_failed"]
    pct = lambda n: round(100 * n / len(compared), 2) if compared else None
    verdict = None
    if compared:
        median = statistics.median(diffs) if diffs else None
        verdict = (f"{len(compared)} paper fill(s) compared with the official tape: median |desk - tape| "
                   f"{median:.2f}c, {pct(len(within))}% had a real print within 1c of the desk's VWAP, "
                   f"{pct(len(covered))}% had at least as much tape volume as the desk filled, and the desk's "
                   f"price sat inside the window's real price range for {pct(len(inside))}% of fills. "
                   f"{'The desk is close to the tape.' if median is not None and median <= 1.0 else 'The desk is optimistic versus the tape: treat its returns as an upper bound.'}")
    return {"schemaVersion": 1, "generatedAt": generated_at, "compared": len(compared),
            "noTapeInWindow": len(no_tape), "tapeFetchFailed": len(failed), "rows": rows,
            "medianAbsCentsDiff": round(statistics.median(diffs), 4) if diffs else None,
            "meanAbsCentsDiff": round(statistics.fmean(diffs), 4) if diffs else None,
            "medianSignedCentsDiff": round(statistics.median(signed), 4) if signed else None,
            "withinOneCentPct": pct(len(within)), "tapeCoveredPct": pct(len(covered)),
            "insideTapeRangePct": pct(len(inside)),
            "byStrategy": {sid: {"compared": sum(1 for r in compared if r["strategyId"] == sid),
                                 "medianAbsCentsDiff": (round(statistics.median(
                                     [abs(r["centsDiff"]) for r in compared
                                      if r["strategyId"] == sid and r.get("centsDiff") is not None]), 4)
                                     if any(r["strategyId"] == sid and r.get("centsDiff") is not None for r in compared) else None)}
                           for sid in sorted({r["strategyId"] for r in compared})},
            "verdict": verdict,
            "method": "GET /markets/trades?ticker=&min_ts=&max_ts= (official, unauthenticated); desk fills from "
                      "forward/trades.jsonl. Every comparison stores the tape response hash and URL."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixtures", help="directory of recorded responses (offline replay)")
    parser.add_argument("--forward-dir", help="season forward directory (default: data/season-2026/forward)")
    parser.add_argument("--window", type=int, default=120, help="+- seconds around each fill")
    parser.add_argument("--limit", type=int, default=200, help="maximum fills to compare in one run")
    parser.add_argument("--kinds", default="fill", help="comma list of event kinds to compare (fill,exit)")
    args = parser.parse_args(argv)
    if not args.live and not args.fixtures:
        parser.error("choose --live or --fixtures DIR")
    forward_dir = args.forward_dir or DEFAULT_FORWARD_DIR
    if args.forward_dir:
        set_paths(forward_dir=args.forward_dir)
    if args.fixtures:
        client = FixtureClient(json.load(open(os.path.join(args.fixtures, "kalshi.json"))))
    else:
        client = KalshiClient()
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    events = [e for e in read_events(forward_dir) if e.get("kind") in kinds]
    events = events[-args.limit:]
    rows = [compare(event, client, args.window) for event in events]
    day = datetime.fromtimestamp(int(time.time()), tz=timezone.utc).strftime("%Y-%m-%d")
    out_dir = os.path.join(forward_dir, "execution")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{day}.jsonl"), "a") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    summary = summarize(rows, iso(int(time.time())))
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
