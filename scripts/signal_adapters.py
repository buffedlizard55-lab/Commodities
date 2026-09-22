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

import os
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
# A monthly table can only ever be one of these two; "quarterly" is decided by a table's own rows.
MONTHLY_SECTIONS = ("month-over-month", "year-over-year")
MONTH_ROW = re.compile(r"^(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})$")
QUARTER_ROW = re.compile(r"^(\d{4}):Q([1-4])$")
# A caption farther than this from a table is not treated as that table's caption.  The live page
# keeps them adjacent (a few hundred characters); the reach only guards against a distant or
# repeated string elsewhere on the page.
NOWCAST_CAPTION_REACH = 4000


def _html_text_map(html: str):
    """The page's visible text plus, for every character, its offset in the original HTML.

    Captions are looked up in this text rather than in the raw markup, so a marker hidden inside a
    tag (an ``id``, an ``href="#month-over-month"``, a script constant) can never be mistaken for a
    printed caption.
    """
    text: list[str] = []
    offsets: list[int] = []
    in_tag = False
    for index, char in enumerate(html):
        if char == "<":
            in_tag = True
        elif char == ">":
            in_tag = False
        elif not in_tag:
            text.append(char)
            offsets.append(index)
    return "".join(text), offsets


def _caption_positions(html: str) -> list[tuple[int, str]]:
    """Every printed caption on the page as ``(html_offset, section)``, in document order."""
    text, offsets = _html_text_map(html)
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for label, marker in NOWCAST_SECTION_MARKERS:
        index = lowered.find(marker)
        while index >= 0:
            found.append((offsets[index], label))
            index = lowered.find(marker, index + 1)
    return sorted(found)


