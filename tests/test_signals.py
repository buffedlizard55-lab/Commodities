#!/usr/bin/env python3
"""Offline tests for the official signal adapters (NWS multi-city, ESPN scoreboard, openFDA).

Every fixture below mirrors a response that was read from the live official endpoint on
2026-09-20 (shapes, not invented fields):
  * Census Gazetteer places file header/columns - the file is published at
    https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_place_national.zip
  * NWS /points/{lat},{lon} -> properties.gridId/gridX/gridY/forecast
  * NWS gridpoint forecast  -> properties.periods[].{isDaytime,startTime,temperature,temperatureUnit}
  * ESPN scoreboard         -> events[].competitions[].competitors[].{homeAway,score,team.*}
  * openFDA Drugs@FDA       -> results[].{application_number,sponsor_name,submissions[].*,openfda.*}

Run: python3 -m unittest tests.test_signals
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import signals as SIG  # noqa: E402

GAZ_HEADER = "USPS|GEOID|ANSICODE|NAME|LSAD|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG"
GAZ_ROWS = [
    "IL|1714000|00428736|Chicago|C1|588733032|17408890|227.312|6.722|+41.8375511|-087.6818441",
    "CA|0644000|02410786|Los Angeles|C1|1214359273|121494649|469.059|46.948|+34.1139177|-118.4067823",
    "IL|1799999|00000000|Chicago Heights|C1|1|1|1|1|+41.5000000|-087.6000000",
]


def gazetteer_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("2026_Gaz_place_national.txt", "\n".join([GAZ_HEADER] + GAZ_ROWS) + "\n")
    return buffer.getvalue()


POINTS = {"properties": {"gridId": "LOT", "gridX": 75, "gridY": 72,
                         "forecast": "https://api.weather.gov/gridpoints/LOT/75,72/forecast",
                         "relativeLocation": {"properties": {"city": "Chicago"}}}}
FORECAST = {"properties": {"updateTime": "2026-09-20T10:00:00+00:00", "generatedAt": "2026-09-20T14:50:00+00:00",
                           "periods": [
                               {"name": "Today", "startTime": "2026-09-20T06:00:00-05:00", "isDaytime": True,
                                "temperature": 78, "temperatureUnit": "F"},
                               {"name": "Tonight", "startTime": "2026-09-20T18:00:00-05:00", "isDaytime": False,
                                "temperature": 61, "temperatureUnit": "F"},
                               {"name": "Monday", "startTime": "2026-09-21T06:00:00-05:00", "isDaytime": True,
                                "temperature": 81, "temperatureUnit": "F"}]}}
ESPN = {"events": [{
    "id": "401872933", "date": "2026-09-20T17:00Z", "name": "Carolina Panthers at Atlanta Falcons",
    "shortName": "CAR @ ATL",
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "24", "winner": True,
         "team": {"abbreviation": "ATL", "displayName": "Atlanta Falcons", "shortDisplayName": "Falcons",
                  "location": "Atlanta"}},
        {"homeAway": "away", "score": "10", "winner": False,
         "team": {"abbreviation": "CAR", "displayName": "Carolina Panthers", "shortDisplayName": "Panthers",
                  "location": "Carolina"}}],
        "status": {"type": {"state": "in", "shortDetail": "Q4 2:31", "completed": False, "displayClock": "2:31"}}}]}]}
FDA = {"meta": {"results": {"total": 1, "limit": 5}}, "results": [{
    "application_number": "NDA207871", "sponsor_name": "ACHIEVE LIFE SCIENCES",
    "openfda": {"brand_name": ["CYTISINICLINE"], "substance_name": ["CYTISINICLINE"]},
    "submissions": [{"submission_type": "ORIG", "submission_number": "1", "submission_status": "AP",
                     "submission_status_date": "20260301", "review_priority": "PRIORITY"},
                    {"submission_type": "SUPPL", "submission_number": "2", "submission_status": "AP",
                     "submission_status_date": "20260601", "review_priority": "STANDARD"}]}]}


def fetcher(url):
    if url.endswith(".zip"):
        raw = gazetteer_zip()
        return raw, raw
    if "/points/" in url:
        return POINTS, json.dumps(POINTS).encode()
    if "/forecast" in url:
        return FORECAST, json.dumps(FORECAST).encode()
    if "site.api.espn.com" in url:
        return ESPN, json.dumps(ESPN).encode()
    if "api.fda.gov" in url:
        return FDA, json.dumps(FDA).encode()
    raise RuntimeError(f"unexpected url {url}")


class NwsCityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nws-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_city_coordinates_come_from_the_census_file_only(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        row = centroids.lookup("Chicago")
        self.assertEqual(row["censusPlace"], "Chicago")
        self.assertEqual(row["state"], "IL")
        self.assertAlmostEqual(row["lat"], 41.8375511, places=5)
        self.assertAlmostEqual(row["lon"], -87.6818441, places=5)
        # an unknown city is refused, never guessed
        self.assertIsNone(centroids.lookup("Atlantis"))
        self.assertTrue(any("no Census place match" in e for e in centroids.errors))

    def test_gridpoint_chain_and_forecast_mapping(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        nws = SIG.NwsCities(gridpoints_path=os.path.join(self.tmp, "gridpoints.json"), fetcher=fetcher,
                            centroids=centroids)
        entry = nws.resolve("KXHIGHCHI", "Highest temperature in Chicago")
        self.assertEqual(entry["gridId"], "LOT")
        self.assertEqual(entry["pointsUrl"], "https://api.weather.gov/points/41.8376,-87.6818")
        forecasts, records = nws.capture({"KXHIGHCHI": "Highest temperature in Chicago"})
        self.assertIn("KXHIGHCHI-26SEP20", forecasts)
        self.assertEqual(forecasts["KXHIGHCHI-26SEP20"]["high_f"], 78)
        self.assertEqual(forecasts["KXHIGHCHI-26SEP21"]["high_f"], 81)
        self.assertEqual(records[0]["kind"], "nws-forecast")
        self.assertEqual(len(records[0]["sha256"]), 64)
        # a series whose title names no city is skipped
        self.assertIsNone(nws.city_for_series("Highest Inflation"))

    def test_resolution_is_cached(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        first = SIG.NwsCities(gridpoints_path=os.path.join(self.tmp, "gridpoints.json"), fetcher=fetcher,
                              centroids=centroids)
        first.resolve("KXHIGHCHI", "Highest temperature in Chicago")
        calls = []

        def counting_fetcher(url):
            calls.append(url)
            return fetcher(url)

        second = SIG.NwsCities(gridpoints_path=os.path.join(self.tmp, "gridpoints.json"), fetcher=counting_fetcher,
                               centroids=SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"),
                                                            fetcher=fetcher))
        cached = second.resolve("KXHIGHCHI", "Highest temperature in Chicago")
        self.assertEqual(cached["gridId"], "LOT")
        self.assertFalse(any("/points/" in c for c in calls))


class EspnTests(unittest.TestCase):
    def market(self, **extra):
        row = {"series_ticker": "KXNFLGAME", "ticker": "KXNFLGAME-26SEP20CARATL-CAR",
               "event_ticker": "KXNFLGAME-26SEP20CARATL", "title": "Carolina wins", "yes_sub_title": "Carolina",
               "rules_primary": "If Carolina wins the Carolina vs Atlanta Pro Football game originally scheduled "
                                "for Sep 20, 2026, then the market resolves to Yes."}
        row.update(extra)
        return row

    def test_rules_primary_parse(self):
        game = SIG.game_from_market(self.market())
        self.assertEqual((game["away"], game["home"], game["scheduled"]), ("Carolina", "Atlanta", "2026-09-20"))

    def test_unique_match_and_live_score(self):
        espn = SIG.EspnScoreboard(fetcher=fetcher)
        espn.fetch("KXNFLGAME", "20260920")
        signal = espn.signal_for(self.market(), SIG.date_keys_around(1789938000))
        self.assertEqual(signal["espnEventId"], "401872933")
        self.assertEqual(signal["marketSide"], "away")
        self.assertEqual(signal["awayScore"], 10.0)
        self.assertEqual(signal["homeScore"], 24.0)
        self.assertEqual(signal["scoreDiff"], -14.0)   # the market's team is behind
        self.assertEqual(signal["state"], "in")

    def test_home_side_market_reads_the_same_event(self):
        espn = SIG.EspnScoreboard(fetcher=fetcher)
        espn.fetch("KXNFLGAME", "20260920")
        signal = espn.signal_for(self.market(ticker="KXNFLGAME-26SEP20CARATL-ATL", title="Atlanta wins",
                                             yes_sub_title="Atlanta",
                                             rules_primary="If Atlanta wins the Carolina vs Atlanta Pro Football game "
                                                           "originally scheduled for Sep 20, 2026, then the market "
                                                           "resolves to Yes."), SIG.date_keys_around(1789938000))
        self.assertEqual(signal["marketSide"], "home")
        self.assertEqual(signal["scoreDiff"], 14.0)

    def test_ambiguity_and_missing_rules_abstain(self):
        espn = SIG.EspnScoreboard(fetcher=fetcher)
        espn.fetch("KXNFLGAME", "20260920")
        # no rules_primary -> no mapping, no signal (never guessed from the ticker)
        self.assertIsNone(espn.signal_for(self.market(rules_primary=""), SIG.date_keys_around(1789938000)))
        # team names that do not match any event
        self.assertIsNone(espn.signal_for(self.market(yes_sub_title="Chicago",
                                                      rules_primary="If Chicago wins the Chicago vs Detroit Pro "
                                                                    "Football game originally scheduled for "
                                                                    "Sep 20, 2026, then the market resolves to Yes."),
                                          SIG.date_keys_around(1789938000)))
        # a non-game series is ignored
        self.assertIsNone(espn.signal_for(self.market(series_ticker="KXFED"), SIG.date_keys_around(1789938000)))


class OpenFdaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fda-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def adapter(self):
        return SIG.OpenFdaRecords(cache_path=os.path.join(self.tmp, "fda.json"), fetcher=fetcher)

    def test_title_parsing_on_verified_titles(self):
        cases = {
            "Will the FDA approve cytisinicline for smoking cessation before Oct 1, 2026?": ("cytisinicline", None),
            "When will the FDA approve retatrutide (LY3437943)?": ("retatrutide", "LY3437943"),
            "When will the FDA approve Lonvoguran Ziclumeran (lonvo-z) for Hereditary Angioedema?":
                ("Lonvoguran Ziclumeran", "lonvo-z"),
        }
        for title, expected in cases.items():
            drug = SIG.drug_from_title(title)
            self.assertEqual((drug["name"], drug["code"]), expected, title)
        # an agency-announcement title names no drug -> None
        self.assertIsNone(SIG.drug_from_title(
            "When will FDA announce Reclassification of BPC-157 to Category 1 of the Bulk Drug Substances List?"))

    def test_lookup_reports_only_what_the_response_contains(self):
        record = self.adapter().lookup({"name": "cytisinicline", "code": None})
        self.assertTrue(record["approvedRecord"])
        application = record["applications"][0]
        self.assertEqual(application["application_number"], "NDA207871")
        self.assertEqual(application["first_orig_approved"], "20260301")
        self.assertEqual(application["review_priority"], "PRIORITY")
        self.assertEqual(record["hits"], 1)
        self.assertIn("api.fda.gov/drug/drugsfda.json", record["url"])
        self.assertEqual(len(record["sha256"]), 64)

    def test_no_record_is_not_traded_as_evidence(self):
        adapter = self.adapter()

        def empty(url):
            return {"results": []}, b'{"results":[]}'

        adapter.fetcher = empty
        record = adapter.lookup({"name": "unobtanium", "code": None})
        self.assertFalse(record["approvedRecord"])
        signal = SIG.fda_signal({"series_ticker": "KXFDAAPPROVE",
                                 "title": "Will the FDA approve unobtanium before Oct 1, 2026?"}, adapter)
        self.assertFalse(signal["approvedRecord"])

    def test_politics_series_are_gated_out(self):
        adapter = self.adapter()
        for series in ("KXFDA", "KXFDAANNOUNCE", "KXFDABANS", "KXFDAVAPE"):
            self.assertIsNone(SIG.fda_signal({"series_ticker": series,
                                              "title": "Will the FDA approve cytisinicline before Oct 1, 2026?"},
                                             adapter))
        for series in ("KXFDAAPPROVE", "KXFDARETATRUTIDE", "KXFDAAPPROVALDATELLY"):
            self.assertIsNotNone(SIG.fda_signal({"series_ticker": series,
                                                 "title": "Will the FDA approve cytisinicline before Oct 1, 2026?"},
                                                adapter))

    def test_cache_is_reused_within_seven_days(self):
        adapter = self.adapter()
        adapter.lookup({"name": "cytisinicline", "code": None})
        second = SIG.OpenFdaRecords(cache_path=os.path.join(self.tmp, "fda.json"), fetcher=fetcher)
        record = second.lookup({"name": "cytisinicline", "code": None})
        self.assertTrue(record["cached"])


if __name__ == "__main__":
    unittest.main()
