#!/usr/bin/env python3
"""Forward-test desk: one scheduled cycle of the paper-trading competition.

Runs unattended (GitHub Actions cron) and does, in order:
  1. Read the committed desk state (cash + open paper positions per strategy).
  2. Fetch the tracked universe of OPEN markets from Kalshi's public API (official quotes).
  3. Capture point-in-time signals (NWS forecast for the KXHIGHNY weather bracket personas,
     verified daily candlesticks for the technical persona).
  4. Reconcile open positions: official settlement (result + settlement_ts) -> $1/$0 payout;
     rule-based exits ONLY when the fresh bid ladder can absorb the whole position.
  5. Evaluate every strategy rule on the fresh quotes, record the upcoming intents, fetch the
     fresh order book for the chosen candidates and simulate taker fills level by level (VWAP,
     slippage vs the touch, exact quadratic fee, unfilled remainder reported).
  6. Mark every open position at the current best bid and append equity rows.
  7. Append compact, hash-bound evidence: trades.jsonl, intents, quotes, cycles, raw books for
     every fill/exit and raw market records for every settlement.

Nothing here can place a real order: the client is read-only and no credentials exist.

Usage:
  python3 scripts/forward_desk.py --live                 # real cycle (needs network egress)
  python3 scripts/forward_desk.py --fixtures DIR --now ISO  # offline replay for tests
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from kalshi_client import KalshiClient, FixtureClient, KalshiError, USER_AGENT  # noqa: E402
from paper_engine import (STARTING_CASH, normalize_market, parse_book, book_quotes, size_and_fill, execute,  # noqa: E402
                          taker_fee, iso, parse_ts, fnum)
import forward_strategies as FS  # noqa: E402
import signals as SIG  # noqa: E402
import signal_adapters as SA  # noqa: E402
from season import resolve_forward_dir, write_seasons_index, season_for  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
# Season memory: data/season-<UTC year>/forward.  The season is resolved from the cycle clock, so
# the first cycle of a new year rolls over automatically and the previous season stays frozen.
FORWARD_DIR = os.path.join(DATA_DIR, "season-2026", "forward")
SEASON = "2026"
UNIVERSE_DIR = os.path.join(ROOT, "data", "universe")
SERIES_INDEX_PATH = os.path.join(UNIVERSE_DIR, "series-index.json")
WRITE_SEASONS_INDEX = True  # tests redirect FORWARD_DIR and must not touch the committed data/ index
# Central Park gridpoint for KXHIGHNY (the series whose rules_primary names station CLINYC);
# verified 2026-09-20.  Other cities are resolved through the official Census Gazetteer + NWS
# /points chain in scripts/signals.py (see IRR-26).
NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/OKX/34,45/forecast"  # Central Park point (verified 2026-09-20)
NWS_EVENT_PREFIX = "KXHIGHNY-"

MAX_NEW_FILLS_PER_STRATEGY = 2
MAX_OPEN_POSITIONS_PER_STRATEGY = 8
MAX_BOOKS_PER_CYCLE = 160
BOOK_DEPTH = 20
MAX_CANDIDATES_PER_STRATEGY = 2
MAX_QUEUED_INTENTS = 1
MAX_TECHNICAL_MARKETS = 8
MAX_MICRO_MARKETS = 6
MAX_CANDLE_ARCHIVES_PER_CYCLE = 10
MAX_NWS_CITIES = 12          # one NWS gridpoint forecast per city per cycle (official api.weather.gov)
MAX_FDA_LOOKUPS = 8          # openFDA Drugs@FDA lookups per cycle (results cached 7 days)
# Injury/nowcast/index adapters added 2026-09-22 (scripts/signal_adapters.py).  The window is how
# far back an ESPN designation row may be dated to still count as news about an upcoming game.
INJURY_WINDOWS = {"KXNFLGAME": 72.0, "KXNBAGAME": 168.0}
# Kalshi CPI series -> (nowcast section, sanity band for the published percent change).
NOWCAST_SERIES = {"KXCPI": ("month-over-month", (-2.0, 2.0)),
                  "KXCPIYOY": ("year-over-year", (-5.0, 20.0))}
FRED_LOOKBACK_DAYS = 10
# Tick mode (2026-09-22): a short, cheap cycle aimed at in-game markets - it only looks at markets
# that close inside this many hours, skips the archive/view rebuilds a full cycle does, and writes
# nothing at all when no live market exists (so a quiet tick leaves no commit behind).
TICK_LIVE_WINDOW_SECONDS = 3 * 3600
# Which trigger started this process, set by the workflow ("schedule:*/5 * * * *", "push", "manual").
# Cron delivery is best-effort (IRR-34) and runner logs are not readable from every environment, so
# the value is recorded in each cycle row: the ledger itself then shows which trigger produced which
# rows - and whether the five-minute ticks were ever delivered.
DESK_TRIGGER = (os.environ.get("DESK_TRIGGER") or "").strip() or None
EXIT_FLOOR_TOLERANCE = 0.03
MARKETABLE_LIMIT_THROUGH = 0.05  # entries are IOC limits at min(rule bound, touch + 5c)
RECENT_EVENTS = 200
RECENT_INTENTS = 150
QUOTE_LOG_LIMIT = 200
FEE_TYPES_MODELED = {"quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}
MONTH_ABBR = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def current_month_label(now_ts: int) -> str:
    """The UTC month the cycle runs in, in the Cleveland Fed table's own label format."""
    stamp = datetime.fromtimestamp(int(now_ts), tz=timezone.utc)
    return f"{MONTH_NAMES[stamp.month - 1]} {stamp.year}"


def set_paths(forward_dir=None, series_index=None, season=None, universe_dir=None):
    """Redirect outputs (used by tests so they never touch the committed season memory).

    When a test forward dir is set, the adapter-cache universe dir moves under it too (IRR-40):
    a fixture cycle must be able to write its openFDA / place-centroid / gridpoint caches, but
    never into the committed data/universe/ - a cache written by a fixture run would look
    identical to verified data.
    """
    global FORWARD_DIR, SERIES_INDEX_PATH, SEASON, WRITE_SEASONS_INDEX, UNIVERSE_DIR
    if forward_dir:
        FORWARD_DIR = forward_dir
        UNIVERSE_DIR = universe_dir or os.path.join(forward_dir, "universe")
        SERIES_INDEX_PATH = series_index or os.path.join(forward_dir, "series-index.json")
        WRITE_SEASONS_INDEX = False
    elif universe_dir:
        UNIVERSE_DIR = universe_dir
    elif series_index:
        SERIES_INDEX_PATH = series_index
    if season:
        SEASON = season


def use_season(now_ts: int) -> tuple[str, str, bool]:
    """Point the desk at the season directory for `now_ts` (rolls over on a new UTC year)."""
    forward, season, created = resolve_forward_dir(DATA_DIR, now_ts)
    global FORWARD_DIR, SEASON
    FORWARD_DIR, SEASON = forward, season
    return forward, season, created


