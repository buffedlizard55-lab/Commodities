#!/usr/bin/env python3
"""Tests for storage compaction (IRR-24) and the season rollover rule.

Run: python3 -m unittest tests.test_compaction tests.test_season
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import compact_storage as CS  # noqa: E402
import season as SEASON  # noqa: E402
from paper_engine import parse_ts  # noqa: E402


def make_ledger(base, day="2026-08-01"):
    forward = os.path.join(base, "forward")
    os.makedirs(os.path.join(forward, "evidence"), exist_ok=True)
    os.makedirs(os.path.join(forward, "quotes"), exist_ok=True)
    os.makedirs(os.path.join(forward, "trades.jsonl").rsplit("/", 1)[0], exist_ok=True)
    evidence = os.path.join(forward, "evidence", f"{day}.jsonl")
    with open(evidence, "w") as fh:
        for i in range(3):
            fh.write(json.dumps({"cycle": f"c{i}", "kind": "orderbook", "ticker": f"T{i}",
                                 "sha256": hashlib.sha256(f"raw{i}".encode()).hexdigest(), "raw": f"raw{i}"}) + "\n")
    quotes = os.path.join(forward, "quotes", f"{day}.csv")
    with open(quotes, "w") as fh:
        fh.write("cycle,ticker\n")
        for i in range(4):
            fh.write(f"c{i},T{i}\n")
    # a fresh file that must NOT be compacted
    fresh = os.path.join(forward, "evidence", "2026-09-20.jsonl")
    with open(fresh, "w") as fh:
        fh.write(json.dumps({"cycle": "fresh", "kind": "orderbook", "ticker": "F",
                             "sha256": hashlib.sha256(b"fresh").hexdigest(), "raw": "fresh"}) + "\n")
    return forward, evidence, quotes


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compaction-")
        self.now = parse_ts("2026-09-20T15:00:00Z")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compacts_old_files_and_keeps_the_bytes(self):
        forward, evidence, quotes = make_ledger(self.tmp)
        with open(evidence, "rb") as fh:
            original = fh.read()
        code = CS.main(["--forward-dir", forward, "--days", "30", "--apply", "--now", str(self.now)])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(evidence))
        self.assertTrue(os.path.exists(evidence + ".gz"))
        self.assertTrue(os.path.exists(quotes + ".gz"))
        # the fresh file is untouched
        self.assertTrue(os.path.exists(os.path.join(forward, "evidence", "2026-09-20.jsonl")))
        index = json.load(open(os.path.join(forward, "COMPRESSED.json")))
        rel = "evidence/2026-08-01.jsonl"
        self.assertEqual(index["files"][rel]["originalSha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(index["files"][rel]["lines"], 3)
        with gzip.open(evidence + ".gz", "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_check_and_restore_round_trip(self):
        forward, evidence, _quotes = make_ledger(self.tmp)
        with open(evidence, "rb") as fh:
            original = fh.read()
        CS.main(["--forward-dir", forward, "--days", "30", "--apply", "--now", str(self.now)])
        self.assertEqual(CS.main(["--forward-dir", forward, "--check"]), 0)
        # tamper: the check must fail loudly
        with open(evidence + ".gz", "wb") as fh:
            fh.write(b"not a gzip file")
        self.assertEqual(CS.main(["--forward-dir", forward, "--check"]), 1)
        # restore a good copy again and verify the bytes come back
        with gzip.open(evidence + ".gz", "wb") as fh:
            fh.write(original)
        self.assertEqual(CS.main(["--forward-dir", forward, "--restore", "evidence/2026-08-01.jsonl"]), 0)
        with open(evidence, "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_dry_run_changes_nothing(self):
        forward, evidence, _quotes = make_ledger(self.tmp)
        CS.main(["--forward-dir", forward, "--days", "30", "--now", str(self.now)])
        self.assertTrue(os.path.exists(evidence))
        self.assertFalse(os.path.exists(os.path.join(forward, "COMPRESSED.json")))

    def test_verify_data_reads_through_gzip(self):
        """verify_data's evidence binding must survive compaction."""
        forward, evidence, _quotes = make_ledger(self.tmp)
        blob = CS.read_maybe_compressed(forward, "evidence/2026-08-01.jsonl")
        CS.main(["--forward-dir", forward, "--days", "30", "--apply", "--now", str(self.now)])
        self.assertEqual(CS.read_maybe_compressed(forward, "evidence/2026-08-01.jsonl"), blob)
        rows = [json.loads(line) for line in blob.decode().splitlines()]
        self.assertTrue(all(hashlib.sha256(r["raw"].encode()).hexdigest() == r["sha256"] for r in rows))

    def test_append_only_files_are_never_compacted(self):
        forward, _evidence, _quotes = make_ledger(self.tmp)
        with open(os.path.join(forward, "trades.jsonl"), "w") as fh:
            fh.write('{"kind":"fill"}\n')
        CS.main(["--forward-dir", forward, "--days", "0", "--apply", "--now", str(self.now)])
        self.assertTrue(os.path.exists(os.path.join(forward, "trades.jsonl")))


class SeasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="season-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_season_is_the_utc_year(self):
        self.assertEqual(SEASON.season_for(parse_ts("2026-12-31T23:59:59Z")), "2026")
        self.assertEqual(SEASON.season_for(parse_ts("2027-01-01T00:00:00Z")), "2027")

    def test_rollover_creates_a_fresh_season_and_freezes_the_old_one(self):
        forward_2026, created = SEASON.ensure_season(self.tmp, "2026", parse_ts("2026-06-01T00:00:00Z"))
        self.assertTrue(created)
        state_path = os.path.join(forward_2026, "state.json")
        state = json.load(open(state_path))
        self.assertEqual(state["startingCash"], 10_000.0)
        # pretend the 2026 season ran
        state["cycles"] = 48
        state["accounts"] = {"book-edge": {"cash": 9_000.0, "positions": []}}
        with open(state_path, "w") as fh:
            json.dump(state, fh)
        # first cycle of 2027 -> new directory, fresh accounts, 2026 untouched
        forward_2027, season, rolled = SEASON.resolve_forward_dir(self.tmp, parse_ts("2027-01-01T00:07:00Z"))
        self.assertEqual(season, "2027")
        self.assertTrue(rolled)
        self.assertTrue(forward_2027.endswith("season-2027/forward"))
        fresh = json.load(open(os.path.join(forward_2027, "state.json")))
        self.assertEqual(fresh["accounts"], {})
        self.assertEqual(fresh["startingCash"], 10_000.0)
        frozen = json.load(open(state_path))
        self.assertEqual(frozen["cycles"], 48)
        self.assertEqual(frozen["accounts"]["book-edge"]["cash"], 9_000.0)
        # and the site index shows both, with 2027 active and 2026 frozen
        index = SEASON.write_seasons_index(self.tmp, parse_ts("2027-01-01T00:07:00Z"))
        self.assertEqual(index["activeSeason"], "2027")
        by_season = {row["season"]: row for row in index["seasons"]}
        self.assertTrue(by_season["2027"]["active"])
        self.assertTrue(by_season["2026"]["frozen"])
        self.assertEqual(by_season["2026"]["cycles"], 48)

    def test_existing_season_is_not_recreated(self):
        SEASON.ensure_season(self.tmp, "2026", parse_ts("2026-06-01T00:00:00Z"))
        _forward, created = SEASON.ensure_season(self.tmp, "2026", parse_ts("2026-06-02T00:00:00Z"))
        self.assertFalse(created)


if __name__ == "__main__":
    unittest.main()
