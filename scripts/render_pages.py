#!/usr/bin/env python3
"""Materialise the committed view files (per-strategy pages, today's roll-up, season index).

A live collector cycle writes these files as part of `scripts/forward_desk.py`, so on the runner they
are always current.  This script exists for the same job offline: it re-renders the *committed* ledger
(`state.json`, `trades.jsonl`, `equity/`, `intents/`, `cycles/`) into

    forward/strategies/<strategyId>.json   one page per persona (rule, evidence, fills, curve, analysis)
    forward/summary/<YYYY-MM-DD>.json      that day's roll-up (+ summary/today.json for the site)
    data/seasons.json                      season index the site's season switcher reads

It invents nothing: every number comes from a row already in the ledger, and if a file it needs is
missing it says so instead of writing a plausible-looking empty page.

Usage:
  python3 scripts/render_pages.py                 # render for the committed ledger's own season
  python3 scripts/render_pages.py --season 2026
  python3 scripts/render_pages.py --day 2026-09-20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import forward_desk as FD  # noqa: E402
from season import write_seasons_index  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", help="season to render (default: the newest data/season-*)")
    parser.add_argument("--day", help="UTC day to roll up (default: the day of the newest committed cycle)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    seasons = sorted(d.replace("season-", "") for d in os.listdir(FD.DATA_DIR)
                     if d.startswith("season-") and os.path.isdir(os.path.join(FD.DATA_DIR, d)))
    season = args.season or (seasons[-1] if seasons else None)
    if not season:
        print("no data/season-* directory found — run the collector first")
        return 1
    FD.FORWARD_DIR = os.path.join(FD.DATA_DIR, f"season-{season}", "forward")
    FD.SEASON = season
    state = FD.read_json(os.path.join(FD.FORWARD_DIR, "state.json"))
    if not state:
        print(f"no committed ledger at {FD.FORWARD_DIR} — nothing to render")
        return 1

    # the summary row of the newest committed cycle is what the roll-up is built from
    cycles = []
    cycles_dir = os.path.join(FD.FORWARD_DIR, "cycles")
    for name in sorted(os.listdir(cycles_dir)) if os.path.isdir(cycles_dir) else []:
        if name.endswith(".jsonl"):
            cycles.extend(FD.read_jsonl(os.path.join("cycles", name)))
    if not cycles:
        print("the committed ledger has no cycle rows — nothing to render")
        return 1
    cycles.sort(key=lambda row: str(row.get("at", "")))
    summary = cycles[-1]
    if args.day:
        chosen = [c for c in cycles if str(c.get("at", "")).startswith(args.day)]
        if not chosen:
            print(f"no committed cycle on {args.day}")
            return 1
        summary = chosen[-1]

    if args.dry_run:
        print(json.dumps({"season": season, "cycle": summary.get("cycle"), "at": summary.get("at"),
                          "accounts": len(state.get("accounts", {})), "cycles": len(cycles),
                          "wouldWrite": ["forward/strategies/*.json", "forward/summary/*.json", "data/seasons.json"]},
                         indent=1))
        return 0

    pages = FD.write_strategy_pages(state, summary)
    day = FD.write_daily_summary(state, summary)
    now = int(datetime.strptime(summary["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()) \
        if summary.get("at") else int(datetime.now(timezone.utc).timestamp())
    write_seasons_index(FD.DATA_DIR, now)
    print(json.dumps({"season": season, "cycle": summary.get("cycle"), "at": summary.get("at"),
                      "strategyPages": len(pages), "dailySummary": day.get("date"),
                      "accounts": day.get("accounts"), "fills": (day.get("activity") or {}).get("fills"),
                      "top": [r["username"] for r in (day.get("leaders") or [])[:3]],
                      "seasonsIndex": os.path.relpath(os.path.join(FD.DATA_DIR, "seasons.json"), FD.ROOT)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
