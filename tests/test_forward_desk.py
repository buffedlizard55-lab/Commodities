#!/usr/bin/env python3
"""Offline tests for the forward desk (no network; fixtures mirror real API shapes).

Run: python3 -m unittest tests.test_forward_desk -v
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

import forward_desk as FD  # noqa: E402
import forward_strategies as FS  # noqa: E402
from kalshi_client import FixtureClient  # noqa: E402
from paper_engine import (execute, taker_fee, affordable_contracts, parse_book, book_quotes, size_and_fill,  # noqa: E402
                          normalize_market, parse_ts)

T0 = parse_ts("2026-09-20T15:00:00Z")


def series(ticker, exchange_index=0, fee_type="quadratic", tags=None, category="Climate and Weather", title=None):
    return {"series": {"ticker": ticker, "title": title or ticker, "category": category, "categories": [category], "tags": tags or [],
                       "fee_type": fee_type, "fee_multiplier": 1, "exchange_index": exchange_index, "frequency": "daily",
                       "settlement_sources": [{"name": "test", "url": "https://example.invalid"}], "contract_terms_url": "x",
                       "last_updated_ts": "2026-09-01T00:00:00Z"}}


def market(ticker, event, close, yes_bid, yes_ask, last=None, prev=None, volume=5000.0, status="active", **extra):
    row = {"ticker": ticker, "event_ticker": event, "title": ticker, "status": status, "close_time": close,
           "yes_bid_dollars": f"{yes_bid:.4f}", "yes_ask_dollars": f"{yes_ask:.4f}",
           "no_bid_dollars": f"{1 - yes_ask:.4f}", "no_ask_dollars": f"{1 - yes_bid:.4f}",
           "last_price_dollars": f"{(last if last is not None else yes_ask):.4f}",
           "previous_price_dollars": f"{(prev if prev is not None else yes_ask):.4f}",
           "volume_fp": f"{volume:.2f}", "volume_24h_fp": f"{volume:.2f}", "open_interest_fp": "100.00",
           "exchange_index": 0, "result": "", "price_level_structure": "linear_cent"}
    row.update(extra)
    return row


def book(yes_levels, no_levels):
    return {"orderbook_fp": {"yes_dollars": [[f"{p:.4f}", f"{q:.2f}"] for p, q in yes_levels],
                             "no_dollars": [[f"{p:.4f}", f"{q:.2f}"] for p, q in no_levels]}}


def nws_body():
    return {"properties": {"updateTime": "2026-09-20T10:00:00+00:00", "generatedAt": "2026-09-20T14:50:00+00:00", "periods": [
        {"name": "Today", "startTime": "2026-09-20T06:00:00-04:00", "isDaytime": True, "temperature": 73, "temperatureUnit": "F"},
        {"name": "Tonight", "startTime": "2026-09-20T18:00:00-04:00", "isDaytime": False, "temperature": 60, "temperatureUnit": "F"},
        {"name": "Monday", "startTime": "2026-09-21T06:00:00-04:00", "isDaytime": True, "temperature": 79, "temperatureUnit": "F"},
    ]}}


def build_fixtures(settled=False):
    """Cycle-1 world (open markets) or cycle-2 world (the same markets settled)."""
    close_wx = "2026-09-21T05:00:00Z"
    close_15 = "2026-09-20T15:10:00Z"  # 10 minutes after T0
    fx = {
        # Verified series record (GET /series/KXHIGHNY, 2026-09-20): tag "Daily temperature".
        "series/KXHIGHNY": series("KXHIGHNY", tags=["Daily temperature"], title="Highest temperature in New York"),
        "series/KXBTC15M": series("KXBTC15M", exchange_index=2, category="Crypto"),
        "series/KXNFLGAME": series("KXNFLGAME", fee_type="quadratic_with_maker_fees", category="Sports"),
        "series/KXFED": series("KXFED", category="Economics"),
    }
    wx = [
        market("KXHIGHNY-26SEP20-T71", "KXHIGHNY-26SEP20", close_wx, 0.04, 0.06, strike_type="less", cap_strike=71),
        market("KXHIGHNY-26SEP20-B72.5", "KXHIGHNY-26SEP20", close_wx, 0.55, 0.60, strike_type="between", floor_strike=72, cap_strike=73),
        market("KXHIGHNY-26SEP20-B74.5", "KXHIGHNY-26SEP20", close_wx, 0.25, 0.30, strike_type="between", floor_strike=74, cap_strike=75),
        market("KXHIGHNY-26SEP20-T78", "KXHIGHNY-26SEP20", close_wx, 0.00, 0.01, strike_type="greater", floor_strike=78, volume=1788),
    ]
    btc = [market("KXBTC15M-26SEP201515-15", "KXBTC15M-26SEP201515", close_15, 0.76, 0.78, last=0.78, prev=0.60, volume=90000, exchange_index=2)]
    nfl = [market("KXNFLGAME-26SEP20AAABBB-AAA", "KXNFLGAME-26SEP20AAABBB", "2026-09-20T23:00:00Z", 0.88, 0.90, last=0.90, prev=0.80,
                  volume=250000, open_time="2026-09-15T12:00:00Z", yes_sub_title="Alpha",
                  rules_primary="If Alpha wins the Alpha vs Bravo Pro Football game originally scheduled for "
                                "Sep 20, 2026, then the market resolves to Yes.")]
    fed = [market("KXFED-26OCT-T4.50", "KXFED-26OCT", "2026-10-28T18:00:00Z", 0.02, 0.03, volume=40000)]
    # Live-score candidate: 0.78/0.80 quote on the team ESPN shows leading 21-7 (ScorePulse band <= 0.85).
    nfl.append(market("KXNFLGAME-26SEP20CCCDDD-CCC", "KXNFLGAME-26SEP20CCCDDD", "2026-09-21T01:00:00Z", 0.78, 0.80,
                      last=0.80, prev=0.80, volume=9000, open_time="2026-09-15T12:00:00Z", yes_sub_title="Charlie",
                      rules_primary="If Charlie wins the Charlie vs Delta Pro Football game originally scheduled for "
                                    "Sep 20, 2026, then the market resolves to Yes."))
    # FDA drug-decision market whose title names a drug (openFDA Drugs@FDA lookup) + official series record.
    fx["series/KXFDAAPPROVE"] = series("KXFDAAPPROVE", tags=["Medicine"], category="Science and Technology",
                                       title="Will the FDA approve drug?")
    fda = [market("KXFDAAPPROVE-TST-26OCT01", "KXFDAAPPROVE-TST", "2026-10-01T03:59:00Z", 0.90, 0.93, volume=8000,
                  title="Will the FDA approve testdrug for smoking cessation before Oct 1, 2026?",
                  yes_sub_title="Before Oct 1, 2026")]
    if not settled:
        fx["markets?series_ticker=KXHIGHNY&status=open&limit=200"] = {"cursor": "", "markets": wx}
        fx["markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2"] = {"cursor": "", "markets": btc}
        fx["markets?series_ticker=KXNFLGAME&status=open&limit=200"] = {"cursor": "", "markets": nfl}
        fx["markets?series_ticker=KXFED&status=open&limit=200"] = {"cursor": "", "markets": fed}
        fx["markets?series_ticker=KXFDAAPPROVE&status=open&limit=200"] = {"cursor": "", "markets": fda}
    else:
        for key in ("markets?series_ticker=KXHIGHNY&status=open&limit=200", "markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2",
                    "markets?series_ticker=KXNFLGAME&status=open&limit=200", "markets?series_ticker=KXFED&status=open&limit=200",
                    "markets?series_ticker=KXFDAAPPROVE&status=open&limit=200"):
            fx[key] = {"cursor": "", "markets": []}
        outcomes = {"KXHIGHNY-26SEP20-T71": "no", "KXHIGHNY-26SEP20-B72.5": "yes", "KXHIGHNY-26SEP20-B74.5": "no",
                    "KXHIGHNY-26SEP20-T78": "no", "KXBTC15M-26SEP201515-15": "yes", "KXNFLGAME-26SEP20AAABBB-AAA": "yes",
                    "KXNFLGAME-26SEP20CCCDDD-CCC": "yes", "KXFED-26OCT-T4.50": "no", "KXFDAAPPROVE-TST-26OCT01": "yes"}
        for row in wx + btc + nfl + fed + fda:
            done = dict(row)
            done.update({"status": "finalized", "result": outcomes[row["ticker"]], "settlement_ts": "2026-09-21T15:00:00Z",
                         "settlement_value_dollars": "1.0000" if outcomes[row["ticker"]] == "yes" else "0.0000",
                         "yes_bid_dollars": "0.0000", "no_bid_dollars": "0.0000"})
            fx[f"markets/{row['ticker']}"] = {"market": done}
            fx[f"markets/{row['ticker']}?exchange_index=2"] = {"market": done}
        # hourly candles for the settled NFL market (archived by the desk once the position settles)
        nfl_open = parse_ts("2026-09-15T12:00:00Z"); nfl_settle = parse_ts("2026-09-21T15:00:00Z")
        fx[f"series/KXNFLGAME/markets/KXNFLGAME-26SEP20AAABBB-AAA/candlesticks?start_ts={nfl_open - 60}&end_ts={nfl_settle + 60}&period_interval=60"] = {
            "candlesticks": [{"end_period_ts": nfl_open + 3600 * i, "price": {"open_dollars": "0.8000", "high_dollars": "0.9000", "low_dollars": "0.8000", "close_dollars": "0.8800"},
                              "yes_bid": {"close_dollars": "0.8700"}, "yes_ask": {"close_dollars": "0.8900"}, "volume_fp": "100.00", "open_interest_fp": "1000.00"} for i in range(1, 4)]}
    # order books (YES bids / NO bids, ascending, best last)
    fx[f"markets/KXHIGHNY-26SEP20-B72.5/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.50, 200), (0.55, 300)], [(0.35, 150), (0.40, 400)])   # yes ask 0.60
    fx[f"markets/KXHIGHNY-26SEP20-T71/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.04, 500)], [(0.90, 50), (0.94, 2000)])              # yes ask 0.06
    fx[f"markets/KXHIGHNY-26SEP20-B74.5/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.25, 100)], [(0.70, 3000)])                         # yes ask 0.30 / no ask 0.75
    fx[f"markets/KXHIGHNY-26SEP20-T78/orderbook?depth={FD.BOOK_DEPTH}"] = book([], [(0.99, 247.84)])                                  # yes ask 0.01
    fx[f"markets/KXBTC15M-26SEP201515-15/orderbook?depth={FD.BOOK_DEPTH}&exchange_index=2"] = book([(0.75, 800), (0.76, 1200)], [(0.20, 500), (0.22, 900)])  # yes ask 0.78
    fx[f"markets/KXNFLGAME-26SEP20AAABBB-AAA/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.88, 5000)], [(0.10, 9000)])                 # yes ask 0.90
    fx[f"markets/KXFED-26OCT-T4.50/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.02, 1000)], [(0.97, 20000)])                           # yes ask 0.03
    fx[f"markets/KXNFLGAME-26SEP20CCCDDD-CCC/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.78, 4000)], [(0.20, 6000)])                    # yes ask 0.80
    fx[f"markets/KXFDAAPPROVE-TST-26OCT01/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.90, 3000)], [(0.07, 5000)])                      # yes ask 0.93
    return fx


def espn_event(event_id, away, home, away_score, home_score, state="in", detail="End 1st Quarter"):
    """One event in the ESPN scoreboard shape verified live 2026-09-20
    (https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard)."""
    return {"id": event_id, "date": "2026-09-20T23:00Z", "name": f"{away} at {home}", "shortName": f"{away[:3]} @ {home[:3]}",
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": home_score, "winner": False,
                 "team": {"abbreviation": home[:3].upper(), "displayName": f"{home} Team", "shortDisplayName": home,
                          "location": home}},
                {"homeAway": "away", "score": away_score, "winner": True,
                 "team": {"abbreviation": away[:3].upper(), "displayName": f"{away} Team", "shortDisplayName": away,
                          "location": away}}],
                "status": {"type": {"state": state, "shortDetail": detail, "completed": state == "post",
                                    "displayClock": "0:00"}}}]}


def espn_body(state="in"):
    return {"events": [espn_event("401872933", "Alpha", "Bravo", "3", "0", state),
                       espn_event("401872934", "Charlie", "Delta", "21", "7", state)]}


def openfda_body():
    """openFDA Drugs@FDA shape verified live 2026-09-20 (api.fda.gov/drug/drugsfda.json?limit=1)."""
    return {"meta": {"results": {"total": 1, "limit": 5}}, "results": [{
        "application_number": "NDA207871", "sponsor_name": "TEST PHARMA",
        "openfda": {"brand_name": ["TESTDRUG"], "substance_name": ["TESTDRUG"]},
        "submissions": [{"submission_type": "ORIG", "submission_number": "1", "submission_status": "AP",
                         "submission_status_date": "20260101", "review_priority": "PRIORITY"}]}]}


def signal_fetcher(url):
    """One fetcher for every official signal source; unknown URLs raise (the adapter abstains)."""
    body = None
    if "api.weather.gov" in url:
        body = nws_body()
    elif "site.api.espn.com" in url and url.endswith("/injuries"):
        body = injury_body() if "/basketball/" in url else nfl_injury_body()
    elif "site.api.espn.com" in url:
        body = espn_body()
    elif "api.fda.gov" in url:
        body = openfda_body()
    if body is None:
        raise RuntimeError(f"no signal fixture for {url}")
    return body, json.dumps(body, separators=(",", ":")).encode()


def evidence_hashes(base, rel):
    with open(os.path.join(base, rel)) as fh:
        return {json.loads(l)["sha256"] for l in fh}


def injury_body():
    """ESPN league injuries shape verified live 2026-09-22T22:51:42Z (basketball/nba).

    Trimmed to two clubs: the Lakers block carries three hard designations inside the window, the
    Suns block carries one.  Each row keeps ESPN's own status string and observation date, placed
    before the fixture cycle time (T0) exactly as the live rows sit behind the cycle that reads them.
    """
    return {"timestamp": "2026-09-22T22:51:42Z", "status": "success",
            "season": {"year": 2027, "type": 1, "name": "Preseason", "displayName": "2026-27"},
            "injuries": [
                {"id": "13", "displayName": "Los Angeles Lakers", "injuries": [
                    {"id": "1", "status": "Out", "date": "2026-09-19T19:50Z", "shortComment": "torn ACL",
                     "athlete": {"displayName": "Laker One", "position": {"abbreviation": "C"},
                                 "team": {"abbreviation": "LAL"}}},
                    {"id": "2", "status": "Out", "date": "2026-09-20T01:00Z", "shortComment": "ankle",
                     "athlete": {"displayName": "Laker Two", "position": {"abbreviation": "PG"},
                                 "team": {"abbreviation": "LAL"}}},
                    {"id": "3", "status": "Doubtful", "date": "2026-09-20T02:00Z", "shortComment": "wrist",
                     "athlete": {"displayName": "Laker Three", "position": {"abbreviation": "SF"},
                                 "team": {"abbreviation": "LAL"}}}]},
                {"id": "23", "displayName": "Phoenix Suns", "injuries": [
                    {"id": "4", "status": "Out", "date": "2026-09-19T20:00Z", "shortComment": "knee",
                     "athlete": {"displayName": "Suns One", "position": {"abbreviation": "PF"},
                                 "team": {"abbreviation": "PHX"}}}]},
            ]}


def nfl_injury_body():
    """NFL variant: the Bills block carries a quarterback OUT dated inside the 72h window."""
    return {"timestamp": "2026-09-20T14:00:00Z", "status": "success",
            "season": {"year": 2026, "type": 2, "name": "Regular Season", "displayName": "2026"},
            "injuries": [
                {"id": "2", "displayName": "Buffalo Bills", "injuries": [
                    {"id": "5", "status": "Out", "date": "2026-09-20T12:00Z", "shortComment": "shoulder",
                     "athlete": {"displayName": "Bills QB", "position": {"abbreviation": "QB"},
                                 "team": {"abbreviation": "BUF"}}}]},
            ]}


NOWCAST_TABLE_HTML = """
<html><body>
<h2>Inflation, month-over-month percent change</h2>
<table><tr><th>Month</th><th>CPI</th><th>Core CPI</th><th>PCE</th><th>Core PCE</th><th>Updated</th></tr>
<tr><td>September 2026</td><td>0.43</td><td>0.20</td><td>0.40</td><td>0.28</td><td>09/22</td></tr>
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

