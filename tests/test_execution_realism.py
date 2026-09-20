#!/usr/bin/env python3
"""Offline tests for the execution-realism comparison against the official trade tape.

The tape fixture below is the verbatim shape returned by
GET /markets/trades?ticker=KXNFLGAME-26SEP17DETBUF-BUF&limit=3 on 2026-09-20 (fields: count_fp,
created_time, is_block_trade, yes_price_dollars, no_price_dollars, taker_book_side,
taker_outcome_side, taker_side, ticker, trade_id).

Run: python3 -m unittest tests.test_execution_realism
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

import execution_realism as ER  # noqa: E402
from kalshi_client import FixtureClient  # noqa: E402

TAPE = {"cursor": "", "trades": [
    {"count_fp": "57.52", "created_time": "2026-09-18T03:29:53.438658Z", "is_block_trade": False,
     "no_price_dollars": "0.0100", "taker_book_side": "ask", "taker_outcome_side": "no", "taker_side": "no",
     "ticker": "KXNFLGAME-26SEP17DETBUF-BUF", "trade_id": "0722783d-8719-86ae-3cd4-0638c22dd08c",
     "yes_price_dollars": "0.9900"},
    {"count_fp": "1000.00", "created_time": "2026-09-18T03:29:53.265515Z", "is_block_trade": False,
     "no_price_dollars": "0.0500", "taker_book_side": "ask", "taker_outcome_side": "no", "taker_side": "no",
     "ticker": "KXNFLGAME-26SEP17DETBUF-BUF", "trade_id": "0722783d-8719-b266-2096-435879798c4c",
     "yes_price_dollars": "0.9500"},
]}
EMPTY_TAPE = {"cursor": "", "trades": []}


def write_ledger(base, events):
    with open(os.path.join(base, "trades.jsonl"), "w") as fh:
        for event in events:
            fh.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")


class ExecutionRealismTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="execution-realism-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fill(self, **extra):
        event = {"kind": "fill", "cycle": "20260920T033910Z", "at": "2026-09-18T03:29:53Z",
                 "strategyId": "book-edge", "username": "BookRocket", "positionId": "p1",
                 "ticker": "KXNFLGAME-26SEP17DETBUF-BUF", "series": "KXNFLGAME", "side": "yes",
                 "contracts": 800.0, "entryPrice": 0.95, "entryTouch": 0.95, "entryNotional": 760.0,
                 "entryFee": 3.0, "entryAt": "2026-09-18T03:29:53Z", "limitPrice": 0.97, "exchangeIndex": 0}
        event.update(extra)
        return event

    def test_compares_a_fill_with_the_tape(self):
        write_ledger(self.tmp, [self.fill()])
        client = FixtureClient({"markets/trades": TAPE})
        row = ER.compare(self.fill(), client, window=120)
        self.assertEqual(row["status"], "compared")
        self.assertEqual(row["tapeTrades"], 2)
        self.assertAlmostEqual(row["tapeContracts"], 1057.52, places=2)
        # tape VWAP over the YES prints: (57.52*0.99 + 1000*0.95)/1057.52
        expected = (57.52 * 0.99 + 1000 * 0.95) / 1057.52
        self.assertAlmostEqual(row["tapeVwap"], expected, places=6)
        self.assertAlmostEqual(row["centsDiff"], (0.95 - expected) * 100, places=4)
        self.assertTrue(row["coveredByTape"])       # 1057.52 >= 800
        self.assertTrue(row["withinOneCent"])       # a real print at 0.95
        self.assertTrue(row["deskPriceInsideTapeRange"])
        self.assertEqual(len(row["tapeSha256"]), 64)
        self.assertTrue(row["tapeUrl"].startswith("https://external-api.kalshi.com/trade-api/v2/markets/trades"))

    def test_no_tape_in_window_is_reported_not_assumed(self):
        client = FixtureClient({"markets/trades": EMPTY_TAPE})
        row = ER.compare(self.fill(), client, window=120)
        self.assertEqual(row["status"], "no_tape_in_window")
        self.assertNotIn("centsDiff", row)

    def test_side_selection_uses_the_official_no_price(self):
        client = FixtureClient({"markets/trades": TAPE})
        row = ER.compare(self.fill(side="no", contracts=10.0, entryPrice=0.02), client, window=120)
        self.assertEqual(row["status"], "compared")
        self.assertAlmostEqual(row["tapeVwap"], (57.52 * 0.01 + 1000 * 0.05) / 1057.52, places=6)
        self.assertTrue(row["withinOneCent"])

    def test_summary_flags_optimism(self):
        # The tape only printed 0.95 and 0.99 in the window, so a desk fill at 0.90 is better than
        # anything that actually traded: the summary must call the desk optimistic.
        events = [self.fill(), self.fill(positionId="p2", entryPrice=0.90, entryTouch=0.90)]
        write_ledger(self.tmp, events)
        client = FixtureClient({"markets/trades": TAPE})
        rows = [ER.compare(e, client, window=120) for e in events]
        summary = ER.summarize(rows, "2026-09-20T12:00:00Z")
        self.assertEqual(summary["compared"], 2)
        self.assertIsNotNone(summary["medianAbsCentsDiff"])
        self.assertGreater(summary["medianAbsCentsDiff"], 1.0)
        self.assertIn("optimistic", summary["verdict"])
        self.assertEqual(summary["insideTapeRangePct"], 50.0)
        self.assertEqual(rows[1]["deskPriceInsideTapeRange"], False)

    def test_missing_timestamp_is_skipped(self):
        client = FixtureClient({"markets/trades": TAPE})
        row = ER.compare(self.fill(entryAt=None), client, window=120)
        self.assertEqual(row["status"], "skipped_missing_timestamp_or_price")

    def test_cli_writes_daily_and_summary_files(self):
        write_ledger(self.tmp, [self.fill()])
        fixtures = os.path.join(self.tmp, "fixtures")
        os.makedirs(fixtures, exist_ok=True)
        with open(os.path.join(fixtures, "kalshi.json"), "w") as fh:
            json.dump({"markets/trades": TAPE}, fh)
        code = ER.main(["--fixtures", fixtures, "--forward-dir", self.tmp])
        self.assertEqual(code, 0)
        summary = json.load(open(os.path.join(self.tmp, "execution", "summary.json")))
        self.assertEqual(summary["compared"], 1)
        daily = [n for n in os.listdir(os.path.join(self.tmp, "execution")) if n.endswith(".jsonl")]
        self.assertEqual(len(daily), 1)


if __name__ == "__main__":
    unittest.main()
