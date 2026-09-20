#!/usr/bin/env python3
"""Enumerate Kalshi series by category (official GET /series) into a compact, reviewable catalog.

Outputs (all derived 1:1 from the API, no editorial fields):
  data/universe/series-catalog.json  - one compact row per series (ticker, title, category, tags,
                                       fee_type, fee_multiplier, exchange_index, volume_fp, sources)
  data/universe/series-index.json    - the desk's working index; tagged CEO series and KXFDA*
                                       series are inserted here so the forward desk can trade them
  data/universe/discovery-log.jsonl  - one row per run (categories, counts, response hashes)

Usage: python3 scripts/discover_universe.py [--categories "Companies,Health,..."]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from kalshi_client import KalshiClient, KalshiError  # noqa: E402
from forward_desk import (UNIVERSE_DIR, load_series_index, save_series_index, compact_series, read_json, write_json,  # noqa: E402
                          append_jsonl)
from paper_engine import iso  # noqa: E402

DEFAULT_CATEGORIES = ["Companies", "Health", "Financials", "Economics", "Crypto", "Commodities", "Climate and Weather",
                      "Sports", "Politics", "Science and Technology", "Entertainment", "World", "Elections"]
DESK_TAGS = {"CEOs"}
DESK_PREFIXES = ("KXFDA",)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES))
    args = parser.parse_args(argv)
    client = KalshiClient()
    catalog_path = os.path.join(UNIVERSE_DIR, "series-catalog.json")
    catalog = read_json(catalog_path, {"schemaVersion": 2, "series": {}})
    if catalog.get("schemaVersion") != 2:
        catalog = {"schemaVersion": 2, "series": {}}
    index = load_series_index()
    now = int(time.time())
    run = {"at": iso(now), "categories": {}, "inserted": []}
    for category in [c.strip() for c in args.categories.split(",") if c.strip()]:
        try:
            payload, raw, url = client.get("series", {"category": category, "include_volume": "true"})
        except KalshiError as error:
            run["categories"][category] = {"error": str(error)[:200]}
            continue
        rows = payload.get("series") or []
        run["categories"][category] = {"count": len(rows), "sha256": client.calls[-1]["sha256"], "url": url, "bytes": len(raw)}
        for raw_series in rows:
            row = compact_series(raw_series)
            # Catalog rows are deliberately compact (13.6k series): settlement-source names only,
            # no per-row timestamps; the full record is one GET /series/{ticker} away and the desk's
            # series-index.json keeps the full compact record for every series it trades.
            catalog["series"][row["ticker"]] = {
                "t": row["title"], "c": row["category"], "g": row.get("tags") or [], "f": row["fee_type"],
                "m": row["fee_multiplier"], "x": row["exchange_index"], "q": row["frequency"], "v": row["volume_fp"],
                "s": [src.get("name") for src in row.get("settlement_sources") or []],
            }
            tags = set(row.get("tags") or [])
            if tags & DESK_TAGS or row["ticker"].startswith(DESK_PREFIXES):
                entry = dict(row)
                entry.update({"status": 200, "fetchedAt": iso(now), "source": url})
                index["series"][row["ticker"]] = entry
                run["inserted"].append(row["ticker"])
    catalog["updatedAt"] = iso(now)
    catalog["count"] = len(catalog["series"])
    catalog["fields"] = {"t": "title", "c": "category", "g": "tags", "f": "fee_type", "m": "fee_multiplier", "x": "exchange_index",
                         "q": "frequency", "v": "volume_fp", "s": "settlement source names"}
    catalog["source"] = f"{client.base_url}/series?category=<category>&include_volume=true"
    write_json(catalog_path, catalog, compact=True)
    save_series_index(index)
    run["catalogCount"] = catalog["count"]
    run["apiCalls"] = len(client.calls)
    append_jsonl(os.path.join(UNIVERSE_DIR, "discovery-log.jsonl"), [run])
    print(json.dumps(run, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
