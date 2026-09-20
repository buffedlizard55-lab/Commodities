#!/usr/bin/env python3
"""Minimal, dependency-free client for Kalshi's public (unauthenticated) Trade API.

Design rules (see README "Verification contract"):
  * Only official endpoints under https://external-api.kalshi.com/trade-api/v2 are called.
  * Every response body is kept verbatim by the caller when it becomes evidence for a
    fill / exit / settlement; this module only returns parsed JSON plus the raw bytes.
  * No credentials are ever read or sent.  This client can only READ.
  * Rate limiting: a small fixed pause between calls plus exponential backoff on 429/5xx.

The same module powers the scheduled collector (GitHub Actions has network egress) and the
offline "fixture" mode used by the unit tests (no network at all).
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
USER_AGENT = "Commodities-ResearchExchange/1.0 (+https://github.com/buffedlizard55-lab/Commodities; paper-trading research, read-only)"


class KalshiError(RuntimeError):
    pass


class KalshiClient:
    """Read-only HTTP client with a call log that the collector stores per cycle."""

    def __init__(self, base_url: str = BASE_URL, pause: float = 0.12, timeout: float = 30.0,
                 max_retries: int = 4, opener=None):
        self.base_url = base_url.rstrip("/")
        self.pause = pause
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self._opener = opener or urllib.request.build_opener()
        self._last_call = 0.0

    # ------------------------------------------------------------------ transport
    def _sleep_for_rate_limit(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.pause:
            time.sleep(self.pause - elapsed)

    def get(self, path: str, params: dict | None = None) -> tuple[dict, bytes, str]:
        """GET a JSON endpoint. Returns (parsed_json, raw_bytes, full_url)."""
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        attempt = 0
        while True:
            self._sleep_for_rate_limit()
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            started = time.time()
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                    status = response.status
            except urllib.error.HTTPError as error:
                raw = error.read() if error.fp else b""
                status = error.code
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                raw = str(error).encode()
                status = 0
            finally:
                self._last_call = time.monotonic()
            self.calls.append({"url": url, "status": status, "bytes": len(raw), "at": _iso(started),
                               "sha256": hashlib.sha256(raw).hexdigest()})
            if status == 200:
                try:
                    return json.loads(raw.decode("utf-8")), raw, url
                except json.JSONDecodeError as error:
                    raise KalshiError(f"non-JSON body from {url}: {error}") from error
            if status in (404, 400):
                raise KalshiError(f"HTTP {status} from {url}: {raw[:200]!r}")
            attempt += 1
            if attempt > self.max_retries:
                raise KalshiError(f"HTTP {status} from {url} after {attempt} attempts: {raw[:200]!r}")
            # 429 / 5xx / network: exponential backoff (documented guidance for 429).
            time.sleep(min(20.0, 0.5 * (2 ** attempt)))

    # ------------------------------------------------------------------ endpoints
    def cutoff(self) -> dict:
        return self.get("historical/cutoff")[0]

    def series(self, ticker: str) -> dict:
        return self.get(f"series/{ticker}")[0].get("series", {})

    def series_list(self, category: str | None = None, include_volume: bool = True) -> list[dict]:
        payload = self.get("series", {"category": category, "include_volume": "true" if include_volume else None})[0]
        return payload.get("series") or []

    def markets(self, series_ticker: str | None = None, status: str | None = None, limit: int = 200,
                max_pages: int = 10, exchange_index: int | None = None, event_ticker: str | None = None,
                min_close_ts: int | None = None, max_close_ts: int | None = None) -> list[dict]:
        """Cursor-paginated market list (documented pagination). Bounded by max_pages."""
        out: list[dict] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": status, "limit": limit, "cursor": cursor or None,
                      "event_ticker": event_ticker, "min_close_ts": min_close_ts, "max_close_ts": max_close_ts}
            if exchange_index:
                params["exchange_index"] = exchange_index
            payload = self.get("markets", params)[0]
            rows = payload.get("markets") or []
            out.extend(rows)
            cursor = str(payload.get("cursor") or "")
            if not cursor or not rows:
                break
        return out

    def market(self, ticker: str, exchange_index: int | None = None) -> tuple[dict, bytes, str]:
        params = {"exchange_index": exchange_index} if exchange_index else None
        payload, raw, url = self.get(f"markets/{urllib.parse.quote(ticker, safe='')}", params)
        return payload.get("market", payload), raw, url

    def orderbook(self, ticker: str, depth: int = 100, exchange_index: int | None = None) -> tuple[dict, bytes, str]:
        params = {"depth": depth}
        if exchange_index:
            params["exchange_index"] = exchange_index
        payload, raw, url = self.get(f"markets/{urllib.parse.quote(ticker, safe='')}/orderbook", params)
        return payload, raw, url

    def candlesticks(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int, period_interval: int,
                     exchange_index: int | None = None) -> tuple[dict, bytes, str]:
        params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval}
        if exchange_index:
            params["exchange_index"] = exchange_index
        return self.get(f"series/{series_ticker}/markets/{urllib.parse.quote(ticker, safe='')}/candlesticks", params)

    def trades(self, ticker: str | None = None, limit: int = 100, min_ts: int | None = None,
               max_ts: int | None = None) -> tuple[dict, bytes, str]:
        return self.get("markets/trades", {"ticker": ticker, "limit": limit, "min_ts": min_ts, "max_ts": max_ts})


class FixtureClient(KalshiClient):
    """Offline client used by tests: serves pre-recorded JSON bodies keyed by path+query."""

    def __init__(self, fixtures: dict[str, object]):
        super().__init__(pause=0.0)
        self.fixtures = fixtures

    def get(self, path: str, params: dict | None = None):
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        key = path.lstrip("/")
        if query:
            key += "?" + urllib.parse.urlencode(query, doseq=True)
        if key not in self.fixtures:
            # Allow lookups without the query string for convenience in tests.
            if path.lstrip("/") in self.fixtures:
                key = path.lstrip("/")
            else:
                self.calls.append({"url": key, "status": 404, "bytes": 0, "at": _iso(time.time()), "sha256": ""})
                raise KalshiError(f"HTTP 404 (fixture missing) for {key}")
        body = self.fixtures[key]
        raw = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
        self.calls.append({"url": f"{self.base_url}/{key}", "status": 200, "bytes": len(raw), "at": _iso(time.time()),
                           "sha256": hashlib.sha256(raw).hexdigest()})
        return json.loads(raw.decode()), raw, f"{self.base_url}/{key}"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