# ----------------------------------------------------------------------------- io helpers
def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def write_json(path, payload, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        if compact:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")


def append_jsonl(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rel = os.path.relpath(path, FORWARD_DIR)
    add_line_refs = rel == "trades.jsonl" or rel.startswith("intents/")
    existing_lines = 0
    if add_line_refs and os.path.exists(path):
        with open(path) as existing:
            # The anchor is a physical JSONL line number, including any historical blank line.
            existing_lines = sum(1 for _ in existing)
    with open(path, "a") as fh:
        for offset, source_row in enumerate(rows, 1):
            row = dict(source_row)
            if add_line_refs:
                row["ledgerFile"] = rel
                row["ledgerLine"] = existing_lines + offset
                # Keep the in-memory event/intent used by the strategy page equally explicit.
                source_row["ledgerFile"] = rel
                source_row["ledgerLine"] = row["ledgerLine"]
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def append_csv(path, header, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        if new:
            writer.writerow(header)
        writer.writerows(rows)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


MARKET_PROJECTION_FIELDS = ("ticker", "event_ticker", "status", "result", "settlement_ts", "settlement_value_dollars",
                            "expiration_value", "close_time", "expected_expiration_time", "expiration_time", "last_price_dollars",
                            "yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars", "volume_fp",
                            "open_interest_fp", "updated_time", "exchange_index", "strike_type", "floor_strike", "cap_strike")


def project_market(raw_market: dict) -> dict:
    """Verbatim copy of the settlement-relevant fields of a market record (rules text omitted)."""
    return {k: raw_market.get(k) for k in MARKET_PROJECTION_FIELDS if k in raw_market}


def fetch_json_url(url: str, timeout: float = 30.0) -> tuple[dict, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "application/geo+json, application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")), raw


# ----------------------------------------------------------------------------- universe
def load_series_index() -> dict:
    payload = read_json(SERIES_INDEX_PATH, {"schemaVersion": 1, "series": {}})
    return payload


def save_series_index(index: dict):
    index["updatedAt"] = iso(int(time.time()))
    write_json(SERIES_INDEX_PATH, index)


def compact_series(raw: dict) -> dict:
    return {
        "ticker": raw.get("ticker"), "title": raw.get("title"), "category": raw.get("category"),
        "categories": raw.get("categories") or [], "tags": raw.get("tags") or [], "frequency": raw.get("frequency"),
        "fee_type": raw.get("fee_type"), "fee_multiplier": raw.get("fee_multiplier"),
        "exchange_index": int(raw.get("exchange_index") or 0),
        "settlement_sources": [{"name": s.get("name"), "url": s.get("url")} for s in raw.get("settlement_sources") or []],
        "contract_terms_url": raw.get("contract_terms_url"), "volume_fp": raw.get("volume_fp"),
        "last_updated_ts": raw.get("last_updated_ts"),
    }


def _selector_matches(ticker: str, row: dict, selector: str) -> bool:
    """Same clause language as resolve_selector, applied to a series-catalog row."""
    for clause in [c.strip() for c in selector.split("&") if c.strip()]:
        kind, _, value = clause.partition(":")
        if kind == "tag" and value not in (row.get("tags") or []):
            return False
        if kind == "prefix" and not ticker.startswith(value):
            return False
        if kind == "contains" and value not in ticker:
            return False
        if kind not in ("tag", "prefix", "contains"):
            return False
    return True


def seed_index_from_catalog(index: dict, catalog_path: str | None = None) -> list[str]:
    """Fill the desk's series index from the weekly official series catalog (offline).

    The catalog is written by scripts/discover_universe.py from GET /series?category=... and holds
    ticker/title/category/tags/fee_type/fee_multiplier/exchange_index for all 13k+ series.  Seeding
    from it is what makes selector universes (CEO / FDA / daily-high weather cities) resolvable
    before any GET /series/{ticker} call; rows are never overwritten once a full record exists.
    """
    path = catalog_path or os.path.join(UNIVERSE_DIR, "series-catalog.json")
    catalog = read_json(path, {})
    rows = catalog.get("series") or {}
    added = []
    for ticker, row in sorted(rows.items()):
        if ticker in index["series"]:
            continue
        expanded = {"title": row.get("t"), "category": row.get("c"), "tags": row.get("g") or []}
        if not any(_selector_matches(ticker, expanded, selector) for selector in FS.TRACKED_SELECTORS):
            continue
        index["series"][ticker] = {
            "ticker": ticker, "title": row.get("t"), "category": row.get("c"), "categories": [row.get("c")],
            "tags": row.get("g") or [], "frequency": row.get("q"), "fee_type": row.get("f"),
            "fee_multiplier": row.get("m"), "exchange_index": int(row.get("x") or 0),
            "settlement_sources": [{"name": name, "url": None} for name in row.get("s") or []],
            "volume_fp": row.get("v"), "status": 200, "fetchedAt": catalog.get("updatedAt"),
            "source": catalog.get("source"),
            "from": "data/universe/series-catalog.json (weekly GET /series by category)",
        }
        added.append(ticker)
    return added


def ensure_series(client: KalshiClient, index: dict, tickers: list[str], errors: list[str]):
    """Fetch GET /series/{ticker} for any tracked series not yet in the index (or stale > 7d)."""
    now = time.time()
    for ticker in tickers:
        entry = index["series"].get(ticker)
        fetched = parse_ts(entry.get("fetchedAt")) if entry else None
        if entry and fetched and now - fetched < 7 * 86400 and entry.get("status") == 200:
            continue
        try:
            raw = client.series(ticker)
            row = compact_series(raw)
            row.update({"status": 200, "fetchedAt": iso(int(now)), "source": f"{client.base_url}/series/{ticker}"})
            index["series"][ticker] = row
        except KalshiError as error:
            index["series"][ticker] = {"ticker": ticker, "status": 404 if "404" in str(error) else 0, "fetchedAt": iso(int(now)),
                                       "error": str(error)[:200], "source": f"{client.base_url}/series/{ticker}"}
            errors.append(f"series {ticker}: {error}")


def resolve_selector(index: dict, selector) -> list[str]:
    """Series list for a strategy universe: explicit list, 'tracked', 'technical' or a filter
    expression over the series index ('tag:X', 'prefix:X', 'contains:X', joined with '&')."""
    if isinstance(selector, list):
        return selector
    if selector == "tracked":
        out = list(FS.TRACKED_SERIES)
        for sel in FS.TRACKED_SELECTORS:
            out.extend(resolve_selector(index, sel))
        return sorted(set(out))
    if selector == "technical":
        return list(FS.SERIES_ECON)
    clauses = [c.strip() for c in selector.split("&") if c.strip()]
    if not clauses:
        return []

    def matches(ticker, row):
        if row.get("status") != 200:
            return False
        for clause in clauses:
            kind, _, value = clause.partition(":")
            if kind == "tag" and value not in (row.get("tags") or []):
                return False
            if kind == "prefix" and not ticker.startswith(value):
                return False
            if kind == "contains" and value not in ticker:
                return False
            if kind not in ("tag", "prefix", "contains"):
                return False
        return True
    return sorted(t for t, row in index["series"].items() if matches(t, row))


def series_info(index: dict, series_ticker: str) -> dict:
    return index["series"].get(series_ticker) or {}


def fee_multiplier_for(index: dict, series_ticker: str) -> float | None:
    info = series_info(index, series_ticker)
    if info.get("status") != 200:
        return None
    if info.get("fee_type") not in FEE_TYPES_MODELED:
        return None
    return float(info.get("fee_multiplier") or 1)


# ----------------------------------------------------------------------------- data capture
def fetch_open_markets(client, index, series_list, errors) -> dict:
    markets = {}
    for series_ticker in series_list:
        info = series_info(index, series_ticker)
        if info.get("status") != 200:
            continue
        exchange_index = info.get("exchange_index") or None
        try:
            rows = client.markets(series_ticker=series_ticker, status="open", limit=200, max_pages=5, exchange_index=exchange_index)
        except KalshiError as error:
            if exchange_index:
                try:
                    rows = client.markets(series_ticker=series_ticker, status="open", limit=200, max_pages=5)
                except KalshiError as error2:
                    errors.append(f"markets {series_ticker}: {error2}")
                    continue
            else:
                errors.append(f"markets {series_ticker}: {error}")
                continue
        for raw in rows:
            m = normalize_market(raw)
            if not m["ticker"]:
                continue
            if m["status"] and m["status"] not in ("open", "active"):
                continue  # IRR-10: production returns status=active for status=open queries
            m["series_ticker"] = series_ticker
            markets[m["ticker"]] = m
    return markets


def event_date_from_ticker(event_ticker: str):
    """KXHIGHNY-26SEP20 -> date(2026, 9, 20). Returns None if the pattern does not match."""
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not match:
        return None
    yy, mon, dd = match.groups()
    if mon not in MONTH_ABBR:
        return None
    try:
        return datetime(2000 + int(yy), MONTH_ABBR[mon], int(dd)).date()
    except ValueError:
        return None


def capture_nws(markets: dict, cycle_id: str, now_ts: int, fetcher=fetch_json_url, errors=None) -> tuple[dict, dict | None]:
    """Point-in-time NWS forecast for Central Park -> {event_ticker: {high_f, period, updated}}."""
    events = sorted({m["event_ticker"] for m in markets.values() if m["event_ticker"].startswith(NWS_EVENT_PREFIX)})
    if not events:
        return {}, None
    try:
        payload, raw = fetcher(NWS_FORECAST_URL)
    except Exception as error:  # network / 5xx: the weather personas simply abstain this cycle
        if errors is not None:
            errors.append(f"nws: {error}")
        return {}, None
    props = payload.get("properties") or {}
    periods = props.get("periods") or []
    updated = props.get("updateTime") or props.get("updated") or props.get("generatedAt")
    daytime = {}
    compact_periods = []
    for period in periods:
        if not period.get("isDaytime"):
            continue
        start = period.get("startTime")
        try:
            local_date = datetime.fromisoformat(start).date()
        except (TypeError, ValueError):
            continue
        temp = fnum(period.get("temperature"))
        if temp is None or period.get("temperatureUnit") not in (None, "F"):
            continue
        daytime[local_date] = {"high_f": temp, "period": period.get("name"), "updated": updated, "start": start}
        compact_periods.append({"date": local_date.isoformat(), "name": period.get("name"), "high_f": temp})
    forecasts = {}
    for event in events:
        date = event_date_from_ticker(event)
        if date and date in daytime:
            forecasts[event] = daytime[date]
    record = {"cycle": cycle_id, "at": iso(now_ts), "source": NWS_FORECAST_URL, "updateTime": updated,
              "generatedAt": props.get("generatedAt"), "sha256": sha256_bytes(raw), "daytime": compact_periods,
              "mapped": {event: forecasts[event]["high_f"] for event in forecasts}}
    return forecasts, record


def capture_candles(client, index, markets: dict, now_ts: int, errors) -> dict:
    """Daily candlesticks (last 45 days) for the most active technical-universe markets."""
    technical = [m for m in markets.values() if m["series_ticker"] in FS.SERIES_ECON and (m["volume_24h"] or 0) > 0]
    technical.sort(key=lambda m: (-(m["volume_24h"] or 0), m["ticker"]))
    out = {}
    for m in technical[:MAX_TECHNICAL_MARKETS]:
        info = series_info(index, m["series_ticker"])
        try:
            payload, _raw, _url = client.candlesticks(m["series_ticker"], m["ticker"], now_ts - 45 * 86400, now_ts, 1440,
                                                     exchange_index=info.get("exchange_index") or None)
        except KalshiError as error:
            errors.append(f"candles {m['ticker']}: {error}")
            continue
        bars = []
        for bar in payload.get("candlesticks") or []:
            price = bar.get("price") or {}
            bars.append({"ts": bar.get("end_period_ts"), "close": fnum(price.get("close_dollars")),
                         "volume": fnum(bar.get("volume_fp"))})
        out[m["ticker"]] = bars
    return out


MICRO_SERIES = ("KXBTC15M", "KXETH15M", "KXGOLD15M")


def capture_micro_candles(client, index, markets: dict, now_ts: int, errors) -> dict:
    """Official 1-minute candlesticks (last 20 minutes) for open 15-minute crypto/gold markets."""
    out = {}
    micro = [m for m in markets.values() if m["series_ticker"] in MICRO_SERIES]
    micro.sort(key=lambda m: (m["close_ts"] or 0, m["ticker"]))
    for m in micro[:MAX_MICRO_MARKETS]:
        info = series_info(index, m["series_ticker"])
        try:
            payload, _raw, _url = client.candlesticks(m["series_ticker"], m["ticker"], now_ts - 20 * 60, now_ts, 1,
                                                     exchange_index=info.get("exchange_index") or None)
        except KalshiError as error:
            errors.append(f"candles1m {m['ticker']}: {error}")
            continue
        bars = []
        for bar in payload.get("candlesticks") or []:
            price = bar.get("price") or {}
            bars.append({"ts": bar.get("end_period_ts"), "close": fnum(price.get("close_dollars")), "volume": fnum(bar.get("volume_fp"))})
        out[m["ticker"]] = bars
    return out


# ----------------------------------------------------------------------------- state
def new_state(now_ts: int, season: str | None = None) -> dict:
    return {"schemaVersion": 1, "season": season or SEASON, "startingCash": STARTING_CASH, "createdAt": iso(now_ts),
            "cycles": 0, "lastCycle": None, "accounts": {},
            "note": "Season memory for the automated paper-trading desk; written only by scripts/forward_desk.py "
                    "from official public API responses. Accounts start at $10,000 each season."}


def account_for(state: dict, strategy: dict) -> dict:
    account = state["accounts"].get(strategy["id"])
    if account is None:
        account = {"strategyId": strategy["id"], "username": strategy["username"], "cash": STARTING_CASH, "positions": [],
                   "realizedPnl": 0.0, "feesPaid": 0.0, "slippagePaid": 0.0, "fills": 0, "exits": 0, "settlements": 0,
                   "wins": 0, "losses": 0, "unfilledContracts": 0.0, "requestedContracts": 0.0, "filledContracts": 0.0,
                   "notionalFilled": 0.0, "bestTradePnl": None, "worstTradePnl": None, "grossWins": 0.0, "grossLosses": 0.0}
        state["accounts"][strategy["id"]] = account
    account["username"] = strategy["username"]
    return account


# ----------------------------------------------------------------------------- the cycle
class Cycle:
    def __init__(self, client, now_ts: int, index: dict, state: dict, nws_fetcher=fetch_json_url,
                 text_fetcher=None, tick: bool = False):
        self.client = client
        self.now_ts = now_ts
        self.cycle_id = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.day = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        self.month = self.day[:7]
        self.index = index
        self.state = state
        self.errors: list[str] = []
        self.events: list[dict] = []
        self.intents: list[dict] = []
        self.equity_rows: list[list] = []
        self.quote_rows: list[list] = []
        self.books: dict[str, dict] = {}
        self.book_meta: dict[str, dict] = {}
        self.market_records: dict[str, dict] = {}
        self.nws_fetcher = nws_fetcher
        # Separate hook for non-JSON official sources (Cleveland Fed HTML, FRED CSV); None means
        # the adapter's own urllib fetcher is used.
        self.text_fetcher = text_fetcher
        self.tick = bool(tick)
        self.tick_actionable = False
        self.nws_record = None
        self.candles = {}
        self.candles1m = {}
        self.markets: dict[str, dict] = {}
        self.evidence_rows: list[dict] = []
        self._evidence_logged: set[str] = set()
        self.settled_tickers: list[tuple] = []
        self.archived: list[dict] = []
        self.strategy_pages: list[str] = []
        self.daily_summary: dict = {}
        self.espn: dict[str, dict] = {}
        self.fda: dict[str, dict] = {}
        self.signal_errors: list[str] = []
        self.nws_cities: "SIG.NwsCities | None" = None
        self.espn_adapter: "SIG.EspnScoreboard | None" = None
        self.fda_adapter: "SIG.OpenFdaRecords | None" = None
        self.injuries: dict[str, dict] = {}
        self.nowcast: dict[str, dict] = {}
        self.index_close: dict[str, dict] = {}
        self.injuries_adapter: "SA.EspnInjuries | None" = None
        self.nowcast_adapter: "SA.ClevelandFedNowcast | None" = None
        self.index_adapter: "SA.FredSeries | None" = None

    # -- data ---------------------------------------------------------------------------
    def load_universe(self):
        tracked = resolve_selector(self.index, "tracked")
        self.markets = fetch_open_markets(self.client, self.index, tracked, self.errors)
        if self.tick:
            # In-game only: keep the markets whose own official close_time falls inside the live
            # window, so a tick is about games that are being played right now.
            horizon = self.now_ts + TICK_LIVE_WINDOW_SECONDS
            live = {t: m for t, m in self.markets.items()
                    if m.get("close_ts") and self.now_ts <= m["close_ts"] <= horizon}
            self.books = {t: b for t, b in self.books.items() if t in live}
            self.markets = live
        self.nws, self.nws_record = capture_nws(self.markets, self.cycle_id, self.now_ts, self.nws_fetcher, self.errors)
        self.candles = capture_candles(self.client, self.index, self.markets, self.now_ts, self.errors)
        self.candles1m = capture_micro_candles(self.client, self.index, self.markets, self.now_ts, self.errors)
        self.capture_signal_adapters()

    # -- official signal adapters (ESPN scoreboard, multi-city NWS, openFDA) --------------
    def nws_signals(self) -> dict:
        """Central Park (verified gridpoint for KXHIGHNY) merged with the other city forecasts."""
        merged = dict(getattr(self, "nws", {}) or {})
        merged.update(getattr(self, "nws_forecasts", {}) or {})
        return merged

    def ctx(self, book=None) -> dict:
        return {"now_ts": self.now_ts, "candles": self.candles, "candles1m": self.candles1m,
                "nws": self.nws_signals(), "espn": self.espn, "fda": self.fda, "book": book,
                "injuries": self.injuries, "nowcast": self.nowcast, "index": self.index_close}

    def capture_signal_adapters(self):
        """Point-in-time signals from the other official public sources (each one archived).

        Every adapter is best-effort: a failed lookup is logged in signal_errors and the strategies
        that need it simply abstain this cycle - no value is inferred or defaulted.
        """
        fetcher = self.nws_fetcher or SIG.http_fetch
        self.nws_forecasts = {}
        weather: dict[str, str] = {}
        for m in self.markets.values():
            series_ticker = m["series_ticker"]
            if series_ticker.startswith("KXHIGH") and series_ticker != "KXHIGHNY":
                title = series_info(self.index, series_ticker).get("title")
                if title:
                    weather[series_ticker] = title
        if weather:
            # IRR-40: explicit cache paths under the (possibly redirected) universe dir, so a
            # fixture cycle writes its caches next to its own ledger, never into data/universe/.
            adapter = SIG.NwsCities(gridpoints_path=os.path.join(UNIVERSE_DIR, "nws-gridpoints.json"),
                                    fetcher=fetcher,
                                    centroids=SIG.PlaceCentroids(cache_path=os.path.join(UNIVERSE_DIR, "place-centroids.json")))
            chosen = dict(sorted(weather.items())[:MAX_NWS_CITIES])
            forecasts, _records = adapter.capture(chosen)
            self.nws_cities = adapter
            self.nws_forecasts = forecasts
            self.signal_errors.extend(adapter.errors)
            self.signal_errors.extend(adapter.centroids.errors)
        leagues = sorted({m["series_ticker"] for m in self.markets.values() if m["series_ticker"] in SIG.ESPN_LEAGUES})
        if leagues:
            adapter = SIG.EspnScoreboard(fetcher=fetcher)
            keys = SIG.date_keys_around(self.now_ts, 1)
            for league in leagues:
                for key in keys:
                    adapter.fetch(league, key)
            self.espn_adapter = adapter
            self.signal_errors.extend(adapter.errors)
            for ticker, m in self.markets.items():
                try:
                    signal = adapter.signal_for(m, keys)
                except Exception as error:
                    self.signal_errors.append(f"espn signal {ticker}: {error}")
                    continue
                if signal:
                    self.espn[ticker] = signal
        fda_markets = [m for m in self.markets.values() if SIG.FDA_DRUG_SERIES.match(m["series_ticker"])]
        if fda_markets:
            adapter = SIG.OpenFdaRecords(cache_path=os.path.join(UNIVERSE_DIR, "fda-records.json"), fetcher=fetcher)
            self.fda_adapter = adapter
            for m in sorted(fda_markets, key=lambda x: (-(x["volume_24h"] or 0), x["ticker"]))[:MAX_FDA_LOOKUPS]:
                try:
                    signal = SIG.fda_signal(m, adapter)
                except Exception as error:
                    self.signal_errors.append(f"openfda signal {m['ticker']}: {error}")
                    continue
                if signal:
                    self.fda[m["ticker"]] = signal
            self.signal_errors.extend(adapter.errors)
        self.capture_injury_signals(fetcher)
        self.capture_nowcast()
        self.capture_index_closes()

    def capture_injury_signals(self, fetcher):
        """ESPN league injury snapshots -> one signal per game market (opponent's hard outs)."""
        leagues = sorted({m["series_ticker"] for m in self.markets.values()
                          if m["series_ticker"] in SA.INJURY_LEAGUES})
        if not leagues:
            return
        adapter = SA.EspnInjuries(fetcher=fetcher)
        for league in leagues:
            adapter.fetch(league)
        self.injuries_adapter = adapter
        self.signal_errors.extend(adapter.errors)
        for ticker, m in self.markets.items():
            if m["series_ticker"] not in adapter.leagues or adapter.reports.get(m["series_ticker"]) is None:
                continue
            game = SIG.game_from_market(m)
            if not game:
                continue
            window = INJURY_WINDOWS.get(m["series_ticker"], 72.0)
            try:
                signal = SA.injury_signal_for_market(adapter, m, game, self.now_ts, window)
            except Exception as error:  # noqa: BLE001 - a bad row must not stop the cycle
                self.signal_errors.append(f"espn-injuries signal {ticker}: {error}")
                continue
            if signal:
                self.injuries[ticker] = signal

    def capture_nowcast(self):
        """Cleveland Fed inflation nowcast for the tracked CPI series (verbatim HTML archived)."""
        wanted = sorted({m["series_ticker"] for m in self.markets.values()
                         if m["series_ticker"] in NOWCAST_SERIES})
        if not wanted:
            return
        kwargs = {"fetcher": self.text_fetcher} if self.text_fetcher else {}
        # A parse that cannot file all three tables keeps the verbatim page under the season's raw
        # evidence store, so the parser is corrected from the bytes rather than from memory.
        raw_dir = os.path.join(os.path.dirname(FORWARD_DIR), "raw")
        adapter = SA.ClevelandFedNowcast(raw_dir=raw_dir, **kwargs)
        if not adapter.fetch():
            self.signal_errors.extend(adapter.errors)
            return
        self.nowcast_adapter = adapter
        label = current_month_label(self.now_ts)
        for series_ticker in wanted:
            section, band = NOWCAST_SERIES[series_ticker]
            row = adapter.monthly(label, section=section, metric="cpi", band=band)
            if row:
                self.nowcast[series_ticker] = row

    def capture_index_closes(self):
        """FRED official index closes for the tracked index-range series."""
        wanted = sorted({m["series_ticker"] for m in self.markets.values()
                         if m["series_ticker"] in SA.FRED_SERIES})
        if not wanted:
            return
        kwargs = {"fetcher": self.text_fetcher} if self.text_fetcher else {}
        adapter = SA.FredSeries(**kwargs)
        start = datetime.fromtimestamp(self.now_ts - FRED_LOOKBACK_DAYS * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        end = datetime.fromtimestamp(self.now_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        for series_ticker in wanted:
            fred_id = SA.FRED_SERIES[series_ticker][0]
            try:
                observed = adapter.close(fred_id, start, end)
            except Exception as error:  # noqa: BLE001
                self.signal_errors.append(f"fred {fred_id}: {error}")
                continue
            if observed:
                self.index_close[series_ticker] = observed
        self.index_adapter = adapter
        self.signal_errors.extend(adapter.errors)

    def get_book(self, ticker: str) -> dict | None:
        if ticker in self.books:
            return self.books[ticker]
        if len(self.books) >= MAX_BOOKS_PER_CYCLE:
            return None
        m = self.markets.get(ticker)
        exchange_index = (m or {}).get("exchange_index") or None
        try:
            payload, raw, url = self.client.orderbook(ticker, depth=BOOK_DEPTH, exchange_index=exchange_index)
        except KalshiError as error:
            self.errors.append(f"orderbook {ticker}: {error}")
            return None
        book = parse_book(payload)
        self.books[ticker] = book
        self.book_meta[ticker] = {"raw": raw, "url": url, "at": self.client.calls[-1]["at"], "sha256": sha256_bytes(raw)}
        return book

    def get_market_record(self, ticker: str, exchange_index=None) -> dict | None:
        if ticker in self.market_records:
            return self.market_records[ticker]
        try:
            raw_market, raw, url = self.client.market(ticker, exchange_index=exchange_index or None)
        except KalshiError as error:
            if exchange_index:
                try:
                    raw_market, raw, url = self.client.market(ticker)
                except KalshiError as error2:
                    self.errors.append(f"market {ticker}: {error2}")
                    return None
            else:
                self.errors.append(f"market {ticker}: {error}")
                return None
        record = {"market": normalize_market(raw_market), "raw": raw, "url": url, "at": self.client.calls[-1]["at"],
                  "sha256": sha256_bytes(raw)}
        self.market_records[ticker] = record
        return record

    def signal_sources(self, ticker: str) -> list[dict]:
        """Every official source this cycle read that produced a signal for this ticker.

        One row per source with its own URL and the SHA-256 of the verbatim response, so a fill can
        be reviewed against the exact bytes behind it.  A source that did not speak to this ticker
        is absent (nothing is inferred); an entry always names its kind.
        """
        out: list[dict] = []
        m = self.markets.get(ticker) or {}
        series = m.get("series_ticker")
        injury = self.injuries.get(ticker)
        if injury:
            out.append({"kind": "espn_injuries", "url": injury.get("sourceUrl"),
                        "sha256": injury.get("sha256"), "detail":
                        {"league": injury.get("league"), "team": injury.get("espnTeam"),
                         "hardOut": injury.get("freshHardOut"), "qbOut": injury.get("freshQbOut"),
                         "players": [p.get("athlete") for p in (injury.get("freshOutPlayers") or [])][:4]}})
        espn = self.espn.get(ticker)
        if espn:
            out.append({"kind": "espn_scoreboard", "url": espn.get("sourceUrl"), "sha256": espn.get("sha256"),
                        "detail": {"eventId": espn.get("espnEventId"), "state": espn.get("state"),
                                   "detail": espn.get("detail"), "scoreDiff": espn.get("scoreDiff")}})
        fda = self.fda.get(ticker)
        if fda:
            out.append({"kind": "openfda", "url": fda.get("source"), "sha256": fda.get("sha256"),
                        "detail": {"drug": fda.get("drug"), "approved": bool(fda.get("approvedRecord"))}})
        nowcast = self.nowcast.get(series)
        if nowcast:
            out.append({"kind": "cleveland_fed_nowcast", "url": nowcast.get("sourceUrl"),
                        "sha256": nowcast.get("sha256"),
                        "detail": {"section": nowcast.get("section"), "metric": nowcast.get("metric"),
                                   "value": nowcast.get("value"), "label": nowcast.get("label")}})
        index = self.index_close.get(series)
        if index:
            out.append({"kind": "fred_index", "url": index.get("sourceUrl"), "sha256": index.get("sha256"),
                        "detail": {"seriesId": index.get("seriesId"),
                                   "latest": (index.get("latest") or {}).get("date"),
                                   "value": (index.get("latest") or {}).get("value")}})
        nws = self.nws_signals().get(ticker) if series and series.startswith("KXHIGH") else None
        if nws:
            out.append({"kind": "nws_forecast", "url": nws.get("source"), "sha256": nws.get("sha256"),
                        "detail": {"high": nws.get("high"), "low": nws.get("low")}})
        return out

    def evidence_book(self, ticker: str) -> dict:
        """Bind a fill/exit to the verbatim order-book response (stored once per cycle+ticker)."""
        meta = self.book_meta[ticker]
        key = f"book:{ticker}"
        if key not in self._evidence_logged:
            self._evidence_logged.add(key)
            self.evidence_rows.append({"cycle": self.cycle_id, "kind": "orderbook", "ticker": ticker, "url": meta["url"],
                                       "retrievedAt": meta["at"], "sha256": meta["sha256"], "raw": meta["raw"].decode("utf-8")})
        return {"file": f"evidence/{self.day}.jsonl", "kind": "orderbook", "sha256": meta["sha256"], "url": meta["url"],
                "retrievedAt": meta["at"]}

    def evidence_market(self, ticker: str, record: dict) -> dict:
        """Bind a settlement to the official market record: verbatim projection + hash of the full body."""
        key = f"market:{ticker}"
        if key not in self._evidence_logged:
            self._evidence_logged.add(key)
            try:
                raw_market = json.loads(record["raw"].decode("utf-8")).get("market", {})
            except (ValueError, AttributeError):
                raw_market = {}
            self.evidence_rows.append({"cycle": self.cycle_id, "kind": "market", "ticker": ticker, "url": record["url"],
                                       "retrievedAt": record["at"], "sha256": record["sha256"], "projection": project_market(raw_market),
                                       "note": "projection of the settlement-relevant fields; sha256 is over the full response body"})
        return {"file": f"evidence/{self.day}.jsonl", "kind": "market", "sha256": record["sha256"], "url": record["url"],
                "retrievedAt": record["at"]}

    def quotes_for(self, ticker: str) -> dict:
        book = self.books.get(ticker)
        if book:
            return book_quotes(book)
        m = self.markets.get(ticker)
        if m:
            return {k: m.get(k) for k in ("yes_bid", "yes_ask", "no_bid", "no_ask")}
        rec = self.market_records.get(ticker)
        if rec:
            return {k: rec["market"].get(k) for k in ("yes_bid", "yes_ask", "no_bid", "no_ask")}
        return {"yes_bid": None, "yes_ask": None, "no_bid": None, "no_ask": None}

    # -- reconciliation -----------------------------------------------------------------
    def reconcile(self, strategy: dict, account: dict):
        ctx = self.ctx()
        remaining = []
        for position in account["positions"]:
            ticker = position["ticker"]
            live = self.markets.get(ticker)
            settled = False
            if live is None or (position.get("closeTs") and position["closeTs"] <= self.now_ts):
                record = self.get_market_record(ticker, position.get("exchangeIndex"))
                rm = record["market"] if record else None
                if rm and rm["settlement_ts"] and (rm["result"] in ("yes", "no") or rm["settlement_value"] is not None):
                    self.settle(strategy, account, position, record)
                    settled = True
                elif rm and rm["status"] in ("finalized", "settled"):
                    self.errors.append(f"{ticker}: finalized without a yes/no result or settlement value - position held, flagged")
            if settled:
                continue
            # rule-based exit: pre-check on the list quote, confirm on the fresh ladder, full size only
            if live is not None and strategy["exit"] is not FS.exit_hold:
                pre = strategy["exit"](position, self.quotes_for(ticker), ctx)
                if pre:
                    book = self.get_book(ticker)
                    if book is not None:
                        reason = strategy["exit"](position, book_quotes(book), ctx)
                        if reason and self.exit_position(strategy, account, position, book, reason):
                            continue
                        if not reason:
                            position["lastCloseError"] = {"at": iso(self.now_ts), "reason": pre,
                                                          "detail": "exit condition did not hold on the fresh order book"}
            # mark: best bid for the held side (liquidation) and bid-else-last-trade (mark)
            quotes = self.quotes_for(ticker)
            last = self.last_for_side(ticker, position["side"])
            position["lastMark"] = self.mark(position, quotes.get(f"{position['side']}_bid"), last,
                                             "book" if ticker in self.books else ("market" if live else "record"))
            remaining.append(position)
        account["positions"] = remaining

    def last_for_side(self, ticker: str, side: str):
        m = self.markets.get(ticker) or (self.market_records.get(ticker) or {}).get("market")
        last = None if not m else m.get("last")
        if last is None or last <= 0:
            return None
        return round(last if side == "yes" else 1.0 - last, 4)

    def mark(self, position: dict, bid, last, source: str) -> dict:
        """Two marks per position: liquidation (best bid, 0 if none) and mark (bid, else last trade, else 0)."""
        contracts = position["contracts"]
        liquidation = 0.0 if bid is None else round(bid * contracts, 4)
        mark_price = bid if bid is not None else last
        return {"bid": bid, "last": last, "value": liquidation, "markPrice": mark_price,
                "markValue": 0.0 if mark_price is None else round(mark_price * contracts, 4), "at": iso(self.now_ts), "source": source}

    @staticmethod
    def book_close(account, pnl):
        account["wins" if pnl > 0 else "losses"] += 1
        account["grossWins"] = round(account.get("grossWins", 0.0) + max(pnl, 0.0), 6)
        account["grossLosses"] = round(account.get("grossLosses", 0.0) + min(pnl, 0.0), 6)
        best, worst = account.get("bestTradePnl"), account.get("worstTradePnl")
        account["bestTradePnl"] = round(pnl, 6) if best is None or pnl > best else best
        account["worstTradePnl"] = round(pnl, 6) if worst is None or pnl < worst else worst

    def settle(self, strategy, account, position, record):
        m = record["market"]
        if m["result"] in ("yes", "no"):
            payout_per = 1.0 if m["result"] == position["side"] else 0.0
            result = m["result"]
        else:  # official scalar settlement value of the YES side (e.g. a tie resolves each team at $0.50)
            value = max(0.0, min(1.0, float(m["settlement_value"])))
            payout_per = value if position["side"] == "yes" else round(1.0 - value, 6)
            result = f"value:{value:.4f}"
        payout = round(payout_per * position["contracts"], 6)
        pnl = round(payout - position["entryNotional"] - position["entryFee"], 6)
        account["cash"] = round(account["cash"] + payout, 6)
        account["realizedPnl"] = round(account["realizedPnl"] + pnl, 6)
        account["settlements"] += 1
        self.book_close(account, pnl)
        self.settled_tickers.append((position["seriesTicker"], position["ticker"], position.get("exchangeIndex")))
        evidence = self.evidence_market(position["ticker"], record)
        self.events.append({
            "kind": "settlement", "cycle": self.cycle_id, "at": iso(self.now_ts), "strategyId": strategy["id"],
            "username": strategy["username"], "positionId": position["id"], "ticker": position["ticker"], "title": position["title"],
            "subtitle": position.get("subtitle"), "series": position.get("seriesTicker"),
            "side": position["side"], "contracts": position["contracts"], "entryPrice": position["entryPrice"],
            "entryAt": position["entryAt"], "exitPrice": payout_per, "exitAt": iso(m["settlement_ts"]),
            "exitType": "settlement", "result": result, "settlementValue": m["settlement_value"],
            "exitFee": 0.0, "feesTotal": position["entryFee"], "slippageEntry": position["entrySlippage"], "slippageExit": 0.0,
            "pnl": pnl, "evidence": evidence,
            "note": "official result/settlement_value + settlement_ts from GET /markets/{ticker}; no fee on simple yes/no settlement",
        })

    def exit_position(self, strategy, account, position, book, reason) -> bool:
        best = book_quotes(book).get(f"{position['side']}_bid")
        floor = None if best is None else round(max(0.0, best - EXIT_FLOOR_TOLERANCE), 4)
        execution = execute(book, position["side"], "sell", position["contracts"], floor)
        if execution["filled"] + 1e-9 < position["contracts"] or execution["vwap"] is None:
            position["lastCloseError"] = {"at": iso(self.now_ts), "reason": reason,
                                          "detail": f"only {execution['filled']} of {position['contracts']} contracts had verified bid liquidity within {EXIT_FLOOR_TOLERANCE:.2f} of the best bid"}
            return False
        fee = taker_fee(execution["vwap"], execution["filled"], position["feeMultiplier"])
        proceeds = round(execution["notional"] - fee, 6)
        pnl = round(proceeds - position["entryNotional"] - position["entryFee"], 6)
        account["cash"] = round(account["cash"] + proceeds, 6)
        account["realizedPnl"] = round(account["realizedPnl"] + pnl, 6)
        account["feesPaid"] = round(account["feesPaid"] + fee, 6)
        slip = round((execution["slippage_per_contract"] or 0.0) * execution["filled"], 6)
        account["slippagePaid"] = round(account["slippagePaid"] + slip, 6)
        account["exits"] += 1
        self.book_close(account, pnl)
        evidence = self.evidence_book(position["ticker"])
        self.events.append({
            "kind": "exit", "cycle": self.cycle_id, "at": iso(self.now_ts), "strategyId": strategy["id"],
            "username": strategy["username"], "positionId": position["id"], "ticker": position["ticker"], "title": position["title"],
            "subtitle": position.get("subtitle"), "series": position.get("seriesTicker"),
            "side": position["side"], "contracts": position["contracts"], "entryPrice": position["entryPrice"],
            "entryAt": position["entryAt"], "exitPrice": execution["vwap"], "exitTouch": execution["touch"],
            "exitAt": self.book_meta[position["ticker"]]["at"], "exitType": "bid_exit", "exitReason": reason,
            "exitFee": fee, "feesTotal": round(position["entryFee"] + fee, 6), "slippageEntry": position["entrySlippage"],
            "slippageExit": slip, "levelsConsumed": len(execution["fills"]), "pnl": pnl, "evidence": evidence,
        })
        return True

    # -- entries ------------------------------------------------------------------------
    def candidates_for(self, strategy) -> list[tuple[dict, dict]]:
        series_list = set(resolve_selector(self.index, strategy["universe"]))
        held = {p["ticker"] for p in self.state["accounts"].get(strategy["id"], {}).get("positions", [])}
        ctx = self.ctx()
        found = []
        pool = self.markets.values()
        if strategy.get("needs_book"):
            pool = [self.markets[t] for t in self.books if t in self.markets]
        for m in pool:
            if m["series_ticker"] not in series_list or m["ticker"] in held:
                continue
            if fee_multiplier_for(self.index, m["series_ticker"]) is None:
                continue
            if m["close_ts"] is not None and m["close_ts"] <= self.now_ts:
                continue
            ctx["book"] = self.books.get(m["ticker"])
            try:
                signal = strategy["entry"](m, ctx)
            except Exception as error:  # a rule bug must never crash the desk; it is logged
                self.errors.append(f"rule {strategy['id']} on {m['ticker']}: {error}")
                continue
            if signal:
                found.append((m, signal))
        found.sort(key=lambda item: (-(item[0]["volume_24h"] or 0), -(item[0]["volume"] or 0), item[0]["ticker"]))
        return found

    def enter(self, strategy, account):
        candidates = self.candidates_for(strategy)
        fills = attempts = queued = 0
        for m, signal in candidates:
            if fills >= MAX_NEW_FILLS_PER_STRATEGY:
                break
            intent = {"cycle": self.cycle_id, "at": iso(self.now_ts), "strategyId": strategy["id"], "username": strategy["username"],
                      "ticker": m["ticker"], "title": m["title"], "subtitle": m.get("yes_sub_title"), "series": m["series_ticker"], "side": signal["side"],
                      "quotePrice": signal["price"], "limit": signal.get("limit"), "reason": signal["reason"], "closeTime": iso(m["close_ts"]),
                      "volume": m["volume"], "volume24h": m["volume_24h"], "status": "proposed", "positionId": None}
            if len(account["positions"]) >= MAX_OPEN_POSITIONS_PER_STRATEGY:
                intent["status"] = "skipped_position_cap"
                self.intents.append(intent)
                break
            if attempts >= MAX_CANDIDATES_PER_STRATEGY:
                if queued < MAX_QUEUED_INTENTS:
                    intent["status"] = "queued"  # next in line; no book fetched this cycle
                    self.intents.append(intent)
                    queued += 1
                    continue
                break
            attempts += 1
            book = self.get_book(m["ticker"])
            if book is None:
                intent["status"] = "no_book"
                self.intents.append(intent)
                continue
            # The rule must still hold on the fresh book (the list quote can be stale).
            fresh = dict(m)
            fresh.update(book_quotes(book))
            ctx = self.ctx(book)
            confirm = strategy["entry"](fresh, ctx)
            intent["bookQuotes"] = book_quotes(book)
            intent["bookAt"] = self.book_meta[m["ticker"]]["at"]
            if not confirm or confirm["side"] != signal["side"]:
                intent["status"] = "not_confirmed_on_book"
                self.intents.append(intent)
                continue
            multiplier = fee_multiplier_for(self.index, m["series_ticker"])
            touch = book_quotes(book).get(f"{signal['side']}_ask")
            limit = round(min(confirm.get("limit") or touch, touch + MARKETABLE_LIMIT_THROUGH), 4)
            execution = size_and_fill(book, signal["side"], account["cash"], strategy["fraction"], multiplier, limit=limit)
            if execution is None:
                intent["status"] = "no_liquidity_or_cash"
                self.intents.append(intent)
                continue
            position = self.open_position(strategy, account, m, confirm, execution, multiplier)
            intent["status"] = "filled"
            intent["positionId"] = position["id"]
            intent["fillPrice"] = position["entryPrice"]
            intent["contracts"] = position["contracts"]
            intent["unfilledContracts"] = position["unfilledContracts"]
            self.intents.append(intent)
            fills += 1

    def intents_for(self, strategy):
        return [i for i in self.intents if i["strategyId"] == strategy["id"]]

    def emit_maker_plans(self):
        """SpreadSmith quote plans from the captured ladders (status `quote_plan`, never fills).

        Only markets whose book was already fetched this cycle are used, so a plan is priced from
        the same verified ladder as a taker fill.  scripts/maker_model.py later matches every
        posted price against the official trade tape; nothing here opens a position.
        """
        candidates = []
        for ticker, book in self.books.items():
            m = self.markets.get(ticker)
            if not m or (m.get("close_ts") is not None and m["close_ts"] <= self.now_ts):
                continue
            if fee_multiplier_for(self.index, m["series_ticker"]) is None:
                continue
            fresh = dict(m)
            fresh.update(book_quotes(book))
            try:
                signal = FS.maker_quote_plan(fresh, self.ctx(book))
            except Exception as error:
                self.errors.append(f"maker quote plan on {ticker}: {error}")
                continue
            if signal:
                candidates.append((m, signal, book))
        candidates.sort(key=lambda item: -(item[1]["meta"]["spreadAtPost"]))
        for m, signal, book in candidates[:FS.MAKER_MAX_PLANS_PER_CYCLE]:
            meta = signal["meta"]
            self.intents.append({
                "cycle": self.cycle_id, "at": iso(self.now_ts), "strategyId": "spread-smith",
                "username": "SpreadSmith", "ticker": m["ticker"], "title": m["title"],
                "subtitle": m.get("yes_sub_title"), "series": m["series_ticker"], "side": signal["side"],
                "quotePrice": signal["price"], "limit": signal["limit"], "reason": signal["reason"],
                "closeTime": iso(m["close_ts"]), "volume": m["volume"], "volume24h": m["volume_24h"],
                "status": "quote_plan", "positionId": None,
                "postedPrice": meta["postedPrice"], "postedContracts": meta["postedContracts"],
                "spreadAtPost": meta["spreadAtPost"], "improvementCents": meta["improvementCents"],
                "bookQuotes": book_quotes(book), "bookAt": self.book_meta[m["ticker"]]["at"],
                "modelNote": "maker quote plan - not a fill; measured later against GET /markets/trades "
                             "by scripts/maker_model.py (forward/execution/maker-model.json)",
            })

    def dedupe_intents(self) -> list[dict]:
        """Persist an intent only when it is new or changed since the previous cycle (fills always)."""
        previous = self.state.get("intentFingerprints") or {}
        current, keep = {}, []
        for intent in self.intents:
            key = f"{intent['strategyId']}|{intent['ticker']}|{intent['side']}|{intent['status']}"
            current[key] = self.cycle_id
            if intent["status"] == "filled" or key not in previous:
                keep.append(intent)
        self.state["intentFingerprints"] = current
        return keep

    def open_position(self, strategy, account, m, signal, execution, multiplier) -> dict:
        cost = round(execution["notional"] + execution["fee"], 6)
        account["cash"] = round(account["cash"] - cost, 6)
        account["feesPaid"] = round(account["feesPaid"] + execution["fee"], 6)
        slip = round((execution["slippage_per_contract"] or 0.0) * execution["filled"], 6)
        account["slippagePaid"] = round(account["slippagePaid"] + slip, 6)
        account["fills"] += 1
        account["unfilledContracts"] = round(account["unfilledContracts"] + execution["unfilled"], 2)
        account["requestedContracts"] = round(account.get("requestedContracts", 0.0) + execution["requested"], 2)
        account["filledContracts"] = round(account.get("filledContracts", 0.0) + execution["filled"], 2)
        account["notionalFilled"] = round(account.get("notionalFilled", 0.0) + execution["notional"], 6)
        evidence = self.evidence_book(m["ticker"])
        position = {
            "id": f"{strategy['id']}-{safe(m['ticker'])}-{self.cycle_id}", "strategyId": strategy["id"], "username": strategy["username"],
            "ticker": m["ticker"], "eventTicker": m["event_ticker"], "seriesTicker": m["series_ticker"], "title": m["title"],
            "subtitle": m.get("yes_sub_title"), "side": signal["side"], "contracts": execution["filled"], "requestedContracts": execution["requested"],
            "unfilledContracts": execution["unfilled"], "entryPrice": execution["vwap"], "entryTouch": execution["touch"],
            "entryNotional": execution["notional"], "entryFee": execution["fee"], "entrySlippage": slip,
            "levelsConsumed": len(execution["fills"]), "fills": execution["fills"][:5], "limitPrice": execution.get("limit"),
            "entryAt": self.book_meta[m["ticker"]]["at"], "entryCycle": self.cycle_id, "entryReason": signal["reason"],
            "closeTs": m["close_ts"], "closeTime": iso(m["close_ts"]), "exchangeIndex": m["exchange_index"],
            "feeMultiplier": multiplier, "quoteAtEntry": {k: m.get(k) for k in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last", "previous", "volume", "volume_24h", "open_interest")},
            # signalMeta: what the entry signal saw at decision time (e.g. the NWS forecast value a
            # weather entry keyed on); exits may compare the live signal against it, never against
            # anything re-fetched for the entry bar.  Carried verbatim from the signal dict.
            "signalMeta": dict(signal.get("meta") or {}),
            # signalSources: the official feeds behind this entry (URL + verbatim-response hash).
            "signalSources": self.signal_sources(m["ticker"]),
            "evidence": evidence, "lastMark": None,
        }
        position["lastMark"] = self.mark(position, book_quotes(self.books[m["ticker"]]).get(f"{signal['side']}_bid"),
                                         self.last_for_side(m["ticker"], signal["side"]), "book")
        account["positions"].append(position)
        self.events.append({
            "kind": "fill", "cycle": self.cycle_id, "at": iso(self.now_ts), "strategyId": strategy["id"], "username": strategy["username"],
            "positionId": position["id"], "ticker": m["ticker"], "title": m["title"], "subtitle": m.get("yes_sub_title"), "series": m["series_ticker"],
            "side": signal["side"], "contracts": execution["filled"], "requestedContracts": execution["requested"], "unfilledContracts": execution["unfilled"],
            "entryPrice": execution["vwap"], "entryTouch": execution["touch"], "entryNotional": execution["notional"],
            "entryFee": execution["fee"], "slippageEntry": slip, "levelsConsumed": len(execution["fills"]),
            "entryAt": position["entryAt"], "closeTime": position["closeTime"], "reason": signal["reason"], "evidence": evidence,
            "signalSources": position["signalSources"],
            "limitPrice": execution.get("limit"), "feeMultiplier": multiplier,
        })
        return position

    # -- candle archive for settled traded markets (grows the verified backtest sample) ----
    def archive_settled_candles(self):
        seen = set()
        for series_ticker, ticker, exchange_index in self.settled_tickers:
            if ticker in seen or len(self.archived) >= MAX_CANDLE_ARCHIVES_PER_CYCLE:
                continue
            seen.add(ticker)
            record = self.market_records.get(ticker)
            m = record["market"] if record else None
            if not m or not m.get("open_ts"):
                continue
            end_ts = m.get("settlement_ts") or m.get("close_ts") or self.now_ts
            start_ts = m["open_ts"]
            period = 1 if (end_ts - start_ts) <= 2 * 3600 else (60 if (end_ts - start_ts) <= 14 * 86400 else 1440)
            try:
                payload, raw, url = self.client.candlesticks(series_ticker, ticker, start_ts - 60, end_ts + 60, period,
                                                             exchange_index=exchange_index or None)
            except KalshiError as error:
                self.errors.append(f"archive {ticker}: {error}")
                continue
            bars = payload.get("candlesticks") or []
            rows = []
            for bar in bars:
                price, yes_bid, yes_ask = bar.get("price") or {}, bar.get("yes_bid") or {}, bar.get("yes_ask") or {}
                rows.append([bar.get("end_period_ts"), fnum(price.get("open_dollars")), fnum(price.get("high_dollars")),
                             fnum(price.get("low_dollars")), fnum(price.get("close_dollars")), fnum(yes_bid.get("close_dollars")),
                             fnum(yes_ask.get("close_dollars")), fnum(bar.get("volume_fp")), fnum(bar.get("open_interest_fp"))])
            rel = f"candles/{safe(series_ticker)}/{safe(ticker)}-p{period}.csv"
            path = os.path.join(FORWARD_DIR, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", newline="") as fh:
                writer = csv.writer(fh, lineterminator="\n")
                writer.writerow(["end_period_ts", "open", "high", "low", "close", "yes_bid_close", "yes_ask_close", "volume", "open_interest"])
                writer.writerows(rows)
            self.archived.append({"cycle": self.cycle_id, "ticker": ticker, "series": series_ticker, "file": rel, "period": period,
                                  "bars": len(rows), "url": url, "sha256": sha256_bytes(raw), "result": m.get("result"),
                                  "settlementTs": iso(m.get("settlement_ts")), "openTs": iso(start_ts), "closeTs": iso(m.get("close_ts"))})

    # -- accounting ---------------------------------------------------------------------
    def mark_and_record(self, strategy, account):
        liquidation = mark_value = 0.0
        marked = 0
        for position in account["positions"]:
            lm = position.get("lastMark") or {}
            liquidation += lm.get("value") or 0.0
            mark_value += lm.get("markValue") or 0.0
            if lm.get("markPrice") is not None:
                marked += 1
        equity = round(account["cash"] + mark_value, 4)
        account["lastEquity"] = equity
        account["lastMarkValue"] = round(mark_value, 4)
        account["lastLiquidationValue"] = round(liquidation, 4)
        account["lastLiquidationEquity"] = round(account["cash"] + liquidation, 4)
        account["lastMarkedAt"] = iso(self.now_ts)
        self.equity_rows.append([self.cycle_id, iso(self.now_ts), strategy["id"], strategy["username"], round(account["cash"], 4),
                                 len(account["positions"]), marked, round(liquidation, 4), equity,
                                 round(account["realizedPnl"], 4), round(account["feesPaid"], 4), round(account["slippagePaid"], 4),
                                 round(mark_value, 4)])

    def log_quotes(self):
        wanted = set(self.books)
        for account in self.state["accounts"].values():
            wanted.update(p["ticker"] for p in account["positions"])
        wanted.update(i["ticker"] for i in self.intents)
        for ticker in sorted(wanted)[:QUOTE_LOG_LIMIT]:
            m = self.markets.get(ticker) or (self.market_records.get(ticker) or {}).get("market")
            if not m:
                continue
            q = self.quotes_for(ticker)
            self.quote_rows.append([self.cycle_id, ticker, m.get("status"), q["yes_bid"], q["yes_ask"], q["no_bid"], q["no_ask"],
                                    m.get("last"), m.get("previous"), m.get("volume"), m.get("volume_24h"), m.get("open_interest"),
                                    iso(m.get("close_ts")), "book" if ticker in self.books else "market"])

    # -- run ----------------------------------------------------------------------------
    def run(self) -> dict:
        started = time.time()
        self.load_universe()
        if self.tick and not self.markets:
            # Nothing is being played inside the window: a tick is a no-op and persists nothing.
            return {"cycle": self.cycle_id, "at": iso(self.now_ts), "tick": "no_live_markets",
                    "trigger": DESK_TRIGGER,
                    "liveWindowHours": TICK_LIVE_WINDOW_SECONDS / 3600,
                    "durationSec": round(time.time() - started, 1),
                    "apiCalls": len(self.client.calls), "marketsSeen": 0, "persisted": False,
                    "note": "tick cycle: no market's official close_time falls inside the live window"}
        for strategy in FS.STRATEGIES:
            account = account_for(self.state, strategy)
            self.reconcile(strategy, account)
        ordered = sorted(FS.STRATEGIES, key=lambda st: 1 if st.get("needs_book") else 0)
        for strategy in ordered:
            account = account_for(self.state, strategy)
            if self.markets:
                self.enter(strategy, account)
        if not self.tick:
            self.emit_maker_plans()
        for strategy in FS.STRATEGIES:
            self.mark_and_record(strategy, account_for(self.state, strategy))
        if not self.tick:
            self.archive_settled_candles()
            self.log_quotes()
        else:
            # An actionable tick is one that actually filled or closed something; a tick that only
            # looked is not committed (the next full cycle re-renders every view anyway).
            self.tick_actionable = any(e["kind"] in ("fill", "exit", "settlement") for e in self.events)
        summary = {
            "cycle": self.cycle_id, "at": iso(self.now_ts), "durationSec": round(time.time() - started, 1),
            "apiCalls": len(self.client.calls), "apiErrors": sum(1 for c in self.client.calls if c["status"] != 200),
            "seriesTracked": len(resolve_selector(self.index, "tracked")), "marketsSeen": len(self.markets),
            "booksFetched": len(self.books), "marketRecordsFetched": len(self.market_records),
            "intents": len(self.intents), "fills": sum(1 for e in self.events if e["kind"] == "fill"),
            "makerQuotePlans": sum(1 for i in self.intents if i.get("status") == "quote_plan"),
            "exits": sum(1 for e in self.events if e["kind"] == "exit"),
            "settlements": sum(1 for e in self.events if e["kind"] == "settlement"),
            "nwsCaptured": self.nws_record is not None, "candleMarkets": len(self.candles), "candlesArchived": len(self.archived),
            "nwsCityForecasts": len(getattr(self, "nws_forecasts", {}) or {}), "espnSignals": len(self.espn),
            "espnLiveSignals": sum(1 for s in self.espn.values() if s.get("state") == "in"),
            "fdaSignals": len(self.fda), "fdaNoRecord": sum(1 for s in self.fda.values() if not s.get("approvedRecord")),
            "injurySignals": len(self.injuries), "injuryQbOutSignals": sum(1 for x in self.injuries.values() if x.get("freshQbOut")),
            "nowcastSignals": len(self.nowcast), "indexCloses": len(self.index_close),
            "signalErrors": self.signal_errors[:30], "signalErrorCount": len(self.signal_errors),
            "season": self.state.get("season", SEASON),
            "trigger": DESK_TRIGGER,
            "tick": ("live" if self.tick_actionable else "idle") if self.tick else None,
            "errors": self.errors[:40], "errorCount": len(self.errors),
        }
        self.persisted_intents = self.dedupe_intents()
        summary["intentsPersisted"] = len(self.persisted_intents)
        self.state["cycles"] += 1
        self.state["lastCycle"] = summary
        self.state["files"] = {"trades": "trades.jsonl", "intents": f"intents/{self.month}.jsonl", "equity": f"equity/{self.month}.csv",
                               "cycles": f"cycles/{self.month}.jsonl", "quotes": f"quotes/{self.day}.csv",
                               "evidence": f"evidence/{self.day}.jsonl", "nws": "signals/nws-central-park.jsonl",
                               "nwsCities": "signals/nws-cities.jsonl", "espn": "signals/espn-scoreboard.jsonl",
                               "openfda": "signals/openfda.jsonl", "strategies": "strategies/", "summary": f"summary/{self.day}.json",
                               "espnInjuries": "signals/espn-injuries.jsonl", "nowcast": "signals/cleveland-fed-nowcast.jsonl",
                               "fredIndex": "signals/fred-index.jsonl", "sources": "sources/status.json",
                               "execution": "execution/", "candles": "candles/index.jsonl", "compressed": "COMPRESSED.json",
                               "tradesReview": "trades-review.md", "tradesReviewJson": "trades-review.json",
                               "makerModel": "execution/maker-model.json"}
        return summary

    def sources_status(self) -> dict:
        """Per-cycle status of every official source this cycle read (links for manual review).

        One row per endpoint: the source's own URL, whether the cycle actually read it, how many
        records/values it produced, and the SHA-256 of the last verbatim response when the adapter
        returned one.  A source that was not needed this cycle is listed as `not_needed`; a source
        that failed is listed as `failed` with the adapter's own error text - never silently absent.
        """
        def rows_for(records, source, url_key="sourceUrl"):
            out = []
            for record in records or []:
                # adapters name their response URL differently ("sourceUrl" in this module,
                # "url"/"source" in signals.py); a ledger row must always carry the real URL.
                url = record.get(url_key) or record.get("url") or record.get("source")
                out.append({"source": source, "status": "read", "url": url,
                            "at": record.get("at") or record.get("retrievedAt"),
                            "sha256": record.get("sha256"),
                            "bytes": record.get("bytes"), "detail": record.get("seriesId")
                            or record.get("league") or record.get("sections")})
            return out
        sources = []
        # market data (always present when the universe loaded)
        sources.append({"source": "Kalshi Trade API v2", "url": "https://external-api.kalshi.com/trade-api/v2",
                        "status": "read" if self.markets else "failed",
                        "detail": {"marketsSeen": len(self.markets), "apiCalls": len(self.client.calls)},
                        "at": iso(self.now_ts)})
        sources.append({"source": "NWS point forecast (KXHIGHNY gridpoint)", "url": NWS_FORECAST_URL,
                        "status": "read" if self.nws_record else "not_needed",
                        "detail": {"mapped": (self.nws_record or {}).get("mapped")}, "at": iso(self.now_ts)})
        if self.nws_cities:
            sources.extend(rows_for(self.nws_cities.records, "NWS city gridpoint forecasts"))
        elif self.nws_record is None:
            sources.append({"source": "NWS city gridpoint forecasts", "url": SIG.NWS_ROOT,
                            "status": "not_needed", "detail": {}, "at": iso(self.now_ts)})
        if self.espn_adapter:
            sources.extend(rows_for(self.espn_adapter.snapshots, "ESPN scoreboard (public JSON)"))
        else:
            sources.append({"source": "ESPN scoreboard (public JSON)",
                            "url": SIG.ESPN_SCOREBOARD.format(sport="football", league="nfl"),
                            "status": "not_needed", "detail": {}, "at": iso(self.now_ts)})
        if self.fda_adapter:
            sources.extend(rows_for(self.fda_adapter.records, "openFDA Drugs@FDA"))
        if self.injuries_adapter:
            sources.extend(rows_for(self.injuries_adapter.records, "ESPN league injuries (public JSON)"))
        elif SA.INJURY_LEAGUES:
            sources.append({"source": "ESPN league injuries (public JSON)",
                            "url": SA.ESPN_INJURIES_URL.format(sport="football", league="nfl"),
                            "status": "not_needed", "detail": {}, "at": iso(self.now_ts)})
        if self.nowcast_adapter:
            sources.extend(rows_for(self.nowcast_adapter.records, "Cleveland Fed Inflation Nowcasting"))
        if self.index_adapter:
            sources.extend(rows_for(self.index_adapter.records, "FRED (Federal Reserve Bank of St. Louis)"))
        errors = list(self.signal_errors)
        return {"schemaVersion": 1, "generatedAt": iso(self.now_ts), "cycle": self.cycle_id,
                "sources": sources, "sourceCount": len(sources),
                "failed": [e for e in errors if e], "errorCount": len(errors),
                "note": "One row per official endpoint this cycle could read; status is read / "
                        "not_needed / failed, and every read row carries the verbatim response "
                        "hash where the adapter returned one. A failed read is recorded here and "
                        "the dependent strategy abstains."}

    def persist(self, summary: dict):
        if summary.get("tick") in ("no_live_markets", "idle"):
            # Nothing was live, or the tick looked but had nothing to do: both leave the ledger
            # untouched, so a quiet 5-minute slot produces no commit and no data growth.
            return
        append_jsonl(os.path.join(FORWARD_DIR, "evidence", f"{self.day}.jsonl"), self.evidence_rows)
        append_jsonl(os.path.join(FORWARD_DIR, "trades.jsonl"), self.events)
        append_jsonl(os.path.join(FORWARD_DIR, "intents", f"{self.month}.jsonl"), self.persisted_intents)
        append_jsonl(os.path.join(FORWARD_DIR, "cycles", f"{self.month}.jsonl"), [summary])
        append_jsonl(os.path.join(FORWARD_DIR, "candles", "index.jsonl"), self.archived)
        if self.tick:
            # A live tick appends the trade/intent/cycle rows and the account state; the equity
            # curve, quote log and rendered views are rebuilt by the next full cycle, which reads
            # these same append-only files.
            self.state["cycles"] += 0  # counters were already advanced in run()
            write_json(os.path.join(FORWARD_DIR, "sources", "status.json"), self.sources_status())
            attach_state_position_refs(self.state)
            write_json(os.path.join(FORWARD_DIR, "state.json"), self.state)
            return
        append_csv(os.path.join(FORWARD_DIR, "equity", f"{self.month}.csv"),
                   ["cycle", "at", "strategyId", "username", "cash", "openPositions", "markedPositions", "liquidationValue", "equity",
                    "realizedPnl", "feesPaid", "slippagePaid", "markValue"], self.equity_rows)
        append_csv(os.path.join(FORWARD_DIR, "quotes", f"{self.day}.csv"),
                   ["cycle", "ticker", "status", "yes_bid", "yes_ask", "no_bid", "no_ask", "last", "previous", "volume", "volume_24h",
                    "open_interest", "close_time", "quote_source"], self.quote_rows)
        if self.nws_record:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "nws-central-park.jsonl"), [self.nws_record])
        if self.nws_cities and self.nws_cities.records:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "nws-cities.jsonl"), self.nws_cities.records)
            self.nws_cities.save()
        if self.espn_adapter and self.espn_adapter.snapshots:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "espn-scoreboard.jsonl"), self.espn_adapter.snapshots)
        if self.fda_adapter and self.fda_adapter.records:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "openfda.jsonl"), self.fda_adapter.records)
            self.fda_adapter.save()
        if self.injuries_adapter and self.injuries_adapter.records:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "espn-injuries.jsonl"), self.injuries_adapter.records)
        if self.nowcast_adapter and self.nowcast_adapter.records:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "cleveland-fed-nowcast.jsonl"),
                         self.nowcast_adapter.records)
        if self.index_adapter and self.index_adapter.records:
            append_jsonl(os.path.join(FORWARD_DIR, "signals", "fred-index.jsonl"), self.index_adapter.records)
        write_json(os.path.join(FORWARD_DIR, "sources", "status.json"), self.sources_status())
        attach_state_position_refs(self.state)
        write_json(os.path.join(FORWARD_DIR, "state.json"), self.state)
        write_json(os.path.join(FORWARD_DIR, "leaderboard.json"), build_leaderboard(self.state, self.state.get("season")))
        self.strategy_pages = write_strategy_pages(self.state, summary)
        self.daily_summary = write_daily_summary(self.state, summary)
        try:
            import trades_review
            trades_review.write_review(FORWARD_DIR)
        except Exception as error:  # the review is a view file; never fail a cycle over it
            self.errors.append(f"trades review: {error}")
        if WRITE_SEASONS_INDEX:
            write_seasons_index(DATA_DIR, self.now_ts)
        self.write_recent(summary)
        self.write_curves()

    def write_recent(self, summary: dict):
        """Small, site-friendly window: latest events, intents and cycles (full history stays in the JSONL files)."""
        path = os.path.join(FORWARD_DIR, "recent.json")
        recent = read_json(path, {"events": [], "intents": [], "cycles": []})
        recent["events"] = (self.events + recent.get("events", []))[:RECENT_EVENTS]
        recent["intents"] = (self.persisted_intents + recent.get("intents", []))[:RECENT_INTENTS]
        recent["cycles"] = ([summary] + recent.get("cycles", []))[:96]
        recent["generatedAt"] = iso(self.now_ts)
        recent["nws"] = self.nws_record and {k: self.nws_record[k] for k in ("at", "updateTime", "daytime", "mapped", "source")}
        recent["nwsCities"] = [{"series": r.get("series"), "city": r.get("city"), "gridId": r.get("gridId"),
                                "gridX": r.get("gridX"), "gridY": r.get("gridY"), "censusPlace": r.get("censusPlace"),
                                "updateTime": r.get("updateTime"), "url": r.get("url"), "sha256": r.get("sha256"),
                                "days": r.get("days"), "retrievedAt": r.get("retrievedAt")}
                               for r in (self.nws_cities.records if self.nws_cities else [])]
        recent["espn"] = [{"league": snap.get("league"), "date": snap.get("date"), "url": snap.get("url"),
                           "events": snap.get("events"), "sha256": snap.get("sha256"), "retrievedAt": snap.get("retrievedAt")}
                          for snap in (self.espn_adapter.snapshots if self.espn_adapter else [])]
        recent["espnSignals"] = self.espn
        recent["openfda"] = [{"drug": r.get("drug"), "code": r.get("code"), "hits": r.get("hits"),
                              "approvedRecord": r.get("approvedRecord"), "url": r.get("url"), "sha256": r.get("sha256"),
                              "retrievedAt": r.get("retrievedAt"),
                              "applications": r.get("applications")}
                             for r in (self.fda_adapter.records if self.fda_adapter else [])]
        recent["fdaSignals"] = self.fda
        # The 2026-09-22 adapters: one compact row per read so the site's signals tab can show the
        # official URL, the retrieval time, the response hash and the value that was used.
        recent["injuries"] = [{"league": r.get("league"), "url": r.get("sourceUrl"), "sha256": r.get("sha256"),
                               "bytes": r.get("bytes"), "at": r.get("at"), "teamCount": r.get("teamCount"),
                               "playerCount": r.get("playerCount"), "season": r.get("season")}
                              for r in (self.injuries_adapter.records if self.injuries_adapter else [])]
        recent["injurySignals"] = self.injuries
        recent["nowcast"] = [{"url": r.get("sourceUrl"), "sha256": r.get("sha256"), "at": r.get("at"),
                              "sections": r.get("sections"), "rows": r.get("rows")}
                             for r in (self.nowcast_adapter.records if self.nowcast_adapter else [])]
        recent["nowcastSignals"] = self.nowcast
        recent["indexCloses"] = [{"seriesId": r.get("seriesId"), "url": r.get("sourceUrl"), "sha256": r.get("sha256"),
                                  "at": r.get("at"), "latestDate": r.get("latestDate"), "latest": r.get("latest"),
                                  "observed": r.get("observed")}
                                 for r in (self.index_adapter.records if self.index_adapter else [])]
        recent["indexSignals"] = self.index_close
        recent["sourceStatus"] = self.sources_status()
        recent["signalErrors"] = self.signal_errors[:30]
        write_json(path, recent, compact=True)

    def write_curves(self):
        """Equity curves for the site: every cycle for the last 96 cycles + one point per UTC day for all history."""
        path = os.path.join(FORWARD_DIR, "curves.json")
        curves = read_json(path, {"recent": {}, "daily": {}})
        for row in self.equity_rows:
            cycle_id, at, strategy_id, _username, _cash, _open, _marked, _liq, equity = row[:9]
            series = curves["recent"].setdefault(strategy_id, [])
            series.append([at, equity])
            del series[:-96]
            daily = curves["daily"].setdefault(strategy_id, [])
            day = at[:10]
            if daily and daily[-1][0] == day:
                daily[-1][1] = equity
            else:
                daily.append([day, equity])
        curves["generatedAt"] = iso(self.now_ts)
        write_json(path, curves, compact=True)


def safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", text)


ARCHIVE_RULE_MATCHES = {
    # Only rules with a documented same-family relationship are mapped.  A missing mapping is
    # intentional: a shared topic (for example gold or sports) is not enough to claim a backtest.
    "pinepilot": ("arch-sma", "same Pine-style SMA5/SMA10 cross family on verified candles"),
    "panic-fade": ("arch-panic-fade", "same volatility-reversion family on verified one-minute candles"),
    "longshot-fader": ("arch-fade-longshot", "same favourite-longshot fade family"),
    "sure-thing": ("arch-favourite", "same high-probability favourite hold family"),
    "game-favourite": ("arch-favourite", "closest verified favourite-hold rule; archive markets are not a sports-only subset"),
    "tick-chaser": ("arch-momentum", "same direction-of-change momentum family"),
    "dip-hunter": ("arch-longshot", "same cheap-longshot entry family"),
    "tail-sprint": ("arch-longshot", "same cheap-tail entry family; archive rule holds to official result"),
    "micro-tail": ("arch-longshot", "same cheap-tail entry family; archive rule holds to official result"),
    "underdog-sweep": ("arch-longshot", "same underdog/longshot sweep family"),
}


def read_jsonl_with_lines(rel: str) -> list[dict]:
    """Read a ledger file while retaining its stable 1-based GitHub line anchor."""
    path = os.path.join(FORWARD_DIR, rel)
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            row["ledgerFile"] = rel
            row["ledgerLine"] = line_number
            out.append(row)
    return out


