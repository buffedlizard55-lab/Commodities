#!/usr/bin/env python3
"""Offline tests for the 2026-09-22 signal adapters and the personas that read them.

Fixtures reproduce the shapes observed live on 2026-09-22 (see the module docstring of
scripts/signal_adapters.py for the exact observations); nothing here is invented data - a fixture
that does not match the recorded shape must make the adapter abstain, which is asserted too.

Run: python3 -m unittest tests.test_signal_adapters
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import forward_strategies as FS  # noqa: E402
import signal_adapters as SA  # noqa: E402

# --- ESPN injuries JSON, trimmed from the live response of 2026-09-22T22:51:42Z ----------------
ESPN_NBA = {
    "timestamp": "2026-09-22T22:51:42Z",
    "status": "success",
    "season": {"year": 2027, "type": 1, "name": "Preseason", "displayName": "2026-27"},
    "injuries": [
        {"id": "1", "displayName": "Atlanta Hawks", "injuries": [
            {"id": "-57577", "status": "Out", "date": "2026-09-21T19:50Z",
             "shortComment": "Veesaar was diagnosed with a torn right ACL.",
             "athlete": {"displayName": "Henri Veesaar", "position": {"abbreviation": "C"},
                         "team": {"abbreviation": "ATL"}}},
            {"id": "-57578", "status": "Day-To-Day", "date": "2026-09-22T01:00Z",
             "shortComment": "soreness", "athlete": {"displayName": "Bench Guard",
                                                      "position": {"abbreviation": "SG"},
                                                      "team": {"abbreviation": "ATL"}}}]},
        {"id": "22", "displayName": "Phoenix Suns", "injuries": [
            {"id": "-57579", "status": "Out", "date": "2026-09-21T12:00Z", "shortComment": "ankle",
             "athlete": {"displayName": "A", "position": {"abbreviation": "PG"}, "team": {"abbreviation": "PHX"}}},
            {"id": "-57580", "status": "Out", "date": "2026-09-20T12:00Z", "shortComment": "knee",
             "athlete": {"displayName": "B", "position": {"abbreviation": "PF"}, "team": {"abbreviation": "PHX"}}},
            {"id": "-57581", "status": "Doubtful", "date": "2026-09-19T12:00Z", "shortComment": "wrist",
             "athlete": {"displayName": "C", "position": {"abbreviation": "SF"}, "team": {"abbreviation": "PHX"}}}]},
    ],
}

ESPN_NFL = {
    "timestamp": "2026-09-22T20:00:00Z",
    "status": "success",
    "season": {"year": 2026, "type": 2, "name": "Regular Season", "displayName": "2026"},
    "injuries": [
        {"id": "2", "displayName": "Buffalo Bills", "injuries": [
            {"id": "-1", "status": "Out", "date": "2026-09-22T17:00Z", "shortComment": "shoulder",
             "athlete": {"displayName": "Starting QB", "position": {"abbreviation": "QB"},
                         "team": {"abbreviation": "BUF"}}},
            {"id": "-2", "status": "Out", "date": "2026-09-01T17:00Z", "shortComment": "old news",
             "athlete": {"displayName": "Old Injury", "position": {"abbreviation": "WR"},
                         "team": {"abbreviation": "BUF"}}}]},
        {"id": "6", "displayName": "Detroit Lions", "injuries": [
            {"id": "-3", "status": "Day-To-Day", "date": "2026-09-22T17:00Z", "shortComment": "rest",
             "athlete": {"displayName": "Receiver", "position": {"abbreviation": "WR"},
                         "team": {"abbreviation": "DET"}}}]},
    ],
}

# --- Cleveland Fed Inflation Nowcasting page, trimmed to the three published tables ------------
NOWCAST_HTML = """
<html><body>
<h2>Inflation, month-over-month percent change</h2>
<table><tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
<tr><td>September 2026</td><td>0.43</td><td>0.20</td><td>0.40</td><td>0.28</td><td>09/22</td></tr>
<tr><td>August 2026</td><td></td><td></td><td>0.34</td><td>0.27</td><td>09/22</td></tr>
</table>
<h2>Inflation, year-over-year percent change</h2>
<table><tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
<tr><td>September 2026</td><td>3.50</td><td>2.39</td><td>3.93</td><td>3.49</td><td>09/22</td></tr>
</table>
<h2>Quarterly annualized percent change</h2>
<table><tr><th>Quarter</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
<tr><td>2026:Q3</td><td>1.44</td><td>2.16</td><td>2.50</td><td>3.00</td><td>09/22</td></tr>
</table>
</body></html>
"""

# --- FRED CSV, the live shape (holiday row published blank) ------------------------------------
FRED_CSV = """observation_date,SP500
2026-09-16,7551.81
2026-09-17,7637.76
2026-09-18,7650.50
2026-09-21,
"""


class EspnInjuryTests(unittest.TestCase):
    def test_parse_counts_hard_designations_only(self):
        report = SA.parse_injuries(ESPN_NBA)
        self.assertEqual(report["teamCount"], 2)
        self.assertEqual(report["playerCount"], 5)
        hawks = report["teams"]["Atlanta Hawks"]
        self.assertEqual(hawks["hardOut"], 1)          # the Day-To-Day row is counted, not traded
        self.assertEqual(len(hawks["players"]), 2)
        suns = report["teams"]["Phoenix Suns"]
        self.assertEqual(suns["hardOut"], 3)
        self.assertTrue(all(p["hard"] for p in suns["players"]))

    def test_unknown_shape_abstains(self):
        with self.assertRaises(ValueError):
            SA.parse_injuries({"timestamp": "2026-09-22T00:00:00Z"})

    def test_adapter_records_url_hash_and_abstains_on_fetch_failure(self):
        def good(url):
            return ESPN_NFL, b"{}"
        adapter = SA.EspnInjuries(fetcher=good)
        self.assertIsNotNone(adapter.fetch("KXNFLGAME"))
        self.assertEqual(len(adapter.records), 1)
        self.assertEqual(adapter.records[0]["sourceUrl"], SA.ESPN_INJURIES_URL.format(sport="football", league="nfl"))
        self.assertEqual(len(adapter.records[0]["sha256"]), 64)

        def broken(url):
            raise RuntimeError("HTTP 403")
        failing = SA.EspnInjuries(fetcher=broken)
        self.assertIsNone(failing.fetch("KXNBAGAME"))
        self.assertTrue(any("403" in e for e in failing.errors))
        self.assertEqual(failing.records, [])

    def test_team_report_matches_by_nickname_and_abbreviation(self):
        report = SA.parse_injuries(ESPN_NFL)
        self.assertEqual(SA.team_report(report, "BUF Bills")["abbreviation"], "BUF")
        self.assertEqual(SA.team_report(report, "Bills")["team"], "Buffalo Bills")
        self.assertEqual(SA.team_report(report, "BUF")["team"], "Buffalo Bills")
        self.assertIsNone(SA.team_report(report, "Nonexistent"))  # no guess

    def test_signal_for_market_only_reports_fresh_rows_for_the_opponent(self):
        adapter = SA.EspnInjuries(fetcher=lambda url: (ESPN_NFL, b"{}"))
        adapter.fetch("KXNFLGAME")
        market = {"ticker": "KXNFLGAME-26SEP27DETBUF-DET", "series_ticker": "KXNFLGAME",
                  "yes_sub_title": "DET Lions", "title": "Will Detroit win?"}
        game = {"away": "DET Lions", "home": "BUF Bills", "scheduled": "2026-09-27",
                "team": "Detroit", "marketTeam": "DET Lions"}
        # the cycle time is the report's own timestamp (2026-09-22T20:00:00Z), as the desk passes it
        now_ts = int(datetime.strptime("2026-09-22T20:00:00Z", "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=timezone.utc).timestamp())
        signal = SA.injury_signal_for_market(adapter, market, game, now_ts=now_ts, window_hours=72)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["espnTeam"], "Buffalo Bills")
        self.assertEqual(signal["freshQbOut"], 1)
        self.assertEqual(signal["staleHardOut"], 1)   # the 2026-09-01 row is outside the window
        self.assertEqual(signal["marketSide"], "away")   # the market names the away team
        # a tighter window leaves only stale rows, so the market abstains
        self.assertIsNone(SA.injury_signal_for_market(adapter, market, game, now_ts=now_ts, window_hours=0.02))
        # and a row dated in the future is not news yet either
        self.assertIsNone(SA.injury_signal_for_market(adapter, market, game, now_ts=now_ts - 86400,
                                                      window_hours=72))

    def test_team_side_refuses_ambiguous_names(self):
        self.assertEqual(SA.team_side("DET Lions", "DET Lions", "BUF Bills"), "away")
        self.assertEqual(SA.team_side("BUF Bills", "DET Lions", "BUF Bills"), "home")
        self.assertIsNone(SA.team_side("Lions", "DET Lions", "GB Lions"))
        self.assertIsNone(SA.team_side("", "DET Lions", "BUF Bills"))


class NowcastTests(unittest.TestCase):
    def test_parse_keeps_blank_cells_blank(self):
        tables = SA.parse_nowcast_html(NOWCAST_HTML)["tables"]
        self.assertEqual(sorted(tables), ["month-over-month", "quarterly", "year-over-year"])
        self.assertEqual(tables["month-over-month"][0][:5], ["September 2026", "0.43", "0.20", "0.40", "0.28"])
        # the August row keeps its published blanks; PCE/Core PCE are still present
        august = tables["month-over-month"][1]
        self.assertEqual(august[1], "")
        self.assertEqual(august[3], "0.34")

    def test_monthly_reads_the_asked_section_and_metric(self):
        adapter = SA.ClevelandFedNowcast(fetcher=lambda url: (NOWCAST_HTML, b"<html/>"))
        self.assertIsNotNone(adapter.fetch())
        mom = adapter.monthly("September 2026", section="month-over-month", metric="cpi")
        self.assertEqual(mom["value"], 0.43)
        # the default sanity band follows the section, so an annual rate is not rejected
        yoy = adapter.monthly("September 2026", section="year-over-year", metric="cpi")
        self.assertEqual(yoy["value"], 3.50)
        q = adapter.monthly("2026:Q3", section="quarterly", metric="corePce")
        self.assertEqual(q["value"], 3.00)
        self.assertIsNone(adapter.monthly("August 2026", "month-over-month", "cpi"))  # blank -> abstain
        self.assertIsNone(adapter.monthly("September 2026", "month-over-month", "cpi", band=(0.6, 0.7)))

    def test_fetch_failure_records_an_error_and_abstains(self):
        def broken(url):
            raise RuntimeError("HTTP 503")
        adapter = SA.ClevelandFedNowcast(fetcher=broken)
        self.assertIsNone(adapter.fetch())
        self.assertEqual(adapter.monthly("September 2026"), None)
        self.assertTrue(adapter.errors)


class FredTests(unittest.TestCase):
    def test_close_skips_absent_rows_and_reports_the_latest_observation(self):
        adapter = SA.FredSeries(fetcher=lambda url: (FRED_CSV, b"observation_date,SP500\n"))
        observed = adapter.close("SP500", "2026-09-16", "2026-09-21")
        self.assertEqual(observed["latest"], {"date": "2026-09-18", "value": 7650.50})
        self.assertEqual(observed["absentDates"], ["2026-09-21"])  # never carried forward
        self.assertIn("id=SP500", observed["sourceUrl"])
        self.assertEqual(adapter.records[0]["seriesId"], "SP500")

    def test_no_numeric_observation_abstains(self):
        adapter = SA.FredSeries(fetcher=lambda url: ("observation_date,SP500\n2026-09-21,\n", b"x"))
        self.assertIsNone(adapter.close("SP500", "2026-09-21", "2026-09-21"))
        self.assertTrue(adapter.errors)

    def test_fetch_failure_abstains(self):
        def broken(url):
            raise RuntimeError("HTTP 404")
        adapter = SA.FredSeries(fetcher=broken)
        self.assertIsNone(adapter.close("SP500", "2026-09-01", "2026-09-02"))
        self.assertTrue(adapter.errors)


def market(**extra):
    row = {"ticker": "KXNBAGAME-26OCT22PHXLAL-PHX", "series_ticker": "KXNBAGAME", "title": "Will Phoenix win?",
           "yes_sub_title": "PHX Suns", "yes_ask": 0.50, "yes_bid": 0.47, "no_ask": 0.50, "no_bid": 0.47,
           "volume_24h": 20000.0, "rules_primary": "If Phoenix wins the Phoenix vs Los Angeles Basketball game"
                                                   " originally scheduled for Oct 22, 2026, then the market"
                                                   " resolves to Yes."}
    row.update(extra)
    return row


class PersonaRuleTests(unittest.TestCase):
    def test_injury_fade_fires_on_a_fresh_quarterback_out(self):
        ctx = {"injuries": {"KXNBAGAME-26OCT22PHXLAL-PHX": {
            "freshQbOut": True, "freshHardOut": 1, "freshOutPlayers": [
                {"athlete": "Starting QB", "position": "QB", "status": "Out", "date": "2026-09-22T17:00Z"}],
            "espnTeam": "Buffalo Bills", "marketTeam": "DET Lions", "league": "KXNFLGAME",
            "gameTeamAway": "DET Lions", "gameTeamHome": "BUF Bills", "scheduled": "2026-09-27"}}}
        signal = FS.entry_injury_fade(market(), ctx)
        self.assertEqual(signal["side"], "yes")
        self.assertIn("Starting QB", signal["reason"])
        # no QB news -> no trade
        self.assertIsNone(FS.entry_injury_fade(market(), {"injuries": {}}))
        # too expensive -> no trade
        expensive = {"injuries": {market()["ticker"]: ctx["injuries"]["KXNBAGAME-26OCT22PHXLAL-PHX"]}}
        self.assertIsNone(FS.entry_injury_fade(market(yes_ask=0.61, yes_bid=0.58), expensive))

    def test_tipoff_triage_needs_three_hard_outs_and_a_cheap_side(self):
        signal_ctx = {"injuries": {market()["ticker"]: {
            "freshHardOut": 3, "freshOutPlayers": [{"athlete": "A", "status": "Out", "date": "2026-09-21T12:00Z"}],
            "espnTeam": "Los Angeles Lakers", "marketTeam": "PHX Suns", "league": "KXNBAGAME",
            "windowHours": 168}}}
        self.assertIsNotNone(FS.entry_tipoff_triage(market(), signal_ctx))
        two_outs = {"injuries": {market()["ticker"]: dict(signal_ctx["injuries"][market()["ticker"]],
                                                         freshHardOut=2)}}
        self.assertIsNone(FS.entry_tipoff_triage(market(), two_outs))
        self.assertIsNone(FS.entry_tipoff_triage(market(yes_ask=0.56, yes_bid=0.53), signal_ctx))

    def test_nowcast_nudge_reads_the_matching_month_and_strike(self):
        cpi = {"ticker": "KXCPI-26SEP-T0.3", "series_ticker": "KXCPI", "title": "Will CPI rise more than 0.3% in September 2026?",
               "floor_strike": 0.3, "yes_ask": 0.72, "yes_bid": 0.69, "no_ask": 0.30, "no_bid": 0.27,
               "rules_primary": "If the Consumer Price Index (CPI) increases by more than 0.3% (single-decimal) in"
                                " September 2026, then the market resolves to Yes."}
        ctx = {"nowcast": {"KXCPI": {"label": "September 2026", "value": 0.43, "metric": "cpi",
                                     "section": "month-over-month", "updated": "09/22"}}}
        signal = FS.entry_nowcast_nudge(cpi, ctx)
        self.assertEqual(signal["side"], "yes")
        # the nowcast must clear the strike by the documented margin (0.10pp for KXCPI)
        near = dict(ctx["nowcast"]["KXCPI"], value=0.35)
        self.assertIsNone(FS.entry_nowcast_nudge(cpi, {"nowcast": {"KXCPI": near}}))
        # a market for a different month abstains
        self.assertIsNone(FS.entry_nowcast_nudge(dict(cpi, title="Will CPI rise more than 0.3% in August 2026?"), ctx))
        # a non-CPI rule text abstains
        self.assertIsNone(FS.entry_nowcast_nudge(dict(cpi, rules_primary="Something else"), ctx))
        # a nowcast below the strike trades NO
        low = {"nowcast": {"KXCPI": dict(ctx["nowcast"]["KXCPI"], value=0.05)}}
        self.assertEqual(FS.entry_nowcast_nudge(cpi, low)["side"], "no")

    def test_leap_mapper_uses_the_published_index_close(self):
        index_market = {"ticker": "KXINX-26SEP22-B7600", "series_ticker": "KXINX", "title": "S&P 500 range",
                        "floor_strike": 7550.0, "cap_strike": 7700.0, "yes_ask": 0.60, "yes_bid": 0.57,
                        "no_ask": 0.40, "no_bid": 0.37, "volume_24h": 25000.0}
        inside = {"index": {"KXINX": {"seriesId": "SP500", "latest": {"date": "2026-09-18", "value": 7650.50}}}}
        signal = FS.entry_leap_mapper(index_market, inside)
        self.assertEqual(signal["side"], "yes")
        self.assertIn("7,650.50", signal["reason"])
        outside = {"index": {"KXINX": {"seriesId": "SP500", "latest": {"date": "2026-09-18", "value": 7800.0}}}}
        self.assertEqual(FS.entry_leap_mapper(index_market, outside)["side"], "no")
        # missing strikes or missing close -> abstain
        self.assertIsNone(FS.entry_leap_mapper(dict(index_market, cap_strike=None), inside))
        self.assertIsNone(FS.entry_leap_mapper(index_market, {"index": {}}))

    def test_new_personas_are_registered_and_no_longer_gated(self):
        ids = {s["id"] for s in FS.STRATEGIES}
        for wanted in ("injury-fade", "tipoff-triage", "nowcast-nudge", "leap-mapper"):
            self.assertIn(wanted, ids)
        gated = {s["id"] for s in FS.GATED}
        self.assertNotIn("nba-injury-gate", gated)
        self.assertNotIn("leap-rotation", gated)
        # the index series are part of the tracked universe now
        self.assertIn("KXINX", FS.TRACKED_SERIES)
        self.assertIn("KXNASDAQ100", FS.TRACKED_SERIES)


if __name__ == "__main__":
    unittest.main()
