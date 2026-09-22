#!/usr/bin/env python3
"""Offline tests for the maker quote-plan model (posted quotes vs the official trade tape).

Tape fixtures reuse the verbatim shape returned by GET /markets/trades (see
tests/test_execution_realism.py for the capture provenance).

Run: python3 -m unittest tests.test_maker_model
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

import maker_model as MM  # noqa: E402
from kalshi_client import FixtureClient  # noqa: E402


def print_row(yes_price, count, created="2026-09-20T15:30:00.000000Z", trade_id="t1"):
    return {"count_fp": f"{count:.2f}", "created_time": created, "is_block_trade": False,
            "no_price_dollars": f"{1 - yes_price:.4f}", "taker_book_side": "ask",
            "taker_outcome_side": "no", "taker_side": "no", "ticker": "KXTINY-26SEP20-YES",
            "trade_id": trade_id, "yes_price_dollars": f"{yes_price:.4f}"}


def plan(**extra):
    row = {"cycle": "20260920T150000Z", "at": "2026-09-20T15:00:00Z", "strategyId": "spread-smith",
           "username": "SpreadSmith", "ticker": "KXTINY-26SEP20-YES", "title": "Tiny market",
           "series": "KXTINY", "side": "yes", "quotePrice": 0.55, "limit": 0.55,
           "status": "quote_plan", "positionId": None,
           "postedPrice": 0.55, "postedContracts": 100.0, "spreadAtPost": 0.05,
           "improvementCents": 1, "bookAt": "2026-09-20T15:00:00Z", "bookQuotes":
               {"yes_bid": 0.55, "yes_ask": 0.60, "no_bid": 0.40, "no_ask": 0.45},
           "closeTime": "2026-09-20T16:00:00Z", "ledgerLine": 1,
           "reason": "maker quote plan: post a resting YES bid at 0.5500"}
    row.update(extra)
    return row


def write_plans(base, plans):
    intent_dir = os.path.join(base, "intents")
    os.makedirs(intent_dir, exist_ok=True)
    with open(os.path.join(intent_dir, "2026-09.jsonl"), "w") as fh:
        for offset, row in enumerate(plans, 1):
            row = dict(row)
            row.setdefault("ledgerFile", "intents/2026-09.jsonl")
            row["ledgerLine"] = offset
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def write_results(base, rows):
    with open(os.path.join(base, "trades.jsonl"), "w") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


class QuotePlanReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maker-model-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_only_spread_smith_quote_plans(self):
        write_plans(self.tmp, [
            plan(),
            plan(ticker="KXOTHER-1", status="filled", positionId="p1"),       # a fill, not a plan
            plan(ticker="KXOTHER-2", status="quote_plan", strategyId="book-edge"),  # wrong strategy
        ])
        plans = MM.read_quote_plans(self.tmp)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["ticker"], "KXTINY-26SEP20-YES")
        self.assertEqual(plans[0]["_ledgerFile"], os.path.join("intents", "2026-09.jsonl"))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maker-model-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, trades):
        return FixtureClient({"markets/trades": {"cursor": "", "trades": trades}})

    def test_through_price_print_proves_the_fill(self):
        # Posted YES bid at 0.55; the tape prints 0.52 AFTER the post -> price priority proof.
        row = MM.compare(plan(), self.client([print_row(0.52, 40)]), 3600, {})
        self.assertEqual(row["status"], "compared")
        self.assertEqual(row["fillEvidence"], "tape_traded_through")
        self.assertEqual(row["filledContracts"], 100.0)
        self.assertEqual(row["firstThroughTs"], "2026-09-20T15:30:00.000000Z")

    def test_touch_only_print_is_queue_uncertain_not_filled(self):
        row = MM.compare(plan(), self.client([print_row(0.55, 25)]), 3600, {})
        self.assertEqual(row["fillEvidence"], "queue_uncertain")
        self.assertEqual(row["filledContracts"], 0.0)
        self.assertEqual(row["queueUncertainContracts"], 25.0)  # min(posted 100, touch volume 25)

    def test_prints_only_at_better_prices_for_us_do_not_fill(self):
        # A print ABOVE our bid (0.58) never reaches our level.
        row = MM.compare(plan(), self.client([print_row(0.58, 900)]), 3600, {})
        self.assertEqual(row["fillEvidence"], "no_fill_evidence")
        self.assertEqual(row["filledContracts"], 0.0)

    def test_prints_before_the_post_never_fill_the_plan(self):
        row = MM.compare(plan(), self.client([print_row(0.52, 40, created="2026-09-20T14:59:59.000000Z")]), 3600, {})
        self.assertEqual(row["fillEvidence"], "no_fill_evidence")

    def test_no_tape_is_no_fill_evidence(self):
        row = MM.compare(plan(), self.client([]), 3600, {})
        self.assertEqual(row["fillEvidence"], "no_fill_evidence")
        self.assertEqual(row["tapePrints"], 0)

    def test_window_is_capped_by_market_close(self):
        row = MM.compare(plan(), self.client([]), 7200, {})
        self.assertEqual(row["windowEnd"], "2026-09-20T16:00:00Z")  # close before post+2h

    def test_tape_fetch_failure_is_a_status_not_a_crash(self):
        client = FixtureClient({})  # every lookup 404s
        row = MM.compare(plan(), client, 3600, {})
        self.assertEqual(row["status"], "tape_fetch_failed")

    def test_settlement_projection_uses_the_official_result_only(self):
        results = {"KXTINY-26SEP20-YES": {"result": "yes", "settlementTs": "2026-09-20T16:05:00Z"}}
        row = MM.compare(plan(), self.client([print_row(0.52, 40)]), 3600, results)
        settlement = row["settlement"]
        self.assertTrue(settlement["settled"])
        self.assertTrue(settlement["won"])
        # 100 contracts bought at 0.55, settled at 1.00 -> +45.00 before maker fees.
        self.assertAlmostEqual(settlement["pnlBeforeMakerFees"], 45.0)
        losing = MM.compare(plan(), self.client([print_row(0.52, 40)]), 3600,
                            {"KXTINY-26SEP20-YES": {"result": "no", "settlementTs": "2026-09-20T16:05:00Z"}})
        self.assertAlmostEqual(losing["settlement"]["pnlBeforeMakerFees"], -55.0)

    def test_unknown_result_is_never_guessed(self):
        row = MM.compare(plan(), self.client([print_row(0.52, 40)]), 3600, {})
        self.assertFalse(row["settlement"]["settled"])
        self.assertIsNone(row["settlement"]["pnlBeforeMakerFees"])


class SummaryTests(unittest.TestCase):
    def test_counts_and_labels(self):
        rows = [
            MM.compare(plan(), FixtureClient({"markets/trades": {"trades": [print_row(0.52, 40)]}}), 3600, {}),
            MM.compare(plan(ticker="KXTOUCH-1", postedPrice=0.55), FixtureClient(
                {"markets/trades": {"trades": [print_row(0.55, 10)]}}), 3600, {}),
            MM.compare(plan(ticker="KXNONE-1"), FixtureClient({"markets/trades": {"trades": []}}), 3600, {}),
        ]
        summary = MM.summarize(rows, "2026-09-22T00:00:00Z")
        self.assertEqual(summary["compared"], 3)
        self.assertEqual(summary["tapeTradedThrough"], 1)
        self.assertEqual(summary["queueUncertain"], 1)
        self.assertEqual(summary["noFillEvidence"], 1)
        self.assertTrue(summary["makerFeesUnverified"])
        self.assertIn("MODELLED", summary["modelLabel"])
        self.assertEqual(summary["tapeTradedThrough"] + summary["queueUncertain"]
                         + summary["noFillEvidence"], summary["compared"])


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maker-model-")
        self.out = tempfile.mkdtemp(prefix="maker-model-out-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def test_main_writes_model_file_and_daily_log(self):
        write_plans(self.tmp, [plan()])
        write_results(self.tmp, [{"kind": "settlement", "ticker": "KXTINY-26SEP20-YES", "result": "yes",
                                  "positionId": "other", "exitAt": "2026-09-20T16:05:00Z"}])
        fixtures = os.path.join(self.out, "fixtures")
        os.makedirs(fixtures)
        with open(os.path.join(fixtures, "kalshi.json"), "w") as fh:
            json.dump({"markets/trades": {"trades": [print_row(0.52, 40)]}}, fh)
        code = MM.main(["--fixtures", fixtures, "--forward-dir", self.tmp, "--window", "3600"])
        self.assertEqual(code, 0)
        summary = json.load(open(os.path.join(self.tmp, "execution", "maker-model.json")))
        self.assertEqual(summary["tapeTradedThrough"], 1)
        self.assertEqual(summary["settledProvenFills"], 1)
        self.assertAlmostEqual(summary["projectedPnlBeforeMakerFees"], 45.0)
        day_files = [n for n in os.listdir(os.path.join(self.tmp, "execution"))
                     if n.startswith("maker-") and n.endswith(".jsonl")]
        self.assertEqual(len(day_files), 1)


if __name__ == "__main__":
    unittest.main()
