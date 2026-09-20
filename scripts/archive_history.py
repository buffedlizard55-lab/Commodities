#!/usr/bin/env python3
"""Archive a market's full official candlestick history in bounded chunks (KXFED gap, IRR/gap list).

The committed 2026 backtest uses a 90-day KXFED window; the full life of `KXFED-26SEP-T4.75`
(2025-08 -> 2026-09) was only partly captured ("chunks 2-13 not archived").  This script closes that
gap mechanically: it pages the official candlesticks endpoint over the market's whole life in small,
verifiable chunks and writes

    data/season-<year>/history/<ticker>-p<period>.csv           deduplicated bars, one row per period
    data/season-<year>/history/<ticker>-p<period>.chunks.jsonl  one row per request: URL, SHA-256,
                                                                bar count, window, retrievedAt

so every stored bar can be traced to the exact request that produced it.  Chunk size is a
conservative local choice (default 90 bars per request for daily bars); the endpoint documents
start_ts/end_ts/period_interval (1, 60, 1440) but no maximum page size, so nothing here assumes one.

`--diff` re-fetches and compares against the stored CSV without writing, which is how the exchange-side
inconsistency IRR-15b was found: any bar that differs is printed and the exit code is 1.

Usage:
  python3 scripts/archive_history.py --ticker KXFED-26SEP-T4.75 --period 1440 --live
  python3 scripts/archive_history.py --ticker KXFED-26SEP-T4.75 --fixtures DIR --diff
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from kalshi_client import KalshiClient, FixtureClient, KalshiError  # noqa: E402
from paper_engine import fnum, parse_ts, iso  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
HEADER = ["end_period_ts", "price_open", "price_high", "price_low", "price_close", "price_mean",
          "price_previous", "yes_bid_close", "yes_ask_close", "volume", "open_interest"]


def safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in text)


def windows(start_ts: int, end_ts: int, period: int, bars_per_chunk: int) -> list[tuple[int, int]]:
    """Non-overlapping [start, end] windows covering the market's life."""
    step = max(1, bars_per_chunk) * period * 60
    out = []
    cursor = start_ts
    while cursor < end_ts:
        out.append((cursor, min(end_ts, cursor + step)))
        cursor += step
    return out or [(start_ts, end_ts)]


def bar_row(bar: dict) -> list:
    price = bar.get("price") or {}
    yes_bid = bar.get("yes_bid") or {}
    yes_ask = bar.get("yes_ask") or {}
    return [bar.get("end_period_ts"), fnum(price.get("open_dollars")), fnum(price.get("high_dollars")),
            fnum(price.get("low_dollars")), fnum(price.get("close_dollars")), fnum(price.get("mean_dollars")),
            fnum(price.get("previous_dollars")), fnum(yes_bid.get("close_dollars")),
            fnum(yes_ask.get("close_dollars")), fnum(bar.get("volume_fp")), fnum(bar.get("open_interest_fp"))]


def collect(client: KalshiClient, series_ticker: str, ticker: str, start_ts: int, end_ts: int, period: int,
            bars_per_chunk: int, exchange_index=None) -> tuple[dict[int, list], list[dict]]:
    bars: dict[int, list] = {}
    log = []
    for chunk_start, chunk_end in windows(start_ts, end_ts, period, bars_per_chunk):
        try:
            payload, raw, url = client.candlesticks(series_ticker, ticker, chunk_start, chunk_end, period,
                                                    exchange_index=exchange_index)
        except KalshiError as error:
            log.append({"window": [chunk_start, chunk_end], "error": str(error)[:200], "url": None})
            continue
        rows = payload.get("candlesticks") or []
        for row in rows:
            ts = row.get("end_period_ts")
            if ts is not None:
                bars[int(ts)] = bar_row(row)
        log.append({"window": [chunk_start, chunk_end], "url": url, "bars": len(rows),
                    "sha256": __import__("hashlib").sha256(raw).hexdigest(), "retrievedAt": iso(int(time.time()))})
    return bars, log


def market_bounds(client: KalshiClient, ticker: str, exchange_index=None) -> tuple[int, int, dict]:
    """open_ts .. (settlement_ts or close_ts) from the official market record."""
    market, _raw, _url = client.market(ticker, exchange_index=exchange_index)
    start = parse_ts(market.get("open_time"))
    end = parse_ts(market.get("settlement_ts")) or parse_ts(market.get("expected_expiration_time")) \
        or parse_ts(market.get("close_time"))
    if start is None or end is None:
        raise KalshiError(f"{ticker}: market record has no open_time/close_time")
    return int(start), int(end) + 60, market


