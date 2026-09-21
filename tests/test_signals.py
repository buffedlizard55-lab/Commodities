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
    "DC|1152000|01500965|Washington|C1|1|1|1|1|+38.9072000|-077.0369000",
    "PA|4283000|02412012|Washington|C1|1|1|1|1|+40.1781000|-080.2539000",
    "GA|1304000|00328600|Atlanta|C1|1|1|1|1|+33.7490000|-084.3880000",
    "TX|4805000|03881100|Austin city|C1|1|1|1|1|+30.2672000|-097.7431000",
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

    def test_noncanonical_titles_are_parsed_from_the_series_title_alone(self):
        # the six title shapes seen in the committed cycle errors (IRR-32): all are Kalshi's own words
        cases = {
            "Highest temperature in Chicago": ("Chicago", None),
            "Atlanta Max Temperature": ("Atlanta", None),
            "Boston Maximum Daily Temperature": ("Boston", None),
            "Daily High Temperature Houston": ("Houston", None),
            "Newark, NJ (EWR) Daily Max Temp": ("Newark", "NJ"),
            "Washington DC Daily Max Temp": ("Washington", "DC"),
        }
        for title, expected in cases.items():
            self.assertEqual(SIG.NwsCities.parse_city_state(title), expected, title)
        for title in ("Highest Inflation", "", "When will the Fed cut rates?"):
            self.assertIsNone(SIG.NwsCities.parse_city_state(title), title)

    def test_city_state_resolves_through_the_census_file(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        nws = SIG.NwsCities(gridpoints_path=os.path.join(self.tmp, "gridpoints.json"), fetcher=fetcher,
                            centroids=centroids)
        entry = nws.resolve("KXHIGHTATL", "Atlanta Max Temperature")
        self.assertIsNotNone(entry, nws.errors + centroids.errors)
        self.assertEqual(entry["city"], "Atlanta")
        self.assertEqual(entry["stateHint"], None)

    def test_ambiguous_names_need_an_exact_state(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        # the alias table (normalised keys) pins 'Washington' to the District of Columbia row
        row = centroids.lookup("Washington")
        self.assertEqual((row["censusPlace"], row["state"]), ("Washington", "DC"))
        # an explicit state hint from a title overrides the alias
        row = centroids.lookup("Washington", "PA")
        self.assertEqual(row["state"], "PA")

    def test_name_suffix_forms_and_diagnostics(self):
        centroids = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places.json"), fetcher=fetcher)
        row = centroids.lookup("Austin")  # the file stores 'Austin city' - suffix pass must find it
        # the place name is kept verbatim as the Census file publishes it
        self.assertEqual((row["censusPlace"], row["state"]), ("Austin city", "TX"))
        self.assertAlmostEqual(row["lat"], 30.2672, places=4)
        centroids2 = SIG.PlaceCentroids(cache_path=os.path.join(self.tmp, "places2.json"), fetcher=fetcher)
        self.assertIsNone(centroids2.lookup("Atlantis"))
        # a failed match says what was scanned (row count + response hash), not just 'no match'
        self.assertTrue(any("scanned 7 gazetteer rows" in e for e in centroids2.errors), centroids2.errors)


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

    def test_rules_primary_nickname_format_keeps_multi_word_teams(self):
        # Verbatim format from the committed official response for KXNFLGAME-26SEP17DETBUF
        # (data/season-2026/raw/refetch-20260920-markets-KXNFLGAME-settled-limit2.json).  IRR-36:
        # the old lazy regex returned home="BUF" here and the market never matched an event.
        row = self.market(series_ticker="KXNFLGAME",
                          rules_primary="If Detroit wins the DET Lions vs BUF Bills Pro Football game "
                                        "originally scheduled for Sep 17, 2026, then the market resolves to Yes.")
        game = SIG.game_from_market(row)
        self.assertEqual((game["away"], game["home"], game["scheduled"]), ("DET Lions", "BUF Bills", "2026-09-17"))
        # a matchup without " vs " is refused, never guessed
        self.assertIsNone(SIG.game_from_market(self.market(rules_primary="If X wins the game originally "
                                                                         "scheduled for Sep 17, 2026")))

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


# Same scoreboard JSON family as the verified NFL fixture above (events[].competitions[].
# competitors[].{homeAway,score,team.*}); the live NHL/WNBA endpoints themselves get their first
# confirmation on the runner's next cycle (IRR-35), so these fixtures pin the shape the adapter
# requires, not a claim about one observed response.
ESPN_NHL = {"events": [{
    "id": "401900001", "date": "2026-09-20T23:00Z", "name": "Boston Bruins at New York Rangers",
    "shortName": "BOS @ NYR",
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "4", "winner": False,
         "team": {"abbreviation": "NYR", "displayName": "New York Rangers", "shortDisplayName": "Rangers",
                  "location": "New York"}},
        {"homeAway": "away", "score": "1", "winner": False,
         "team": {"abbreviation": "BOS", "displayName": "Boston Bruins", "shortDisplayName": "Bruins",
                  "location": "Boston"}}],
        "status": {"type": {"state": "in", "shortDetail": "3rd 5:12", "completed": False,
                            "displayClock": "5:12"}}}]}]}

