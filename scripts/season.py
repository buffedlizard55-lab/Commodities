#!/usr/bin/env python3
"""Season lifecycle for the paper-trading competition.

A season is one calendar year (UTC).  Every season owns its own directory
`data/season-<year>/` with an independent `forward/` ledger, and a season is never rewritten once
the next one starts: 2026 stays frozen and readable while 2027 counts up from $10,000 again.

Nothing here invents a result.  It only decides *which* directory the desk writes to and keeps
`data/seasons.json` (the site's season switcher) in sync with what is actually on disk.

Used by scripts/forward_desk.py (writes) and read by the site (data/seasons.json).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
SEASON_PREFIX = "season-"
STARTING_CASH = 10_000.0


def utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def season_for(now_ts: int) -> str:
    """The season a wall-clock instant belongs to: the UTC calendar year, as a string."""
    return str(utc(int(now_ts)).year)


def season_dir(root: str, season: str) -> str:
    return os.path.join(root, f"{SEASON_PREFIX}{season}")


def existing_seasons(root: str) -> list[str]:
    if not os.path.isdir(root):
        return []
    found = []
    for name in sorted(os.listdir(root)):
        match = re.fullmatch(rf"{SEASON_PREFIX}(\d{{4}})", name)
        if match and os.path.isdir(os.path.join(root, name)):
            found.append(match.group(1))
    return sorted(found)


def latest_season(root: str) -> str | None:
    seasons = existing_seasons(root)
    return seasons[-1] if seasons else None


def ensure_season(root: str, season: str, now_ts: int) -> tuple[str, bool]:
    """Create data/season-<year>/forward/ (and an empty state) if needed.

    Returns (forward_dir, created).  Creating a season never touches an older season's files.
    """
    base = season_dir(root, season)
    forward = os.path.join(base, "forward")
    created = not os.path.isdir(forward)
    for sub in ("", "forward", os.path.join("forward", "evidence"), os.path.join("forward", "intents"),
                os.path.join("forward", "cycles"), os.path.join("forward", "equity"),
                os.path.join("forward", "quotes"), os.path.join("forward", "candles"),
                os.path.join("forward", "signals"), os.path.join("forward", "strategies"),
                os.path.join("forward", "summary"), os.path.join("forward", "execution")):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    state_path = os.path.join(forward, "state.json")
    if not os.path.exists(state_path):
        state = {"schemaVersion": 1, "season": season, "startingCash": STARTING_CASH,
                 "createdAt": utc(now_ts).strftime("%Y-%m-%dT%H:%M:%SZ"), "cycles": 0,
                 "lastCycle": None, "accounts": {},
                 "note": "Season memory for the automated paper-trading desk. Written only by "
                         "scripts/forward_desk.py from official public API responses."}
        with open(state_path, "w") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
            fh.write("\n")
        created = True
    return forward, created


def resolve_forward_dir(root: str, now_ts: int) -> tuple[str, str, bool]:
    """(forward_dir, season, rolled_over) for the desk's current cycle.

    On the first cycle of a new UTC year the desk rolls over: a fresh season directory is created
    and the previous season is left exactly as it was (frozen).  Before any season exists, the
    current year's directory is created.
    """
    season = season_for(now_ts)
    forward, created = ensure_season(root, season, now_ts)
    return forward, season, created


def season_summary(root: str, season: str) -> dict:
    """Compact, file-derived description of one season (no invented numbers)."""
    base = season_dir(root, season)
    forward = os.path.join(base, "forward")
    out = {"season": season, "dir": f"data/{os.path.basename(base)}", "forwardDir": f"data/{os.path.basename(base)}/forward",
           "exists": os.path.isdir(base), "hasLedger": os.path.exists(os.path.join(forward, "state.json"))}
    state_path = os.path.join(forward, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path) as fh:
                state = json.load(fh)
        except ValueError:
            state = {}
        last = state.get("lastCycle") or {}
        out.update({
            "createdAt": state.get("createdAt"), "startingCash": state.get("startingCash", STARTING_CASH),
            "cycles": state.get("cycles", 0), "lastCycleAt": last.get("at"),
            "participants": len(state.get("accounts") or {}),
            "openPositions": sum(len(a.get("positions") or []) for a in (state.get("accounts") or {}).values()),
            "fills": sum(a.get("fills", 0) for a in (state.get("accounts") or {}).values()),
            "settlements": sum(a.get("settlements", 0) for a in (state.get("accounts") or {}).values()),
        })
    out["backtest"] = os.path.exists(os.path.join(base, "competition.json"))
    out["archiveBacktest"] = os.path.exists(os.path.join(base, "backtest-archive", "competition.json"))
    return out


def write_seasons_index(root: str, now_ts: int) -> dict:
    """Rewrite data/seasons.json: every season on disk, which one is active, which are frozen."""
    active = season_for(now_ts)
    seasons = sorted(set(existing_seasons(root)) | {active})
    rows = []
    for season in seasons:
        row = season_summary(root, season)
        row["active"] = season == active
        row["frozen"] = season < active
        rows.append(row)
    payload = {"schemaVersion": 1, "generatedAt": utc(now_ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "activeSeason": active, "rolloverRule": "A season is one UTC calendar year. The first desk cycle of a new "
                                                      "year creates data/season-<year>/ with fresh $10,000 accounts; earlier "
                                                      "seasons are never rewritten.",
               "seasons": rows}
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "seasons.json"), "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return payload


if __name__ == "__main__":  # tiny CLI: python3 scripts/season.py [epoch]
    import sys
    import time
    now = int(sys.argv[1]) if len(sys.argv) > 1 else int(time.time())
    print(json.dumps(write_seasons_index(DATA_DIR, now), indent=1))
