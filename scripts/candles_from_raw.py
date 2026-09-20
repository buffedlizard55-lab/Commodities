#!/usr/bin/env python3
"""Flatten raw Kalshi candlestick JSON responses into the committed CSV schema.

Usage:
  python3 scripts/candles_from_raw.py --list                 # list raw JSON files found
  python3 scripts/candles_from_raw.py --diff <raw.json> <csv>  # compare raw->CSV (values)

The CSV schema (see data/season-2026/candles-*.csv) is:
  end_period_ts,price_open,price_high,price_low,price_close,price_mean,price_previous,volume,open_interest,yes_bid_close,yes_ask_close

Raw schema (official API response):
  candlesticks: [{end_period_ts, volume_fp, open_interest_fp,
                  price:{open_dollars,high_dollars,low_dollars,close_dollars,mean_dollars,previous_dollars},
                  yes_bid:{open_dollars,high_dollars,low_dollars,close_dollars},
                  yes_ask:{open_dollars,high_dollars,low_dollars,close_dollars}}]

Field mapping:
  price_*        <- price.*_dollars
  volume         <- volume_fp
  open_interest  <- open_interest_fp
  yes_bid_close  <- yes_bid.close_dollars
  yes_ask_close  <- yes_ask.close_dollars
Missing values render as empty cells (exactly as in the committed CSVs).
"""
import argparse
import csv
import io
import json
import os
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "data", "season-2026")
RAW = os.path.join(BASE, "raw")

CSV_FIELDS = ["end_period_ts", "price_open", "price_high", "price_low", "price_close",
              "price_mean", "price_previous", "volume", "open_interest",
              "yes_bid_close", "yes_ask_close"]


def flatten(bars):
    rows = []
    for b in bars:
        p = b.get("price") or {}
        bid = b.get("yes_bid") or {}
        ask = b.get("yes_ask") or {}
        def g(d, k):
            v = d.get(k)
            return v if v not in (None, "") else None
        rows.append([
            str(b.get("end_period_ts", "")),
            g(p, "open_dollars") or "",
            g(p, "high_dollars") or "",
            g(p, "low_dollars") or "",
            g(p, "close_dollars") or "",
            g(p, "mean_dollars") or "",
            g(p, "previous_dollars") or "",
            b.get("volume_fp", "") or "",
            b.get("open_interest_fp", "") or "",
            g(bid, "close_dollars") or "",
            g(ask, "close_dollars") or "",
        ])
    return rows


def read_committed_csv(path):
    rows = []
    with open(path, newline="") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            rows.append(next(csv.reader(io.StringIO(line))))
    header, data = rows[0], rows[1:]
    if header != CSV_FIELDS:
        raise SystemExit(f"unexpected committed header in {path}: {header}")
    return data


def norm(v):
    v = (v or "").strip()
    if v == "":
        return ""
    try:
        return repr(float(v))
    except ValueError:
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--diff", nargs=2, metavar=("RAW", "CSV"))
    args = ap.parse_args()

    if args.list:
        for name in sorted(os.listdir(RAW)):
            if name.endswith(".json") and "candles" in name:
                print(name)
        return

    raw_path, csv_path = [os.path.join(BASE, p) if not os.path.isabs(p) else p for p in args.diff]
    with open(raw_path) as fh:
        raw = json.load(fh)
    rows = flatten(raw["candlesticks"])
    committed = read_committed_csv(csv_path)

    problems = []
    if len(rows) != len(committed):
        problems.append(f"row count: raw={len(rows)} committed={len(committed)}")
    for i, (r, c) in enumerate(zip(rows, committed)):
        for field, rv, cv in zip(CSV_FIELDS, r, c):
            if norm(rv) != norm(cv):
                problems.append(f"row {i} ({r[0]}) {field}: raw={rv!r} committed={cv!r}")
    if problems:
        print(f"MISMATCH {os.path.basename(raw_path)} vs {os.path.basename(csv_path)}")
        for p in problems[:40]:
            print("  " + p)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        sys.exit(1)
    print(f"OK {os.path.basename(raw_path)} == {os.path.basename(csv_path)} ({len(rows)} bars, all fields)")


if __name__ == "__main__":
    main()