ESPN_WNBA = {"events": [{
    "id": "401900002", "date": "2026-09-20T23:00Z", "name": "New York Liberty at Las Vegas Aces",
    "shortName": "NY @ LV",
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "71", "winner": False,
         "team": {"abbreviation": "LV", "displayName": "Las Vegas Aces", "shortDisplayName": "Aces",
                  "location": "Las Vegas"}},
        {"homeAway": "away", "score": "84", "winner": False,
         "team": {"abbreviation": "NY", "displayName": "New York Liberty", "shortDisplayName": "Liberty",
                  "location": "New York"}}],
        "status": {"type": {"state": "in", "shortDetail": "Q4 3:00", "completed": False,
                            "displayClock": "3:00"}}}]}]}


class EspnNewLeagueTests(unittest.TestCase):
    """KXNHLGAME / KXWNBAGAME were added to the adapter on 2026-09-21 (README next-work item:
    signal coverage).  These tests prove the mapping works on the documented scoreboard shape."""

    def nhl_market(self, **extra):
        row = {"series_ticker": "KXNHLGAME", "ticker": "KXNHLGAME-26SEP20BOSNYR-BOS",
               "event_ticker": "KXNHLGAME-26SEP20BOSNYR", "title": "Boston wins", "yes_sub_title": "Boston",
               "rules_primary": "If Boston wins the Boston vs New York Pro Hockey game originally scheduled "
                                "for Sep 20, 2026, then the market resolves to Yes."}
        row.update(extra)
        return row

    def wnba_market(self, **extra):
        row = {"series_ticker": "KXWNBAGAME", "ticker": "KXWNBAGAME-26SEP20NYLV-NY",
               "event_ticker": "KXWNBAGAME-26SEP20NYLV", "title": "New York wins",
               "yes_sub_title": "New York",
               "rules_primary": "If New York wins the New York vs Las Vegas Pro Basketball game originally "
                                "scheduled for Sep 20, 2026, then the market resolves to Yes."}
        row.update(extra)
        return row

    def test_nhl_league_is_registered_and_maps_by_rules_primary(self):
        self.assertIn("KXNHLGAME", SIG.ESPN_LEAGUES)
        self.assertEqual(SIG.ESPN_LEAGUES["KXNHLGAME"], ("hockey", "nhl"))
        espn = SIG.EspnScoreboard(fetcher=lambda url: (ESPN_NHL, json.dumps(ESPN_NHL).encode()))
        espn.fetch("KXNHLGAME", "20260920")
        signal = espn.signal_for(self.nhl_market(), SIG.date_keys_around(1789938000))
        self.assertIsNotNone(signal, espn.errors)
        self.assertEqual(signal["espnEventId"], "401900001")
        self.assertEqual(signal["marketSide"], "away")
        self.assertEqual(signal["awayScore"], 1.0)
        self.assertEqual(signal["homeScore"], 4.0)
        self.assertEqual(signal["scoreDiff"], -3.0)  # goals: Boston trails by 3
        self.assertEqual(signal["state"], "in")

    def test_wnba_league_is_registered_and_maps_by_rules_primary(self):
        self.assertIn("KXWNBAGAME", SIG.ESPN_LEAGUES)
        self.assertEqual(SIG.ESPN_LEAGUES["KXWNBAGAME"], ("basketball", "wnba"))
        espn = SIG.EspnScoreboard(fetcher=lambda url: (ESPN_WNBA, json.dumps(ESPN_WNBA).encode()))
        espn.fetch("KXWNBAGAME", "20260920")
        signal = espn.signal_for(self.wnba_market(), SIG.date_keys_around(1789938000))
        self.assertIsNotNone(signal, espn.errors)
        self.assertEqual(signal["marketSide"], "away")
        self.assertEqual(signal["scoreDiff"], 13.0)  # points: Liberty lead by 13

    def test_new_leagues_abstain_when_no_unique_match(self):
        espn = SIG.EspnScoreboard(fetcher=lambda url: ({"events": []}, b'{"events": []}'))
        espn.fetch("KXNHLGAME", "20260920")
        self.assertIsNone(espn.signal_for(self.nhl_market(), SIG.date_keys_around(1789938000)))
        self.assertIsNone(espn.signal_for(self.wnba_market(rules_primary=""), SIG.date_keys_around(1789938000)))