def archive_backtest_attachment(strategy_id: str) -> dict:
    """Attach only a committed archive rule that has an explicit family mapping."""
    archive_dir = os.path.join(os.path.dirname(FORWARD_DIR), "backtest-archive")
    required = ["competition.json", "leaderboard.json", "trades.json", "curves.json", "walkforward.json"]
    if not all(os.path.exists(os.path.join(archive_dir, name)) for name in required):
        return {"available": False, "matched": False, "note": "No committed archive backtest is available for this season.",
                "archivePath": "backtest-archive/"}
    competition = read_json(os.path.join(archive_dir, "competition.json"), {})
    board = read_json(os.path.join(archive_dir, "leaderboard.json"), [])
    trades = read_json(os.path.join(archive_dir, "trades.json"), [])
    curves = read_json(os.path.join(archive_dir, "curves.json"), {})
    walk = read_json(os.path.join(archive_dir, "walkforward.json"), {})
    explanations = read_json(os.path.join(archive_dir, "explanations.json"), [])
    mapping = ARCHIVE_RULE_MATCHES.get(strategy_id)
    catalog = [{"strategyId": row.get("strategyId"), "username": row.get("username"), "name": row.get("name"),
                "rule": row.get("rule"), "trades": row.get("trades"), "returnPct": row.get("returnPct")}
               for row in board]
    base = {"available": True, "matched": bool(mapping), "archivePath": "backtest-archive/",
            "catalog": catalog, "files": {"competition": "backtest-archive/competition.json", "leaderboard": "backtest-archive/leaderboard.json",
                                            "trades": "backtest-archive/trades.json", "curves": "backtest-archive/curves.json",
                                            "walkForward": "backtest-archive/walkforward.json"}}
    if not competition.get("marketCount"):
        base["matched"] = False
        base["note"] = "Archive output exists for this fresh season, but no verified candle market has been archived yet."
        return base
    if not mapping:
        base["note"] = "No archive rule is claimed as a verified match for this forward rule; shared topic or source is not enough."
        return base
    archive_id, mapping_note = mapping
    metric = next((row for row in board if row.get("strategyId") == archive_id), None)
    if metric is None:
        base["matched"] = False
        base["note"] = f"Mapping names {archive_id}, but that archive rule is absent from the committed leaderboard."
        return base
    explanation = next((row for row in explanations if row.get("strategyId") == archive_id), {})
    base.update({"archiveStrategyId": archive_id, "mappingNote": mapping_note, "metrics": metric,
                 "rule": {"id": archive_id, "username": metric.get("username"), "name": metric.get("name"),
                          "text": metric.get("rule"), "explanation": explanation.get("explanation")},
                 "trades": [row for row in trades if row.get("strategyId") == archive_id],
                 "curve": (curves.get("strategies") or {}).get(archive_id),
                 "walkForward": [row for row in walk.get("rows", []) if row.get("strategyId") == archive_id],
                 "walkForwardCaveat": walk.get("caveat"), "competition": {
                     "generatedAt": competition.get("generatedAt"), "marketCount": competition.get("marketCount"),
                     "verifiedBars": competition.get("verifiedBars"), "series": competition.get("series"),
                     "source": competition.get("source")}})
    return base