def _pair_monthly(tables: list[dict], captions: list[tuple[int, str]]) -> list[tuple[dict, tuple]]:
    """Pair monthly tables with monthly captions, in document order and cheapest first.

    The page prints its month-over-month caption before its year-over-year caption and its two
    monthly tables in that same order, whether the captions sit above or below the tables, so a
    pairing may not cross: a caption can only belong to a table that comes after the previous
    pair's caption and table.  The cheapest non-crossing pairing wins, a pair farther apart than
    ``NOWCAST_CAPTION_REACH`` costs more than leaving both unpaired, so it is dropped - which is how
    an extra mention of a section name in the page's prose cannot shift a real table onto the wrong
    caption.
    """
    monthly = [c for c in captions if c[1] in MONTHLY_SECTIONS]
    n, m = len(tables), len(monthly)
    skip = NOWCAST_CAPTION_REACH + 1  # dearer than any acceptable pair, cheaper than no pairing

    def gap(table: dict, position: int) -> int:
        return min(abs(position - table["start"]), abs(position - table["end"]))

    cost = [[gap(tables[i], pos) for pos, _label in monthly] for i in range(n)]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    take = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * skip
        take[i][0] = "skip-table"
    for j in range(1, m + 1):
        dp[0][j] = j * skip
        take[0][j] = "skip-caption"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, action = dp[i - 1][j] + skip, "skip-table"
            if dp[i][j - 1] + skip < best:
                best, action = dp[i][j - 1] + skip, "skip-caption"
            paired = dp[i - 1][j - 1] + cost[i - 1][j - 1]
            if paired < best:
                best, action = paired, "pair"
            dp[i][j], take[i][j] = best, action
    pairs: list[tuple[dict, tuple]] = []
    i, j = n, m
    while i > 0 and j > 0:
        action = take[i][j]
        if action == "pair":
            pairs.append((tables[i - 1], monthly[j - 1]))
            i, j = i - 1, j - 1
        elif action == "skip-table":
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def parse_nowcast_html(html: str) -> dict:
    """Extract the published nowcast tables from the official page HTML.

    Returns ``{"tables": {"month-over-month": [cells, ...], "year-over-year": [...],
    "quarterly": [...]}, "diagnostics": {...}}`` where each row is the table's own cell list
    (``["September 2026", "0.43", "0.20", "0.40", "0.28", "09/22"]``).  Blank cells stay blank: the
    page prints nothing when the official actual has already been released, so a missing number can
    never be read as a value.

    Classification (the page's caption placement has not been stable, so no rule assumes a side):
      * a table whose own rows are quarter labels ("2026:Q3") is the quarterly section - decided by
        content, never by a caption;
      * a table of month labels is monthly and takes the nearest printed caption before or after it
        ("Inflation, month-over-month percent change" / "... year-over-year percent change"), and a
        caption is claimed at most once, nearest table first;
      * a monthly table is never filed as the quarterly section, even if a "quarterly annualized"
        string happens to sit nearer (the runner's 2026-09-22T23:21Z read did exactly that);
      * a monthly table whose nearest caption is beyond ``NOWCAST_CAPTION_REACH``, or whose caption
        was already claimed by a closer table, is skipped - never guessed.

    ``diagnostics`` says what was seen and why anything was skipped, and ``complete`` is true only
    when all three sections were found, so a partial read is visible instead of silently wrong.
    """
    candidates: list[dict] = []
    for match in re.finditer(r"<table[^>]*>(.*?)</table>", html, flags=re.S | re.I):
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
            candidates.append({"start": match.start(), "end": match.end(), "rows": rows,
                               "quarterly": any(QUARTER_ROW.match((r[0] or "").strip())
                                                for r in rows)})
    tables: dict[str, list[list[str]]] = {}
    skipped: list[str] = []
    for candidate in candidates:
        if candidate["quarterly"]:
            if "quarterly" in tables:
                skipped.append(f"quarter table at {candidate['start']}: quarterly already found")
            else:
                tables["quarterly"] = candidate["rows"]
    monthly = [c for c in candidates if not c["quarterly"]]
    captions = _caption_positions(html)
    claims = []
    for candidate, (position, label) in _pair_monthly(monthly, captions):
        distance = min(abs(position - candidate["start"]), abs(position - candidate["end"]))
        if distance > NOWCAST_CAPTION_REACH:
            skipped.append(f"monthly table at {candidate['start']}: nearest caption "
                           f"{distance} characters away")
            continue
        claims.append((distance, candidate["start"], candidate, label))
    for distance, position, candidate, label in sorted(claims, key=lambda item: (item[0], item[1])):
        if label in tables:
            skipped.append(f"monthly table at {candidate['start']}: caption {label} already taken")
            continue
        tables[label] = candidate["rows"]
    for candidate in monthly:
        if not any(claim[2] is candidate for claim in claims):
            skipped.append(f"monthly table at {candidate['start']}: no caption paired")
    missing = [name for name in ("month-over-month", "year-over-year", "quarterly")
               if name not in tables]
    return {"tables": tables,
            "diagnostics": {"tablesSeen": len(candidates), "monthlyTables": len(monthly),
                            "quarterlyTables": sum(1 for c in candidates if c["quarterly"]),
                            "captionPositions": len(captions), "skipped": skipped,
                            "missing": missing, "complete": not missing}}


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

    def __init__(self, fetcher=None, url: str = CLEVELAND_FED_NOWCAST_URL,
                 raw_dir: str | None = None):
        self.fetcher = fetcher or self._fetch_bytes
        self.url = url
        # When the parse cannot be trusted the verbatim page is kept under raw_dir (the ledger's
        # raw evidence store) so the parser can be corrected from the bytes, not from memory.
        self.raw_dir = raw_dir
        self.errors: list[str] = []
        self.records: list[dict] = []
        self.tables: dict = {}
        self.diagnostics: dict = {}
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
            parsed = parse_nowcast_html(html)
        except Exception as error:  # noqa: BLE001
            self.last_error = f"cleveland-fed nowcast parse: {error}"
            self.errors.append(self.last_error)
            return None
        tables = parsed["tables"]
        self.diagnostics = parsed.get("diagnostics") or {}
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
        if not self.diagnostics.get("complete", True):
            missing = ", ".join(self.diagnostics.get("missing") or []) or "unknown"
            record["partial"] = True
            record["missingSections"] = self.diagnostics.get("missing")
            record["skippedTables"] = self.diagnostics.get("skipped")
            self.last_error = f"cleveland-fed nowcast parse: sections missing ({missing})"
            self.errors.append(self.last_error)
            raw_path = self.keep_raw_html(raw, record["sha256"])
            if raw_path:
                record["rawPath"] = raw_path
        self.records.append(record)
        return {"tables": tables, "sourceUrl": self.url, "sha256": record["sha256"],
                "at": record["at"], "bytes": record["bytes"],
                "complete": bool(not record.get("partial"))}

    def keep_raw_html(self, raw: bytes, digest: str) -> str | None:
        """Keep the verbatim page for a parse that could not be trusted (at most two files).

        The page is HTML-only, so when its tables cannot be filed with certainty the bytes are the
        only way to correct the parser from evidence - and they are committed with the ledger.  The
        oldest dump is rotated away so the store stays bounded.
        """
        if not self.raw_dir:
            return None
        try:
            os.makedirs(self.raw_dir, exist_ok=True)
            path = os.path.join(self.raw_dir, f"nowcast-{digest[:12]}.html")
            if not os.path.exists(path):
                with open(path, "wb") as handle:
                    handle.write(raw)
            dumps = sorted((os.path.join(self.raw_dir, name) for name in os.listdir(self.raw_dir)
                            if name.startswith("nowcast-") and name.endswith(".html")),
                           key=os.path.getmtime, reverse=True)
            for stale in dumps[2:]:
                os.remove(stale)
            return f"{os.path.basename(os.path.normpath(self.raw_dir))}/{os.path.basename(path)}"
        except OSError as error:
            self.errors.append(f"cleveland-fed nowcast raw dump: {error}")
            return None

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
