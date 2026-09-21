#!/usr/bin/env python3
"""Tests for the backtest over the collector's archived candlesticks (verified prices only).

Run: python3 -m unittest tests.test_backtest_archive
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

import backtest_archive as BA  # noqa: E402

HEADER = ["end_period_ts", "open", "high", "low", "close", "yes_bid_close", "yes_ask_close", "volume", "open_interest"]


def write_csv(path, bars):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        fh.write(",".join(HEADER) + "\n")
        for bar in bars:
            fh.write(",".join("" if v is None else str(v) for v in bar) + "\n")


def bars_favourite(n=12, start=1_789_800_000, ask=0.90, bid=0.88, volume=10_000, result_close=None):
    """A settled favourite: verified ask 0.90 / bid 0.88 every bar."""
    return [(start + i * 86_400, ask - 0.02, ask, bid, ask - 0.01, bid, ask, volume, 50_000 + i) for i in range(n)]


def bars_longshot(n=12, start=1_789_800_000, ask=0.10, bid=0.08, volume=5_000):
    return [(start + i * 86_400, ask, ask + 0.02, ask - 0.02, ask, bid, ask, volume, 20_000) for i in range(n)]


def bars_no_volume(n=12, start=1_789_800_000):
    return [(start + i * 86_400, 0.90, 0.92, 0.88, 0.90, 0.88, 0.90, 0, 1_000) for i in range(n)]


class ArchiveBacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="archive-backtest-")
        self.season = os.path.join(self.tmp, "season-2026")
        self.candles = os.path.join(self.season, "forward", "candles")
        os.makedirs(self.candles, exist_ok=True)
        self.rows = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_market(self, ticker, series, bars, result, period=1440, fee_type="quadratic", multiplier=1,
                   settlement_ts="2026-09-20T10:00:00Z"):
        rel = f"candles/{series}/{ticker}-p{period}.csv"
        write_csv(os.path.join(self.season, "forward", rel), bars)
        self.rows.append({"ticker": ticker, "series": series, "file": rel, "period": period, "bars": len(bars),
                          "result": result, "settlementTs": settlement_ts, "openTs": "2026-09-08T00:00:00Z",
                          "closeTs": "2026-09-20T09:00:00Z", "url": f"https://example.invalid/{ticker}",
                          "sha256": "0" * 64, "cycle": "20260920T000000Z",
                          "feeType": fee_type, "feeMultiplier": multiplier})
        with open(os.path.join(self.candles, "index.jsonl"), "w") as fh:
            for row in self.rows:
                fh.write(json.dumps(row) + "\n")

    def test_load_uses_series_fees_from_the_catalog(self):
        self.add_market("KXMLBGAME-1-MIA", "KXMLBGAME", bars_favourite(), "yes", multiplier=0.5,
                        fee_type="quadratic_with_maker_fees")
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars_favourite(), "yes")
        markets = BA.load_archive(self.season, min_bars=8)
        self.assertEqual([m["ticker"] for m in markets], ["KXBTC15M-1-30", "KXMLBGAME-1-MIA"])
        by = {m["ticker"]: m for m in markets}
        # Verified official values (GET /series, stored in data/universe/series-catalog.json):
        # KXMLBGAME has fee_multiplier 0.5, KXBTC15M has 1.  The replay must use them.
        self.assertEqual(by["KXMLBGAME-1-MIA"]["feeMultiplier"], 0.5)
        self.assertEqual(by["KXBTC15M-1-30"]["feeMultiplier"], 1.0)

    def test_markets_without_an_official_result_are_never_backtested(self):
        self.add_market("KXFED-1-T4.5", "KXFED", bars_favourite(), "")
        markets = BA.load_archive(self.season, min_bars=8)
        self.assertEqual(markets, [])

    def test_unknown_fee_type_is_skipped(self):
        self.add_market("ODD-1", "ODDSERIES", bars_favourite(), "yes", fee_type="constant", multiplier=None)
        self.assertEqual(BA.load_archive(self.season, min_bars=8), [])

    def test_favourite_rule_fills_at_the_verified_ask_and_settles_at_one(self):
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars_favourite(), "yes")
        strategy = next(s for s in BA.STRATEGIES if s["id"] == "arch-favourite")
        trades, totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=8))
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["side"], "yes")
        self.assertEqual(trade["entryPrice"], 0.90)          # the bar's verified yes_ask_close
        self.assertEqual(trade["exitPrice"], 1.0)            # official settlement
        self.assertEqual(trade["exitType"], "settlement")
        self.assertEqual(trade["exitFee"], 0.0)
        self.assertLess(trade["entryTs"], trade["exitTs"])   # no look-ahead
        expected_fee = BA.fee(0.90, trade["contracts"], 1.0)
        self.assertAlmostEqual(trade["entryFee"], expected_fee, places=6)
        self.assertAlmostEqual(trade["pnl"],
                               trade["exitNotional"] - trade["entryNotional"] - trade["entryFee"], places=6)
        self.assertAlmostEqual(totals["cash"], BA.STARTING_CASH + totals["realized"], places=6)

    def test_losing_favourite_pays_the_whole_stake(self):
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars_favourite(), "no")
        strategy = next(s for s in BA.STRATEGIES if s["id"] == "arch-favourite")
        trades, totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=8))
        self.assertEqual(trades[0]["exitPrice"], 0.0)
        self.assertLess(trades[0]["pnl"], 0)
        self.assertEqual(totals["losses"], 1)

    def test_no_entry_without_verified_volume(self):
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars_no_volume(), "yes")
        strategy = next(s for s in BA.STRATEGIES if s["id"] == "arch-favourite")
        trades, totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=8))
        self.assertEqual(trades, [])
        self.assertGreater(totals["skippedNoLiquidity"], 0)
        self.assertEqual(totals["cash"], BA.STARTING_CASH)

    def test_volume_cap_bounds_the_size(self):
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars_favourite(volume=100), "yes")
        strategy = next(s for s in BA.STRATEGIES if s["id"] == "arch-favourite")
        trades, _totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=8))
        self.assertLessEqual(trades[0]["contracts"], 100 * BA.VOLUME_CAP_FRACTION)

    def test_take_profit_exit_uses_a_later_verified_bid(self):
        # Tape: flat at 0.08/0.10, a jump at bar 6 (entry, target = entry + 5c) and a further rise at
        # bar 8 whose verified bid clears the target -> mechanical bid exit, then nothing left to settle.
        start = 1_789_800_000
        bars = []
        for i in range(12):
            bid = 0.08 if i < 6 else (0.30 if i < 8 else 0.40)
            ask = bid + 0.02
            bars.append((start + i * 86_400, ask, ask + 0.01, bid, round((bid + ask) / 2, 4), bid, ask, 5_000, 20_000))
        self.add_market("KXBTC15M-1-30", "KXBTC15M", bars, "yes")
        strategy = next(s for s in BA.STRATEGIES if s["id"] == "arch-momentum")
        trades, _totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=8))
        self.assertTrue(trades)
        exits = [t for t in trades if t["exitType"] == "bid_exit"]
        self.assertTrue(exits, f"expected a bid exit, got {[t['exitType'] for t in trades]}")
        for trade in exits:
            self.assertGreater(trade["exitTs"], trade["entryTs"])
            self.assertEqual(trade["exitBar"]["yes_bid_close"], trade["exitPrice"])

    def test_all_rules_run_without_error_on_the_committed_archive(self):
        """Smoke test against the real committed archive (skipped if the desk has not archived yet)."""
        real = os.path.join(ROOT, "data", "season-2026", "forward", "candles", "index.jsonl")
        if not os.path.exists(real):
            self.skipTest("no committed candle archive yet")
        out = BA.build(os.path.join(ROOT, "data", "season-2026"), min_bars=8)
        self.assertGreater(out["competition"]["marketCount"], 0)
        for trade in out["trades"]:
            self.assertTrue(0 < trade["entryPrice"] < 1)
            self.assertTrue(0 <= trade["exitPrice"] <= 1)
            self.assertLessEqual(trade["entryTs"], trade["exitTs"])
            self.assertEqual(len(trade["sha256"]), 64)
        for row in out["board"]:
            closed = [t for t in out["trades"] if t["strategyId"] == row["strategyId"]]
            self.assertAlmostEqual(row["realizedPnl"], sum(t["pnl"] for t in closed), places=6)

    # ---- curves + walk-forward + series filter (grown-archive machinery) -------------------
    def test_curves_end_exactly_at_the_leaderboard_numbers(self):
        self.add_market("KXBTC15M-F1", "KXBTC15M", bars_favourite(), "yes")
        self.add_market("KXBTC15M-L1", "KXBTC15M", bars_longshot(), "no")
        out = BA.build(self.season, min_bars=8)
        curves = out["curves"]["strategies"]
        total_points = 0
        for row in out["board"]:
            points = curves[row["strategyId"]]["points"]
            total_points += len(points)
            if not points:
                self.assertEqual(row["trades"], 0, row["strategyId"])
                continue
            self.assertAlmostEqual(points[-1][1], row["realizedPnl"], places=6)
            stamps = [ts for ts, _ in points]
            self.assertEqual(stamps, sorted(stamps))  # a curve never steps backwards in time
        self.assertEqual(total_points, len(out["trades"]))  # exactly one point per closed trade

    def test_walk_forward_assigns_each_trade_to_one_fold(self):
        self.add_market("KXBTC15M-F1", "KXBTC15M", bars_favourite(), "yes")
        self.add_market("KXBTC15M-L1", "KXBTC15M", bars_longshot(), "no")
        out = BA.build(self.season, min_bars=8, folds=3)
        walk = out["walkForward"]
        self.assertEqual(len(walk["windows"]), 3)
        by_strategy = {}
        for row in walk["rows"]:
            by_strategy[row["strategyId"]] = by_strategy.get(row["strategyId"], 0) + row["trades"]
        for row in out["board"]:
            self.assertEqual(by_strategy.get(row["strategyId"], 0), row["trades"], row["strategyId"])
        realized_by_fold = {}
        for row in walk["rows"]:
            realized_by_fold[row["strategyId"]] = round(realized_by_fold.get(row["strategyId"], 0.0) + row["realizedPnl"], 6)
        for row in out["board"]:
            if row["trades"]:
                self.assertAlmostEqual(realized_by_fold[row["strategyId"]], row["realizedPnl"], places=6)

    def test_series_filter_restricted_replay(self):
        self.add_market("KXMLBGAME-1MIA-F", "KXMLBGAME", bars_favourite(), "yes")
        self.add_market("KXBTC15M-1UP-L", "KXBTC15M", bars_longshot(), "no")
        out = BA.build(self.season, min_bars=8, series_filter=["kxmlbgame"])
        self.assertEqual(list(out["competition"]["markets"]), ["KXMLBGAME-1MIA-F"])
        self.assertEqual(out["competition"]["seriesFilter"], ["KXMLBGAME"])
        self.assertTrue(all(t["series"] == "KXMLBGAME" for t in out["trades"]))

    # ---- the two new rules -----------------------------------------------------------------
    def test_gap_fade_fades_both_directions_at_the_verified_ask(self):
        start = 1_789_800_000
        flat = (start, 0.50, 0.51, 0.49, 0.50, 0.49, 0.51, 5000, 1000)
        gap_down = (start + 86_400, 0.38, 0.41, 0.37, 0.40, 0.39, 0.40, 5000, 1000)
        recover = (start + 172_800, 0.45, 0.47, 0.44, 0.46, 0.46, 0.48, 5000, 1000)
        self.add_market("KXBTC15M-GAP1", "KXBTC15M", [flat, gap_down, recover], "yes")
        trades, totals = BA.replay({"id": "gap", "username": "U", "name": "n", "rule": BA.rule_gap_fade,
                                    "exit": "profit"}, BA.load_archive(self.season, min_bars=3))
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["side"], "yes")            # a 12c dump is faded by buying YES
        self.assertEqual(trade["entryPrice"], 0.40)       # the gap bar's verified ask
        self.assertEqual(trade["exitPrice"], 0.46)        # verified bid >= entry + 0.05
        self.assertEqual(trade["exitType"], "bid_exit")
        self.assertGreater(trade["pnl"], 0)

    def test_favourite_stop_exits_when_the_bid_undermines_the_stop(self):
        start = 1_789_800_000
        good = [(start + i * 86_400, 0.88, 0.91, 0.87, 0.89, 0.88, 0.90, 5000, 1000) for i in range(6)]
        crash = (start + 6 * 86_400, 0.79, 0.80, 0.77, 0.78, 0.77, 0.79, 5000, 1000)  # bid 0.77 <= stop 0.80
        tail = [(start + (7 + i) * 86_400, 0.77, 0.78, 0.75, 0.76, 0.75, 0.78, 5000, 1000) for i in range(5)]
        self.add_market("KXBTC15M-STOP1", "KXBTC15M", good + [crash] + tail, "no")
        strategy = {"id": "stop", "username": "U", "name": "n", "rule": BA.rule_favourite_stop, "exit": "stop"}
        trades, totals = BA.replay(strategy, BA.load_archive(self.season, min_bars=3))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exitType"], "bid_exit")   # stopped, NOT settled at zero
        self.assertEqual(trades[0]["exitPrice"], 0.77)
        self.assertIn("stop", trades[0]["exitReason"])


if __name__ == "__main__":
    unittest.main()
