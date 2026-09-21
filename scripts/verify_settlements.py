#!/usr/bin/env python3
"""Full settlement backfill: re-read EVERY settled market in the ledger from the official API.

The per-cycle season audit (scripts/audit_season.py --live) spot-checks only the most recent
settled positions (--samples, default 8).  This script is the exhaustive version recommended in
the README ("a full backfill over every settled market of the season ... worth one run per
month"): every unique settled ticker in every season's forward/trades.jsonl is re-read from
GET /markets/{ticker} and its official result + settlement_ts are compared with the ledger's
settlement event.

Design rules (identical to the rest of the repository):
  * Official endpoints only (KalshiClient, unauthenticated GET); each response is recorded with
    its URL and SHA-256 so a reviewer can replay the comparison.
  * The ledger is NEVER adjusted here: a mismatch is a *finding* for human review, and the exit
    code flags it (1) so a workflow step can surface it.  Nothing is re-simulated or rewritten.
  * A market that cannot be fetched is "unreachable" - never counted as a match or a mismatch.
  * value-scalar settlements (ledger result "value:...") compare settlement_ts only, the same
    convention audit_season.py uses for its sampled re-read.
  * Mismatches do NOT fail scripts/verify_data.py permanently: the verifier reports them as a
    warning while the finding is fresh.  Rationale: a monthly artifact that blocked the
    twice-hourly ledger commit would stall the whole experiment until a human arrived; the
    finding is instead displayed on the site's season-health panel and stays committed in
    forward/audit/settlement-backfill.json for review.

Usage:
  python3 scripts/verify_settlements.py --live                  # every settled market, every season
  python3 scripts/verify_settlements.py --live --season 2026    # one season only
  python3 scripts/verify_settlements.py --live --limit 25       # first 25 tickers (sorted) per season
  python3 scripts/verify_settlements.py                         # offline: report what would run, change nothing

Outputs (per season that has settlements):
  forward/audit/settlement-backfill.json          latest full report (rewritten each run)
  forward/audit/settlement-backfill-history.jsonl one append-only row per run (hash of the report)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

DATA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

METHOD = ("GET /markets/{ticker} (official, unauthenticated, per-series exchange shard) compared "
          "with the settlement events in forward/trades.jsonl; both sides are the exchange's own "
          "records. The ledger is never adjusted by this script.")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_jsonl(path: str) -> list[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def season_dirs() -> list[tuple[str, str]]:
    """(season year, season dir) for every committed season, oldest first."""
    out = []
    for name in sorted(os.listdir(DATA_ROOT)):
        if name.startswith("season-") and os.path.isdir(os.path.join(DATA_ROOT, name)):
            out.append((name.replace("season-", "", 1), os.path.join(DATA_ROOT, name)))
    return out


def shard_by_series() -> dict[str, int]:
    """Series -> exchange shard, from the committed universe index (market reads must target the
    series' shard; verified per series in data/universe/series-index.json)."""
    out: dict[str, int] = {}
    path = os.path.join(DATA_ROOT, "universe", "series-index.json")
    if os.path.exists(path):
        with open(path) as fh:
            for ticker, info in (json.load(fh).get("series") or {}).items():
                if info.get("exchange_index"):
                    out[ticker] = int(info["exchange_index"])
    return out


def collect_settled(season_dir: str) -> dict[str, dict]:
    """Unique settled tickers for one season: last settlement event wins, every position listed."""
    settled: dict[str, dict] = {}
    for event in read_jsonl(os.path.join(season_dir, "forward", "trades.jsonl")):
        if event.get("kind") != "settlement" or not event.get("ticker"):
            continue
        ticker = event["ticker"]
        row = settled.setdefault(ticker, {
            "ticker": ticker, "series": event.get("series"), "ledgerResult": event.get("result"),
            "ledgerSettlementTs": event.get("exitAt"), "positionIds": [], "strategyIds": [],
            "settlementEvents": 0,
        })
        row["settlementEvents"] += 1
        row["ledgerResult"] = event.get("result")          # last event wins (matches audit_season)
        row["ledgerSettlementTs"] = event.get("exitAt")
        if event.get("positionId") and event["positionId"] not in row["positionIds"]:
            row["positionIds"].append(event["positionId"])
        if event.get("strategyId") and event["strategyId"] not in row["strategyIds"]:
            row["strategyIds"].append(event["strategyId"])
    return settled


def new_report(season: str, mode: str, settled_count: int) -> dict:
    return {"schemaVersion": 1, "season": season, "generatedAt": now_iso(), "mode": mode,
            "settledTickers": settled_count, "attempted": 0, "matches": 0,
            "mismatches": [], "unreachable": [], "records": [], "method": METHOD,
            "note": "The ledger is never adjusted here; a mismatch stays a finding for human review."}


def backfill_season(client, season: str, season_dir: str, limit: int = 0) -> dict:
    """Re-read every settled market of one season. `client` is a KalshiClient (or a test stub with
    the same .market(ticker, exchange_index=...) signature)."""
    settled = collect_settled(season_dir)
    report = new_report(season, "live", len(settled))
    if not settled:
        report["note"] = "no settled positions in this season's ledger yet"
        return report
    shards = shard_by_series()
    tickers = sorted(settled)
    if limit and limit > 0:
        tickers = tickers[:limit]
    for ticker in tickers:
        row = settled[ticker]
        record = {"ticker": ticker, "series": row["series"], "positionIds": row["positionIds"],
                  "strategyIds": row["strategyIds"], "settlementEvents": row["settlementEvents"],
                  "ledgerResult": row["ledgerResult"], "ledgerSettlementTs": row["ledgerSettlementTs"]}
        try:
            payload, raw, url = client.market(ticker, exchange_index=shards.get(row["series"] or ""))
            market = payload.get("market") or payload
            record.update({"url": url, "responseSha256": sha256_bytes(raw),
                           "apiResult": market.get("result"), "apiSettlementTs": market.get("settlement_ts"),
                           "apiStatus": market.get("status")})
        except Exception as error:  # network or API error: record, never assume
            record.update({"status": "unreachable", "error": str(error)[:200]})
            report["unreachable"].append(record)
            report["attempted"] += 1
            report["records"].append(record)
            continue
        # value-scalar settlements (result "value:...") have no yes/no result to compare; ts only.
        result_ok = (record["apiResult"] == record["ledgerResult"]) or \
            str(record["ledgerResult"]).startswith("value:")
        ts_ok = record["apiSettlementTs"] == record["ledgerSettlementTs"]
        record["resultMatches"] = bool(result_ok)
        record["settlementMatches"] = bool(ts_ok)
        report["attempted"] += 1
        if result_ok and ts_ok:
            report["matches"] += 1
        else:
            report["mismatches"].append(record)
        report["records"].append(record)
    return report


def write_report(season_dir: str, report: dict) -> str:
    audit_dir = os.path.join(season_dir, "forward", "audit")
    os.makedirs(audit_dir, exist_ok=True)
    payload = json.dumps(report, indent=1, sort_keys=True) + "\n"
    with open(os.path.join(audit_dir, "settlement-backfill.json"), "w") as fh:
        fh.write(payload)
    history = {"at": report["generatedAt"], "mode": report["mode"], "season": report["season"],
               "settledTickers": report["settledTickers"], "attempted": report["attempted"],
               "matches": report["matches"], "mismatches": len(report["mismatches"]),
               "unreachable": len(report["unreachable"]), "reportSha256": sha256_bytes(payload.encode())}
    with open(os.path.join(audit_dir, "settlement-backfill-history.jsonl"), "a") as fh:
        fh.write(json.dumps(history, sort_keys=True) + "\n")
    return payload


def offline_plan(seasons: list[tuple[str, str]], limit: int) -> int:
    """Without --live nothing is fetched and nothing is written: print the plan instead."""
    total = 0
    for season, season_dir in seasons:
        settled = collect_settled(season_dir)
        count = len(settled) if not limit else min(limit, len(settled))
        total += count
        print(f"season {season}: {len(settled)} unique settled tickers in the ledger "
              f"({count} would be re-read)")
    print(f"offline run: 0 API calls made, no report written. Use --live on a machine with "
          f"network egress (the GitHub runner). Total tickers to re-read: {total}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="actually re-read the settled markets from GET /markets/{ticker}")
    parser.add_argument("--season", help="one season year only (default: every committed season)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap the number of tickers re-read per season (0 = all)")
    args = parser.parse_args(argv)

    seasons = season_dirs()
    if args.season:
        seasons = [(s, d) for s, d in seasons if s == args.season]
        if not seasons:
            print(f"no data/season-{args.season} directory found")
            return 2

    if not args.live:
        return offline_plan(seasons, args.limit)

    try:
        from kalshi_client import KalshiClient
        client = KalshiClient(pause=0.15)
    except Exception as error:  # pragma: no cover - import guard
        print(f"client import failed: {error}")
        return 2

    mismatches_total = 0
    for season, season_dir in seasons:
        report = backfill_season(client, season, season_dir, limit=args.limit)
        if report["settledTickers"] == 0:
            print(f"season {season}: no settled positions yet, nothing to backfill")
            continue
        write_report(season_dir, report)
        mismatches_total += len(report["mismatches"])
        print(f"season {season}: re-read {report['attempted']}/{report['settledTickers']} settled tickers "
              f"-> {report['matches']} match, {len(report['mismatches'])} mismatch(es), "
              f"{len(report['unreachable'])} unreachable")
        for bad in report["mismatches"]:
            print(f"  MISMATCH {bad['ticker']}: ledger {bad['ledgerResult']}@{bad['ledgerSettlementTs']} "
                  f"vs API {bad.get('apiResult')}@{bad.get('apiSettlementTs')} ({bad.get('url')})")
    if mismatches_total:
        print(f"FINDINGS: {mismatches_total} ledger-vs-API settlement mismatch(es) recorded in "
              f"forward/audit/settlement-backfill.json - the ledger was NOT adjusted; review manually.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