def analysis_for(strategy: dict, account: dict, equity: float) -> str:
    """Result narrative derived only from the account's own verified ledger counters."""
    if not account["fills"]:
        return (f"{strategy['username']} has not filled yet: its rule either never triggered on the tracked universe or was not "
                f"confirmed on a fresh order book. Unranked until a verified fill exists.")
    parts = []
    requested = account.get("requestedContracts") or 0.0
    filled = account.get("filledContracts") or 0.0
    fill_ratio = (filled / requested * 100) if requested else 0.0
    avg_px = (account.get("notionalFilled") or 0.0) / filled if filled else None
    depth_note = "" if fill_ratio >= 99.5 else "; the remainder exceeded displayed depth within the limit price"
    parts.append(f"{account['fills']} verified fill(s), {filled:,.0f} of {requested:,.0f} requested contracts filled "
                 f"({fill_ratio:.0f}%{depth_note})" + (f", average entry ${avg_px:.3f}" if avg_px else "") + ".")
    closed = account["exits"] + account["settlements"]
    if closed:
        parts.append(f"{closed} closed ({account['settlements']} official settlement(s), {account['exits']} bid exit(s)): "
                     f"{account['wins']} win(s) / {account['losses']} loss(es), realized ${account['realizedPnl']:,.2f} "
                     f"(gross +${account.get('grossWins', 0.0):,.2f} / -${abs(account.get('grossLosses', 0.0)):,.2f}); "
                     f"best trade ${account.get('bestTradePnl') or 0:,.2f}, worst ${account.get('worstTradePnl') or 0:,.2f}.")
    else:
        parts.append("Nothing has closed yet, so realized PnL is $0.00 and the return is entirely a mark against displayed bids / last trades.")
    drag = account["feesPaid"] + account["slippagePaid"]
    parts.append(f"Cost drag so far: ${account['feesPaid']:,.2f} taker fees + ${account['slippagePaid']:,.2f} slippage versus the touch "
                 f"= {drag / STARTING_CASH * 100:.2f}% of starting cash.")
    ret = (equity / STARTING_CASH - 1) * 100
    if account["positions"]:
        parts.append(f"{len(account['positions'])} open position(s) marked at ${account.get('lastMarkValue', 0.0):,.2f} "
                     f"(liquidation ${account.get('lastLiquidationValue', 0.0):,.2f}); equity {ret:+.2f}%.")
    verdict = ("working so far" if ret > 0.5 else "roughly flat" if ret > -0.5 else "losing so far")
    parts.append(f"Verdict: {verdict}. {strategy['why']}")
    return " ".join(parts)


