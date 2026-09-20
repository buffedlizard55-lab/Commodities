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


def series(ticker, exchange_index=0, fee_type="quadratic", tags=None, category="Climate and Weather"):
    return {"series": {"ticker": ticker, "title": ticker, "category": category, "categories": [category], "tags": tags or [],
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
        "series/KXHIGHNY": series("KXHIGHNY"),
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
                  volume=250000, open_time="2026-09-15T12:00:00Z")]
    fed = [market("KXFED-26OCT-T4.50", "KXFED-26OCT", "2026-10-28T18:00:00Z", 0.02, 0.03, volume=40000)]
    if not settled:
        fx["markets?series_ticker=KXHIGHNY&status=open&limit=200"] = {"cursor": "", "markets": wx}
        fx["markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2"] = {"cursor": "", "markets": btc}
        fx["markets?series_ticker=KXNFLGAME&status=open&limit=200"] = {"cursor": "", "markets": nfl}
        fx["markets?series_ticker=KXFED&status=open&limit=200"] = {"cursor": "", "markets": fed}
    else:
        for key in ("markets?series_ticker=KXHIGHNY&status=open&limit=200", "markets?series_ticker=KXBTC15M&status=open&limit=200&exchange_index=2",
                    "markets?series_ticker=KXNFLGAME&status=open&limit=200", "markets?series_ticker=KXFED&status=open&limit=200"):
            fx[key] = {"cursor": "", "markets": []}
        outcomes = {"KXHIGHNY-26SEP20-T71": "no", "KXHIGHNY-26SEP20-B72.5": "yes", "KXHIGHNY-26SEP20-B74.5": "no",
                    "KXHIGHNY-26SEP20-T78": "no", "KXBTC15M-26SEP201515-15": "yes", "KXNFLGAME-26SEP20AAABBB-AAA": "yes",
                    "KXFED-26OCT-T4.50": "no"}
        for row in wx + btc + nfl + fed:
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
    return fx


def evidence_hashes(base, rel):
    with open(os.path.join(base, rel)) as fh:
        return {json.loads(l)["sha256"] for l in fh}


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
        FD.ensure_series(client, index, FS.TRACKED_SERIES, errors)
        state = FD.read_json(os.path.join(self.tmp, "state.json")) or FD.new_state(now_ts)
        body = nws_body()
        cycle = FD.Cycle(client, now_ts, index, state, nws_fetcher=lambda url: (body, json.dumps(body).encode()))
        cycle.errors.extend(errors)
        summary = cycle.run()
        FD.save_series_index(index)
        cycle.persist(summary)
        return cycle, summary

    def test_two_cycle_lifecycle(self):
        cycle, summary = self.run_cycle(build_fixtures(), T0)
        self.assertEqual(summary["marketsSeen"], 7)
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


if __name__ == "__main__":
    unittest.main()