FRED_BODY = "observation_date,SP500\n2026-09-18,7650.50\n2026-09-21,\nobservation_date,NASDAQ100\n2026-09-18,29644.17\n"


def official_text_fetcher(url):
    """Cleveland Fed HTML and FRED CSV fixtures; anything else raises (the adapter abstains)."""
    if "clevelandfed.org" in url:
        return NOWCAST_TABLE_HTML, NOWCAST_TABLE_HTML.encode()
    if "fred.stlouisfed.org" in url:
        body = ("observation_date,SP500\n2026-09-18,7650.50\n2026-09-21,\n"
                if "id=SP500" in url else "observation_date,NASDAQ100\n2026-09-18,29644.17\n")
        return body, body.encode()
    raise RuntimeError(f"no text fixture for {url}")


def build_new_signal_fixtures():
    """Cycle world for the 2026-09-22 adapters: injuries, CPI nowcast and index range markets."""
    fx = build_fixtures()
    fx["series/KXNBAGAME"] = series("KXNBAGAME", fee_type="quadratic_with_maker_fees", category="Sports")
    fx["series/KXCPI"] = series("KXCPI", category="Economics", title="CPI")
    fx["series/KXINX"] = series("KXINX", category="Financials", title="S&P 500 range")
    nba = [market("KXNBAGAME-26SEP22PHXLAL-PHX", "KXNBAGAME-26SEP22PHXLAL", "2026-09-22T23:00:00Z", 0.50, 0.53,
                  last=0.53, prev=0.52, volume=40000, open_time="2026-09-16T12:00:00Z", yes_sub_title="PHX Suns",
                  rules_primary="If Phoenix wins the PHX Suns vs LAL Lakers Pro Basketball game originally "
                                "scheduled for Sep 22, 2026, then the market resolves to Yes.")]
    nfl = [market("KXNFLGAME-26SEP21DETBUF-DET", "KXNFLGAME-26SEP21DETBUF", "2026-09-21T23:00:00Z", 0.44, 0.47,
                  last=0.47, prev=0.45, volume=60000, open_time="2026-09-15T12:00:00Z", yes_sub_title="DET Lions",
                  rules_primary="If Detroit wins the DET Lions vs BUF Bills Pro Football game originally "
                                "scheduled for Sep 21, 2026, then the market resolves to Yes.")]
    cpi = [market("KXCPI-26SEP-T0.3", "KXCPI-26SEP", "2026-10-13T12:00:00Z", 0.70, 0.73, volume=12000,
                  yes_sub_title="Above 0.3%", strike_type="greater", floor_strike=0.3,
                  title="Will CPI rise more than 0.3% in September 2026?",
                  rules_primary="If the Consumer Price Index (CPI) increases by more than 0.3% (single-decimal) "
                                "in September 2026, then the market resolves to Yes.")]
    inx = [market("KXINX-26SEP22-B7550", "KXINX-26SEP22", "2026-09-22T20:00:00Z", 0.57, 0.60, volume=25000,
                  strike_type="between", floor_strike=7550, cap_strike=7700,
                  title="Will the S&P 500 close between 7550 and 7700 on Sep 22, 2026?")]
    fx["markets?series_ticker=KXNBAGAME&status=open&limit=200"] = {"cursor": "", "markets": nba}
    fx["markets?series_ticker=KXNFLGAME&status=open&limit=200"]["markets"].extend(nfl)
    fx["markets?series_ticker=KXCPI&status=open&limit=200"] = {"cursor": "", "markets": cpi}
    fx["markets?series_ticker=KXINX&status=open&limit=200"] = {"cursor": "", "markets": inx}
    # books reproduce each market's quoted ask (yes ask = 1 - best NO bid), so the desk's
    # book-confirmation step accepts the rule's price instead of downgrading the intent.
    fx[f"markets/KXNBAGAME-26SEP22PHXLAL-PHX/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.50, 3000)], [(0.47, 8000)])
    fx[f"markets/KXNFLGAME-26SEP21DETBUF-DET/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.44, 3000)], [(0.53, 8000)])
    fx[f"markets/KXCPI-26SEP-T0.3/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.70, 4000)], [(0.27, 9000)])
    fx[f"markets/KXINX-26SEP22-B7550/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.57, 4000)], [(0.40, 9000)])
    return fx


class TickModeTests(unittest.TestCase):
    """`--tick`: cheap live-window cycles for in-game markets (2026-09-22)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forward-desk-tick-")
        FD.set_paths(forward_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle_full(self, fixtures, now_ts):
        client = FixtureClient(fixtures)
        index = FD.load_series_index()
        errors = []
        FD.ensure_series(client, index, FS.TRACKED_SERIES + ["KXFDAAPPROVE"], errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=signal_fetcher,
                         text_fetcher=official_text_fetcher)
        cycle.errors.extend(errors)
        summary = cycle.run()
        cycle.persist(summary)
        return cycle, summary

    def run_tick(self, fixtures, now_ts):
        client = FixtureClient(fixtures)
        index = FD.load_series_index()
        errors = []
        FD.ensure_series(client, index, FS.TRACKED_SERIES + ["KXFDAAPPROVE"], errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=signal_fetcher,
                         text_fetcher=official_text_fetcher, tick=True)
        cycle.errors.extend(errors)
        summary = cycle.run()
        cycle.persist(summary)
        return cycle, summary

    def test_tick_only_keeps_markets_closing_inside_the_window(self):
        # T0 is 2026-09-20T15:00Z: the 15-minute BTC market closes at 15:10, the NFL game at 23:00
        # (both inside 3h), the weather market the next morning (outside), the NFL week-2 game too.
        cycle, summary = self.run_tick(build_fixtures(), T0)
        self.assertEqual(summary["tick"], "live")
        self.assertLessEqual(summary["marketsSeen"], 4)
        self.assertTrue(all(m["close_ts"] <= T0 + FD.TICK_LIVE_WINDOW_SECONDS
                            for m in cycle.markets.values()))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "strategies/score-pulse.json")),
                         "a tick must not rebuild the rendered strategy pages")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "equity/2026-09.csv")),
                         "a tick leaves the equity curve to the next full cycle")
        # the append-only record and the account state are still written
        for rel in ("state.json", "trades.jsonl", "cycles/2026-09.jsonl", "sources/status.json"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)

    def test_live_game_tick_fills_in_real_time(self):
        """A tick fired while the game is in progress trades the live-score rule, not 30 minutes later."""
        # 2026-09-20T22:30Z: both NFL fixtures are inside the 3h window (closing 23:00 and 01:00) and
        # ESPN shows Charlie leading Delta 21-7, so the live-score rule trades on the tick itself -
        # no waiting for the next 30-minute slot.  The closed BTC market is not part of the universe.
        cycle, summary = self.run_tick(build_fixtures(), T0 + 27000)
        self.assertEqual(summary["tick"], "live")
        fills = [(e["strategyId"], e["ticker"]) for e in cycle.events if e["kind"] == "fill"]
        self.assertIn(("score-pulse", "KXNFLGAME-26SEP20CCCDDD-CCC"), fills)
        self.assertEqual(len(cycle.markets), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "equity/2026-09.csv")))

    def test_idle_tick_writes_nothing(self):
        """A tick that looks at live markets but has nothing to do must not touch the ledger."""
        cycle, _ = self.run_cycle_full(build_fixtures(), T0)          # the full cycle takes the fills
        before = {rel: (os.path.getsize(os.path.join(self.tmp, rel))
                        if os.path.exists(os.path.join(self.tmp, rel)) else None)
                  for rel in ("trades.jsonl", "equity/2026-09.csv", "state.json")}
        _, summary = self.run_tick(build_fixtures(), T0 + 600)
        self.assertEqual(summary["tick"], "idle")
        after = {rel: (os.path.getsize(os.path.join(self.tmp, rel))
                       if os.path.exists(os.path.join(self.tmp, rel)) else None)
                 for rel in ("trades.jsonl", "equity/2026-09.csv", "state.json")}
        self.assertEqual(before, after, "an idle tick must not grow the ledger")

    def test_tick_with_no_live_market_persists_nothing(self):
        fixtures = build_fixtures()
        quiet = parse_ts("2026-09-23T12:00:00Z")   # all fixture markets have closed by then
        cycle, summary = self.run_tick(fixtures, quiet)
        self.assertEqual(summary["tick"], "no_live_markets")
        self.assertEqual(summary["marketsSeen"], 0)
        self.assertEqual(os.listdir(self.tmp), [], "a quiet tick must leave the ledger untouched")


class NewSignalWiringTests(unittest.TestCase):
    """The 2026-09-22 adapters end to end: capture, archive, trade, and source-status ledger."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forward-desk-new-signals-")
        FD.set_paths(forward_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle(self, fixtures, now_ts):
        client = FixtureClient(fixtures)
        index = FD.load_series_index()
        errors = []
        FD.ensure_series(client, index, FS.TRACKED_SERIES + ["KXFDAAPPROVE"], errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=signal_fetcher,
                         text_fetcher=official_text_fetcher)
        cycle.errors.extend(errors)
        summary = cycle.run()
        FD.save_series_index(index)
        cycle.persist(summary)
        return cycle, summary

    def test_signals_are_captured_archived_and_traded(self):
        cycle, summary = self.run_cycle(build_new_signal_fixtures(), T0)
        self.assertEqual(len(cycle.injuries), 2)                 # PHX vs LAL, DET vs BUF
        self.assertEqual(len(cycle.nowcast), 1)
        self.assertEqual(len(cycle.index_close), 1)
        self.assertEqual(summary["injurySignals"], 2)
        self.assertEqual(summary["nowcastSignals"], 1)
        self.assertEqual(summary["indexCloses"], 1)
        for error in cycle.signal_errors:
            self.assertNotIn("Traceback", error)

        fills = [e for e in cycle.events if e["kind"] == "fill"]
        by_strategy = {}
        for e in fills:
            by_strategy.setdefault(e["strategyId"], []).append(e)

        # TipoffTriage: the Lakers carry three hard designations -> buy the Suns at the 0.53 ask.
        triage = by_strategy.get("tipoff-triage")
        self.assertTrue(triage, f"tipoff-triage did not fill; injuries={cycle.injuries}")
        self.assertEqual(triage[0]["ticker"], "KXNBAGAME-26SEP22PHXLAL-PHX")
        self.assertEqual(triage[0]["side"], "yes")
        self.assertIn("ESPN injuries", triage[0]["reason"])

        # InjuryFade: the Bills' quarterback is OUT inside 72h -> buy the Lions at the 0.47 ask.
        fade = by_strategy.get("injury-fade")
        self.assertTrue(fade, f"injury-fade did not fill; injuries={cycle.injuries}")
        self.assertEqual(fade[0]["ticker"], "KXNFLGAME-26SEP21DETBUF-DET")
        self.assertIn("Bills QB", fade[0]["reason"])

        # NowcastNudge: 0.43% MoM CPI clears the 0.3% strike by the 0.10pp margin -> YES.
        nudge = by_strategy.get("nowcast-nudge")
        self.assertTrue(nudge, f"nowcast-nudge did not fill; nowcast={cycle.nowcast}")
        self.assertEqual(nudge[0]["ticker"], "KXCPI-26SEP-T0.3")
        self.assertEqual(nudge[0]["side"], "yes")
        self.assertIn("Cleveland Fed nowcast", nudge[0]["reason"])

        # LeapMapper: the last published S&P 500 close (7650.50) is inside [7550, 7700] -> YES.
        leap = by_strategy.get("leap-mapper")
        self.assertTrue(leap, f"leap-mapper did not fill; index={cycle.index_close}")
        self.assertEqual(leap[0]["ticker"], "KXINX-26SEP22-B7550")
        self.assertIn("7,650.50", leap[0]["reason"])

        # every one of these fills cites the official feed it read, with the response hash
        expected = {"tipoff-triage": ("espn_injuries", "/injuries"),
                    "injury-fade": ("espn_injuries", "/injuries"),
                    "nowcast-nudge": ("cleveland_fed_nowcast", "clevelandfed.org"),
                    "leap-mapper": ("fred_index", "fred.stlouisfed.org")}
        for strategy_id, (kind, url_part) in expected.items():
            sources = by_strategy[strategy_id][0]["signalSources"]
            match = [source for source in sources if source["kind"] == kind]
            self.assertTrue(match, f"{strategy_id} fill did not cite {kind}: {sources}")
            self.assertIn(url_part, match[0]["url"])
            self.assertEqual(len(match[0]["sha256"]), 64)
        # the sources ledger is also on the persisted position (state.json), same hash
        position = next(p for p in cycle.state["accounts"]["tipoff-triage"]["positions"]
                        if p["ticker"] == "KXNBAGAME-26SEP22PHXLAL-PHX")
        self.assertEqual(position["signalSources"][0]["sha256"],
                         by_strategy["tipoff-triage"][0]["signalSources"][0]["sha256"])

    def test_official_signal_files_and_source_status_are_written(self):
        cycle, _ = self.run_cycle(build_new_signal_fixtures(), T0)
        for rel in ("signals/espn-injuries.jsonl", "signals/cleveland-fed-nowcast.jsonl",
                    "signals/fred-index.jsonl", "sources/status.json"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)
        injury_rows = [json.loads(line) for line in open(os.path.join(self.tmp, "signals/espn-injuries.jsonl"))]
        self.assertEqual({row["league"] for row in injury_rows}, {"KXNFLGAME", "KXNBAGAME"})
        self.assertTrue(all(len(row["sha256"]) == 64 for row in injury_rows))
        fred_rows = [json.loads(line) for line in open(os.path.join(self.tmp, "signals/fred-index.jsonl"))]
        self.assertEqual(fred_rows[0]["seriesId"], "SP500")
        status = FD.read_json(os.path.join(self.tmp, "sources/status.json"))
        names = {row["source"] for row in status["sources"]}
        for wanted in ("ESPN league injuries (public JSON)", "Cleveland Fed Inflation Nowcasting",
                       "FRED (Federal Reserve Bank of St. Louis)", "ESPN scoreboard (public JSON)",
                       "NWS point forecast (KXHIGHNY gridpoint)", "Kalshi Trade API v2"):
            self.assertIn(wanted, names)
        injuries_row = next(row for row in status["sources"] if row["source"] == "ESPN league injuries (public JSON)")
        self.assertEqual(injuries_row["status"], "read")
        self.assertTrue(injuries_row["url"].endswith("/injuries"))
        self.assertEqual(len(injuries_row["sha256"]), 64)


class EngineTests(unittest.TestCase):
    def test_fee_rounds_up_to_grid(self):
        self.assertEqual(taker_fee(0.5, 100), 1.75)
        self.assertEqual(taker_fee(0.99, 100), 0.0693)
        self.assertEqual(taker_fee(0.999, 7), 0.0005)  # 0.000489... rounds up
        self.assertEqual(taker_fee(0.5, 0), 0.0)

    def test_affordable(self):
        q = affordable_contracts(100.0, 0.5)
        self.assertTrue(q * 0.5 + taker_fee(0.5, q) <= 100.0)
        self.assertTrue((q + 1) * 0.5 + taker_fee(0.5, q + 1) > 100.0)

    def test_execute_walks_levels_and_reports_slippage(self):
        b = parse_book(book([(0.50, 10), (0.55, 20)], [(0.35, 5), (0.40, 10)]))
        self.assertEqual(book_quotes(b), {"yes_bid": 0.55, "no_bid": 0.40, "yes_ask": 0.60, "no_ask": 0.45})
        ex = execute(b, "yes", "buy", 12)  # 10 @ 0.60 then 2 @ 0.65
        self.assertEqual(ex["filled"], 12)
        self.assertAlmostEqual(ex["vwap"], (10 * 0.60 + 2 * 0.65) / 12, places=6)
        self.assertEqual(ex["touch"], 0.60)
        self.assertAlmostEqual(ex["slippage_per_contract"], ex["vwap"] - 0.60, places=6)
        ex2 = execute(b, "yes", "buy", 100)
        self.assertEqual(ex2["filled"], 15)
        self.assertEqual(ex2["unfilled"], 85)
        sell = execute(b, "yes", "sell", 25)
        self.assertEqual(sell["filled"], 25)
        self.assertAlmostEqual(sell["vwap"], (20 * 0.55 + 5 * 0.50) / 25, places=6)

    def test_size_and_fill_respects_cash_and_depth(self):
        b = parse_book(book([], [(0.90, 5), (0.94, 20)]))  # yes ask 0.06 for 20 then 0.10 for 5
        ex = size_and_fill(b, "yes", 10.0, 0.5, 1.0)
        self.assertIsNotNone(ex)
        self.assertLessEqual(ex["notional"] + ex["fee"], 5.0 + 1e-9)
        ex_touch = size_and_fill(b, "yes", 1_000_000.0, 0.5, 1.0)  # default limit = touch -> only the 0.06 level
        self.assertEqual(ex_touch["filled"], 20)
        self.assertGreater(ex_touch["unfilled"], 0)
        ex_big = size_and_fill(b, "yes", 1_000_000.0, 0.5, 1.0, limit=0.10)
        self.assertEqual(ex_big["filled"], 25)
        self.assertAlmostEqual(ex_big["vwap"], (20 * 0.06 + 5 * 0.10) / 25, places=6)
        ex_lim = execute(b, "yes", "buy", 100, limit=0.05)  # limit below the touch -> nothing fills
        self.assertEqual(ex_lim["filled"], 0)

    def test_normalize_market_reads_fp_fields(self):
        m = normalize_market(market("X-1", "X", "2026-09-21T05:00:00Z", 0.70, 0.71, volume=3483.61, strike_type="less", cap_strike=71))
        self.assertEqual(m["yes_bid"], 0.70)
        self.assertEqual(m["no_ask"], 0.30)
        self.assertEqual(m["cap_strike"], 71)
        self.assertEqual(m["close_ts"], parse_ts("2026-09-21T05:00:00Z"))


class DeskCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forward-desk-test-")
        FD.set_paths(forward_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle(self, fixtures, now_ts):
        client = FixtureClient(fixtures)
        index = FD.load_series_index()
        errors = []
        FD.ensure_series(client, index, FS.TRACKED_SERIES + ["KXFDAAPPROVE"], errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=signal_fetcher)
        cycle.errors.extend(errors)
        summary = cycle.run()
        FD.save_series_index(index)
        cycle.persist(summary)
        return cycle, summary

    def test_two_cycle_lifecycle(self):
        cycle, summary = self.run_cycle(build_fixtures(), T0)
        self.assertEqual(summary["marketsSeen"], 9)
        self.assertTrue(summary["nwsCaptured"])
        fills = [e for e in cycle.events if e["kind"] == "fill"]
        by_strategy = {}
        for e in fills:
            by_strategy.setdefault(e["strategyId"], []).append(e)
        # Weather: the NWS high (73F) is inside the 72-73 bracket at a 0.60 ask (<= 0.70) -> WeatherCatalyst fills.
        wx = by_strategy.get("weather-bracket")
        self.assertTrue(wx and wx[0]["ticker"] == "KXHIGHNY-26SEP20-B72.5" and wx[0]["side"] == "yes")
        self.assertEqual(wx[0]["entryTouch"], 0.60)
        # WeatherFader: T78 (>78) is 5F away -> NO ask 0.99 > 0.92 -> no trade; T71 (<71) is 2F away -> no trade.
        self.assertNotIn("weather-fade", by_strategy)
        # Favourite personas: GridironPulse buys the 0.90 NFL favourite (close in 8h, volume 250k).
        self.assertEqual(by_strategy["game-favourite"][0]["ticker"], "KXNFLGAME-26SEP20AAABBB-AAA")
        # Scalp persona: BTC15M at 0.78 within 75-80c band with 10 minutes left.
        self.assertEqual(by_strategy["scalp-8095"][0]["entryTouch"], 0.78)
        # 24h momentum: the NFL market is 5 days old with last 0.90 vs 0.80 a day ago -> TickChaser buys YES;
        # the 15-minute BTC market has no day-ago trade, so momentum personas must ignore it.
        tick = by_strategy["tick-chaser"]
        self.assertEqual([t["ticker"] for t in tick], ["KXNFLGAME-26SEP20AAABBB-AAA"])
        self.assertEqual(tick[0]["limitPrice"], 0.92)
        # Cheap tail personas: DipHunter picks the 1c T78 ticket or the 3c Fed ticket, sized to depth.
        self.assertIn("dip-hunter", by_strategy)
        # ScorePulse: ESPN shows the market's team leading 21-7 (>= 8) -> YES at the 0.80 touch.
        pulse = by_strategy.get("score-pulse")
        self.assertTrue(pulse, f"score-pulse did not fill; espn signals={cycle.espn}; errors={cycle.signal_errors}")
        self.assertEqual(pulse[0]["ticker"], "KXNFLGAME-26SEP20CCCDDD-CCC")
        self.assertEqual(pulse[0]["side"], "yes")
        self.assertEqual(pulse[0]["entryTouch"], 0.80)
        self.assertIn("ESPN scoreboard", pulse[0]["reason"])
        # FdaRecordCheck: openFDA Drugs@FDA lists an approved application -> YES at the 0.93 touch.
        record = by_strategy.get("fda-record")
        self.assertTrue(record, f"fda-record did not fill; fda signals={cycle.fda}; errors={cycle.signal_errors}")
        self.assertEqual(record[0]["ticker"], "KXFDAAPPROVE-TST-26OCT01")
        self.assertEqual(record[0]["side"], "yes")
        self.assertIn("openFDA Drugs@FDA", record[0]["reason"])
        # Committed per-strategy page + daily summary for the site.
        for rel in ("strategies/score-pulse.json", "strategies/weather-bracket.json", "summary/2026-09-20.json"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)
        page = FD.read_json(os.path.join(self.tmp, "strategies/score-pulse.json"))
        self.assertEqual(page["strategy"]["username"], "ScorePulse")
        self.assertEqual(page["account"]["fills"], len([e for e in cycle.events
                                                        if e["strategyId"] == "score-pulse" and e["kind"] == "fill"]))
        self.assertTrue(page["analysis"] and page["equity"])
        self.assertTrue(all(e["ledgerFile"] == "trades.jsonl" and isinstance(e["ledgerLine"], int) for e in page["events"]))
        self.assertTrue(all(i["ledgerFile"].startswith("intents/") and isinstance(i["ledgerLine"], int) for i in page["intents"]))
        self.assertIn("archiveBacktest", page)
        self.assertFalse(page["archiveBacktest"]["available"], "isolated fixture has no archive directory")
        day = FD.read_json(os.path.join(self.tmp, "summary/2026-09-20.json"))
        self.assertEqual(day["fills"], sum(1 for e in cycle.events if e["kind"] == "fill"))
        # Ledger invariants per account: cash + entry notional + fees == starting cash (nothing realized yet).
        for strategy_id, account in cycle.state["accounts"].items():
            spent = sum(p["entryNotional"] + p["entryFee"] for p in account["positions"])
            self.assertAlmostEqual(account["cash"] + spent, FD.STARTING_CASH, places=4, msg=strategy_id)
            for p in account["positions"]:
                self.assertTrue(os.path.exists(os.path.join(self.tmp, p["evidence"]["file"])))
                self.assertGreater(p["contracts"], 0)
                self.assertEqual(len(p["evidence"]["sha256"]), 64)
                self.assertIn(p["evidence"]["sha256"], evidence_hashes(self.tmp, p["evidence"]["file"]))
        # Files written
        for rel in ("state.json", "leaderboard.json", "trades.jsonl", "intents/2026-09.jsonl", "equity/2026-09.csv",
                    "cycles/2026-09.jsonl", "quotes/2026-09-20.csv", "signals/nws-central-park.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, rel)), rel)
        with open(os.path.join(self.tmp, "intents/2026-09.jsonl")) as fh:
            intents = [json.loads(l) for l in fh]
        self.assertTrue(all(i["status"] != "proposed" for i in intents))
        self.assertTrue(any(i["status"] == "filled" for i in intents))
        # Cycle 2: everything settled -> positions close at $1/$0 with official result rows.
        cycle2, summary2 = self.run_cycle(build_fixtures(settled=True), T0 + 86400)
        self.assertEqual(summary2["settlements"], sum(1 for e in cycle.events if e["kind"] == "fill"))
        for strategy_id, account in cycle2.state["accounts"].items():
            self.assertEqual(account["positions"], [], strategy_id)
            self.assertAlmostEqual(account["cash"], FD.STARTING_CASH + account["realizedPnl"], places=4, msg=strategy_id)
        settle_events = [e for e in cycle2.events if e["kind"] == "settlement"]
        wx_settle = [e for e in settle_events if e["strategyId"] == "weather-bracket"][0]
        self.assertEqual(wx_settle["result"], "yes")
        self.assertGreater(wx_settle["pnl"], 0)
        self.assertIn(wx_settle["evidence"]["sha256"], evidence_hashes(self.tmp, wx_settle["evidence"]["file"]))
        # order-book evidence rows are self-verifying: sha256(raw) == recorded sha256
        import hashlib
        with open(os.path.join(self.tmp, "evidence/2026-09-20.jsonl")) as fh:
            rows = [json.loads(l) for l in fh]
        books = [r for r in rows if r["kind"] == "orderbook"]
        self.assertTrue(books)
        for r in books:
            self.assertEqual(hashlib.sha256(r["raw"].encode()).hexdigest(), r["sha256"])
        markets = [r for r in rows if r["kind"] == "market"]
        self.assertTrue(all(r["projection"]["result"] in ("yes", "no") for r in markets))
        board = FD.read_json(os.path.join(self.tmp, "leaderboard.json"))
        self.assertEqual(board["rows"][0]["rank"], 1)
        self.assertTrue(all("analysis" in r and r["analysis"] for r in board["rows"]))
        filled_rows = [r for r in board["rows"] if r["fills"]]
        self.assertTrue(all(r["firstFill"]["ledgerFile"] == "trades.jsonl" and isinstance(r["firstFill"]["ledgerLine"], int) for r in filled_rows))
        self.assertTrue(all(p.get("ledger", {}).get("fillLine") for a in cycle2.state["accounts"].values() for p in a["positions"]))
        # settled NFL market's official candles were archived once (period 60) and indexed with the response hash
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "candles/KXNFLGAME/KXNFLGAME-26SEP20AAABBB-AAA-p60.csv")))
        with open(os.path.join(self.tmp, "candles/index.jsonl")) as fh:
            archived = [json.loads(l) for l in fh]
        self.assertEqual([a["ticker"] for a in archived], ["KXNFLGAME-26SEP20AAABBB-AAA"])
        self.assertEqual(archived[0]["bars"], 3)
        self.assertEqual(sum(r["fills"] for r in board["rows"]), len(fills))
        # attribution: equity == cash when flat, and cash == start + realized
        for row in board["rows"]:
            self.assertAlmostEqual(row["equity"], row["cash"], places=2)

    def test_fixture_cycle_never_writes_committed_universe_caches(self):
        """IRR-40: a fixture cycle's adapter caches must land under the test universe dir, never
        in the committed data/universe/ (a fixture-written cache would look like verified data)."""
        committed = os.path.join(ROOT, "data", "universe")
        snapshot = {}
        for name in ("fda-records.json", "place-centroids.json", "nws-gridpoints.json"):
            path = os.path.join(committed, name)
            snapshot[name] = (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
        self.run_cycle(build_fixtures(), T0)  # fixture includes an open KXFDA market -> FDA adapter fires
        for name, (existed, mtime) in snapshot.items():
            path = os.path.join(committed, name)
            self.assertEqual(os.path.exists(path), existed, f"committed {name} created or deleted by a fixture cycle")
            if existed:
                self.assertEqual(os.path.getmtime(path), mtime, f"committed {name} modified by a fixture cycle")
        # and the fixture's own cache sits next to its own ledger
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "universe", "fda-records.json")))
        record = FD.read_json(os.path.join(self.tmp, "universe", "fda-records.json"))
        self.assertTrue(record["records"], "fixture cycle left no FDA cache under the test universe dir")

    def test_scalar_settlement_uses_official_value(self):
        cycle, _ = self.run_cycle(build_fixtures(), T0)
        fx = build_fixtures(settled=True)
        # A tie: no yes/no result, settlement_value 0.50 for the YES side (official rules_secondary for game markets).
        tie = dict(fx["markets/KXNFLGAME-26SEP20AAABBB-AAA"]["market"])
        tie.update({"result": "", "settlement_value_dollars": "0.5000"})
        fx["markets/KXNFLGAME-26SEP20AAABBB-AAA"] = {"market": tie}
        cycle2, _ = self.run_cycle(fx, T0 + 86400)
        events = [e for e in cycle2.events if e["kind"] == "settlement" and e["ticker"] == "KXNFLGAME-26SEP20AAABBB-AAA"]
        self.assertTrue(events)
        for e in events:
            self.assertEqual(e["result"], "value:0.5000")
            self.assertAlmostEqual(e["exitPrice"], 0.5, places=6)
            self.assertAlmostEqual(e["pnl"], 0.5 * e["contracts"] - [p for p in cycle.state["accounts"][e["strategyId"]]["positions"] if p["id"] == e["positionId"]][0]["entryNotional"] - [p for p in cycle.state["accounts"][e["strategyId"]]["positions"] if p["id"] == e["positionId"]][0]["entryFee"], places=4)

    def test_rule_must_confirm_on_fresh_book(self):
        fx = build_fixtures()
        # Make the NFL book contradict the list quote (fresh ask 0.99 -> outside the 0.80-0.95 band).
        fx[f"markets/KXNFLGAME-26SEP20AAABBB-AAA/orderbook?depth={FD.BOOK_DEPTH}"] = book([(0.88, 5000)], [(0.01, 9000)])
        cycle, _ = self.run_cycle(fx, T0)
        intents = [i for i in cycle.intents if i["strategyId"] == "game-favourite"]
        self.assertTrue(intents and intents[0]["status"] == "not_confirmed_on_book")
        self.assertFalse(any(e["strategyId"] == "game-favourite" for e in cycle.events))

    def test_partial_exit_is_refused(self):
        fx = build_fixtures()
        cycle, _ = self.run_cycle(fx, T0)
        pos = [p for p in cycle.state["accounts"]["scalp-8095"]["positions"]][0]
        # Cycle 2 (still open): bid jumps to 0.96 but only 1 contract of depth -> exit refused, position held.
        fx2 = build_fixtures()
        fx2["markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2"]["markets"][0].update(
            {"yes_bid_dollars": "0.9600", "yes_ask_dollars": "0.9700", "close_time": "2026-09-20T16:10:00Z"})
        fx2[f"markets/KXBTC15M-26SEP201515-15/orderbook?depth={FD.BOOK_DEPTH}&exchange_index=2"] = book([(0.96, 1)], [(0.03, 100)])
        cycle2, _ = self.run_cycle(fx2, T0 + 1800)
        still = [p for p in cycle2.state["accounts"]["scalp-8095"]["positions"] if p["id"] == pos["id"]]
        self.assertTrue(still and "lastCloseError" in still[0])
        # Cycle 3: enough depth at 0.96 -> full exit at the bid ladder, fee charged, pnl booked.
        fx3 = build_fixtures()
        fx3["markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2"]["markets"][0].update(
            {"yes_bid_dollars": "0.9600", "yes_ask_dollars": "0.9700", "close_time": "2026-09-20T17:10:00Z"})
        fx3[f"markets/KXBTC15M-26SEP201515-15/orderbook?depth={FD.BOOK_DEPTH}&exchange_index=2"] = book([(0.95, 10000), (0.96, 10000)], [(0.03, 100)])
        cycle3, _ = self.run_cycle(fx3, T0 + 3600)
        exits = [e for e in cycle3.events if e["kind"] == "exit" and e["strategyId"] == "scalp-8095"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["exitTouch"], 0.96)
        self.assertGreater(exits[0]["pnl"], 0)


class HeatConfirmTests(unittest.TestCase):
    """The new persona's rule functions, exercised offline against synthetic NWS forecasts."""

    def market(self, **over):
        m = {"ticker": "KXHIGHTATL-26SEP21-B80.5", "event_ticker": "KXHIGHTATL-26SEP21",
             "series_ticker": "KXHIGHTATL", "strike_type": "between", "floor_strike": 80.5, "cap_strike": 81.5,
             "yes_ask": 0.40, "yes_bid": 0.38}
        m.update(over)
        return m

    def ctx(self, high_f):
        return {"nws": {"KXHIGHTATL-26SEP21": {"high_f": high_f, "date": "2026-09-21", "updated": "t",
                                               "source": "https://api.weather.gov/gridpoints/HEC/60,95/forecast"}}}

    def test_entry_requires_forecast_and_price_and_spread(self):
        sig = FS.entry_heat_confirm(self.market(), self.ctx(81.0))
        self.assertIsNotNone(sig)
        self.assertEqual((sig["side"], sig["price"], sig["limit"]), ("yes", 0.40, 0.42))
        self.assertEqual(sig["meta"]["forecastHighF"], 81.0)
        self.assertEqual(sig["meta"]["capStrike"], 81.5)
        # forecast at exactly 77F is NOT above the community threshold
        self.assertIsNone(FS.entry_heat_confirm(self.market(floor_strike=76.5, cap_strike=77.5), self.ctx(77.0)))
        # 42c ask is not "below 42c"
        self.assertIsNone(FS.entry_heat_confirm(self.market(yes_ask=0.42), self.ctx(81.0)))
        # spread 9c > 8c
        self.assertIsNone(FS.entry_heat_confirm(self.market(yes_bid=0.31), self.ctx(81.0)))
        # forecast above the threshold but outside the bracket -> no confirmation -> no trade
        self.assertIsNone(FS.entry_heat_confirm(self.market(), self.ctx(78.5)))
        # no forecast at all (adapter gap) -> abstain, never trade on price alone
        self.assertIsNone(FS.entry_heat_confirm(self.market(), {"nws": {}}))

    def test_exit_only_when_the_fresh_forecast_left_the_bracket(self):
        sig = FS.entry_heat_confirm(self.market(), self.ctx(81.0))
        position = {"eventTicker": "KXHIGHTATL-26SEP21", "side": "yes", "entryPrice": 0.40,
                    "signalMeta": dict(sig["meta"])}
        self.assertIsNone(FS.exit_heat_forecast_cools(position, {"yes_bid": 0.50}, self.ctx(81.0)))  # still inside
        reason = FS.exit_heat_forecast_cools(position, {"yes_bid": 0.50}, self.ctx(75.0))
        self.assertIsNotNone(reason)
        self.assertIn("no longer falls in the entered bracket", reason)
        # no fresh forecast -> hold; a missing adapter must never force an exit
        self.assertIsNone(FS.exit_heat_forecast_cools(position, {"yes_bid": 0.50}, {"nws": {}}))

    def test_desk_copies_signal_meta_into_the_position(self):
        # open_position() is the one place the forward desk builds a position; the meta must survive
        with open(os.path.join(ROOT, "scripts", "forward_desk.py")) as fh:
            src = fh.read()
        self.assertIn('"signalMeta": dict(signal.get("meta") or {})', src)


class SportsExpansionTests(unittest.TestCase):
    """KXEPLGAME / KXNCAAMBGAME joined the sports universe on 2026-09-22 (IRR-37)."""

    def test_new_series_are_tracked_with_thresholds(self):
        for series in ("KXEPLGAME", "KXNCAAMBGAME"):
            self.assertIn(series, FS.SERIES_SPORTS)
            self.assertIn(series, FS.LIVE_SCORE_THRESHOLDS)
        self.assertEqual(FS.LIVE_SCORE_THRESHOLDS["KXEPLGAME"], 2.0)      # goals, like hockey
        self.assertEqual(FS.LIVE_SCORE_THRESHOLDS["KXNCAAMBGAME"], 10.0)  # points, like NBA/WNBA
        self.assertEqual(len(FS.SERIES_SPORTS), 8)

    def market(self, series, ticker, yes_ask=0.80, yes_bid=0.78):
        return {"series_ticker": series, "ticker": ticker, "yes_ask": yes_ask, "yes_bid": yes_bid}

    def ctx(self, ticker, diff):
        return {"espn": {ticker: {"state": "in", "scoreDiff": diff, "detail": "2nd Half",
                                  "espnEventId": "1", "away": "A", "home": "H",
                                  "awayScore": 3.0, "homeScore": 1.0, "marketSide": "away"}}}

    def test_epl_two_goal_lead_fires_one_goal_and_draws_abstain(self):
        ticker = "KXEPLGAME-26SEP20LIVBOU-LIV"
        sig = FS.entry_live_score(self.market("KXEPLGAME", ticker), self.ctx(ticker, 2.0))
        self.assertIsNotNone(sig)
        self.assertEqual((sig["side"], sig["limit"]), ("yes", 0.85))
        self.assertIn(">= 2.0", sig["reason"])
        # a 1-goal lead and a tied game are below the soccer threshold
        self.assertIsNone(FS.entry_live_score(self.market("KXEPLGAME", ticker), self.ctx(ticker, 1.0)))
        self.assertIsNone(FS.entry_live_score(self.market("KXEPLGAME", ticker), self.ctx(ticker, 0.0)))

    def test_ncaam_ten_point_lead_fires_nine_abstains(self):
        ticker = "KXNCAAMBGAME-26FEB15INDILL-ILL"
        sig = FS.entry_live_score(self.market("KXNCAAMBGAME", ticker), self.ctx(ticker, 10.0))
        self.assertIsNotNone(sig)
        self.assertEqual((sig["side"], sig["limit"]), ("yes", 0.85))
        self.assertIsNone(FS.entry_live_score(self.market("KXNCAAMBGAME", ticker), self.ctx(ticker, 9.0)))

    def test_price_and_spread_caps_still_apply_to_new_leagues(self):
        ticker = "KXEPLGAME-26SEP20LIVBOU-LIV"
        # above the 85c cap -> no trade even with a big lead
        self.assertIsNone(FS.entry_live_score(self.market("KXEPLGAME", ticker, yes_ask=0.90, yes_bid=0.89),
                                              self.ctx(ticker, 3.0)))
        # spread 10c > 5c -> no trade
        self.assertIsNone(FS.entry_live_score(self.market("KXEPLGAME", ticker, yes_ask=0.80, yes_bid=0.70),
                                              self.ctx(ticker, 3.0)))
        # a final (not live) scoreboard never fires
        ctx = self.ctx(ticker, 3.0)
        ctx["espn"][ticker]["state"] = "post"
        self.assertIsNone(FS.entry_live_score(self.market("KXEPLGAME", ticker), ctx))


class ZeroFillRuleTests(unittest.TestCase):
    """Can-fire proofs for the personas with zero committed intents (prior-session follow-up).

    FdaRecordCheck, WeatherFader (and HeatConfirm, covered above) have no ledger intents because
    their rules never triggered on the tracked universe - not because the rules cannot fire.
    These tests prove each rule fires on the adapter shape it requires and abstains otherwise."""

    def test_fda_record_fires_only_on_an_approved_record(self):
        ticker = "KXFDAAPPROVE-26OCT01-CYTI"
        market = {"series_ticker": "KXFDAAPPROVE", "ticker": ticker, "yes_ask": 0.90, "yes_bid": 0.88}
        approved = {"fda": {ticker: {"drug": "cytisinicline", "code": None, "approvedRecord": True, "hits": 1,
                                     "applications": [{"application_number": "NDA207871",
                                                       "first_orig_approved": "20260301"}]}}}
        sig = FS.entry_fda_record(market, approved)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "yes")
        self.assertIn("NDA207871", sig["reason"])
        # a verified absence is never traded
        denied = {"fda": {ticker: {"drug": "retatrutide", "approvedRecord": False, "hits": 0, "applications": []}}}
        self.assertIsNone(FS.entry_fda_record(market, denied))
        # no adapter entry (no drug named / lookup failed) -> abstain
        self.assertIsNone(FS.entry_fda_record(market, {"fda": {}}))
        # above the 97c cap -> no trade even with a record
        rich = dict(market, yes_ask=0.98, yes_bid=0.97)
        self.assertIsNone(FS.entry_fda_record(rich, approved))

    def test_weather_fade_fires_only_far_from_the_forecast(self):
        event = "KXHIGHCHI-26SEP21"
        market = {"series_ticker": "KXHIGHCHI", "ticker": f"{event}-B80", "event_ticker": event,
                  "strike_type": "between", "floor_strike": 80.0, "cap_strike": 81.0,
                  "no_ask": 0.90, "no_bid": 0.88}
        cold = {"nws": {event: {"high_f": 70.0, "date": "2026-09-21", "updated": "t"}}}
        sig = FS.entry_weather_fade(market, cold)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["side"], "no")
        self.assertIn("10F away", sig["reason"])
        # the forecast sits inside the bracket -> nothing to fade
        hot = {"nws": {event: {"high_f": 80.5, "date": "2026-09-21", "updated": "t"}}}
        self.assertIsNone(FS.entry_weather_fade(market, hot))
        # no forecast -> abstain, never fade on price alone
        self.assertIsNone(FS.entry_weather_fade(market, {"nws": {}}))


