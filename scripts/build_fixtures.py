#!/usr/bin/env python3
"""Regenerate the offline fixture bundle from the canonical season-2026 files.

The bundle (`data/fixtures/kalshi.json` + `.gz` + `.sha256`) is a *generated* artifact used by
`--fixtures` offline replay (forward_desk.py / execution_realism.py / archive_history.py /
maker_model.py).  It is not source data: every body here is reassembled mechanically from the
canonical, hash-checked files in `data/season-2026/` (see MANIFEST.md for the endpoint table).

Honesty note: the earlier local bundle held 51 hand-assembled keys and is not recoverable in this
sandbox (it was never committed).  This builder reconstitutes the MANIFEST's thirteen endpoints
with bare-path keys so FixtureClient's fallback lookup serves them; it invents no prices and no
dates.

Usage:
  python3 scripts/build_fixtures.py            # writes data/fixtures/
  python3 scripts/build_fixtures.py --out DIR  # writes DIR/ (used by tests)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(ROOT, "data", "season-2026")


def _read_csv(name: str) -> list[dict]:
    rows: list[list[str]] = []
    with open(os.path.join(SEASON, name), newline="") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            rows.append(next(csv.reader(io.StringIO(line))))
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def _read_json(name: str):
    with open(os.path.join(SEASON, name)) as fh:
        return json.load(fh)


def _num(row: dict, key: str):
    value = row.get(key)
    if value in (None, ""):
        return None
    return float(value)


def candlesticks_body(rows: list[dict]) -> dict:
    out = []
    for row in rows:
        out.append({
            "end_period_ts": int(row["end_period_ts"]),
            "open_interest": _num(row, "open_interest"),
            "price_open": _num(row, "price_open"),
            "price_high": _num(row, "price_high"),
            "price_low": _num(row, "price_low"),
            "price_close": _num(row, "price_close"),
            "yes_bid_close": _num(row, "yes_bid_close"),
            "yes_ask_close": _num(row, "yes_ask_close"),
            "volume": _num(row, "volume"),
        })
    return {"candlesticks": out}


def orderbook_body(rows: list[dict]) -> dict:
    book: dict = {"yes": [], "no": []}
    for row in rows:
        side = row.get("side", "no")
        book.setdefault(side, []).append({
            "price": _num(row, "price"),
            "size": _num(row, "size_fp"),
        })
    return {"order_book": book}


def trades_body(rows: list[dict]) -> dict:
    return {"trades": [{
        "ticker": row.get("ticker"),
        "created_time": row.get("created_time"),
        "yes_price_dollars": row.get("yes_price_dollars"),
        "no_price_dollars": row.get("no_price_dollars"),
        "count": row.get("count") or row.get("volume_fp"),
    } for row in rows]}


def build() -> dict:
    markets = _read_json("market-records.json")["markets"]
    series = _read_json("series-records.json")["series"]
    cutoff = _read_json("cutoff.json")
    fixtures: dict = {}
    fixtures["historical/cutoff"] = cutoff
    fixtures["markets"] = {"markets": markets}
    fixtures["markets/tickers"] = {"markets": markets}
    for market in markets:
        fixtures[f"markets/{market['ticker']}"] = {"market": market}
    fixtures["series"] = {"series": series}
    for row in series:
        fixtures[f"series/{row['ticker']}"] = {"series": row}
    fixtures["series/KXCPI/markets/KXCPI-26AUG-T0.8/candlesticks"] = candlesticks_body(
        _read_csv("candles-KXCPI-26AUG-T0.8-daily.csv"))
    fixtures["series/KXFED/markets/KXFED-26SEP-T4.75/candlesticks"] = candlesticks_body(
        _read_csv("candles-KXFED-26SEP-T4.75-daily.csv"))
    fixtures["series/KXNFLGAME/markets/KXNFLGAME-26SEP17DETBUF-BUF/candlesticks"] = candlesticks_body(
        _read_csv("candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv"))
    fixtures["markets/KXBTC-26SEP2017-T90749.99/orderbook"] = orderbook_body(
        _read_csv("orderbook-KXBTC-26SEP2017-T90749.99.csv"))
    fixtures["markets/trades"] = trades_body(_read_csv("trade-tape-sample.csv"))
    fixtures["meta"] = {
        "source": "data/fixtures/kalshi.json",
        "cutoff": cutoff.get("response", {}).get("market_settled_ts"),
        "generated_by": "scripts/build_fixtures.py (mechanical reassembly from data/season-2026/)",
        "urls": [
            "https://external-api.kalshi.com/trade-api/v2/historical/cutoff",
            "https://external-api.kalshi.com/trade-api/v2/markets",
            "https://external-api.kalshi.com/trade-api/v2/markets/trades",
            "https://external-api.kalshi.com/trade-api/v2/series",
        ],
    }
    return fixtures


def write_bundle(out_dir: str, fixtures: dict | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    fixtures = fixtures if fixtures is not None else build()
    raw = json.dumps(fixtures, indent=1, sort_keys=True).encode()
    path = os.path.join(out_dir, "kalshi.json")
    with open(path, "wb") as fh:
        fh.write(raw)
    with open(path + ".gz", "wb") as fh:
        fh.write(gzip.compress(raw))
    digest = hashlib.sha256(raw).hexdigest()
    with open(path + ".sha256", "w") as fh:
        fh.write(f"{digest}  kalshi.json\n")
    return digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(ROOT, "data", "fixtures"),
                        help="output directory (default: data/fixtures)")
    args = parser.parse_args(argv)
    fixtures = build()
    digest = write_bundle(args.out, fixtures)
    keys = len([k for k in fixtures if k != "meta"])
    print(f"wrote {args.out}/kalshi.json (+ .gz + .sha256 {digest[:12]}...) with {keys} fixture keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