def build_leaderboard(state: dict, season: str | None = None) -> dict:
    rows = []
    ledger_events = read_jsonl_with_lines("trades.jsonl")
    for strategy in FS.STRATEGIES:
        account = state["accounts"].get(strategy["id"])
        if not account:
            continue
        equity = account.get("lastEquity", account["cash"])
        liquidation_equity = account.get("lastLiquidationEquity", account["cash"])
        rows.append({
            "strategyId": strategy["id"], "username": strategy["username"], "name": strategy["name"], "group": strategy["group"],
            "source": strategy["source"], "rule": strategy["rule"], "why": strategy["why"],
            "startingCash": STARTING_CASH, "cash": round(account["cash"], 2), "equity": round(equity, 2),
            "returnPct": round((equity / STARTING_CASH - 1) * 100, 4), "openPositions": len(account["positions"]),
            "liquidationEquity": round(liquidation_equity, 2), "liquidationReturnPct": round((liquidation_equity / STARTING_CASH - 1) * 100, 4),
            "liquidationValue": account.get("lastLiquidationValue", 0.0), "markValue": account.get("lastMarkValue", 0.0),
            "realizedPnl": round(account["realizedPnl"], 4),
            "feesPaid": round(account["feesPaid"], 4), "slippagePaid": round(account["slippagePaid"], 4),
            "fills": account["fills"], "exits": account["exits"], "settlements": account["settlements"],
            "wins": account["wins"], "losses": account["losses"], "unfilledContracts": account["unfilledContracts"],
            "bestTradePnl": account.get("bestTradePnl"), "worstTradePnl": account.get("worstTradePnl"),
            "evidenceState": ("verified forward fills" if account["fills"] else "no fill yet (rule never confirmed on a fresh book)"),
            "analysis": analysis_for(strategy, account, equity),
            "firstFill": next(({k: event.get(k) for k in ("ledgerFile", "ledgerLine", "positionId", "ticker")}
                                for event in ledger_events if event.get("strategyId") == strategy["id"] and event.get("kind") == "fill"), None),
            "latestFill": next(({k: event.get(k) for k in ("ledgerFile", "ledgerLine", "positionId", "ticker")}
                                 for event in reversed(ledger_events) if event.get("strategyId") == strategy["id"] and event.get("kind") == "fill"), None),
        })
    ranked = sorted([r for r in rows if r["fills"] > 0], key=lambda r: (-r["returnPct"], r["username"]))
    unranked = sorted([r for r in rows if r["fills"] == 0], key=lambda r: r["username"])
    for i, row in enumerate(ranked):
        row["rank"] = i + 1
    for row in unranked:
        row["rank"] = None
    rows = ranked + unranked
    return {"schemaVersion": 2, "season": season or state.get("season") or SEASON,
            "generatedAt": (state.get("lastCycle") or {}).get("at"),
            "cycles": state.get("cycles", 0), "participants": len(rows), "ranked": len(ranked),
            "markPolicy": "equity = cash + sum(contracts x mark) where mark = best displayed bid for the held side, else the last "
                          "official trade price, else 0; liquidationEquity uses the bid only (0 when no bid). Rank by equity; "
                          "strategies without a verified fill are unranked.",
            "fillPolicy": f"entries are immediate-or-cancel limit orders at min(rule bound, touch + {MARKETABLE_LIMIT_THROUGH:.2f}) sized at "
                          f"50% of free cash, consuming displayed depth level by level; exits sell the full position into the bid ladder "
                          f"within {EXIT_FLOOR_TOLERANCE:.2f} of the best bid or not at all; fee 0.07*q*p*(1-p)*series multiplier "
                          f"rounded up to $0.0001; settlement at $1/$0 from the official result with no fee.",
            "gated": [{k: g[k] for k in ("id", "username", "name", "group", "source", "blocker")} for g in FS.GATED],
            "rows": rows,
            "strategyPages": {r["strategyId"]: f"strategies/{r['strategyId']}.json" for r in rows},
            "pageUrl": "strategy.html"}