class MakerQuotePlanTests(unittest.TestCase):
    """SpreadSmith quote plans: upcoming maker trades, never fills (measured later against the tape)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="maker-plans-test-")
        FD.set_paths(forward_dir=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle(self, fixtures, now_ts):
        client = FixtureClient(fixtures)
        index = FD.load_series_index()
        errors = []
        FD.ensure_series(client, index, FS.TRACKED_SERIES + ["KXFDAAPPROVE"], errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=signal_fetcher)
        cycle.errors.extend(errors)
        summary = cycle.run()
        FD.save_series_index(index)
        cycle.persist(summary)
        return cycle, summary

    def test_quote_plans_are_emitted_from_captured_books_and_never_fill(self):
        cycle, summary = self.run_cycle(build_fixtures(), T0)
        plans = [i for i in cycle.intents if i.get("status") == "quote_plan"]
        self.assertTrue(plans)
        self.assertLessEqual(len(plans), FS.MAKER_MAX_PLANS_PER_CYCLE)
        self.assertEqual(summary["makerQuotePlans"], len(plans))
        for plan in plans:
            self.assertEqual(plan["strategyId"], "spread-smith")
            self.assertEqual(plan["username"], "SpreadSmith")
            self.assertIsNone(plan["positionId"])
            self.assertGreaterEqual(plan["spreadAtPost"], FS.MAKER_MIN_SPREAD)
            self.assertGreater(plan["postedContracts"], 0)
            self.assertGreater(plan["postedPrice"], 0)
            self.assertIn("a plan, not a fill", plan["reason"])
            # the posted price is 1c inside the touch on the quoted side
            side = plan["side"]
            quotes = plan["bookQuotes"]
            self.assertAlmostEqual(plan["postedPrice"], quotes[f"{side}_bid"] + FS.MAKER_IMPROVEMENT, places=4)
        # a quote plan is not a fill and never touches cash: no account exists for the plan-only persona
        self.assertNotIn("spread-smith", cycle.state["accounts"])
        fills = [e for e in cycle.events if e["kind"] == "fill"]
        self.assertFalse([e for e in fills if e.get("strategyId") == "spread-smith"])

    def test_quote_plans_persist_with_ledger_anchors_and_dedupe(self):
        cycle, _ = self.run_cycle(build_fixtures(), T0)
        path = os.path.join(self.tmp, "intents", "2026-09.jsonl")
        rows = [json.loads(l) for l in open(path) if l.strip()]
        plans = [r for r in rows if r.get("status") == "quote_plan"]
        self.assertTrue(plans)
        for index, row in enumerate(rows, 1):
            self.assertEqual(row["ledgerFile"], os.path.join("intents", "2026-09.jsonl"))
            self.assertEqual(row["ledgerLine"], index)
        # a second identical cycle must not duplicate an unchanged plan (fingerprint dedupe)
        self.run_cycle(build_fixtures(), T0 + 1800)
        rows2 = [json.loads(l) for l in open(path) if l.strip()]
        plans2 = [r for r in rows2 if r.get("status") == "quote_plan"]
        keys = [(r["ticker"], r["side"]) for r in plans2]
        self.assertEqual(len(keys), len(set(keys)), "an unchanged quote plan must be persisted once")

    def test_narrow_spread_market_produces_no_plan(self):
        # The KXFED fixture book is 0.02/0.03 on YES (1c spread): below MAKER_MIN_SPREAD.
        signal = FS.maker_quote_plan({"series_ticker": "KXFED", "ticker": "KXFED-26OCT-T4.50",
                                      "yes_bid": 0.02, "yes_ask": 0.03, "no_bid": 0.97, "no_ask": 0.98},
                                     {"book": parse_book(book([(0.02, 1000)], [(0.97, 20000)]))})
        self.assertIsNone(signal)

    def test_tight_posted_price_that_would_cross_is_skipped(self):
        # 1c spread: bid+1c would equal the ask -> no resting improvement exists.
        signal = FS.maker_quote_plan({"series_ticker": "X", "ticker": "X-1",
                                      "yes_bid": 0.50, "yes_ask": 0.51, "no_bid": 0.49, "no_ask": 0.50},
                                     {"book": parse_book(book([(0.50, 10)], [(0.49, 10)]))})
        self.assertIsNone(signal)

    def test_posted_price_stays_below_the_ask_at_the_minimum_spread(self):
        signal = FS.maker_quote_plan({"series_ticker": "X", "ticker": "X-1B",
                                      "yes_bid": 0.50, "yes_ask": 0.52, "no_bid": 0.48, "no_ask": 0.50},
                                     {"book": parse_book(book([(0.50, 10)], [(0.48, 10)]))})
        self.assertIsNotNone(signal)
        self.assertLess(signal["meta"]["postedPrice"], 0.52)
        self.assertEqual(signal["meta"]["postedPrice"], 0.51)

    def test_plan_size_joins_the_displayed_best_bid_queue(self):
        signal = FS.maker_quote_plan({"series_ticker": "X", "ticker": "X-2",
                                      "yes_bid": 0.55, "yes_ask": 0.60, "no_bid": 0.40, "no_ask": 0.45},
                                     {"book": parse_book(book([(0.50, 20), (0.55, 77)], [(0.35, 5), (0.40, 9)]))})
        self.assertIsNotNone(signal)
        self.assertEqual(signal["meta"]["postedContracts"], 77)  # size resting at the 0.55 best bid
        self.assertEqual(signal["meta"]["postedPrice"], 0.56)
        self.assertEqual(signal["meta"]["spreadAtPost"], 0.05)


class HalftimeHypeTests(unittest.TestCase):
    """The r/Kalshi 'buy 20-30%, sell near 50%' tip, recreated mechanically (HalftimeHype)."""

    def market(self, **extra):
        row = {"series_ticker": "KXNFLGAME", "ticker": "KXNFLGAME-26SEP20AAABBB-AAA",
               "event_ticker": "KXNFLGAME-26SEP20AAABBB", "title": "Alpha wins",
               "volume": 250000, "yes_bid": 0.72, "yes_ask": 0.75, "no_bid": 0.25, "no_ask": 0.28}
        row.update(extra)
        return row

    def espn(self, state="in", score_diff=-3):
        return {"KXNFLGAME-26SEP20AAABBB-AAA": {"state": state, "scoreDiff": score_diff, "detail": "2nd Quarter"}}

    def test_fires_on_the_25c_underdog_while_the_game_is_live(self):
        signal = FS.entry_halftime_hype(self.market(), {"espn": self.espn()})
        self.assertIsNotNone(signal)
        self.assertEqual(signal["side"], "no")
        self.assertEqual(signal["limit"], 0.30)
        self.assertIn("target a 0.50 bid", signal["reason"])
        self.assertEqual(signal["meta"]["source"],
                         "r/Kalshi 2026-01-15 tip, recreated mechanically (discovery-only source)")

    def test_abstains_before_the_game_and_without_a_mapping(self):
        self.assertIsNone(FS.entry_halftime_hype(self.market(), {"espn": self.espn(state="pre")}))
        self.assertIsNone(FS.entry_halftime_hype(self.market(), {"espn": {}}))
        self.assertIsNone(FS.entry_halftime_hype(self.market(), {}))

    def test_price_band_and_spread_and_volume_gates(self):
        # 35c ask is outside the 20-30c band
        self.assertIsNone(FS.entry_halftime_hype(
            self.market(no_bid=0.30, no_ask=0.35, yes_bid=0.65, yes_ask=0.70), {"espn": self.espn()}))
        # 8c displayed spread is too wide (IRR-25 rule)
        self.assertIsNone(FS.entry_halftime_hype(
            self.market(no_bid=0.20, no_ask=0.28, yes_bid=0.72, yes_ask=0.80), {"espn": self.espn()}))
        # thin market
        self.assertIsNone(FS.entry_halftime_hype(self.market(volume=9999), {"espn": self.espn()}))

    def test_exit_is_the_first_verified_bid_at_50c(self):
        strategy = next(s for s in FS.STRATEGIES if s["id"] == "halftime-hype")
        position = {"side": "no", "entryPrice": 0.25}
        self.assertIsNone(strategy["exit"](position, {"no_bid": 0.49}, {}))
        reason = strategy["exit"](position, {"no_bid": 0.50}, {})
        self.assertIsNotNone(reason)


if __name__ == "__main__":
    unittest.main()
