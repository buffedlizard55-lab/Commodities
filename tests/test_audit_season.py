#!/usr/bin/env python3
"""Offline tests for scripts/audit_season.py (season health audit).

Run: python3 -m unittest tests.test_audit_season
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

import audit_season as AUD  # noqa: E402


def cycle(at, **extra):
    row = {"cycle": at.replace("-", "").replace(":", "").rstrip("Z") + "Z", "at": at}
    row.update(extra)
    return row


class ScheduleTests(unittest.TestCase):
    def test_expected_slots_on_time_late_and_missed(self):
        cycles = [cycle("2026-09-20T07:07:00Z"), cycle("2026-09-20T07:37:00Z"),
                  cycle("2026-09-20T09:35:00Z")]
        out = AUD.audit_schedule(cycles)
        # slots in range: 07:07, 07:37, 08:07, 08:37, 09:07, 09:37
        self.assertEqual(out["cycles"], 3)
        self.assertEqual(out["expectedSlots"], 6)
        self.assertEqual(out["matchedSlots"], 3)
        self.assertEqual(out["onTime"], 2)     # 07:07 and 07:37 exactly on their slots
        self.assertEqual(out["late"], 1)      # 09:35 fills the 08:37 slot 58 min late (>15 -> late)
        self.assertEqual(out["missed"], 3)    # 08:07, 09:07 and the 09:37 slot itself are unfilled
        self.assertEqual(out["missedSlotsSample"], ["2026-09-20T08:07Z", "2026-09-20T09:07Z",
                                                     "2026-09-20T09:37Z"])
        self.assertLessEqual(out["maxGapHours"], 2.1)

    def test_empty_ledger_reports_zero(self):
        out = AUD.audit_schedule([])
        self.assertEqual(out["cycles"], 0)
        self.assertNotIn("expectedSlots", out)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="audit-season-")
        self.season = os.path.join(self.tmp, "season-2026")
        self.forward = os.path.join(self.season, "forward")
        os.makedirs(os.path.join(self.forward, "intents"), exist_ok=True)
        os.makedirs(os.path.join(self.forward, "equity"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.forward, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def test_orphan_fills_are_found(self):
        fill = {"kind": "fill", "positionId": "a-1", "ticker": "T1"}
        settled = {"kind": "settlement", "positionId": "a-2", "ticker": "T2", "result": "yes", "exitAt": "x"}
        self.write("trades.jsonl", "\n".join(json.dumps(r) for r in (fill, settled)) + "\n")
        state = {"accounts": {"alpha": {"positions": [{"id": "a-3"}], "lastEquity": 1000.0},
                              "beta": {"positions": [], "lastEquity": 999.0}}}
        self.write("state.json", json.dumps(state))
        self.write("equity/2026-09.csv",
                   "cycle,at,strategyId,username,cash,openPositions,markedPositions,liquidationValue,equity,"
                   "realizedPnl,feesPaid,slippagePaid,markValue\n"
                   "c1,2026-09-20T00:00:00Z,alpha,A,1000.0,1,1,0,1000.0,0,0,0,0\n"
                   "c1,2026-09-20T00:00:00Z,beta,B,990.0,0,0,0,999.0,0,0,0,0\n")
        self.write("intents/2026-09.jsonl", json.dumps({"strategyId": "alpha", "status": "filled"}) + "\n")
        out = AUD.audit_ledger(self.season, [])
        self.assertEqual(out["fillsWithoutExitOrPosition"], 1)
        self.assertEqual(out["orphanFillSample"], ["a-1"])
        self.assertEqual(out["intentStatusByStrategy"]["alpha"]["filled"], 1)
        self.assertEqual(out["equityCsvVsStateMismatches"], [])  # equity matches state for both

    def test_equity_drift_between_csv_and_state_is_reported(self):
        self.write("trades.jsonl", "")
        self.write("state.json", json.dumps({"accounts": {"alpha": {"positions": [], "lastEquity": 1234.0}}}))
        self.write("equity/2026-09.csv",
                   "cycle,at,strategyId,username,cash,openPositions,markedPositions,liquidationValue,equity,"
                   "realizedPnl,feesPaid,slippagePaid,markValue\n"
                   "c1,2026-09-20T00:00:00Z,alpha,A,1000.0,0,0,0,1000.0,0,0,0,0\n")
        out = AUD.audit_ledger(self.season, [])
        self.assertEqual(len(out["equityCsvVsStateMismatches"]), 1)
        self.assertAlmostEqual(out["equityCsvVsStateMismatches"][0]["stateLastEquity"], 1234.0)


class SignalsAndTapeTests(unittest.TestCase):
    def test_signal_totals_sum_across_cycles(self):
        cycles = [cycle("2026-09-20T07:07:00Z", nwsCityForecasts=3, espnSignals=10, espnLiveSignals=2,
                        fdaSignals=1, fdaNoRecord=1, signalErrorCount=4, candlesArchived=5),
                  cycle("2026-09-20T07:37:00Z", nwsCityForecasts=2, espnSignals=8, fdaSignals=0,
                        signalErrorCount=1, candlesArchived=3)]
        out = AUD.audit_signals(cycles)
        self.assertEqual(out["totals"]["nwsCityForecasts"], 5)
        self.assertEqual(out["totals"]["espnSignals"], 18)
        self.assertEqual(out["totals"]["espnLiveSignals"], 2)
        self.assertEqual(out["totals"]["fdaSignals"], 1)
        self.assertEqual(out["totals"]["signalErrors"], 5)

    def test_missing_tape_summary_is_reported_as_unavailable(self):
        tmp = tempfile.mkdtemp(prefix="audit-tape-")
        try:
            out = AUD.audit_tape(tmp)
            self.assertFalse(out["available"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audit_history_is_append_only_and_deduplicated_by_report_hash(self):
        tmp = tempfile.mkdtemp(prefix="audit-history-")
        try:
            report = {"generatedAt": "2026-09-20T07:07:00Z", "season": "2026", "status": "PASS",
                      "schedule": {"expectedSlots": 4, "matchedSlots": 3, "executed": 3, "onTime": 2,
                                   "late": 1, "missed": 1, "extraManual": 0, "maxGapHours": 1.5},
                      "ledger": {"events": 12}, "signals": {"totals": {"signalErrors": 2}},
                      "storage": {"compactedFiles": 4}, "tape": {"compared": 8}}
            payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
            row = AUD.append_audit_history(tmp, report, payload)
            self.assertEqual(row["slotRatePct"], 75.0)
            # A retry with the same full report must not add a second trend point.
            AUD.append_audit_history(tmp, report, payload)
            rows = AUD.read_jsonl(os.path.join(tmp, AUD.AUDIT_HISTORY_JSONL))
            self.assertEqual(len(rows), 1)
            view = json.load(open(os.path.join(tmp, AUD.AUDIT_HISTORY_JSON)))
            self.assertEqual(view[0]["reportSha256"], row["reportSha256"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LiveSettlementTests(unittest.TestCase):
    def test_empty_ledger_never_invents_a_sample(self):
        tmp = tempfile.mkdtemp(prefix="audit-live-")
        try:
            out = AUD.audit_live_settlements(tmp, samples=5)
            self.assertFalse(out["available"])
            self.assertEqual(out["attempts"], 0)
            self.assertIn("note", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
