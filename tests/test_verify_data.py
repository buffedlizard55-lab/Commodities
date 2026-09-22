#!/usr/bin/env python3
"""Data-contract tests: the committed artifacts and the verification script must agree.

Every assertion here is checked against the real, current files in the repository (canonical
season data, the generated fixture bundle builder, the source registry, the strategy roster and
the trades review / maker model artifacts).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

import scripts.verify_data as verify_data  # noqa: E402
from scripts.forward_strategies import STRATEGIES  # noqa: E402
from scripts.build_fixtures import build as build_fixtures, write_bundle  # noqa: E402
from scripts.kalshi_client import FixtureClient  # noqa: E402


class CanonicalCsvTests(unittest.TestCase):
    def test_canonical_candles_have_monotonic_timestamps_and_bounded_prices(self):
        rows = verify_data.read_csv("candles-KXCPI-26AUG-T0.8-daily.csv")
        self.assertGreater(len(rows), 40)
        ts = [int(r["end_period_ts"]) for r in rows]
        self.assertEqual(ts, sorted(ts))
        for row in rows:
            for col in ("price_open", "price_high", "price_low", "price_close",
                        "yes_bid_close", "yes_ask_close"):
                value = verify_data.fnum(row[col])
                if value is not None:
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)

    def test_trade_tape_sample_is_self_consistent(self):
        rows = verify_data.read_csv("trade-tape-sample.csv")
        self.assertGreater(len(rows), 10)
        for row in rows:
            yes = verify_data.fnum(row.get("yes_price_dollars")) or 0.0
            no = verify_data.fnum(row.get("no_price_dollars")) or 0.0
            self.assertAlmostEqual(yes + no, 1.0, places=2)


class FixtureBundleTests(unittest.TestCase):
    """The offline bundle is generated (scripts/build_fixtures.py) from the canonical season files."""

    def test_builder_payload_is_schema_complete(self):
        payload = build_fixtures()
        self.assertEqual(payload["meta"]["source"], "data/fixtures/kalshi.json")
        self.assertEqual(payload["meta"]["cutoff"], "2026-07-21T00:00:00Z")
        self.assertTrue(any("markets" in url for url in payload["meta"]["urls"]))
        self.assertIn("historical/cutoff", payload)
        self.assertTrue(payload["markets/trades"]["trades"])
        self.assertTrue(payload["series/KXCPI/markets/KXCPI-26AUG-T0.8/candlesticks"]["candlesticks"])

    def test_fixture_gzip_bytes_match_json_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_bundle(tmp, build_fixtures())
            with open(os.path.join(tmp, "kalshi.json.gz"), "rb") as handle:
                gz_bytes = handle.read()
            with open(os.path.join(tmp, "kalshi.json"), "rb") as handle:
                raw = handle.read()
            self.assertEqual(gzip.decompress(gz_bytes), raw)
            expected = hashlib.sha256(raw).hexdigest()
            with open(os.path.join(tmp, "kalshi.json.sha256")) as handle:
                self.assertEqual(handle.read().split()[0], expected)
            os.remove(os.path.join(tmp, "kalshi.json"))  # simulate compaction (gz only)
            blob = verify_data.read_maybe_compressed(tmp, "kalshi.json")
            self.assertIn(b'"meta"', blob)

    def test_fixture_client_replays_the_built_bundle(self):
        client = FixtureClient(build_fixtures())
        body, raw, url = client.get("historical/cutoff")
        self.assertIn("response", body)
        self.assertTrue(url.startswith("https://"))
        body, _, _ = client.get("markets/trades", {"limit": 25})
        self.assertTrue(body["trades"])


class ArchiveCompetitionTests(unittest.TestCase):
    @staticmethod
    def _load():
        with open("data/season-2026/backtest-archive/competition.json") as handle:
            return json.load(handle)

    def test_archive_competition_counts_are_consistent(self):
        competition = self._load()
        self.assertEqual(competition["marketCount"], len(competition["markets"]))
        self.assertGreaterEqual(competition["verifiedBars"], competition["marketCount"])
        self.assertEqual(competition["season"], "2026")
        self.assertIn("0.07", competition["feeRule"])
        self.assertIn("candlesticks", competition["source"])

    def test_archive_leaderboard_rows_are_complete(self):
        with open("data/season-2026/backtest-archive/leaderboard.json") as handle:
            rows = json.load(handle)
        if isinstance(rows, dict):
            rows = rows.get("rows", [])
        self.assertGreaterEqual(len(rows), 9)
        for row in rows:
            self.assertTrue(row.get("username"))
            self.assertIn("returnPct", row)
            self.assertEqual(row["wins"] + row["losses"], row["trades"])


class SourceRegistryTests(unittest.TestCase):
    @staticmethod
    def _load():
        with open("data/source-registry.json") as handle:
            return json.load(handle)

    def test_sources_url_role_and_id_contract(self):
        registry = self._load()
        sources = registry["sources"]
        self.assertGreaterEqual(len(sources), 80)
        self.assertEqual(len({s["id"] for s in sources}), len(sources))
        for source in sources:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertTrue(source.get("name"))
            self.assertTrue(source.get("kind"))

    def test_irregularities_are_numbered_uniquely_through_irr_44(self):
        registry = self._load()
        codes = [str(item).split(" ")[0].rstrip("·") for item in registry["irregularities"]]
        self.assertEqual(len(set(codes)), len(codes))
        self.assertIn("IRR-41", codes)     # maker fees (resolved 2026-09-22)
        self.assertIn("IRR-42", codes)     # social sweep round 1 (discovery only)
        self.assertIn("IRR-43", codes)     # injuries are third-party evidence, never settlement
        self.assertIn("IRR-44", codes)     # index ranges are a signal mapping, not a forecast


class RosterMetadataTests(unittest.TestCase):
    def test_every_live_strategy_has_a_registered_username_and_name(self):
        metadata = json.load(open("data/strategies.json"))
        names = {row.get("name") for row in metadata}
        usernames = {row.get("username") for row in metadata}
        for strategy in STRATEGIES:
            self.assertIn(strategy["name"], names, f"strategy {strategy['name']} must exist in strategies.json")
            self.assertIn(strategy["username"], usernames, f"{strategy['username']} must exist in strategies.json")
        self.assertIn("SpreadSmith", usernames)

    def test_quote_plan_roster_entry_stays_gated_and_named(self):
        metadata = json.load(open("data/strategies.json"))
        spread = next(row for row in metadata if row["id"] == "spread-smith")
        self.assertIn("quote_plan", spread["status"])
        self.assertIn("MODELLED", spread["status"])


class TradesReviewTests(unittest.TestCase):
    def test_trades_review_matches_the_ledger_it_renders(self):
        with redirect_stdout(io.StringIO()):
            verify_data.verify_trades_review("data/season-2026/forward")
        self.assertEqual(verify_data.FAILURES, [])
        review = json.load(open("data/season-2026/forward/trades-review.json"))
        fills = sum(1 for line in open("data/season-2026/forward/trades.jsonl")
                    if line.strip() and json.loads(line).get("kind") == "fill")
        self.assertEqual(len(review["placed"]), fills)
        self.assertTrue(all(row.get("status") != "quote_plan" or row.get("isQuotePlan")
                            for row in review["upcoming"]))


class MakerModelTests(unittest.TestCase):
    def test_maker_model_summary_is_labelled_and_counts_consistent(self):
        summary = json.load(open("data/season-2026/forward/execution/maker-model.json"))
        self.assertIn("MODELLED", summary["modelLabel"])
        self.assertTrue(summary["makerFeesUnverified"])
        with redirect_stdout(io.StringIO()):
            verify_data.verify_maker_model("data/season-2026/forward")
        self.assertEqual(verify_data.FAILURES, [])


class VerifierEndToEndTests(unittest.TestCase):
    def test_verify_data_reports_zero_failures_on_the_committed_tree(self):
        with redirect_stdout(io.StringIO()) as out:
            try:
                verify_data.main()
            except SystemExit as exc:
                self.assertIn(exc.code, (None, 0))
        self.assertIn(" passed, 0 failed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
