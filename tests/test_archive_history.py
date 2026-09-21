#!/usr/bin/env python3
"""Tests for the chunked history archiver (the KXFED full-history gap).

Run: python3 -m unittest tests.test_archive_history
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

import archive_history as AH  # noqa: E402
from kalshi_client import FixtureClient  # noqa: E402
from paper_engine import parse_ts  # noqa: E402

TICKER = "KXFED-26SEP-T4.75"
OPEN_TS = parse_ts("2025-08-07T00:00:00Z")
SETTLE_TS = parse_ts("2026-09-16T18:20:58Z")


def candle(ts, close="0.5000"):
    return {"end_period_ts": ts,
            "price": {"open_dollars": "0.4800", "high_dollars": "0.5200", "low_dollars": "0.4700",
                      "close_dollars": close, "mean_dollars": "0.4950", "previous_dollars": "0.4800"},
            "yes_bid": {"close_dollars": "0.4900"}, "yes_ask": {"close_dollars": "0.5100"},
            "volume_fp": "12.00", "open_interest_fp": "317.00"}


def build_fixtures():
    """One chunk per 90-day window, each returning daily bars for its own window."""
    fx = {"markets/" + TICKER: {"market": {
        "ticker": TICKER, "event_ticker": "KXFED-26SEP", "status": "finalized", "result": "no",
        "open_time": "2025-08-07T00:00:00Z", "settlement_ts": "2026-09-16T18:20:58.459351Z",
        "close_time": "2026-09-16T17:55:00Z", "expected_expiration_time": "2026-09-16T18:00:00Z"}}}
    for start, end in AH.windows(OPEN_TS, SETTLE_TS + 60, 1440, 90):
        bars = [candle(ts) for ts in range(start + 86400, end + 1, 86400)]
        key = (f"series/KXFED/markets/{TICKER}/candlesticks?start_ts={start}&end_ts={end}&period_interval=1440")
        fx[key] = {"ticker": TICKER, "candlesticks": bars}
    return fx


class ArchiveHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="archive-history-")
        self.season = os.path.join(self.tmp, "season-2026", "history")
        os.makedirs(self.season, exist_ok=True)
        self._data_dir = AH.DATA_DIR
        AH.DATA_DIR = self.tmp

    def tearDown(self):
        AH.DATA_DIR = self._data_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_fixtures(self, fx):
        directory = os.path.join(self.tmp, "fixtures")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "kalshi.json"), "w") as fh:
            json.dump(fx, fh)
        return directory

    def test_windows_cover_the_life_without_overlap(self):
        import math
        spans = AH.windows(OPEN_TS, SETTLE_TS, 1440, 90)
        step = 90 * 1440 * 60
        # 2025-08-07 -> 2026-09-16 is 405 days of daily bars; 90 bars per request tiles it exactly.
        self.assertEqual(len(spans), math.ceil((SETTLE_TS - OPEN_TS) / step))
        self.assertEqual((SETTLE_TS - OPEN_TS) // 86400, 405)
        self.assertEqual(spans[0][0], OPEN_TS)
        self.assertEqual(spans[-1][1], SETTLE_TS)
        for (a_start, a_end), (b_start, _b_end) in zip(spans, spans[1:]):
            self.assertEqual(a_end, b_start)
        # a finer period needs more, smaller requests
        self.assertGreater(len(AH.windows(OPEN_TS, SETTLE_TS, 60, 90)), len(spans))

    def test_archives_every_chunk_with_its_request_hash(self):
        directory = self.write_fixtures(build_fixtures())
        code = AH.main(["--fixtures", directory, "--ticker", TICKER, "--period", "1440",
                        "--bars-per-chunk", "90", "--season", "2026"])
        self.assertEqual(code, 0)
        csv_path = os.path.join(self.season, f"{TICKER}-p1440.csv")
        self.assertTrue(os.path.exists(csv_path))
        bars = AH.read_csv(csv_path)
        self.assertGreater(len(bars), 380)                      # 2025-08 -> 2026-09 daily bars
        timestamps = sorted(bars)
        self.assertEqual(timestamps, sorted(set(timestamps)))   # deduplicated
        chunks = [json.loads(line) for line in open(csv_path + ".chunks.jsonl")]
        self.assertTrue(all(len(c["sha256"]) == 64 and c["url"] for c in chunks if not c.get("error")))
        self.assertTrue(all("/candlesticks?start_ts=" in c["url"] for c in chunks if c.get("url")))
        index_path = os.path.join(self.season, f"{TICKER}-p1440.index.json")
        index = json.load(open(index_path))
        self.assertEqual(index["barsReturned"], len(bars))
        self.assertEqual(index["windowsWithErrors"], 0)
        self.assertIn("no missing bar is synthesized", index["coverageNote"])
        # Rerunning an unchanged request is a no-op for the evidence log, not 5 more lines.
        self.assertEqual(AH.main(["--fixtures", directory, "--ticker", TICKER, "--period", "1440",
                                  "--bars-per-chunk", "90", "--season", "2026"]), 0)
        chunks_again = [json.loads(line) for line in open(csv_path + ".chunks.jsonl")]
        self.assertEqual(len(chunks_again), len(chunks))

    def test_diff_detects_an_exchange_side_change(self):
        directory = self.write_fixtures(build_fixtures())
        AH.main(["--fixtures", directory, "--ticker", TICKER, "--season", "2026"])
        self.assertEqual(AH.main(["--fixtures", directory, "--ticker", TICKER, "--season", "2026", "--diff"]), 0)
        # the same window re-served with a different close must be reported, not silently accepted
        fx = build_fixtures()
        first_window = AH.windows(OPEN_TS, SETTLE_TS + 60, 1440, 90)[0]
        key = f"series/KXFED/markets/{TICKER}/candlesticks?start_ts={first_window[0]}&end_ts={first_window[1]}&period_interval=1440"
        fx[key]["candlesticks"][0]["price"]["close_dollars"] = "0.7700"
        directory2 = self.write_fixtures(fx)
        self.assertEqual(AH.main(["--fixtures", directory2, "--ticker", TICKER, "--season", "2026", "--diff"]), 1)

    def test_a_failed_chunk_is_logged_not_invented(self):
        fx = build_fixtures()
        spans = AH.windows(OPEN_TS, SETTLE_TS + 60, 1440, 90)
        missing = spans[2]
        fx.pop(f"series/KXFED/markets/{TICKER}/candlesticks?start_ts={missing[0]}&end_ts={missing[1]}&period_interval=1440")
        directory = self.write_fixtures(fx)
        AH.main(["--fixtures", directory, "--ticker", TICKER, "--season", "2026"])
        chunks = [json.loads(line) for line in open(os.path.join(self.season, f"{TICKER}-p1440.csv.chunks.jsonl"))]
        errors = [c for c in chunks if c.get("error")]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["window"], list(missing))


if __name__ == "__main__":
    unittest.main()