def write_csv(path: str, bars: dict[int, list]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        for ts in sorted(bars):
            writer.writerow(["" if v is None else v for v in bars[ts]])
    return len(bars)


def read_csv(path: str) -> dict[int, list]:
    out = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            def num(key):
                value = (row.get(key) or "").strip()
                return float(value) if value else None
            out[int(row["end_period_ts"])] = [int(row["end_period_ts"]), num("price_open"), num("price_high"),
                                              num("price_low"), num("price_close"), num("price_mean"),
                                              num("price_previous"), num("yes_bid_close"), num("yes_ask_close"),
                                              num("volume"), num("open_interest")]
    return out


def diff(stored: dict[int, list], fresh: dict[int, list]) -> list[dict]:
    changes = []
    for ts in sorted(set(stored) | set(fresh)):
        if ts not in stored:
            changes.append({"end_period_ts": ts, "change": "missing_in_stored", "fresh": fresh[ts]})
        elif ts not in fresh:
            changes.append({"end_period_ts": ts, "change": "missing_in_fresh", "stored": stored[ts]})
        elif stored[ts] != fresh[ts]:
            changes.append({"end_period_ts": ts, "change": "values_differ", "stored": stored[ts], "fresh": fresh[ts]})
    return changes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", default="KXFED-26SEP-T4.75")
    parser.add_argument("--series", help="series ticker (default: the first segment of the ticker)")
    parser.add_argument("--period", type=int, default=1440, choices=[1, 60, 1440])
    parser.add_argument("--bars-per-chunk", type=int, default=90)
    parser.add_argument("--start", help="ISO start (default: the market record's open_time)")
    parser.add_argument("--end", help="ISO end (default: settlement/expiration/close time)")
    parser.add_argument("--season", help="season directory to write into (default: newest data/season-*)")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixtures", help="directory with kalshi.json (offline replay)")
    parser.add_argument("--diff", action="store_true", help="compare with the stored CSV, do not write")
    parser.add_argument("--exchange-index", type=int)
    args = parser.parse_args(argv)
    if not args.live and not args.fixtures:
        parser.error("choose --live or --fixtures DIR")
    client = KalshiClient() if args.live else FixtureClient(json.load(open(os.path.join(args.fixtures, "kalshi.json"))))
    series_ticker = args.series or args.ticker.split("-")[0]
    start_ts = parse_ts(args.start) if args.start else None
    end_ts = parse_ts(args.end) if args.end else None
    market = {}
    if start_ts is None or end_ts is None:
        start_ts, end_ts, market = market_bounds(client, args.ticker, args.exchange_index)
        start_ts = start_ts if args.start is None else parse_ts(args.start)
        end_ts = end_ts if args.end is None else parse_ts(args.end)
    bars, log = collect(client, series_ticker, args.ticker, start_ts, end_ts, args.period,
                        args.bars_per_chunk, args.exchange_index)
    seasons = sorted(d.replace("season-", "") for d in os.listdir(DATA_DIR)
                     if d.startswith("season-") and os.path.isdir(os.path.join(DATA_DIR, d)))
    season = args.season or (seasons[-1] if seasons else "2026")
    history_dir = os.path.join(DATA_DIR, f"season-{season}", "history")
    csv_path = os.path.join(history_dir, f"{safe(args.ticker)}-p{args.period}.csv")
    if args.diff:
        if not os.path.exists(csv_path):
            print(json.dumps({"status": "no_stored_file", "path": os.path.relpath(csv_path, ROOT)}))
            return 1
        changes = diff(read_csv(csv_path), bars)
        print(json.dumps({"status": "clean" if not changes else "differences", "barsStored": len(read_csv(csv_path)),
                          "barsFetched": len(bars), "chunks": len(log), "changes": changes[:20]}, indent=1))
        return 1 if changes else 0
    written = write_csv(csv_path, bars)
    with open(csv_path + ".chunks.jsonl", "a") as fh:
        for row in log:
            fh.write(json.dumps({"ticker": args.ticker, "series": series_ticker, "period": args.period, **row},
                                separators=(",", ":"), sort_keys=True) + "\n")
    print(json.dumps({"ticker": args.ticker, "series": series_ticker, "period": args.period,
                      "window": [iso(start_ts), iso(end_ts)], "chunks": len(log),
                      "barsWritten": written, "errors": [c for c in log if c.get("error")],
                      "written": os.path.relpath(csv_path, ROOT),
                      "result": market.get("result"), "settlementTs": market.get("settlement_ts"),
                      "apiCalls": len(client.calls)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
