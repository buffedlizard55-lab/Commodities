#!/usr/bin/env python3
"""Point-in-time signal adapters for official, free, key-less public sources.

Design rules (identical to the rest of the repository):
  * Every adapter returns the *verbatim* response bytes next to the parsed value so the caller can
    archive them with a SHA-256 (see forward_desk.Cycle.evidence_*).
  * A failed or ambiguous lookup returns None (the strategy abstains); nothing is inferred.
  * Every mapping step is itself a fetch from an official source and is cached with its URL, hash
    and timestamp, so a reviewer can replay it:
      - US Census Gazetteer (place centroids, official federal file) -> city lat/lon
      - NWS GET /points/{lat},{lon}                                 -> forecast gridpoint
      - NWS gridpoint forecast                                      -> daily high in F
      - ESPN scoreboard (public JSON)                               -> live game score/status
      - openFDA Drugs@FDA (public JSON)                             -> drug application record
  * No credentials are used anywhere; all endpoints below are documented as public and free.

Verified endpoint references (checked 2026-09-20):
  NWS API root ............ https://api.weather.gov            (docs: https://www.weather.gov/documentation/services-web-api)
  NWS point lookup ........ https://api.weather.gov/points/{lat},{lon}
  Census Gazetteer files .. https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
  Places file (2026) ...... https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/2026_Gaz_place_national.zip
  ESPN scoreboard ......... https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard
  openFDA Drugs@FDA ....... https://api.fda.gov/drug/drugsfda.json  (docs: https://open.fda.gov/apis/drug/drugsfda/)
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

USER_AGENT = ("Commodities-ResearchExchange/1.0 "
              "(+https://github.com/buffedlizard55-lab/Commodities; paper-trading research, read-only)")
ROOT = os.path.join(os.path.dirname(__file__), "..")
UNIVERSE_DIR = os.path.join(ROOT, "data", "universe")

NWS_ROOT = "https://api.weather.gov"
GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2026_Gazetteer/"
                 "2026_Gaz_place_national.zip")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
OPENFDA_DRUGSFDA = "https://api.fda.gov/drug/drugsfda.json"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def http_fetch(url: str, timeout: float = 30.0, accept: str = "application/json") -> tuple[object, bytes]:
    """Default fetcher: returns (parsed_json, raw_bytes).  Tests inject their own."""
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")), raw


def http_fetch_bytes(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def read_cache(path: str, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path) as fh:
        return json.load(fh)


def write_cache(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ============================================================================ NWS · multi-city
# Kalshi daily-high series (verified in data/universe/series-catalog.json, tag "Daily temperature").
# The settlement station is named in each market's rules_primary, e.g. KXHIGHNY:
#   "the maximum temperature recorded at New York City (CLINYC) ... according to The Weather Company".
# Only KXHIGHHOU names an NWS source ("NWS Climatological Report Houston"); for the others the NWS
# forecast is a *signal*, never the settlement value (recorded as IRR-26 in data/source-registry.json).
WEATHER_CITY_TITLES = re.compile(r"^Highest temperature in (?P<city>.+)$", re.IGNORECASE)

# Cities where Kalshi's series title is not a Census place name verbatim.  Each alias is the
# official Census place NAME; nothing else is invented (no hand-typed coordinates anywhere).
CITY_ALIASES = {
    "new york": ("New York", "NY"),
    "los angeles": ("Los Angeles", "CA"),
    "san francisco": ("San Francisco", "CA"),
    "washington": ("Washington", "DC"),
    "las vegas": ("Las Vegas", "NV"),
    "philadelphia": ("Philadelphia", "PA"),
    "miami": ("Miami", "FL"),
    "houston": ("Houston", "TX"),
    "chicago": ("Chicago", "IL"),
    "denver": ("Denver", "CO"),
    "austin": ("Austin", "TX"),
    "phoenix": ("Phoenix", "AZ"),
    "seattle": ("Seattle", "WA"),
    "atlanta": ("Atlanta", "GA"),
    "boston": ("Boston", "MA"),
    "dallas": ("Dallas", "TX"),
    "minneapolis": ("Minneapolis", "MN"),
    "oklahoma city": ("Oklahoma City", "OK"),
    "new orleans": ("New Orleans", "LA"),
    "san antonio": ("San Antonio", "TX"),
    "san diego": ("San Diego", "CA"),
}


class PlaceCentroids:
    """City -> official interior-point coordinates from the US Census Gazetteer places file.

    The zip is read once (only when a city is missing from the cache) and only the rows this
    project needs are written to data/universe/place-centroids.json together with the source URL,
    the response hash and the retrieval time.  A city that is not an exact match is left absent.
    """

    def __init__(self, cache_path: str | None = None, fetcher=http_fetch_bytes, url: str = GAZETTEER_URL):
        self.cache_path = cache_path or os.path.join(UNIVERSE_DIR, "place-centroids.json")
        self.fetcher = fetcher
        self.url = url
        self.cache = read_cache(self.cache_path, {"schemaVersion": 1, "source": url, "places": {}})
        self.cache.setdefault("places", {})
        self.cache["source"] = url
        self._rows: list[tuple[str, str, float, float]] | None = None
        self.errors: list[str] = []
        self.downloaded = False

    def _load_rows(self) -> list[tuple[str, str, float, float]]:
        if self._rows is not None:
            return self._rows
        raw = self.fetcher(self.url)
        if isinstance(raw, tuple):  # adapters that return (payload, raw_bytes)
            raw = raw[-1]
        self.downloaded = True
        self._raw_sha256 = sha256_bytes(raw)
        self._raw_at = iso(int(time.time()))
        rows: list[tuple[str, str, float, float]] = []
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            name = archive.namelist()[0]
            text = archive.read(name).decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            self.errors.append(f"gazetteer {self.url}: empty file")
            self._rows = rows
            return rows
        header = [column.strip() for column in lines[0].split("|")]
        try:
            idx = {col: header.index(col) for col in ("NAME", "USPS", "INTPTLAT", "INTPTLONG")}
        except ValueError as error:
            self.errors.append(f"gazetteer header missing a column: {error}; header={header}")
            self._rows = rows
            return rows
        for line in lines[1:]:
            parts = line.split("|")
            if len(parts) < len(header):
                continue
            try:
                lat = float(parts[idx["INTPTLAT"]].lstrip("+"))
                lon = float(parts[idx["INTPTLONG"]].lstrip("+"))
            except ValueError:
                continue
            rows.append((parts[idx["NAME"]].strip(), parts[idx["USPS"]].strip(), lat, lon))
        self._rows = rows
        return rows

    def lookup(self, city: str) -> dict | None:
        """Coordinates for a Kalshi weather city, or None (never guessed)."""
        key = normalize_name(city)
        if key in self.cache["places"]:
            return self.cache["places"][key]
        alias = CITY_ALIASES.get(key)
        target_name = normalize_name(alias[0] if alias else city)
        target_state = (alias[1] if alias else "").upper()
        try:
            rows = self._load_rows()
        except Exception as error:  # network / archive problem: the weather personas abstain
            self.errors.append(f"gazetteer fetch failed: {error}")
            return None
        matches = [row for row in rows
                   if normalize_name(row[0]) == target_name and (not target_state or row[1].upper() == target_state)]
        if not matches:
            self.errors.append(f"no Census place match for '{city}'")
            return None
        name, state, lat, lon = matches[0]
        row = {"city": city, "censusPlace": name, "state": state, "lat": round(lat, 6), "lon": round(lon, 6),
               "source": self.url, "sha256": getattr(self, "_raw_sha256", None),
               "retrievedAt": getattr(self, "_raw_at", None), "candidates": len(matches)}
        self.cache["places"][key] = row
        self.cache["updatedAt"] = iso(int(time.time()))
        write_cache(self.cache_path, self.cache)
        return row

    def save(self) -> None:
        self.cache["updatedAt"] = iso(int(time.time()))
        write_cache(self.cache_path, self.cache)


class NwsCities:
    """NWS point forecasts for every tracked Kalshi daily-high city series.

    Resolution chain (all official, all archived):
      series title -> city -> Census Gazetteer interior point -> NWS /points/{lat},{lon}
      -> NWS gridpoint forecast URL -> daytime high per calendar day.
    Gridpoint resolutions are cached in data/universe/nws-gridpoints.json (with URL + hash +
    timestamp); forecasts are re-read every cycle and archived by the desk.
    """

    def __init__(self, gridpoints_path: str | None = None, fetcher=http_fetch,
                 centroids: PlaceCentroids | None = None, max_age_days: int = 30):
        self.gridpoints_path = gridpoints_path or os.path.join(UNIVERSE_DIR, "nws-gridpoints.json")
        self.fetcher = fetcher
        self.centroids = centroids or PlaceCentroids()
        self.max_age_days = max_age_days
        self.gridpoints = read_cache(self.gridpoints_path, {"schemaVersion": 1, "gridpoints": {}})
        self.gridpoints.setdefault("gridpoints", {})
        self.errors: list[str] = []
        self.records: list[dict] = []

    # -- resolution --------------------------------------------------------------------
    def city_for_series(self, series_title: str) -> str | None:
        match = WEATHER_CITY_TITLES.match((series_title or "").strip())
        return match.group("city").strip() if match else None

    def resolve(self, series_ticker: str, series_title: str) -> dict | None:
        entry = self.gridpoints["gridpoints"].get(series_ticker)
        fetched = entry.get("fetchedAt") if entry else None
        fresh = False
        if entry and fetched:
            try:
                fresh = (time.time() - datetime.fromisoformat(fetched.replace("Z", "+00:00")).timestamp()) < self.max_age_days * 86400
            except ValueError:
                fresh = False
        if entry and entry.get("status") == 200 and fresh:
            return entry
        city = self.city_for_series(series_title)
        if not city:
            self.errors.append(f"{series_ticker}: title '{series_title}' does not name a city")
            return None
        place = self.centroids.lookup(city)
        if not place:
            self.errors.append(f"{series_ticker}: no official coordinates for '{city}' - series skipped")
            return None
        points_url = f"{NWS_ROOT}/points/{place['lat']:.4f},{place['lon']:.4f}"
        try:
            payload, raw = self.fetcher(points_url)
        except Exception as error:
            self.errors.append(f"{series_ticker}: NWS {points_url}: {error}")
            return None
        props = payload.get("properties") or {}
        forecast_url = props.get("forecast")
        if not forecast_url:
            self.errors.append(f"{series_ticker}: NWS point response had no 'forecast' link")
            return None
        entry = {"series": series_ticker, "title": series_title, "city": city, "status": 200,
                 "censusPlace": place["censusPlace"], "state": place["state"],
                 "lat": place["lat"], "lon": place["lon"], "placeSource": place["source"],
                 "pointsUrl": points_url, "pointsSha256": sha256_bytes(raw),
                 "gridId": props.get("gridId"), "gridX": props.get("gridX"), "gridY": props.get("gridY"),
                 "forecastUrl": forecast_url, "relativeLocation": ((props.get("relativeLocation") or {}).get("properties") or {}).get("city"),
                 "fetchedAt": iso(int(time.time()))}
        self.gridpoints["gridpoints"][series_ticker] = entry
        self.gridpoints["updatedAt"] = iso(int(time.time()))
        write_cache(self.gridpoints_path, self.gridpoints)
        return entry

    # -- forecast ----------------------------------------------------------------------
    def forecast(self, series_ticker: str) -> dict | None:
        """Fetch + archive the gridpoint forecast for one series.  Returns a compact record."""
        entry = self.gridpoints["gridpoints"].get(series_ticker)
        if not entry or not entry.get("forecastUrl"):
            return None
        url = entry["forecastUrl"]
        try:
            payload, raw = self.fetcher(url)
        except Exception as error:
            self.errors.append(f"nws forecast {series_ticker}: {error}")
            return None
        props = payload.get("properties") or {}
        days, daytime = {}, []
        for period in props.get("periods") or []:
            if not period.get("isDaytime"):
                continue
            start = period.get("startTime")
            try:
                day = datetime.fromisoformat(start).date().isoformat()
            except (TypeError, ValueError):
                continue
            temp = period.get("temperature")
            if not isinstance(temp, (int, float)) or period.get("temperatureUnit") not in (None, "F"):
                continue
            days[day] = float(temp)
            daytime.append({"date": day, "name": period.get("name"), "high_f": float(temp)})
        record = {"kind": "nws-forecast", "series": series_ticker, "city": entry["city"],
                  "gridId": entry.get("gridId"), "gridX": entry.get("gridX"), "gridY": entry.get("gridY"),
                  "url": url, "pointsUrl": entry.get("pointsUrl"), "censusPlace": entry.get("censusPlace"),
                  "state": entry.get("state"), "lat": entry.get("lat"), "lon": entry.get("lon"),
                  "updateTime": props.get("updateTime"), "generatedAt": props.get("generatedAt"),
                  "validPeriods": len(props.get("periods") or []), "daytime": daytime, "days": days,
                  "retrievedAt": iso(int(time.time())), "sha256": sha256_bytes(raw),
                  "note": "NWS gridpoint forecast for the Census interior point of the city named in the "
                          "Kalshi series title. The settlement value comes from the station named in each "
                          "market's rules_primary (The Weather Company for most KXHIGH* series) - IRR-26."}
        self.records.append(record)
        return record

    def capture(self, weather_series: dict) -> tuple[dict, list[dict]]:
        """weather_series = {series_ticker: series_title} for the tracked daily-high series.

        Returns (forecasts_by_event_ticker, archived_records).
        """
        by_series = {}
        for series_ticker, title in weather_series.items():
            if self.resolve(series_ticker, title):
                record = self.forecast(series_ticker)
                if record:
                    by_series[series_ticker] = record
        forecasts: dict[str, dict] = {}
        for series_ticker, record in by_series.items():
            days = record.get("days") or {}
            if not days:
                continue
            for day, high in days.items():
                forecasts[f"{series_ticker}-{event_date_suffix(day)}"] = {
                    "high_f": high, "date": day, "series": series_ticker, "city": record["city"],
                    "period": next((p["name"] for p in record["daytime"] if p["date"] == day), None),
                    "updated": record.get("updateTime"), "source": record["url"], "sha256": record["sha256"],
                }
        return forecasts, self.records

    def save(self) -> None:
        write_cache(self.gridpoints_path, self.gridpoints)


def event_date_suffix(day: str) -> str:
    """2026-09-21 -> 26SEP21 (Kalshi weather event ticker suffix)."""
    try:
        year, month, dd = day.split("-")
    except ValueError:
        return ""
    names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return f"{year[2:]}{names[int(month) - 1]}{dd}"


def date_from_event_ticker(event_ticker: str):
    """KXHIGHCHI-26SEP21 -> datetime.date(2026, 9, 21) (None when the pattern does not match)."""
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not match or match.group(2) not in MONTHS:
        return None
    try:
        return datetime(2000 + int(match.group(1)), MONTHS[match.group(2)], int(match.group(3))).date()
    except ValueError:
        return None


# ============================================================================ ESPN · scoreboard
ESPN_LEAGUES = {  # Kalshi game series -> ESPN site-API sport/league (verified live 2026-09-20)
    "KXNFLGAME": ("football", "nfl"),
    "KXNBAGAME": ("basketball", "nba"),
    "KXMLBGAME": ("baseball", "mlb"),
    "KXNCAAFGAME": ("football", "college-football"),
}

GAME_RULES = re.compile(
    r"If\s+(?P<team>.+?)\s+wins the\s+(?P<away>.+?)\s+vs\s+(?P<home>.+?)\s+.*?game originally scheduled for\s+"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})", re.IGNORECASE)
ESPN_MONTHS = {name: index + 1 for index, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"])}
# Kalshi's rules_primary writes the month abbreviated (verified 2026-09-20 on
# KXNFLGAME-26SEP28PHICHI-PHI: "... originally scheduled for Sep 28, 2026"), so accept both forms.
ESPN_MONTHS.update({name[:3]: value for name, value in ESPN_MONTHS.items()})


def game_from_market(market: dict) -> dict | None:
    """Away/home team names + scheduled date straight from the market's official rules_primary.

    Verified example (KXNFLGAME-26SEP28PHICHI-PHI, 2026-09-20):
      "If Philadelphia wins the Philadelphia vs Chicago Pro Football game originally scheduled for
       Sep 28, 2026, then the market resolves to Yes."  -> away=Philadelphia, home=Chicago, 2026-09-28
    """
    text = market.get("rules_primary") or ""
    match = GAME_RULES.search(text)
    if not match or match.group("month") not in ESPN_MONTHS:
        return None
    try:
        scheduled = datetime(int(match.group("year")), ESPN_MONTHS[match.group("month")], int(match.group("day"))).date()
    except ValueError:
        return None
    return {"team": match.group("team").strip(), "away": match.group("away").strip(),
            "home": match.group("home").strip(), "scheduled": scheduled.isoformat(),
            "marketTeam": (market.get("yes_sub_title") or market.get("title") or "").strip()}


class EspnScoreboard:
    """Public ESPN scoreboard snapshots mapped to Kalshi game markets by team names + date.

    ESPN is a listed settlement source for KXNCAAFGAME / KXMLBGAME / KXNBAGAME and the scoreboard is
    public JSON; the mapping is only accepted when exactly one event matches, otherwise the adapter
    returns None and the strategy abstains (recorded as an irregularity, not guessed).
    """

    def __init__(self, fetcher=http_fetch, leagues: dict | None = None):
        self.fetcher = fetcher
        self.leagues = leagues or ESPN_LEAGUES
        self.errors: list[str] = []
        self.events: list[dict] = []
        self.snapshots: list[dict] = []
        self._by_date: dict[str, list[dict]] = {}

    def _compact_event(self, event: dict, league_key: str) -> dict:
        competitions = event.get("competitions") or [{}]
        competition = competitions[0] if competitions else {}
        status = ((competition.get("status") or event.get("status") or {}).get("type") or {})
        competitors = []
        for entry in competition.get("competitors") or []:
            team = entry.get("team") or {}
            competitors.append({
                "homeAway": entry.get("homeAway"), "score": _number(entry.get("score")),
                "abbreviation": team.get("abbreviation"), "displayName": team.get("displayName"),
                "shortDisplayName": team.get("shortDisplayName"), "location": team.get("location"),
                "winner": entry.get("winner"),
            })
        return {"id": event.get("id"), "date": event.get("date"), "name": event.get("name"),
                "shortName": event.get("shortName"), "league": league_key,
                "state": status.get("state"), "detail": status.get("shortDetail") or status.get("detail"),
                "clock": status.get("displayClock"), "period": ((competition.get("status") or {}).get("period")),
                "completed": bool(status.get("completed")), "competitors": competitors,
                "customStrike": None}

    def fetch(self, league_key: str, date_yyyymmdd: str) -> list[dict]:
        sport, league = self.leagues[league_key]
        url = ESPN_SCOREBOARD.format(sport=sport, league=league) + f"?dates={date_yyyymmdd}&limit=200"
        try:
            payload, raw = self.fetcher(url)
        except Exception as error:
            self.errors.append(f"espn {league} {date_yyyymmdd}: {error}")
            return []
        events = [self._compact_event(e, league_key) for e in (payload.get("events") or [])]
        self.snapshots.append({"kind": "espn-scoreboard", "league": league_key, "sport": sport, "date": date_yyyymmdd,
                               "url": url, "events": len(events), "retrievedAt": iso(int(time.time())),
                               "sha256": sha256_bytes(raw),
                               "events_compact": [{k: e[k] for k in ("id", "date", "name", "shortName", "state", "detail", "competitors")}
                                                  for e in events]})
        self.events.extend(events)
        self._by_date.setdefault(date_yyyymmdd, []).extend(events)
        return events

    def all_events(self) -> list[dict]:
        return self.events

    @staticmethod
    def _team_matches(name: str, competitor: dict) -> bool:
        for field in ("location", "abbreviation", "shortDisplayName", "displayName"):
            value = competitor.get(field)
            if value and normalize_name(value) == normalize_name(name):
                return True
        return False

    def match(self, game: dict, league_key: str, date_keys: list[str]) -> dict | None:
        """Unique ESPN event for a Kalshi game (date within +-1 day, both team names matching)."""
        candidates = []
        for key in date_keys:
            for event in self._by_date.get(key, []):
                if event.get("league") != league_key:
                    continue
                competitors = event.get("competitors") or []
                if len(competitors) != 2:
                    continue
                by_role = {c.get("homeAway"): c for c in competitors}
                away, home = by_role.get("away"), by_role.get("home")
                if not away or not home:
                    continue
                if self._team_matches(game["away"], away) and self._team_matches(game["home"], home):
                    candidates.append({"event": event, "away": away, "home": home, "dateKey": key,
                                       "matchedVia": "rules_primary team names + scheduled date"})
                elif self._team_matches(game["away"], home) and self._team_matches(game["home"], away):
                    candidates.append({"event": event, "away": home, "home": away, "dateKey": key,
                                       "matchedVia": "rules_primary team names (home/away swapped) + scheduled date"})
        unique: dict[str, dict] = {}
        for candidate in candidates:  # the same event can appear in more than one date query
            unique.setdefault(str(candidate["event"].get("id")), candidate)
        if len(unique) != 1:
            if unique:
                self.errors.append(f"espn: {len(unique)} events match {game['away']} @ {game['home']} - abstaining")
            return None
        return next(iter(unique.values()))

    def signal_for(self, market: dict, date_keys: list[str]) -> dict | None:
        """Live-score snapshot for one Kalshi game market (or None: no unique official match)."""
        series = market.get("series_ticker") or ""
        if series not in self.leagues:
            return None
        game = game_from_market(market)
        if not game:
            return None
        found = self.match(game, series, date_keys)
        if not found:
            return None
        event, away, home = found["event"], found["away"], found["home"]
        market_team = normalize_name(market.get("yes_sub_title") or market.get("title") or "")
        side = None
        for candidate, side_name in ((away, "away"), (home, "home")):
            if self._team_matches(market_team, candidate):
                side = side_name
        if side is None:
            return None
        away_score, home_score = away.get("score"), home.get("score")
        diff = None
        if away_score is not None and home_score is not None:
            diff = (home_score - away_score) if side == "home" else (away_score - home_score)
        return {"espnEventId": event.get("id"), "league": series, "state": event.get("state"),
                "detail": event.get("detail"), "clock": event.get("clock"),
                "scheduled": game["scheduled"], "away": game["away"], "home": game["home"],
                "marketSide": side, "awayScore": away_score, "homeScore": home_score,
                "scoreDiff": diff, "completed": event.get("completed"), "matchedVia": found["matchedVia"]}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def date_keys_around(ts: int, days: int = 1) -> list[str]:
    """YYYYMMDD keys for ts -days .. ts +days (UTC)."""
    out = []
    for offset in range(-days, days + 1):
        out.append(datetime.fromtimestamp(int(ts) + offset * 86400, tz=timezone.utc).strftime("%Y%m%d"))
    return out


# ============================================================================ openFDA · Drugs@FDA
FDA_TITLE_PATTERNS = [
    re.compile(r"approve\s+(?P<name>[A-Za-z][A-Za-z0-9'\- ]{2,60}?)(?:\s*\((?P<code>[A-Za-z0-9\- ]{2,20})\))?"
               r"(?:\s+for\b|\s+before\b|\s*\?|$)", re.IGNORECASE),
    re.compile(r"approval of\s+(?P<name>[A-Za-z][A-Za-z0-9'\- ]{2,60}?)(?:\s*\((?P<code>[A-Za-z0-9\- ]{2,20})\))?"
               r"(?:\s+for\b|\s+before\b|\s*\?|$)", re.IGNORECASE),
]
# Series whose markets are about an FDA drug *application* decision (verified in the series catalog:
# KXFDAAPPROVE "Will the FDA approve drug?", KXFDARETATRUTIDE, KXFDAAPPROVALDATE*).  Politics/agency
# series (KXFDA, KXFDABANS, KXFDAVAPE, KXFDACALIFF, KXFDANOM, KXFDAANNOUNCE) are deliberately excluded:
# Drugs@FDA records do not describe them.
FDA_DRUG_SERIES = re.compile(r"^KXFDA(APPROVE|APPROVAL|APPROVALDATE|RETATRUTIDE|TYPE1DIABETES)")


def drug_from_title(title: str) -> dict | None:
    """Drug name (+ optional development code) from an official Kalshi market title, or None.

    Verified titles this parses (from the committed ledger, captured from GET /markets):
      "Will the FDA approve cytisinicline for smoking cessation before Oct 1, 2026?"
      "When will the FDA approve retatrutide (LY3437943)?"
      "When will the FDA approve Lonvoguran Ziclumeran (lonvo-z) for Hereditary Angioedema?"
    """
    text = (title or "").strip()
    for pattern in FDA_TITLE_PATTERNS:
        match = pattern.search(text)
        if match:
            name = match.group("name").strip(" ,")
            code = (match.group("code") or "").strip()
            if len(name) >= 3:
                return {"name": name, "code": code or None, "title": text}
    return None


class OpenFdaRecords:
    """openFDA Drugs@FDA lookups for the drug named in a Kalshi FDA market.

    Drugs@FDA (openFDA `drug/drugsfda`) publishes application numbers, sponsors and submission
    status dates (YYYYMMDD).  It does NOT publish PDUFA target action dates - that gap is recorded
    as an irregularity, and this adapter only ever reports what the response contains.
    """

    def __init__(self, cache_path: str | None = None, fetcher=http_fetch, max_age_days: int = 7):
        self.cache_path = cache_path or os.path.join(UNIVERSE_DIR, "fda-records.json")
        self.fetcher = fetcher
        self.max_age_days = max_age_days
        self.cache = read_cache(self.cache_path, {"schemaVersion": 1, "source": OPENFDA_DRUGSFDA, "records": {}})
        self.cache.setdefault("records", {})
        self.errors: list[str] = []
        self.records: list[dict] = []

    def _search(self, query: str) -> tuple[list[dict], str, str] | None:
        url = OPENFDA_DRUGSFDA + "?" + urllib.parse.urlencode({"search": query, "limit": 5})
        try:
            payload, raw = self.fetcher(url)
        except Exception as error:
            self.errors.append(f"openfda {query}: {error}")
            return None
        rows = payload.get("results") or []
        return rows, url, sha256_bytes(raw)

    def lookup(self, drug: dict) -> dict | None:
        key = normalize_name(drug["name"]) + ("|" + normalize_name(drug.get("code") or ""))
        cached = self.cache["records"].get(key)
        fetched = cached.get("retrievedAt") if cached else None
        if cached and fetched:
            try:
                age = time.time() - datetime.fromisoformat(fetched.replace("Z", "+00:00")).timestamp()
                if age < self.max_age_days * 86400:
                    hit = dict(cached, cached=True)
                    self.records.append(hit)
                    return hit
            except ValueError:
                pass
        queries = [f'openfda.substance_name:"{drug["name"].upper()}"',
                   f'openfda.brand_name:"{drug["name"].upper()}"']
        if drug.get("code"):
            queries.append(f'openfda.substance_name:"{drug["code"].upper()}"')
        applications: list[dict] = []
        used = None
        sha = None
        for query in queries:
            found = self._search(query)
            if found is None:
                return None
            rows, url, digest = found
            if rows:
                used, sha = url, digest
                for row in rows:
                    submissions = [s for s in row.get("submissions") or []
                                   if s.get("submission_type") == "ORIG"]
                    approved = sorted([s for s in submissions if s.get("submission_status") == "AP"
                                       and s.get("submission_status_date")],
                                      key=lambda s: s["submission_status_date"])
                    applications.append({
                        "application_number": row.get("application_number"),
                        "sponsor_name": row.get("sponsor_name"),
                        "brand_name": ((row.get("openfda") or {}).get("brand_name") or [None])[0],
                        "substance_name": ((row.get("openfda") or {}).get("substance_name") or [None])[0],
                        "orig_submissions": len(submissions),
                        "first_orig_approved": (approved[0]["submission_status_date"] if approved else None),
                        "latest_orig_approved": (approved[-1]["submission_status_date"] if approved else None),
                        "review_priority": (submissions[-1].get("review_priority") if submissions else None),
                    })
                break
        record = {"kind": "openfda-drugsfda", "drug": drug["name"], "code": drug.get("code"),
                  "query": used, "url": used, "sha256": sha, "hits": len(applications),
                  "applications": applications[:5], "approvedRecord": bool(applications),
                  "retrievedAt": iso(int(time.time())), "cached": False,
                  "note": "openFDA Drugs@FDA: application/sponsor + ORIG submission status dates (YYYYMMDD). "
                          "No PDUFA target action date is published by this endpoint (IRR-27); absence of a "
                          "record means no approved application is listed as of retrievedAt."}
        self.cache["records"][key] = record
        self.cache["updatedAt"] = iso(int(time.time()))
        write_cache(self.cache_path, self.cache)
        self.records.append(record)
        return record

    def save(self) -> None:
        write_cache(self.cache_path, self.cache)


def fda_signal(market: dict, records: "OpenFdaRecords") -> dict | None:
    """Signal for one Kalshi FDA market: only positive, verifiable record evidence is returned."""
    series = market.get("series_ticker") or ""
    if not FDA_DRUG_SERIES.match(series):
        return None
    drug = drug_from_title(market.get("title") or "")
    if not drug:
        return None
    record = records.lookup(drug)
    if not record:
        return None
    return {"drug": drug["name"], "code": drug.get("code"), "approvedRecord": record["approvedRecord"],
            "hits": record["hits"], "applications": record["applications"][:2], "asOf": record["retrievedAt"],
            "source": record["url"], "sha256": record["sha256"]}