# ----------------------------------------------------------------------------- per-strategy pages
def read_jsonl(rel: str) -> list[dict]:
    path = os.path.join(FORWARD_DIR, rel)
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def read_equity_rows() -> list[dict]:
    rows = []
    directory = os.path.join(FORWARD_DIR, "equity")
    if not os.path.isdir(directory):
        return rows
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".csv"):
            continue
        with open(os.path.join(directory, name), newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append(row)
    return rows


def evidence_storage_ref(event: dict, compressed_index: dict) -> dict:
    """Copy an event and make an IRR-24 evidence link explicit without changing its hash binding."""
    out = dict(event)
    evidence = dict(event.get("evidence") or {})
    file = evidence.get("file")
    entry = (compressed_index.get("files") or {}).get(file) if file else None
    if entry:
        evidence["compressedFile"] = entry.get("compressedFile", f"{file}.gz")
        evidence["compacted"] = True
    out["evidence"] = evidence
    return out


def attach_state_position_refs(state: dict):
    """Add fill/close line anchors to open positions before state.json is written."""
    events = read_jsonl_with_lines("trades.jsonl")
    compressed_index = read_json(os.path.join(FORWARD_DIR, "COMPRESSED.json"), {})
    fills = {event.get("positionId"): event for event in events if event.get("kind") == "fill" and event.get("positionId")}
    closes = {event.get("positionId"): event for event in events if event.get("kind") in ("exit", "settlement") and event.get("positionId")}
    for account in state.get("accounts", {}).values():
        for position in account.get("positions") or []:
            fill = fills.get(position.get("id")); close = closes.get(position.get("id"))
            position["ledger"] = {"fillFile": fill.get("ledgerFile") if fill else "trades.jsonl",
                                  "fillLine": fill.get("ledgerLine") if fill else None,
                                  "closeFile": close.get("ledgerFile") if close else "trades.jsonl",
                                  "closeLine": close.get("ledgerLine") if close else None}
            if position.get("evidence"):
                position["evidence"] = evidence_storage_ref({"evidence": position["evidence"]}, compressed_index)["evidence"]


def write_strategy_pages(state: dict, summary: dict) -> list[str]:
    """One committed JSON file per persona: identity, rule, analysis, full trade + equity history.

    These are the data files behind strategy.html?id=<strategyId>; everything in them is copied
    from the ledger the cycle just appended, so the page cannot disagree with trades.jsonl.
    """
    events = read_jsonl_with_lines("trades.jsonl")
    compressed_index = read_json(os.path.join(FORWARD_DIR, "COMPRESSED.json"), {})
    equity = read_equity_rows()
    intents: list[dict] = []
    intents_dir = os.path.join(FORWARD_DIR, "intents")
    if os.path.isdir(intents_dir):
        for name in sorted(os.listdir(intents_dir)):
            if name.endswith(".jsonl"):
                intents.extend(read_jsonl_with_lines(os.path.join("intents", name)))
    written = []
    for strategy in FS.STRATEGIES:
        account = state["accounts"].get(strategy["id"])
        if not account:
            continue
        mine = [e for e in events if e.get("strategyId") == strategy["id"]]
        my_equity = [[r["cycle"], float(r["equity"]), float(r["cash"]), int(r["openPositions"]),
                      float(r["realizedPnl"]), float(r["feesPaid"]), float(r["slippagePaid"])]
                     for r in equity if r.get("strategyId") == strategy["id"]]
        my_intents = [i for i in intents if i.get("strategyId") == strategy["id"]][-RECENT_INTENTS:]
        fill_by_position = {e.get("positionId"): e for e in mine if e.get("kind") == "fill" and e.get("positionId")}
        close_by_position = {e.get("positionId"): e for e in mine if e.get("kind") in ("exit", "settlement") and e.get("positionId")}
        my_positions = []
        for position in account.get("positions") or []:
            enriched = dict(position)
            fill = fill_by_position.get(position.get("id"))
            close = close_by_position.get(position.get("id"))
            enriched["ledger"] = {
                "fillFile": fill.get("ledgerFile") if fill else "trades.jsonl",
                "fillLine": fill.get("ledgerLine") if fill else None,
                "closeFile": close.get("ledgerFile") if close else "trades.jsonl",
                "closeLine": close.get("ledgerLine") if close else None,
            }
            my_positions.append(enriched)
        equity_now = account.get("lastEquity", account["cash"])
        my_events = [evidence_storage_ref(event, compressed_index) for event in mine[-RECENT_EVENTS:]]
        for position in my_positions:
            position["evidence"] = (evidence_storage_ref({"evidence": position.get("evidence")}, compressed_index).get("evidence")
                                     if position.get("evidence") else {})
        payload = {
            "schemaVersion": 2, "season": state.get("season", SEASON), "generatedAt": summary.get("at"),
            "cycle": summary.get("cycle"),
            "strategy": {k: strategy[k] for k in ("id", "username", "name", "group", "rule", "why", "source")},
            "account": {k: account.get(k) for k in ("cash", "realizedPnl", "feesPaid", "slippagePaid", "fills",
                                                    "exits", "settlements", "wins", "losses", "filledContracts",
                                                    "requestedContracts", "unfilledContracts", "bestTradePnl",
                                                    "worstTradePnl", "lastEquity", "lastLiquidationEquity",
                                                    "lastMarkValue", "lastLiquidationValue", "lastMarkedAt")},
            "returnPct": round((equity_now / STARTING_CASH - 1) * 100, 4),
            "liquidationReturnPct": round((account.get("lastLiquidationEquity", account["cash"]) / STARTING_CASH - 1) * 100, 4),
            "analysis": analysis_for(strategy, account, equity_now),
            "positions": my_positions,
            "events": my_events,
            "eventCount": len(mine),
            "equity": my_equity[-1000:],
            "intents": my_intents,
            "archiveBacktest": archive_backtest_attachment(strategy["id"]),
            "evidenceFiles": sorted({(e.get("evidence") or {}).get("file") for e in mine if (e.get("evidence") or {}).get("file")}),
            "ledger": {"trades": "trades.jsonl", "intents": "intents/", "equity": "equity/", "evidence": "evidence/",
                       "quotes": "quotes/", "cycles": "cycles/", "compressed": "COMPRESSED.json"},
        }
        path = os.path.join(FORWARD_DIR, "strategies", f"{strategy['id']}.json")
        write_json(path, payload, compact=True)
        written.append(os.path.relpath(path, FORWARD_DIR))
    return written


def write_daily_summary(state: dict, summary: dict) -> dict:
    """Roll up the UTC day's cycles into one small file the site shows as 'today'."""
    day = summary.get("at", "")[:10]
    cycles = [c for c in read_jsonl(os.path.join("cycles", f"{day[:7]}.jsonl")) if str(c.get("at", "")).startswith(day)]
    events = [e for e in read_jsonl("trades.jsonl") if str(e.get("at", "")).startswith(day)]
    fills = [e for e in events if e["kind"] == "fill"]
    closes = [e for e in events if e["kind"] in ("exit", "settlement")]
    rows = []
    for strategy in FS.STRATEGIES:
        account = state["accounts"].get(strategy["id"])
        if not account:
            continue
        equity = account.get("lastEquity", account["cash"])
        mine = [e for e in events if e.get("strategyId") == strategy["id"]]
        rows.append({"strategyId": strategy["id"], "username": strategy["username"], "name": strategy["name"],
                     "equity": round(equity, 2), "returnPct": round((equity / STARTING_CASH - 1) * 100, 4),
                     "realizedPnl": round(account["realizedPnl"], 4), "openPositions": len(account["positions"]),
                     "fillsToday": sum(1 for e in mine if e["kind"] == "fill"),
                     "closesToday": sum(1 for e in mine if e["kind"] in ("exit", "settlement")),
                     "pnlToday": round(sum(e["pnl"] for e in mine if "pnl" in e), 4)})
    rows.sort(key=lambda r: -r["returnPct"])
    accounts = [a for a in state.get("accounts", {}).values()]
    equity_total = sum(a.get("lastEquity", a["cash"]) for a in accounts)
    liquidation_total = sum(a.get("lastLiquidationEquity", a["cash"]) for a in accounts)
    positions = [p for a in accounts for p in a.get("positions", [])]
    intents_today = [i for i in (read_jsonl(os.path.join("intents", f"{day[:7]}.jsonl"))
                                 if os.path.exists(os.path.join(FORWARD_DIR, "intents", f"{day[:7]}.jsonl")) else [])
                     if str(i.get("at", "")).startswith(day)]
    errors_today = [str(e) for c in cycles for e in (c.get("errors") or [])]
    payload = {"schemaVersion": 1, "season": state.get("season", SEASON), "day": day, "generatedAt": summary.get("at"),
               "cycles": len(cycles), "apiCalls": sum(c.get("apiCalls", 0) for c in cycles),
               "apiErrors": sum(c.get("apiErrors", 0) for c in cycles),
               "fills": len(fills), "exits": sum(1 for e in closes if e["kind"] == "exit"),
               "settlements": sum(1 for e in closes if e["kind"] == "settlement"),
               "realizedToday": round(sum(e["pnl"] for e in closes), 4),
               "feesToday": round(sum(e.get("entryFee", 0.0) for e in fills) + sum(e.get("exitFee", 0.0) for e in closes), 4),
               "bestMove": (max(rows, key=lambda r: r["pnlToday"]) if rows else None),
               "worstMove": (min(rows, key=lambda r: r["pnlToday"]) if rows else None),
               "rows": rows, "cycleIds": [c.get("cycle") for c in cycles],
               # derived views for the site; every one of these is a sum of the rows above
               "date": day,
               "weekday": datetime.strptime(day, "%Y-%m-%d").strftime("%A") if day else None,
               "accounts": len(rows),
               "equity": {"total": round(equity_total, 2),
                          "returnPct": round(((equity_total / (STARTING_CASH * len(rows))) - 1) * 100, 4) if rows else 0.0,
                          "liquidationTotal": round(liquidation_total, 2),
                          "liquidationReturnPct": round(((liquidation_total / (STARTING_CASH * len(rows))) - 1) * 100, 4) if rows else 0.0,
                          "ranked": sum(1 for r in rows if r["fillsToday"] or r["closesToday"]),
                          "realizedPnl": round(sum(a["realizedPnl"] for a in accounts), 4),
                          "feesPaid": round(sum(a["feesPaid"] for a in accounts), 4),
                          "slippagePaid": round(sum(a["slippagePaid"] for a in accounts), 4)},
               "activity": {"fills": len(fills), "exits": sum(1 for e in closes if e["kind"] == "exit"),
                            "settlements": sum(1 for e in closes if e["kind"] == "settlement"),
                            "intents": len(intents_today)},
               "positions": {"open": len(positions),
                             "contracts": int(sum(float(p.get("contracts") or 0) for p in positions)),
                             "entryNotional": round(sum(float(p.get("entryNotional") or 0) for p in positions), 2)},
               "ledger": {"contracts": int(sum(float(e.get("contracts") or 0) for e in fills)),
                          "markets": len({e.get("ticker") for e in events if e.get("ticker")})},
               "movers": [{"strategyId": r["strategyId"], "username": r["username"], "returnPct": r["returnPct"],
                           "fills": r["fillsToday"], "settlements": r["closesToday"], "pnlToday": r["pnlToday"]}
                          for r in rows[:5]],
               "leaders": [{"strategyId": r["strategyId"], "username": r["username"], "returnPct": r["returnPct"],
                            "fills": r["fillsToday"]} for r in rows[:3]],
               "signals": [{"source": "NWS point forecast", "ok": any(c.get("nwsCaptured") for c in cycles)},
                           {"source": "NWS city gridpoints", "ok": sum(c.get("nwsCityForecasts", 0) for c in cycles) > 0,
                            "captured": sum(c.get("nwsCityForecasts", 0) for c in cycles)},
                           {"source": "ESPN scoreboards", "ok": sum(c.get("espnSignals", 0) for c in cycles) > 0,
                            "captured": sum(c.get("espnSignals", 0) for c in cycles)},
                           {"source": "openFDA drugsfda", "ok": sum(c.get("fdaSignals", 0) for c in cycles) > 0,
                            "captured": sum(c.get("fdaSignals", 0) for c in cycles)},
                           {"source": "official candlesticks", "ok": sum(c.get("candleMarkets", 0) for c in cycles) > 0,
                            "captured": sum(c.get("candleMarkets", 0) for c in cycles)}],
               "errors": len(errors_today), "errorSamples": errors_today[:5],
               "note": "Roll-up of the committed ledger for one UTC day (cycles/, trades.jsonl). "
                       "No value here is computed outside the ledger."}
    write_json(os.path.join(FORWARD_DIR, "summary", f"{day}.json"), payload, compact=True)
    write_json(os.path.join(FORWARD_DIR, "summary", "today.json"), payload, compact=True)
    return payload


# ----------------------------------------------------------------------------- entry point
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="run one real cycle against the public API")
    parser.add_argument("--fixtures", help="directory of recorded responses (offline replay)")
    parser.add_argument("--now", help="ISO timestamp to use as the cycle clock (fixtures mode)")
    parser.add_argument("--dry-run", action="store_true", help="run but do not persist anything")
    parser.add_argument("--tick", action="store_true",
                        help="cheap live-window cycle: only markets closing inside 3h, no view rebuilds, "
                             "no-op (nothing persisted) when no market is live")
    parser.add_argument("--out", help="override the forward-desk output directory (tests)")
    parser.add_argument("--series-index", help="override the series index path (tests)")
    parser.add_argument("--season", help="force a season year (default: the UTC year of the cycle clock)")
    args = parser.parse_args(argv)
    if not args.live and not args.fixtures:
        parser.error("choose --live or --fixtures DIR")
    now_ts = parse_ts(args.now) if args.now else int(time.time())
    if args.out:
        set_paths(forward_dir=args.out, series_index=args.series_index, season=args.season or season_for(now_ts))
        rolled_over = False
    else:
        if args.season:
            set_paths(forward_dir=os.path.join(DATA_DIR, f"season-{args.season}", "forward"), season=args.season)
            os.makedirs(FORWARD_DIR, exist_ok=True)
            rolled_over = False
        else:
            _forward, season_name, rolled_over = use_season(now_ts)
    if args.fixtures:
        client, nws, text = fixture_client(args.fixtures)
    else:
        client, nws, text = KalshiClient(), fetch_json_url, None
    index = load_series_index()
    seeded = seed_index_from_catalog(index)
    ensure_series(client, index, sorted(set(resolve_selector(index, "tracked"))), errors := [])
    if seeded:
        print(f"series index seeded from the weekly catalog: {len(seeded)} series "
              f"({', '.join(seeded[:6])}{'...' if len(seeded) > 6 else ''})", file=sys.stderr)
    state = read_json(os.path.join(FORWARD_DIR, "state.json")) or new_state(now_ts, SEASON)
    if state.get("season") != SEASON:  # rolled over into a directory that held another season
        state = new_state(now_ts, SEASON)
    backfill_counters(state)
    cycle = Cycle(client, now_ts, index, state, nws_fetcher=nws, text_fetcher=text,
                  tick=getattr(args, "tick", False))
    cycle.errors.extend(errors)
    if rolled_over:
        cycle.errors.append(f"season rollover: started Season {SEASON} at {iso(now_ts)} with fresh "
                            f"${STARTING_CASH:,.0f} accounts; earlier seasons are frozen")
    summary = cycle.run()
    if not args.dry_run:
        save_series_index(index)
        cycle.persist(summary)
    print(json.dumps(summary, indent=1))
    return 0


