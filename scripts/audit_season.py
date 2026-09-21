#!/usr/bin/env python3
"""Season health audit - schedule adherence, ledger coverage, compaction, signal coverage,
trade-tape realism and (with --live) a re-read of settled markets from the official API.

This is the standing audit the README's "let the season run and audit it" item asked for:
GitHub's cron is best-effort, adapters can quietly return nothing, and settlements are the one
number every strategy score rests on.  The audit makes all three observable:

  schedule   compares forward/cycles/*.jsonl timestamps against the workflow's cron (7,37 UTC):
             executed, on-time, late (>15 min), extra (manual/dispatch or push-triggered), missed.
  ledger     every fill has an open position or a recorded exit/settlement; the last equity row of
             each strategy matches state.json's lastEquity; per-strategy intent statuses.
  storage    COMPRESSED.json: files compacted, bytes before/after, and a full re-verify pass.
  signals    per-source coverage totals from the cycle log (NWS cities, ESPN, openFDA) and the
             cycle's recorded signal-error count.
  tape       forward/execution/summary.json headline numbers (filled by the runner).
  live       --live re-reads GET /markets/{ticker} for the most recent settled positions and
             requires result AND settlement_ts to match the ledger; mismatches are reported as
             FAIL and flip the exit code - they are the irregularity, never re-simulated away.
             A network failure records `unreachable` and never fakes a pass.

Writes data/season-<year>/forward/audit/<YYYY-MM-DD>.json, forward/audit/latest.json, and the
append-only audit-history.jsonl plus bounded browser view history.json.

Usage:
  python3 scripts/audit_season.py            # offline parts only (safe anywhere)
  python3 scripts/audit_season.py --live     # + the official settlement re-read (runner)
  python3 scripts/audit_season.py --samples 12 --season 2026
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
DATA_DIR = os.path.join(ROOT, "data")
sys.path.insert(0, HERE)

CRON_MINUTES = (7, 37)          # schedule in .github/workflows/forward-desk.yml
LATE_AFTER_MIN = 15             # a cycle starting >15 min after its slot counts as late (GitHub delays)
MATCH_WINDOW_MIN = 65           # a slot may be filled by a cycle up to this far after it


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def season_dir_for(season: str | None) -> tuple[str, str]:
    if not season:
        index = os.path.join(DATA_DIR, "seasons.json")
        if os.path.exists(index):
            with open(index) as fh:
                season = json.load(fh).get("activeSeason")
    if not season:
        seasons = sorted(d.replace("season-", "") for d in os.listdir(DATA_DIR)
                         if d.startswith("season-") and os.path.isdir(os.path.join(DATA_DIR, d)))
        season = seasons[-1] if seasons else "2026"
    return season, os.path.join(DATA_DIR, f"season-{season}")


# -------------------------------------------------------------------------------------- schedule
def audit_schedule(cycles: list[dict]) -> dict:
    """Match cycle timestamps to the cron slots between the first and last cycle."""
    out = {"cron": f"{CRON_MINUTES[0]},{CRON_MINUTES[1]} * * * * (UTC)", "cycles": len(cycles)}
    if not cycles:
        return out
    stamps = sorted(parse_ts(c["at"]) for c in cycles if c.get("at"))
    if not stamps:
        return out
    first, last = stamps[0], stamps[-1]
    slots = []
    cursor = first.replace(minute=0, second=0, microsecond=0)
    while cursor <= last + dt.timedelta(minutes=59):
        for minute in CRON_MINUTES:
            slot = cursor.replace(minute=minute)
            if slot >= first - dt.timedelta(minutes=2) and slot <= last + dt.timedelta(minutes=2):
                slots.append(slot)
        cursor += dt.timedelta(hours=1)
    remaining = list(stamps)
    on_time = late = 0
    missed: list[str] = []
    delays = []
    for slot in slots:
        limit = slot + dt.timedelta(minutes=MATCH_WINDOW_MIN)
        match = next((s for s in remaining if slot - dt.timedelta(minutes=2) <= s <= limit), None)
        if match is None:
            missed.append(slot.strftime("%Y-%m-%dT%H:%MZ"))
            continue
        remaining.remove(match)
        delay_min = (match - slot).total_seconds() / 60.0
        delays.append(round(delay_min, 1))
        if delay_min <= LATE_AFTER_MIN:
            on_time += 1
        else:
            late += 1
    gaps = [round((b - a).total_seconds() / 3600.0, 2) for a, b in zip(stamps, stamps[1:])]
    out.update({
        "firstCycle": first.strftime("%Y-%m-%dT%H:%M:%SZ"), "lastCycle": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expectedSlots": len(slots), "executed": len(cycles), "matchedSlots": on_time + late,
        "onTime": on_time, "late": late, "extraManual": len(remaining),
        "missed": len(missed), "missedSlotsSample": missed[:30],
        "maxGapHours": max(gaps) if gaps else 0.0,
        "medianDelayMin": sorted(delays)[len(delays) // 2] if delays else None,
        "note": "GitHub's schedule trigger is best-effort: delayed and missed slots are expected "
                "behaviour of the platform, recorded here rather than smoothed away.",
    })
    return out


# ----------------------------------------------------------------------------------------- ledger
def audit_ledger(season_dir: str, cycles: list[dict]) -> dict:
    forward = os.path.join(season_dir, "forward")
    events = read_jsonl(os.path.join(forward, "trades.jsonl"))
    state_path = os.path.join(forward, "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"accounts": {}}
    accounts = state.get("accounts", {})
    open_ids = {p["id"] for a in accounts.values() for p in a.get("positions", [])}
    settled: set[str] = set()
    by_kind: dict[str, int] = {}
    for event in events:
        kind = event.get("kind")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if kind in ("exit", "settlement"):
            settled.add(event.get("positionId"))
    orphan_fills = [e.get("positionId") for e in events
                    if e.get("kind") == "fill" and e.get("positionId") not in open_ids and e.get("positionId") not in settled]
    evidence_bound = sum(1 for e in events if (e.get("evidence") or {}).get("sha256"))

    # intents per strategy status
    intents = []
    intents_dir = os.path.join(forward, "intents")
    if os.path.isdir(intents_dir):
        for name in sorted(os.listdir(intents_dir)):
            if name.endswith(".jsonl"):
                intents.extend(read_jsonl(os.path.join(intents_dir, name)))
    intent_stats: dict[str, dict[str, int]] = {}
    for row in intents:
        bucket = intent_stats.setdefault(row.get("strategyId", "?"), {})
        bucket[row.get("status", "?")] = bucket.get(row.get("status", "?"), 0) + 1

    # the last equity row per strategy must equal state.json's lastEquity
    equity_mismatches = []
    last_equity: dict[str, float] = {}
    equity_dir = os.path.join(forward, "equity")
    if os.path.isdir(equity_dir):
        rows = []
        for name in sorted(os.listdir(equity_dir)):
            if name.endswith(".csv"):
                with open(os.path.join(equity_dir, name), newline="") as fh:
                    rows.extend(csv.DictReader(fh))
        for row in rows:
            last_equity[row["strategyId"]] = float(row["equity"])
        for sid, account in accounts.items():
            if sid in last_equity and account.get("lastEquity") is not None \
                    and abs(last_equity[sid] - account["lastEquity"]) > 0.011:
                equity_mismatches.append({"strategyId": sid, "equityCsv": last_equity[sid],
                                          "stateLastEquity": account["lastEquity"]})
    return {
        "events": len(events), "eventKinds": by_kind, "fillsWithoutExitOrPosition": len(orphan_fills),
        "orphanFillSample": orphan_fills[:10], "evidenceBound": evidence_bound,
        "eventsTotal": len(events), "openPositions": len(open_ids),
        "settledOrExited": len(settled), "intentStatusByStrategy": intent_stats,
        "equityCsvVsStateMismatches": equity_mismatches,
        "cyclesWithApiErrors": sum(1 for c in cycles if c.get("apiErrors")),
        "cycleErrorRows": sum(1 for c in cycles if c.get("errorCount")),
    }


# --------------------------------------------------------------------------------------- storage
def audit_storage(forward_dir: str) -> dict:
    from compact_storage import check_index, load_index
    index = load_index(forward_dir)
    files = index.get("files", {})
    passed, failures = check_index(forward_dir)
    return {
        "compactedFiles": len(files),
        "bytesBefore": sum(int(entry.get("bytes", 0)) for entry in files.values()),
        "bytesAfter": sum(int(entry.get("gzBytes", 0)) for entry in files.values()),
        "linesPreserved": sum(int(entry.get("lines", 0)) for entry in files.values()),
        "hashVerifyPass": passed, "hashVerifyFailures": failures,
        "indexUpdatedAt": index.get("updatedAt"),
        "note": "evidence/ and quotes/ captures older than the compaction horizon are gzipped with their "
                "SHA-256 kept (IRR-24); append-only ledgers are never compressed.",
    }


# --------------------------------------------------------------------------------------- signals
def audit_signals(cycles: list[dict]) -> dict:
    def total(key):
        return sum(int(c.get(key) or 0) for c in cycles)
    latest = cycles[-1] if cycles else {}
    return {
        "totals": {"nwsCentralPark": total("nwsCaptured") and len(cycles),
                   "nwsCityForecasts": total("nwsCityForecasts"), "espnSignals": total("espnSignals"),
                   "espnLiveSignals": total("espnLiveSignals"), "fdaSignals": total("fdaSignals"),
                   "fdaNoRecord": total("fdaNoRecord"), "signalErrors": total("signalErrorCount"),
                   "candlesArchived": total("candlesArchived")},
        "latestCycle": latest.get("cycle"),
        "latestSignalErrors": latest.get("signalErrors", []),
        "note": "a source that answers nothing shows 0 here with its error lines below it - that is the "
                "adapter abstaining, never a defaulted value (the same fail-closed rule as the desk).",
    }


# ------------------------------------------------------------------------------------------ tape
def audit_tape(season_dir: str) -> dict:
    path = os.path.join(season_dir, "forward", "execution", "summary.json")
    if not os.path.exists(path):
        return {"available": False, "note": "the runner has not written forward/execution/summary.json yet"}
    with open(path) as fh:
        summary = json.load(fh)
    return {"available": True, "generatedAt": summary.get("generatedAt"), "compared": summary.get("compared"),
            "medianAbsCentsDiff": summary.get("medianAbsCentsDiff"), "meanAbsCentsDiff": summary.get("meanAbsCentsDiff"),
            "withinOneCentPct": summary.get("withinOneCentPct"), "insideTapeRangePct": summary.get("insideTapeRangePct"),
            "tapeCoveredPct": summary.get("tapeCoveredPct"), "noTapeInWindow": summary.get("noTapeInWindow"),
            "tapeFetchFailed": summary.get("tapeFetchFailed"), "verdict": summary.get("verdict"),
            "byStrategy": summary.get("byStrategy", {}), "method": summary.get("method")}


# ------------------------------------------------------------------------------ audit history
AUDIT_HISTORY_JSONL = "audit-history.jsonl"
AUDIT_HISTORY_JSON = "history.json"


def audit_history_row(report: dict, report_sha256: str | None = None) -> dict:
    """Return the small, append-only row used for the site's health sparkline.

    The full audit remains in the dated JSON and latest.json files.  This row deliberately contains
    only counters copied from that report, plus a hash of the full report, so a trend view cannot
    silently become a second source of truth.
    """
    schedule = report.get("schedule") or {}
    expected = int(schedule.get("expectedSlots") or 0)
    matched = int(schedule.get("matchedSlots") or 0)
    return {
        "schemaVersion": 1,
        "generatedAt": report.get("generatedAt"),
        "season": report.get("season"),
        "status": report.get("status"),
        "expectedSlots": expected,
        "matchedSlots": matched,
        "executed": int(schedule.get("executed") or 0),
        "onTime": int(schedule.get("onTime") or 0),
        "late": int(schedule.get("late") or 0),
        "missed": int(schedule.get("missed") or 0),
        "extraManual": int(schedule.get("extraManual") or 0),
        "slotRatePct": round(100 * matched / expected, 2) if expected else None,
        "maxGapHours": schedule.get("maxGapHours"),
        "events": int((report.get("ledger") or {}).get("events") or 0),
        "signalErrors": int(((report.get("signals") or {}).get("totals") or {}).get("signalErrors") or 0),
        "compactedFiles": int((report.get("storage") or {}).get("compactedFiles") or 0),
        "tapeCompared": int((report.get("tape") or {}).get("compared") or 0),
        "reportSha256": report_sha256,
    }


def append_audit_history(audit_dir: str, report: dict, payload: str | None = None) -> dict:
    """Append one auditable trend row and regenerate the compact JSON view.

    Both files live under forward/audit.  The JSONL is the append-only record; history.json is a
    bounded browser view.  Existing rows are preserved byte-for-byte and duplicate report hashes
    are ignored, which keeps retries from growing storage without evidence of a new report.
    """
    os.makedirs(audit_dir, exist_ok=True)
    payload = payload if payload is not None else json.dumps(report, sort_keys=True, separators=(",", ":"))
    report_hash = sha256_bytes(payload.encode())
    row = audit_history_row(report, report_hash)
    path = os.path.join(audit_dir, AUDIT_HISTORY_JSONL)
    rows = read_jsonl(path)
    if not any(existing.get("reportSha256") == report_hash for existing in rows):
        with open(path, "a") as fh:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        rows.append(row)
    # Keep the browser artifact small while retaining the append-only source in JSONL.
    view = rows[-365:]
    with open(os.path.join(audit_dir, AUDIT_HISTORY_JSON), "w") as fh:
        json.dump(view, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return row


# ----------------------------------------------------------------------------------- live read
def audit_live_settlements(forward_dir: str, samples: int) -> dict:
    """Re-read the settled markets from the official API and require the ledger's result+ts to match."""
    out = {"available": False, "attempts": 0, "matches": 0, "mismatches": [], "unreachable": None,
           "samples": [], "method": "GET /markets/{ticker} (official, unauthenticated) vs forward/trades.jsonl "
                                    "settlement events; both sides are the exchange's own records"}
    events = read_jsonl(os.path.join(forward_dir, "trades.jsonl"))
    settled: dict[str, dict] = {}
    for event in events:
        if event.get("kind") == "settlement" and event.get("ticker"):
            settled[event["ticker"]] = event  # last one wins per ticker
    if not settled:
        out["note"] = "no settled positions in the ledger yet"
        return out
    picks = [settled[t] for t in sorted(settled)][-samples:]
    # market reads must target the series' exchange shard (verified per series in series-index.json)
    shard_by_series: dict[str, int] = {}
    index_path = os.path.join(DATA_DIR, "universe", "series-index.json")
    if os.path.exists(index_path):
        with open(index_path) as fh:
            for ticker, info in (json.load(fh).get("series") or {}).items():
                if info.get("exchange_index"):
                    shard_by_series[ticker] = int(info["exchange_index"])
    try:
        from kalshi_client import KalshiClient, KalshiError
        client = KalshiClient(pause=0.15)
    except Exception as error:  # pragma: no cover - import guard
        out["unreachable"] = f"client import failed: {error}"
        return out
    out["available"] = True
    for event in picks:
        ticker = event["ticker"]
        record = {"ticker": ticker, "strategyId": event.get("strategyId"), "positionId": event.get("positionId"),
                  "ledgerResult": event.get("result"), "ledgerSettlementTs": event.get("exitAt")}
        try:
            payload, raw, url = client.market(ticker, exchange_index=shard_by_series.get(event.get("series") or ""))
            market = payload.get("market") or payload
            record.update({"url": url, "responseSha256": sha256_bytes(raw), "apiResult": market.get("result"),
                           "apiSettlementTs": market.get("settlement_ts"), "apiStatus": market.get("status")})
        except (KalshiError, Exception) as error:  # network or API error: record, never assume
            record["status"] = "unreachable"
            record["error"] = str(error)[:200]
            out["samples"].append(record)
            out["attempts"] += 1
            continue
        result_ok = (record["apiResult"] == record["ledgerResult"]) or \
            str(record["ledgerResult"]).startswith("value:")  # value-scalar settlement compares ts only
        ts_ok = record["apiSettlementTs"] == record["ledgerSettlementTs"]
        record["resultMatches"] = bool(result_ok)
        record["settlementMatches"] = bool(ts_ok)
        out["attempts"] += 1
        if result_ok and ts_ok:
            out["matches"] += 1
        else:
            out["mismatches"].append(record)
        out["samples"].append(record)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", help="season year (default: data/seasons.json activeSeason)")
    parser.add_argument("--live", action="store_true", help="re-read settled markets from the official API")
    parser.add_argument("--samples", type=int, default=8, help="settled markets to re-read with --live")
    parser.add_argument("--out", help="also write the audit JSON here (outside the season tree)")
    args = parser.parse_args(argv)

    season, season_dir = season_dir_for(args.season)
    forward = os.path.join(season_dir, "forward")
    cycles = []
    cycles_dir = os.path.join(forward, "cycles")
    for name in sorted(os.listdir(cycles_dir)) if os.path.isdir(cycles_dir) else []:
        if name.endswith(".jsonl"):
            cycles.extend(read_jsonl(os.path.join(cycles_dir, name)))
    cycles.sort(key=lambda c: c.get("at") or "")

    report = {"schemaVersion": 1, "season": season, "generatedAt": now_iso(),
              "schedule": audit_schedule(cycles), "ledger": audit_ledger(season_dir, cycles),
              "storage": audit_storage(forward), "signals": audit_signals(cycles), "tape": audit_tape(season_dir)}
    if args.live:
        report["live"] = audit_live_settlements(forward, args.samples)
    else:
        report["live"] = {"available": False, "note": "offline run: the settlement re-read only runs with --live "
                                                      "on a machine with network egress (the GitHub runner does it each cycle)"}

    fail = bool(report["ledger"]["fillsWithoutExitOrPosition"]) or bool(report["ledger"]["equityCsvVsStateMismatches"]) \
        or bool(report["storage"]["hashVerifyFailures"]) or bool((report.get("live") or {}).get("mismatches"))
    report["status"] = "FAIL" if fail else "PASS"

    audit_dir = os.path.join(forward, "audit")
    os.makedirs(audit_dir, exist_ok=True)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    payload = json.dumps(report, indent=1, sort_keys=True) + "\n"
    for path in (os.path.join(audit_dir, f"{day}.json"), os.path.join(audit_dir, "latest.json"), args.out):
        if path:
            with open(path, "w") as fh:
                fh.write(payload)
    history_row = append_audit_history(audit_dir, report, payload)
    sched = report["schedule"]
    print(f"audit[{season}] {report['status']}: schedule {sched.get('executed', 0)}/{sched.get('expectedSlots', 0)} slots "
          f"(on-time {sched.get('onTime', 0)}, late {sched.get('late', 0)}, missed {sched.get('missed', 0)}, "
          f"extra {sched.get('extraManual', 0)}); ledger {report['ledger']['events']} events, "
          f"{report['ledger']['openPositions']} open; storage {report['storage']['compactedFiles']} compressed, "
          f"verify {report['storage']['hashVerifyPass']} ok/{len(report['storage']['hashVerifyFailures'])} bad; "
          f"signals {report['signals']['totals']}; tape compared {report['tape'].get('compared', 0)}")
    live = report.get("live") or {}
    if live.get("available"):
        print(f"live settlement re-read: {live.get('matches')}/{live.get('attempts')} match "
              f"({len(live.get('mismatches', []))} mismatch(es))")
    else:
        print(f"live settlement re-read: skipped ({live.get('note') or live.get('unreachable')})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
