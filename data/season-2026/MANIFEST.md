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
  90-day window (2026-06-20 → 2026-09-16) plus the 27-bar cold-open sample are stored. Backtests
  are scoped to stored data only.

## Re-verification pass, 2026-09-20 (IRR-12 closure + new findings)

Every URL above was re-fetched on 2026-09-20 via the sandbox fetch tool (direct egress is
still blocked; the fetch tool is the sandbox's network path). Raw JSON responses are stored
verbatim in `raw/` and SHA-256 bound. Results:

| Item | Result |
|---|---|
| cutoff, KXCPI/KXFED/KXNFLGAME settled, KXFED/KXBTC open, orderbook, series x3 | match the committed files (orderbook byte-exact) |
| KXFED open limit=4 | 3 of 4 tickers (KXFED-27APR-T5.50/T5.75/T6.00) match field-for-field; the list is dynamic (new 27APR strikes created 2026-09-18) |
| KXCPI-26AUG-T0.8 daily candles | `scripts/candles_from_raw.py --diff` OK — 44 bars, all fields (IRR-12 closure) |
| KXFED-26SEP-T4.75 90-day candles | `--diff` OK — 72 bars, all fields (IRR-12 closure) |
| KXNFLGAME-26SEP17DETBUF-BUF hourly candles | `--diff` OK after the IRR-15a fix (see below) |
| KXFED full-history window (start_ts=1754438400&end_ts=1789690000, 14 chunks) | chunks 0-1 read: 2025-08-07→2025-09-28 zone is zero volume/OI (untradeable), consistent with the committed cold-open file; remaining chunks not archived (see gaps) |

**Raw files stored this pass** (`raw/refetch-20260920-*`): the 13 manifest responses above plus
`lbma-gold-am-2026.json` (official LBMA AM gold, 2026-06-15..2026-09-18).

### IRR-15a — transcription shift found and fixed (2026-09-20)

The 2026-09-19 committed NFL CSV had `price_mean` shifted by one bar on four bars
(1789660800/1789664400/1789668000/1789675200) and `price_close` 0.70 (official 0.69) on
1789678800. The CSV was mechanically regenerated from the canonical raw
(`raw/refetch-20260920-candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.json`); `--diff` is now clean
on all 14 bars. Backtest impact: one trade's entry-bar attribution corrected. The old (shifted)
CSV showed a 0.70 ask on the 21:00Z bar (1789678800) where the verified ask was 0.69, so
CrossChaser's entry was recorded on that bar. With the corrected CSV the same signal fires on the
22:00Z bar (1789682400), where 0.70 was the verified ask: entry price $0.70, contracts 7142, exit
$0.69 bid, PnL -$283.34 and fees are unchanged, and the leaderboard is identical — but the fill is
now attributed to the bar on which that price was actually verified (the old record was an
implicit use of a not-yet-verified price).

### IRR-15b — exchange-side inconsistency (flagged, not "fixed")

Two official candlesticks calls for the same market, minutes apart, disagree for bars
1789678800-1789686000: the narrow window (`start_ts=1789657200&end_ts=1789686000`) returns
volume 188254.68/282642.38/457661.27, OI 2037998.19/**2295532.84**/2731437.64, close
0.69/0.70/**0.70**; the documented MANIFEST window returns volume 282642.38/457661.27/724830.69,
OI 2037998.19/2731437.64/3427644.61, close 0.69/0.70/**0.69**. Values are shifted one hour
later in the narrow response. **Decision: the documented MANIFEST URL is canonical** for this
repository. A future session should re-poll both windows and record which one persists.

### New official data verified 2026-09-20

- **Settlement & fee docs** (closes IRR-11): `https://docs.kalshi.com/getting_started/market_settlement`
  — "Settlement fees are zero for simple yes/no determinations"; `https://docs.kalshi.com/getting_started/fee_rounding`
  — 6-decimal fee granularity, rounding fee onto the member balance grid.
- **Per-market trade tape** (closes IRR-14): the filter parameter is `ticker` —
  `GET /markets/trades?ticker=KXBTC-26SEP2017-T90749.99` returned exactly 1 trade = the
  market's volume.
- **Exchange sharding** (`https://docs.kalshi.com/getting_started/exchange_sharding`): shard 2
  = crypto + commodities (since 2026-09-10); sharded market data needs `exchange_index=2`.
- **KXGOLDH — Gold Hourly** (Commodities, shard 2, Pyth-settled hourly 1-minute-candle strike
  binaries). Verified market rows (active + settled) and candlesticks: recent events have zero
  volume (e.g. KXGOLDH-26SEP1920-T4407.99 settled `no` @ 2026-09-20T01:03:14.503804Z,
  settlement value 4376.42, zero-volume bars, 0.24→0.03 yes-ask only). Forward-desk watch only —
  not backtest-ready (no liquid history yet).
- **LBMA gold AM** (official, free): `https://prices.lbma.org.uk/json/gold_am.json` reachable;
  2026 rows stored. Season context: peak 4634.20 (2026-08-24), 4387.00 (2026-09-18), ~8% off
  the peak. Cross-checks the Pyth-settled KXGOLDH level (4376.42 on 09-19 20:00 ET).
- **FRED/Stooq**: still unreachable from this sandbox (re-tested 2026-09-20) — IRR-13 gap stands
  for those two hosts only.

## Updated known gaps (2026-09-20)

- **KXFED full-history window**: only chunks 0-1 of 14 archived (the 2025-08-07→2025-09-28
  zero-volume prefix, consistent with the cold-open file). Chunks 2-13 (the 2025-09→2026-06
  middle) remain unarchived; the committed backtest window is unchanged.
- **KXGOLDH liquidity**: no KXGOLDH market with trading volume has been found yet; the next
  session should page settled KXGOLDH markets (cursor pagination) to locate the first liquid
  event and start the gold backtest.
- **Fresh KXBTC forward markets**: the committed forward intents reference 26SEP2017 markets that
  close 2026-09-20T21:00:00Z; a future desk refresh should pick up later-dated KXBTC markets.
- **CEO / NWS / SEC / FDA / NCAA / MLB / NFL / NBA adapters**: unchanged (see above).

## Forward desk added, 2026-09-20 (automated collector)

- `forward/` is written by `scripts/forward_desk.py` on GitHub Actions (`.github/workflows/forward-desk.yml`,
  cron `7,37 * * * *` on the default branch, manual dispatch on any branch). It is **excluded from
  `SHA256SUMS.txt`** because it changes every cycle; it binds itself through per-record `sha256`
  fields (order-book evidence rows are self-hashing: `sha256(raw) == sha256`) and its git history.
  `scripts/verify_data.py` checks the ledger invariants on every run.
- Endpoints per cycle (all official, unauthenticated): `GET /series/{ticker}` (cached 7 days),
  `GET /markets?series_ticker=…&status=open&limit=200` (+`exchange_index` for shards 2/3),
  `GET /markets/{ticker}/orderbook?depth=20`, `GET /markets/{ticker}` (settlement),
  `GET /series/{s}/markets/{t}/candlesticks` (period 1440 for the SMA persona, period 1 for the
  15-minute panic-fade persona), and `https://api.weather.gov/gridpoints/OKX/34,45/forecast` (NWS).
- Universe catalog: `data/universe/series-catalog.json` from `GET /series?category=…&include_volume=true`
  for 13 categories (13,607 series on 2026-09-20; `discovery-log.jsonl` holds the response hashes).
- Dry runs discarded before the first counted cycle: `20260920T031222Z`, `20260920T031848Z` (IRR-17;
  still visible in git history at commits 33c47ad / 36c4089). First counted cycle: `20260920T032126Z`.
- Resolved gaps from the list above: **KXGOLD liquidity** — the liquid gold series is `KXGOLD15M`
  (settled markets show 140k–154k contracts each; `KXGOLDH` stays thin); **fresh KXBTC forward
  markets** — the desk now refreshes its own universe every cycle; **CEO** — no MasterSite project
  exists (verified negative), Kalshi CEO series are traded directly; **NWS / FDA / game adapters** —
  live in the collector (NWS forecast archived; FDA and game personas trade the exchange price).
  Still open: KXFED full-history chunks 2–13, SEC/insider mapping, NBA official feed, ESPN scoreboard
  archive, other NWS cities, index/commodity range series for LeapMapper.
