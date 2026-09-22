#!/usr/bin/env python3
"""Additional official, free, key-less point-in-time signal adapters.

This module extends ``scripts/signals.py`` with the adapters added on 2026-09-22 and keeps the
same design rules:

  * a fetch returns ``(parsed_value, verbatim_bytes)`` so the caller can archive the exact bytes
    with their SHA-256 BEFORE any rule reads the value (evidence, not memory);
  * a failed, empty or ambiguous lookup returns ``None`` and appends a reason to ``errors`` - the
    strategy abstains and the reason is committed, nothing is inferred or defaulted;
  * every endpoint is public, free, key-less and documented below with its official reference.

Verified endpoint references (all read 2026-09-22):

  ESPN injuries JSON (public, key-less) ....... https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/injuries
      Shape observed live 2026-09-22T22:51:42Z on basketball/nba:
        {timestamp, status, season{year,type,name}, injuries:[{id, displayName,
          injuries:[{id, longComment, shortComment, status, date, athlete{...,
          position{abbreviation}, team{abbreviation}}}]}]}
      The sibling NFLInjuryReport project measured the same endpoint at HTTP 200 from a GitHub
      runner on 2026-09-10 and re-probed it 2026-09-18 (its data/latest/health.json source
      ledger); it also measured the per-team form (.../teams/22/injuries) returning an empty
      body, so only the league-wide endpoint is used here.  ESPN is NOT official league
      confirmation: the adapter reports what ESPN published, with the row's own date.

  Cleveland Fed Inflation Nowcasting (official). https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting
      Official daily nowcast of CPI / Core CPI / PCE / Core PCE, month-over-month percent change,
      year-over-year percent change and quarterly annualized percent change.  The page is HTML
      (no documented JSON/CSV API was found on 2026-09-22), so the parser reads the published
      table cells and the verbatim HTML is archived; a value is only accepted when the row's
      month label matches the calendar month the caller asks for and the number is inside a
      sanity band, otherwise the adapter abstains.

  FRED CSV graph endpoint (official, key-less). https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}
      Federal Reserve Bank of St. Louis data service.  ``observation_date,{SERIES}`` CSV; a
      holiday row is published with an empty value and is recorded as an absent observation -
      never carried forward.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from signals import (USER_AGENT, http_fetch, iso, sha256_bytes, normalize_name)  # noqa: F401

# --------------------------------------------------------------------------------------- ESPN
ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/injuries"
# Kalshi game series -> ESPN sport/league for the injuries endpoint (same split as the scoreboard).
INJURY_LEAGUES = {
    "KXNFLGAME": ("football", "nfl"),
    "KXNBAGAME": ("basketball", "nba"),
}
# ESPN's own status strings (observed verbatim: "Out").  Only these are treated as a
# designation; anything else ("Day-To-Day", "Questionable", ...) is counted but never traded.
INJURY_HARD_STATUSES = {"out", "doubtful", "injured reserve", "suspended"}


def parse_injuries(payload: dict) -> dict:
    """Compact the ESPN injuries payload into per-team reports (raises on an unknown shape)."""
    if not isinstance(payload, dict) or "injuries" not in payload:
        raise ValueError("ESPN injuries payload has no 'injuries' array")
    teams: dict[str, dict] = {}
    total = 0
    for entry in payload.get("injuries") or []:
        if not isinstance(entry, dict):
            continue
        team = entry.get("displayName") or ((entry.get("team") or {}).get("displayName"))
        if not team:
            continue
        rows = []
        abbreviations = []
        for player in entry.get("injuries") or []:
            if not isinstance(player, dict):
                continue
            athlete = player.get("athlete") or {}
            status = (player.get("status") or "").strip()
            if not status:
                continue
            position = ((athlete.get("position") or {}).get("abbreviation") or "").strip().upper()
            row_abbrev = (((player.get("team") or {}).get("abbreviation")
                           or (athlete.get("team") or {}).get("abbreviation") or "")).strip().upper()
            if row_abbrev:
                abbreviations.append(row_abbrev)
            rows.append({
                "athlete": athlete.get("displayName") or "",
                "position": position,
                "status": status,
                "hard": status.lower() in INJURY_HARD_STATUSES,
                "date": player.get("date"),
                "shortComment": (player.get("shortComment") or "")[:400],
            })
            total += 1
        entry_abbrev = ((entry.get("team") or {}).get("abbreviation") or "").strip().upper()
        teams[team] = {
            "team": team,
            # the league-wide payload carries the club abbreviation on each injury row; the entry
            # itself only names the club ("Atlanta Hawks"), so both are used for matching.
            "abbreviation": entry_abbrev or (abbreviations[0] if abbreviations else ""),
            "nickname": team.split()[-1] if team.split() else team,
            "players": rows,
            "hardOut": sum(1 for r in rows if r["hard"]),
            "qbOut": any(r["hard"] and r["position"] == "QB" for r in rows),
            "latestDate": max([r["date"] for r in rows if r.get("date")], default=None),
        }
    return {"teams": teams, "playerCount": total, "teamCount": len(teams),
            "timestamp": payload.get("timestamp"),
            "season": ((payload.get("season") or {}).get("displayName") or None)}


def _token_match(name: str, candidate: dict) -> bool:
    """Same widening rule as signals.EspnScoreboard._team_matches (abbreviation or >=3-char token).

    ``candidate`` may be a stored team report (keys "team"/"abbreviation"/"nickname") or a bare
    ``{"team": ...}`` mapping, which is what the scoreboard-side helpers pass.
    """
    if not name:
        return False
    wanted = name.strip()
    abbrev = (candidate.get("abbreviation") or "").strip().upper()
    if abbrev and abbrev == wanted.upper():
        return True
    if candidate.get("nickname") and candidate["nickname"].strip().lower() == wanted.lower():
        return True
    want = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 3}
    if not want:
        return False
    have_text = " ".join(str(candidate.get(k) or "") for k in ("team", "nickname"))
    have = {t for t in re.split(r"[^a-z0-9]+", have_text.lower()) if len(t) >= 3}
    return bool(want & have)


def team_report(report: dict, team_name: str) -> dict | None:
    """The stored per-team report matching a Kalshi team string, or None (never a guess)."""
    if not report:
        return None
    teams = report.get("teams") or {}
    matches = [v for v in teams.values() if _token_match(team_name, v)]
    if len(matches) != 1:
        return None
    return matches[0]


class EspnInjuries:
    """League-wide ESPN injury snapshots, archived verbatim, matched to teams by name."""

    def __init__(self, fetcher=http_fetch, leagues: dict | None = None):
        self.fetcher = fetcher
        self.leagues = leagues or INJURY_LEAGUES
        self.errors: list[str] = []
        self.records: list[dict] = []
        self.reports: dict[str, dict | None] = {}

    def fetch(self, league_key: str) -> dict | None:
        if league_key not in self.leagues:
            return None
        if league_key in self.reports:
            return self.reports[league_key]
        sport, league = self.leagues[league_key]
        url = ESPN_INJURIES_URL.format(sport=sport, league=league)
        try:
            payload, raw = self.fetcher(url)
        except Exception as error:  # noqa: BLE001 - a failed feed must abstain, not raise
            self.errors.append(f"espn-injuries {league_key}: {error}")
            self.reports[league_key] = None
            return None
        try:
            report = parse_injuries(payload)
        except Exception as error:  # noqa: BLE001
            self.errors.append(f"espn-injuries {league_key}: {error}")
            self.reports[league_key] = None
            return None
        report.update({"league": league_key, "sourceUrl": url, "sha256": sha256_bytes(raw),
                       "bytes": len(raw), "at": iso(int(datetime.now(tz=timezone.utc).timestamp()))})
        self.records.append({k: report[k] for k in ("league", "sourceUrl", "sha256", "bytes", "at",
                                                    "teamCount", "playerCount", "timestamp", "season")})
        self.reports[league_key] = report
        return report

    def signal_for(self, market: dict, game: dict, opponent_name: str, window_hours: float,
                   now_ts: int) -> dict | None:
        """Hard-out summary for one team, restricted to rows dated inside ``window_hours``.

        Returns None when the feed is absent or the team cannot be matched uniquely.
        """
        report = self.reports.get(market.get("series_ticker"))
        if not report:
            return None
        found = team_report(report, opponent_name)
        if not found:
            return None
        cutoff = now_ts - window_hours * 3600
        ceiling = now_ts + 3600  # one hour of clock skew; a future-dated row is not news yet
        fresh, stale = [], []
        for row in found["players"]:
            if not row["hard"]:
                continue
            try:
                stamp = datetime.strptime(row["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                ts = int(stamp.timestamp())
            except (TypeError, ValueError):
                stale.append(row)   # an unparseable date is never treated as fresh
                continue
            (fresh if cutoff <= ts <= ceiling else stale).append(dict(row, ts=iso(ts)))
        if not fresh:
            return None
        return {
            "league": report["league"], "espnTeam": found["team"], "espnAbbreviation": found["abbreviation"],
            "opponent": opponent_name, "windowHours": window_hours,
            "freshHardOut": len(fresh), "freshQbOut": sum(1 for r in fresh if r["position"] == "QB"),
            "freshOutPlayers": [{"athlete": r["athlete"], "position": r["position"],
                                 "status": r["status"], "date": r["date"]} for r in fresh],
            "staleHardOut": len(stale), "reportTimestamp": report.get("timestamp"),
            "sourceUrl": report["sourceUrl"], "sha256": report["sha256"],
            "matchedVia": "ESPN league injuries entry matched to the Kalshi rules_primary team names",
        }


# ------------------------------------------------------------------------------ Cleveland Fed
CLEVELAND_FED_NOWCAST_URL = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
NOWCAST_METRICS = ("cpi", "coreCpi", "pce", "corePce")
# Sanity bands per published section: a month-over-month percent change is a small number, while
# the year-over-year and quarterly tables are annual rates.  A value outside its band is treated as
# a parse problem and the adapter abstains (the observation is still archived verbatim).
NOWCAST_BANDS = {"month-over-month": (-2.0, 2.0), "year-over-year": (-5.0, 20.0),
                 "quarterly": (-10.0, 20.0)}
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def _strip_tags(fragment: str) -> str:
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


NOWCAST_SECTION_MARKERS = (("month-over-month", "month-over-month"),
                           ("year-over-year", "year-over-year"),
                           ("quarterly", "quarterly annualized"))
MONTH_ROW = re.compile(r"^(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})$")
QUARTER_ROW = re.compile(r"^(\d{4}):Q([1-4])$")


def _section_for(preceding_text: str) -> str | None:
    """The caption a table belongs to: the LAST marker seen in the text just before it.

    The page prints three captioned tables in order (month-over-month, year-over-year,
    quarterly annualized), so the nearest preceding caption is the table's own.
    """
    best = None
    for label, marker in NOWCAST_SECTION_MARKERS:
        index = preceding_text.rfind(marker)
        if index >= 0 and (best is None or index > best[0]):
            best = (index, label)
    return best[1] if best else None


def parse_nowcast_html(html: str) -> dict:
    """Extract the published nowcast tables from the official page HTML.

    Returns ``{"tables": {"month-over-month": [cells, ...], "year-over-year": [...],
    "quarterly": [...]}}`` where each row is the table's own cell list (``["September 2026",
    "0.43", "0.20", "0.40", "0.28", "09/22"]``).  Blank cells stay blank: the page prints
    nothing when the official actual has already been released, so a missing number can never be
    read as a value.  A table whose caption cannot be identified is skipped, not guessed.
    """
    tables: dict[str, list[list[str]]] = {}
    for match in re.finditer(r"<table[^>]*>(.*?)</table>", html, flags=re.S | re.I):
        preceding = _strip_tags(html[max(0, match.start() - 4000):match.start()]).lower()
        label = _section_for(preceding)
        if not label:
            continue
        rows: list[list[str]] = []
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), flags=re.S | re.I):
            cells = [_strip_tags(c) for c in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S | re.I)]
            if not cells:
                continue
            head = (cells[0] or "").strip()
            if MONTH_ROW.match(head) or QUARTER_ROW.match(head):
                rows.append(cells)
        if rows:
            tables[label] = rows
    return {"tables": tables}


def _cell_number(cell: str):
    if cell is None:
        return None
    value = cell.strip().replace("%", "").replace(",", "")
    if not value or value in {"-", "—"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def nowcast_row(tables: dict, section: str, label: str) -> dict | None:
    """One published row (``label`` like "September 2026" or "2026:Q3") from one table."""
    rows = (tables or {}).get(section) or []
    for cells in rows:
        if (cells[0] or "").strip().lower() == label.strip().lower():
            values = [_cell_number(c) for c in cells[1:5]]
            return {"section": section, "label": cells[0].strip(),
                    "updated": (cells[5].strip() if len(cells) > 5 else None),
                    **{NOWCAST_METRICS[i]: values[i] for i in range(len(values))}}
    return None


class ClevelandFedNowcast:
    """Official Cleveland Fed inflation nowcast (monthly MoM / YoY and quarterly)."""

    def __init__(self, fetcher=None, url: str = CLEVELAND_FED_NOWCAST_URL):
        self.fetcher = fetcher or self._fetch_bytes
        self.url = url
        self.errors: list[str] = []
        self.records: list[dict] = []
        self.tables: dict = {}
        self.last_error: str | None = None

    @staticmethod
    def _fetch_bytes(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "text/html"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return raw.decode("utf-8", errors="replace"), raw

    def fetch(self) -> dict | None:
        try:
            html, raw = self.fetcher(self.url)
        except Exception as error:  # noqa: BLE001
            self.last_error = f"cleveland-fed nowcast: {error}"
            self.errors.append(self.last_error)
            return None
        try:
            tables = parse_nowcast_html(html)["tables"]
        except Exception as error:  # noqa: BLE001
            self.last_error = f"cleveland-fed nowcast parse: {error}"
            self.errors.append(self.last_error)
            return None
        if not tables:
            self.last_error = "cleveland-fed nowcast parse: no published table rows matched"
            self.errors.append(self.last_error)
            return None
        self.tables = tables
        record = {"source": "Cleveland Fed Inflation Nowcasting", "sourceUrl": self.url,
                  "sha256": sha256_bytes(raw), "bytes": len(raw),
                  "at": iso(int(datetime.now(tz=timezone.utc).timestamp())),
                  "sections": {k: len(v) for k, v in tables.items()},
                  "rows": {k: [r[0] for r in v] for k, v in tables.items()}}
        self.records.append(record)
        return {"tables": tables, "sourceUrl": self.url, "sha256": record["sha256"],
                "at": record["at"], "bytes": record["bytes"]}

    def monthly(self, month_label: str, section: str = "month-over-month",
                metric: str = "cpi", band: tuple[float, float] | None = None) -> dict | None:
        """One month's published value with a sanity gate (abstains outside ``band``).

        ``band`` defaults to the published section's own plausible range (``NOWCAST_BANDS``); a
        caller may narrow it, but a value outside the range is never returned.
        """
        row = nowcast_row(self.tables, section, month_label)
        if not row:
            return None
        if band is None:
            band = NOWCAST_BANDS.get(section, (-2.0, 2.0))
        value = row.get(metric)
        if value is None or not (band[0] <= value <= band[1]):
            return None
        return {"label": row["label"], "section": section, "metric": metric, "value": value,
                "updated": row.get("updated"), "sourceUrl": self.url,
                "sha256": (self.records[-1]["sha256"] if self.records else None)}


# ---------------------------------------------------------------------------------------- FRED
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}&coed={end}"
FRED_SERIES = {  # Kalshi index series -> (FRED series id, published title)
    "KXINX": ("SP500", "S&P 500 (index level, FRED series SP500)"),
    "KXNASDAQ100": ("NASDAQ100", "Nasdaq-100 (index level, FRED series NASDAQ100)"),
}


class FredSeries:
    """Key-less official FRED CSV observations (S&P 500, Nasdaq-100 index levels)."""

    def __init__(self, fetcher=None, url_template: str = FRED_CSV_URL):
        self.url_template = url_template
        self.fetcher = fetcher or self._fetch_bytes
        self.errors: list[str] = []
        self.records: list[dict] = []
        self.observations: dict[str, list[dict]] = {}

    @staticmethod
    def _fetch_bytes(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        return raw.decode("utf-8", errors="replace"), raw

    def close(self, series_id: str, start: str, end: str) -> dict | None:
        url = self.url_template.format(series=urllib.parse.quote(series_id), start=start, end=end)
        try:
            text, raw = self.fetcher(url)
        except Exception as error:  # noqa: BLE001
            self.errors.append(f"fred {series_id}: {error}")
            return None
        rows: list[dict] = []
        for line in text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 2:
                continue
            day, value = parts[0].strip(), parts[1].strip()
            if not day:
                continue
            rows.append({"date": day, "value": (float(value) if value else None)})
        observed = [r for r in rows if r["value"] is not None]
        if not observed:
            self.errors.append(f"fred {series_id}: no numeric observation in {start}..{end}")
            return None
        record = {"source": "FRED (Federal Reserve Bank of St. Louis)", "seriesId": series_id,
                  "sourceUrl": url, "sha256": sha256_bytes(raw), "bytes": len(raw),
                  "at": iso(int(datetime.now(tz=timezone.utc).timestamp())),
                  "rows": len(rows), "observed": len(observed),
                  "latestDate": observed[-1]["date"], "latest": observed[-1]["value"]}
        self.records.append(record)
        self.observations[series_id] = rows
        return {"seriesId": series_id, "observations": rows, "latest": observed[-1],
                "absentDates": [r["date"] for r in rows if r["value"] is None],
                "sourceUrl": url, "sha256": record["sha256"], "at": record["at"]}


# ------------------------------------------------------------------- injury signal composition
def team_side(market_team: str, away: str, home: str) -> str | None:
    """Which side of a Kalshi game market a team string names ("away", "home" or None).

    Uses the same widening rule as the scoreboard matcher: exact abbreviation first, then any
    shared token of 3+ characters.  An ambiguous or absent match returns None (abstain), never a
    guess - and if both sides match, the market string is too generic to trade on.
    """
    if not market_team:
        return None
    first, second = _token_match(market_team, {"team": away}), _token_match(market_team, {"team": home})
    if first and not second:
        return "away"
    if second and not first:
        return "home"
    return None


def injury_signal_for_market(adapter: "EspnInjuries", market: dict, game: dict, now_ts: int,
                             window_hours: float) -> dict | None:
    """Compose the injury signal for one Kalshi game market (opponent's hard outs).

    Returns None when the feed is missing, the market's team cannot be resolved unambiguously, or
    the opponent has no hard-out designation inside the window.  The signal names the opponent and
    carries the ESPN source URL + SHA-256 so the desk can archive the evidence it traded on.
    """
    report = adapter.reports.get(market.get("series_ticker"))
    if not report:
        return None
    side = team_side(market.get("yes_sub_title") or market.get("title") or "", game.get("away") or "",
                     game.get("home") or "")
    if side is None:
        return None
    opponent = game.get("home") if side == "away" else game.get("away")
    detail = adapter.signal_for(market, game, opponent or "", window_hours, now_ts)
    if not detail:
        return None
    detail.update({"marketSide": side, "marketTeam": market.get("yes_sub_title") or market.get("title"),
                   "scheduled": game.get("scheduled"), "gameTeamAway": game.get("away"),
                   "gameTeamHome": game.get("home")})
    return detail
