#!/usr/bin/env python3
"""Offline tests for the trades review (all placed + all upcoming trades).

Run: python3 -m unittest tests.test_trades_review
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

import trades_review as TR  # noqa: E402


def event(kind, **extra):
    row = {"kind": kind, "cycle": "20260920T150000Z", "at": "2026-09-20T15:00:00Z",
           "strategyId": "book-edge", "username": "BookRocket", "positionId": "p1",
           "ticker": "KXTINY-26SEP20-YES", "title": "Tiny market", "series": "KXTINY",
           "side": "yes", "contracts": 50.0, "ledgerFile": "trades.jsonl", "ledgerLine": 1}
    row.update(extra)
    return row


class TradesReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trades-review-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_ledger(self):
        events = [
            event("fill", requestedContracts=80.0, entryPrice=0.55, entryTouch=0.55, limitPrice=0.57,
                  entryAt="2026-09-20T15:00:00Z", closeTime="2026-09-20T16:00:00Z",
                  entryFee=0.9, slippageEntry=0.01, unfilledContracts=30.0,
                  reason="book edge", evidence={"sha256": "abc123def456"}),
            event("settlement", positionId="p1", result="yes", exitPrice=1.0,
                  exitAt="2026-09-20T16:05:00Z", exitType="settlement", pnl=21.5,
                  feesTotal=0.9, entryPrice=0.55, entryAt="2026-09-20T15:00:00Z", ledgerLine=2),
            event("fill", positionId="p2", requestedContracts=10.0, contracts=10.0, entryPrice=0.30,
                  entryTouch=0.30, limitPrice=0.32, entryAt="2026-09-20T15:30:00Z",
                  closeTime="2026-09-20T17:00:00Z", entryFee=0.1, slippageEntry=0.0,
                  evidence={"sha256": "fff123def456"}, ledgerLine=3),
        ]
        with open(os.path.join(self.tmp, "trades.jsonl"), "w") as fh:
            for row in events:
                fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        intents = [
            {"cycle": "c1", "at": "2026-09-20T15:00:00Z", "strategyId": "tail-sprint", "username": "TailSprint",
             "ticker": "KXTINY-26SEP20-NO", "title": "T2", "series": "KXTINY", "side": "no", "quotePrice": 0.10,
             "limit": 0.15, "closeTime": "2026-09-20T18:00:00Z", "reason": "tail", "status": "queued",
             "ledgerFile": "intents/2026-09.jsonl", "ledgerLine": 1},
            {"cycle": "c2", "at": "2026-09-20T15:05:00Z", "strategyId": "spread-smith", "username": "SpreadSmith",
             "ticker": "KXTINY-26SEP20-YES", "title": "Tiny market", "series": "KXTINY", "side": "yes",
             "quotePrice": 0.55, "limit": 0.55, "status": "quote_plan", "positionId": None,
             "postedPrice": 0.55, "postedContracts": 100.0, "reason": "maker quote plan",
             "closeTime": "2026-09-20T16:00:00Z", "ledgerFile": "intents/2026-09.jsonl", "ledgerLine": 2},
            {"cycle": "c3", "at": "2026-09-20T15:06:00Z", "strategyId": "book-edge", "username": "BookRocket",
             "ticker": "KXTINY-26SEP20-YES", "title": "Tiny market", "series": "KXTINY", "side": "yes",
             "quotePrice": 0.55, "status": "filled", "positionId": "p1",
             "ledgerFile": "intents/2026-09.jsonl", "ledgerLine": 3},
        ]
        intent_dir = os.path.join(self.tmp, "intents")
        os.makedirs(intent_dir)
        with open(os.path.join(intent_dir, "2026-09.jsonl"), "w") as fh:
            for row in intents:
                fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")

    def test_placed_rows_join_fill_with_settlement(self):
        self.write_ledger()
        review = TR.build_review(self.tmp)
        self.assertEqual(len(review["placed"]), 2)
        settled = next(p for p in review["placed"] if p["positionId"] == "p1")
        self.assertEqual(settled["status"], "settlement")
        self.assertEqual(settled["result"], "yes")
        self.assertAlmostEqual(settled["pnl"], 21.5)
        self.assertEqual(settled["evidenceSha256"], "abc123def456")
        open_pos = next(p for p in review["placed"] if p["positionId"] == "p2")
        self.assertEqual(open_pos["status"], "open")  # still open
        self.assertIsNone(open_pos["exitAt"])

    def test_upcoming_excludes_filled_and_splits_quote_plans(self):
        self.write_ledger()
        review = TR.build_review(self.tmp)
        statuses = [row["status"] for row in review["upcoming"]]
        self.assertNotIn("filled", statuses)
        self.assertIn("queued", statuses)
        self.assertIn("quote_plan", statuses)
        self.assertEqual(review["totals"]["quotePlans"], 1)
        self.assertEqual(review["totals"]["upcomingByStatus"], {"queued": 1, "quote_plan": 1})

    def test_totals_agree_with_events(self):
        self.write_ledger()
        review = TR.build_review(self.tmp)
        self.assertEqual(review["totals"]["fills"], 2)
        self.assertEqual(review["totals"]["settlements"], 1)
        self.assertEqual(review["totals"]["openPositions"], 1)
        self.assertAlmostEqual(review["totals"]["realizedPnl"], 21.5)
        self.assertAlmostEqual(review["totals"]["feesPaid"], 1.0)  # 0.9 settled + 0.1 open entry fee

    def test_markdown_names_every_trade_and_marks_open(self):
        self.write_ledger()
        review = TR.build_review(self.tmp)
        md = TR.render_markdown(review)
        self.assertIn("BookRocket", md)
        self.assertIn("SpreadSmith maker quote plans (MODELLED", md)
        self.assertIn("**open**", md)
        self.assertIn("queued", md)
        self.assertNotIn("@SpreadSmith | KXTINY-26SEP20-YES | YES", md.split("## 2.")[0])  # plans are not placed

    def test_write_review_emits_both_files(self):
        self.write_ledger()
        TR.write_review(self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "trades-review.md")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "trades-review.json")))

    def test_empty_ledger_is_explicit_not_synthetic(self):
        review = TR.build_review(self.tmp)
        self.assertEqual(review["placed"], [])
        md = TR.render_markdown(review)
        self.assertIn("no fill recorded yet", md)
        self.assertIn("no quote plan recorded yet", md)


if __name__ == "__main__":
    unittest.main()