COUNTERS_VERSION = 2




def backfill_counters(state: dict):
    """Recompute the derived per-account counters from trades.jsonl (the source of truth) once,
    so accounts created before a counter existed carry correct fill/size/best-worst statistics."""
    if state.get("countersVersion") == COUNTERS_VERSION:
        return
    path = os.path.join(FORWARD_DIR, "trades.jsonl")
    events = []
    if os.path.exists(path):
        with open(path) as fh:
            events = [json.loads(line) for line in fh if line.strip()]
    for strategy_id, account in state["accounts"].items():
        mine = [e for e in events if e["strategyId"] == strategy_id]
        fills = [e for e in mine if e["kind"] == "fill"]
        closes = [e for e in mine if e["kind"] in ("exit", "settlement")]
        account["requestedContracts"] = round(sum(e.get("requestedContracts", e["contracts"]) for e in fills), 2)
        account["filledContracts"] = round(sum(e["contracts"] for e in fills), 2)
        account["notionalFilled"] = round(sum(e["entryNotional"] for e in fills), 6)
        pnls = [e["pnl"] for e in closes]
        account["bestTradePnl"] = round(max(pnls), 6) if pnls else None
        account["worstTradePnl"] = round(min(pnls), 6) if pnls else None
        account["grossWins"] = round(sum(p for p in pnls if p > 0), 6)
        account["grossLosses"] = round(sum(p for p in pnls if p < 0), 6)
    state["countersVersion"] = COUNTERS_VERSION


def fixture_client(directory: str):
    """Load DIR/kalshi.json ({path?query: body}), DIR/nws.json and DIR/text.json ({url: content}).

    ``text.json`` feeds the non-JSON official adapters (Cleveland Fed HTML, FRED CSV) in offline
    runs; when the file is absent those adapters abstain exactly as they would on a failed read.
    """
    kalshi = read_json(os.path.join(directory, "kalshi.json"), {})
    nws_body = read_json(os.path.join(directory, "nws.json"), None)

    def nws_fetcher(url):
        if nws_body is None:
            raise RuntimeError("no NWS fixture")
        raw = json.dumps(nws_body, separators=(",", ":")).encode()
        return nws_body, raw
    text_bodies = read_json(os.path.join(directory, "text.json"), {}) or {}

    def text_fetcher(url):
        if url not in text_bodies:
            raise RuntimeError(f"no fixture response recorded for {url}")
        body = text_bodies[url]
        return body, body.encode("utf-8")

    return FixtureClient(kalshi), nws_fetcher, text_fetcher


if __name__ == "__main__":
    sys.exit(main())