# IRR-36 regression: Kalshi's verified NFL rules_primary uses the nickname form
# "DET Lions vs BUF Bills".  The ESPN competitor fields use the full city + nickname
# ("Detroit Lions"), so matching must survive via token overlap, not exact strings only.
ESPN_NICKNAME = {"events": [{
    "id": "401900003", "date": "2026-09-17T23:15Z", "name": "Detroit Lions at Buffalo Bills",
    "shortName": "DET @ BUF",
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "21", "winner": False,
         "team": {"abbreviation": "BUF", "displayName": "Buffalo Bills", "shortDisplayName": "Bills",
                  "location": "Buffalo"}},
        {"homeAway": "away", "score": "24", "winner": False,
         "team": {"abbreviation": "DET", "displayName": "Detroit Lions", "shortDisplayName": "Lions",
                  "location": "Detroit"}}],
        "status": {"type": {"state": "in", "shortDetail": "Q4 1:00", "completed": False,
                            "displayClock": "1:00"}}}]}]}


class EspnNicknameFormatTests(unittest.TestCase):
    def test_nickname_rules_match_full_city_teams_by_token_overlap(self):
        espn = SIG.EspnScoreboard(fetcher=lambda url: (ESPN_NICKNAME, json.dumps(ESPN_NICKNAME).encode()))
        espn.fetch("KXNFLGAME", "20260917")
        market = {"series_ticker": "KXNFLGAME", "ticker": "KXNFLGAME-26SEP17DETBUF-DET",
                  "event_ticker": "KXNFLGAME-26SEP17DETBUF", "title": "Detroit wins",
                  "yes_sub_title": "Detroit",
                  "rules_primary": "If Detroit wins the DET Lions vs BUF Bills Pro Football game originally "
                                   "scheduled for Sep 17, 2026, then the market resolves to Yes."}
        signal = espn.signal_for(market, SIG.date_keys_around(1789678800))  # 2026-09-17 21:00Z
        self.assertIsNotNone(signal, espn.errors)
        self.assertEqual(signal["espnEventId"], "401900003")
        self.assertEqual(signal["marketSide"], "away")   # DET Lions are the away competitor
        self.assertEqual(signal["scoreDiff"], 3.0)       # Detroit leads 24-21


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

    def test_404_is_a_verified_absence_not_an_error(self):
        import urllib.error

        def fetcher_404(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {},
                                         io.BytesIO(b'{"error":"HTTP 404 Not Found"}'))

        adapter = SIG.OpenFdaRecords(cache_path=os.path.join(self.tmp, "fda-404.json"), fetcher=fetcher_404)
        record = adapter.lookup({"name": "retatrutide", "code": "LY3437943"})
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "no-record")
        self.assertFalse(record["approvedRecord"])
        self.assertEqual(len(record["attempts"]), 3)  # substance, brand, code - all answered 404
        self.assertTrue(record["url"].startswith("https://api.fda.gov/drug/drugsfda.json"))
        self.assertEqual(len(record["sha256"]), 64)   # the 404 body is bound as the response hash
        self.assertEqual(adapter.errors, [])           # absence is not an error
        # an absence still yields a signal object, so the persona abstains visibly, not silently
        signal = SIG.fda_signal({"series_ticker": "KXFDAAPPROVE",
                                 "title": "When will the FDA approve retatrutide (LY3437943)?"}, adapter)
        self.assertIsNotNone(signal)
        self.assertFalse(signal["approvedRecord"])

    def test_server_errors_stay_errors(self):
        import urllib.error

        def fetcher_500(url):
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO(b""))

        adapter = SIG.OpenFdaRecords(cache_path=os.path.join(self.tmp, "fda-500.json"), fetcher=fetcher_500)
        self.assertIsNone(adapter.lookup({"name": "who-knows", "code": None}))
        self.assertTrue(any("HTTP Error 500" in e for e in adapter.errors))

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
