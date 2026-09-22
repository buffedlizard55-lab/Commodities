#!/usr/bin/env python3
"""Offline tests for scripts/verify_settlements.py (full settlement backfill).

The stub client has the same contract as kalshi_client.KalshiClient.market:
market(ticker, exchange_index=None) -> (payload, raw_bytes, url).

Run: python3 -m unittest tests.test_verify_settlements
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_settlements as VS  # noqa: E402


class StubClient:
    """Returns canned market records per ticker; everything else raises (unreachable)."""

    def __init__(self, records: dict[str, dict]):
        self.records = records
        self.calls: list[tuple[str, int | None]] = []

    def market(self, ticker: str, exchange_index: int | None = None):
        self.calls.append((ticker, exchange_index))
        if ticker not in self.records:
            raise RuntimeError(f"404 not found: {ticker}")
        raw = json.dumps(self.records[ticker]).encode()
        return self.records[ticker], raw, f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}"


def settlement(ticker, result, ts, strategy, position, series="KXNFLGAME"):
    return {"kind": "settlement", "ticker": ticker, "series": series, "result": result, "exitAt": ts,
            "strategyId": strategy, "positionId": position}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="settle-backfill-")
        self.season_dir = os.path.join(self.tmp, "season-2026")
        os.makedirs(os.path.join(self.season_dir, "forward"), exist_ok=True)
        # the script resolves DATA_ROOT at import time; point it at our temp tree
        self._old_root = VS.DATA_ROOT
        VS.DATA_ROOT = self.tmp

    def tearDown(self):
        VS.DATA_ROOT = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_ledger(self, events):
        with open(os.path.join(self.season_dir, "forward", "trades.jsonl"), "w") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")

    def api(self, ticker, result, ts, status="settled"):
        return {"market": {"ticker": ticker, "result": result, "settlement_ts": ts, "status": status}}

    def test_every_settled_ticker_is_reread_and_matches(self):
        self.write_ledger([
            settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "tick-chaser", "p1"),
            settlement("KXNFLGAME-26SEP20B-B", "no", "2026-09-21T04:00:00Z", "dip-hunter", "p2"),
        ])
        client = StubClient({
            "KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z"),
            "KXNFLGAME-26SEP20B-B": self.api("KXNFLGAME-26SEP20B-B", "no", "2026-09-21T04:00:00Z"),
        })
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(report["settledTickers"], 2)
        self.assertEqual(report["attempted"], 2)
        self.assertEqual(report["matches"], 2)
        self.assertEqual(report["mismatches"], [])
        self.assertEqual(report["unreachable"], [])
        self.assertTrue(all(len(r["responseSha256"]) == 64 for r in report["records"]))

    def test_mismatch_is_a_finding_not_a_correction(self):
        self.write_ledger([settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1")])
        client = StubClient({"KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "no",
                                                              "2026-09-21T03:00:00Z")})
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(report["matches"], 0)
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertEqual(report["mismatches"][0]["apiResult"], "no")
        # the ledger on disk is untouched
        ledger = [json.loads(l) for l in
                  open(os.path.join(self.season_dir, "forward", "trades.jsonl"))]
        self.assertEqual(ledger[0]["result"], "yes")

    def test_settlement_ts_mismatch_also_flags(self):
        self.write_ledger([settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1")])
        client = StubClient({"KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "yes",
                                                              "2026-09-21T09:59:59Z")})
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertFalse(report["records"][0]["settlementMatches"])
        self.assertTrue(report["records"][0]["resultMatches"])

    def test_fractional_api_seconds_still_match(self):
        # IRR-39: the API returns fractional seconds (e.g. 2026-09-20T03:33:45.191125Z) while
        # the ledger stores exitAt truncated to whole seconds; the same second must match -
        # for both yes/no and value-scalar settlements.
        self.write_ledger([
            settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1"),
            settlement("KXCPI-26AUG-T0.8", "value:0.4", "2026-09-11T13:28:53Z", "s", "p2", series="KXCPI"),
        ])
        client = StubClient({
            "KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "yes",
                                             "2026-09-21T03:00:00.123456Z"),
            "KXCPI-26AUG-T0.8": {"market": {"ticker": "KXCPI-26AUG-T0.8", "result": None,
                                            "settlement_ts": "2026-09-11T13:28:53.999999Z",
                                            "settlement_value_dollars": "0.40", "status": "settled"}},
        })
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(report["matches"], 2)
        self.assertEqual(report["mismatches"], [])
        self.assertTrue(all(r["settlementMatches"] for r in report["records"]))

    def test_unreachable_is_never_match_nor_mismatch(self):
        self.write_ledger([settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1")])
        client = StubClient({})  # every fetch raises
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(report["attempted"], 1)
        self.assertEqual(report["matches"], 0)
        self.assertEqual(report["mismatches"], [])
        self.assertEqual(len(report["unreachable"]), 1)
        self.assertEqual(report["unreachable"][0]["status"], "unreachable")

    def test_empty_ledger_reports_nothing_invented(self):
        self.write_ledger([])
        report = VS.backfill_season(StubClient({}), "2026", self.season_dir)
        self.assertEqual(report["settledTickers"], 0)
        self.assertEqual(report["attempted"], 0)
        self.assertIn("no settled positions", report["note"])

    def test_value_scalar_settlement_compares_ts_only(self):
        self.write_ledger([settlement("KXCPI-26AUG-T0.8", "value:0.4", "2026-09-11T13:28:53Z", "s", "p1",
                                      series="KXCPI")])
        # the API has no yes/no result for a value market, but the ts matches -> match
        client = StubClient({"KXCPI-26AUG-T0.8": {"market": {"ticker": "KXCPI-26AUG-T0.8", "result": None,
                                                             "settlement_ts": "2026-09-11T13:28:53Z",
                                                             "status": "settled"}}})
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(report["matches"], 1)
        self.assertEqual(report["mismatches"], [])

    def test_multiple_positions_on_one_ticker_make_one_api_call(self):
        self.write_ledger([
            settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "tick-chaser", "p1"),
            settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "dip-hunter", "p2"),
        ])
        client = StubClient({"KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "yes",
                                                              "2026-09-21T03:00:00Z")})
        report = VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(report["settledTickers"], 1)
        record = report["records"][0]
        self.assertEqual(record["positionIds"], ["p1", "p2"])
        self.assertEqual(record["strategyIds"], ["tick-chaser", "dip-hunter"])
        self.assertEqual(record["settlementEvents"], 2)

    def test_exchange_shard_is_passed_from_the_series_index(self):
        # series-index.json lives under DATA_ROOT/universe (patched to the temp tree in setUp)
        os.makedirs(os.path.join(self.tmp, "universe"), exist_ok=True)
        with open(os.path.join(self.tmp, "universe", "series-index.json"), "w") as fh:
            json.dump({"series": {"KXMLBGAME": {"exchange_index": 3}}}, fh)
        self.write_ledger([settlement("KXMLBGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1",
                                      series="KXMLBGAME")])
        client = StubClient({"KXMLBGAME-26SEP20A-A": self.api("KXMLBGAME-26SEP20A-A", "yes",
                                                              "2026-09-21T03:00:00Z")})
        VS.backfill_season(client, "2026", self.season_dir)
        self.assertEqual(client.calls[0], ("KXMLBGAME-26SEP20A-A", 3))

    def test_limit_is_deterministic_and_sorted(self):
        self.write_ledger([settlement(f"KXNFLGAME-26SEP2{i}X-X", "yes", "2026-09-21T03:00:00Z",
                                      "s", f"p{i}") for i in range(5)])
        client = StubClient({f"KXNFLGAME-26SEP2{i}X-X": self.api(f"KXNFLGAME-26SEP2{i}X-X", "yes",
                                                                "2026-09-21T03:00:00Z") for i in range(5)})
        report = VS.backfill_season(client, "2026", self.season_dir, limit=2)
        self.assertEqual(report["settledTickers"], 5)
        self.assertEqual([c[0] for c in client.calls], ["KXNFLGAME-26SEP20X-X", "KXNFLGAME-26SEP21X-X"])

    def test_write_report_creates_latest_and_history(self):
        self.write_ledger([settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1")])
        client = StubClient({"KXNFLGAME-26SEP20A-A": self.api("KXNFLGAME-26SEP20A-A", "yes",
                                                              "2026-09-21T03:00:00Z")})
        report = VS.backfill_season(client, "2026", self.season_dir)
        VS.write_report(self.season_dir, report)
        VS.write_report(self.season_dir, report)  # a second run appends, never rewrites history
        latest = os.path.join(self.season_dir, "forward", "audit", "settlement-backfill.json")
        history = os.path.join(self.season_dir, "forward", "audit", "settlement-backfill-history.jsonl")
        self.assertEqual(json.load(open(latest))["matches"], 1)
        rows = [json.loads(l) for l in open(history)]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["reportSha256"], rows[1]["reportSha256"])
        self.assertEqual(rows[0]["mismatches"], 0)

    def test_multi_season_scan_and_offline_plan(self):
        other = os.path.join(self.tmp, "season-2027")
        os.makedirs(os.path.join(other, "forward"), exist_ok=True)
        self.write_ledger([settlement("KXNFLGAME-26SEP20A-A", "yes", "2026-09-21T03:00:00Z", "s", "p1")])
        with open(os.path.join(other, "forward", "trades.jsonl"), "w") as fh:
            fh.write(json.dumps(settlement("KXNFLGAME-27JAN01B-B", "no", "2027-01-02T03:00:00Z",
                                           "s", "p9")) + "\n")
        seasons = VS.season_dirs()
        self.assertEqual([s for s, _ in seasons], ["2026", "2027"])
        self.assertEqual(len(VS.collect_settled(other)), 1)
        # offline_plan only prints; it must not write reports
        rc = VS.offline_plan(seasons, 0)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.season_dir, "forward", "audit",
                                                     "settlement-backfill.json")))


if __name__ == "__main__":
    unittest.main()
