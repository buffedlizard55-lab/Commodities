# Season 2026 · Verified Data Manifest

All data below was retrieved on **2026-09-19 (UTC), session window ≈ 23:42–00:20Z**, from Kalshi's
production Trade API (`https://external-api.kalshi.com/trade-api/v2`). In-band evidence for the
window: the open-market snapshot contains a row created at `2026-09-19T23:42:44Z` and the official
trade tape sample contains fills timestamped `2026-09-19T23:44:09Z`.

Retrieval method note (honesty flag **IRR-12**): this sandbox has no direct egress to
`kalshi.com` (TLS blocked); each response was fetched through a server-side reader and
transcribed into the compact CSV/JSON files stored here. Single-field transcription errors are a
residual risk; they are mitigated by the automated cross-checks in `scripts/verify_data.py`
(volume sums, open-interest continuity, timestamp monotonicity, price bounds, settlement fields).
A future collector with direct API access can re-fetch the same URLs and diff byte-for-byte.

## Endpoints used (all official, unauthenticated, free)

| # | URL | Saved as | Purpose |
|---|-----|----------|---------|
| 1 | `GET /trade-api/v2/historical/cutoff` | `cutoff.json` | Live/historical tier boundary (2026-07-21T00:00:00Z) |
| 2 | `GET /trade-api/v2/markets?series_ticker=KXCPI&status=settled&limit=3` | `market-records.json` (settled KXCPI rows) | Verified settled market records incl. result + settlement_ts |
| 3 | `GET /trade-api/v2/markets?series_ticker=KXFED&status=settled&limit=3` | `market-records.json` (settled KXFED rows) | Same |
| 4 | `GET /trade-api/v2/markets?series_ticker=KXNFLGAME&status=settled&limit=2` | `market-records.json` (settled NFL rows) | Same |
| 5 | `GET /trade-api/v2/markets?series_ticker=KXFED&status=open&limit=4` | `market-records.json` (open KXFED rows) | Forward-desk open contracts |
| 6 | `GET /trade-api/v2/markets?series_ticker=KXBTC&status=open&limit=3` | `market-records.json` (open KXBTC rows) | Forward-desk open contracts |
| 7 | `GET /trade-api/v2/series/KXCPI/markets/KXCPI-26AUG-T0.8/candlesticks?start_ts=1784505600&end_ts=1789257600&period_interval=1440` | `candles-KXCPI-26AUG-T0.8-daily.csv` | Verified daily candles (44 bars) |
| 8 | `GET /trade-api/v2/series/KXFED/markets/KXFED-26SEP-T4.75/candlesticks?start_ts=1781913600&end_ts=1789776000&period_interval=1440` | `candles-KXFED-26SEP-T4.75-daily.csv` | Verified daily candles (72 bars) |
| 9 | `GET /trade-api/v2/series/KXFED/markets/KXFED-26SEP-T4.75/candlesticks?start_ts=1754006400&end_ts=1789776000&period_interval=1440` (chunk 0 only) | `candles-KXFED-26SEP-T4.75-cold-open-2025-08.csv` | Cold-open context (27 bars, zero volume; not backtested) |
| 10 | `GET /trade-api/v2/series/KXNFLGAME/markets/KXNFLGAME-26SEP17DETBUF-BUF/candlesticks?start_ts=1789646400&end_ts=1789697600&period_interval=60` | `candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv` | Verified hourly candles (14 bars) |
| 11 | `GET /trade-api/v2/markets/KXBTC-26SEP2017-T90749.99/orderbook?depth=40` | `orderbook-KXBTC-26SEP2017-T90749.99.csv` | Verified live order book (liquidity) |
| 12 | `GET /trade-api/v2/markets/trades?limit=25` | `trade-tape-sample.csv` | Verified official trade tape (all markets, last 25 fills) |
| 13 | `GET /trade-api/v2/series/KXFED` · `GET /trade-api/v2/series/KXCPI` · `GET /trade-api/v2/series/KXBTC` | `series-records.json` | Fee type, fee multiplier, settlement sources, contract terms URLs |

Docs verified for the same session (for manual review):
- Historical data tiers & cutoff semantics: `https://docs.kalshi.com/getting_started/historical_data`
- Live candlesticks endpoint (`/series/{series_ticker}/markets/{ticker}/candlesticks`, `period_interval` ∈ {1,60,1440}): `https://docs.kalshi.com/api-reference/market/get-market-candlesticks`
- Historical candlesticks endpoint: `https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks`
- Orderbook semantics (bids only; YES ask = 1 − best NO bid): `https://docs.kalshi.com/getting_started/orderbook_responses`
- Docs index: `https://docs.kalshi.com/llms.txt`

## Cross-checks that must pass (`scripts/verify_data.py`)

1. Every candle file: JSON/CSV parses; `end_period_ts` strictly increasing; all prices in [0,1].
2. KXCPI: sum of `volume` column == market record `volume_fp` 22306.56 (exact); last bar open
   interest == market record `open_interest_fp` 15842.78 (exact).
3. KXFED: last bar open interest == market record `open_interest_fp` 208343.78 (exact); first bar
   1781928000 has volume 1.00 and OI 317.00.
4. NFL BUF: 14 bars, first 1789646400 last 1789693200, last bar close 0.94, volume sum 8989894.63,
   OI path ends 6349995.83. No bar was returned for the 01:00–02:00Z hour (recorded, not filled).
5. Orderbook: 15 NO levels, 0 YES levels, total NO depth 53827.00.
6. Settlement fields: KXCPI-26AUG-T0.8 result `no` @ 2026-09-11T13:28:53.706257Z;
   KXFED-26SEP-T4.75 result `no` @ 2026-09-16T18:20:58.459351Z;
   KXNFLGAME-26SEP17DETBUF-BUF result `yes` @ 2026-09-18T03:34:55.133992Z; DET result `no`, same ts.
7. Hashes: `SHA256SUMS.txt` (generated) binds every stored file.

## Known gaps (flagged, not filled)

- **Gold/commodity price feed**: `fred.stlouisfed.org` returned an error page and `stooq.com`
  returned "Access denied" from this environment on 2026-09-19. No gold price is stored or used.
  The MetalMomentum strategy stays data-gated. Manual review links:
  `https://www.lbma.org.uk/prices-and-data/lbma-gold-price`, `https://fred.stlouisfed.org/series/GOLDAMGBD228NLBM`.
- **CEO**: still not resolvable to a MasterSite entry (carried over as unresolved from the prior
  session; see `data/source-registry.json`, id `ceo-unresolved`).
- **NWS / SEC / FDA / NCAA / MLB / NFL / NBA adapters**: not collected this session; the related
  strategies remain source-gated (no signal without the primary source response).
- The KXFED full-history window (2025-08 → 2026-09) was not fully captured; only the verified
  90-day window (2026-06-20 → 2026-09-16) plus the 26-bar cold-open sample are stored. Backtests
  are scoped to stored data only.
