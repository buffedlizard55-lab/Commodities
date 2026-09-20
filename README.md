# Commodities · Kalshi Research Exchange

An evidence-first paper-trading lab for Kalshi event contracts: return-seeking strategy
personas, a **committed backtest on verified official Kalshi price data**, and a **live
forward-test desk** that simulates upcoming trades against the public API at runtime.

Everything on this page traces to a stored, hash-bound source file or a documented official
endpoint. No synthetic quotes, no reconstructed bars, no silent fallbacks.

> Simulation only. This site never submits orders to Kalshi and never asks for API keys.

## What is in the repository

```
index.html, styles.css, src/        GitHub Pages site (published from the repo root)
  src/engine.js                     pure accounting / orderbook / fee / settlement helpers
  src/app.js                        live UI: market list, books, intents, paper fills, ledger
scripts/
  verify_data.py                    51 assertions over every stored data file + SHA-256 manifest
  regen_candles.py                  structural regeneration of the candle CSVs (transcription guard)
  build_competition.py              deterministic backtest + competition memory builder
data/
  strategies.json                   22 personas: 4 live-book, 5 committed-backtest, 1 quote-plan, 12 source-gated
  source-registry.json              official sources, statuses, and irregularities IRR-01..14
  season-2026/                      COMMITTED SEASON MEMORY (this is the durable store)
    MANIFEST.md                     retrieval log: exact URLs, window, cross-checks, known gaps
    SHA256SUMS.txt                  hash binding for every file in this directory
    candles-*.csv                   verified Kalshi candlesticks (44 + 72 + 14 backtest bars, 27 context bars)
    market-records.json             verified market rows incl. result + settlement timestamps
    orderbook-*.csv                 verified live order book (KXBTC-26SEP2017-T90749.99)
    trade-tape-sample.csv           verified official trade tape (25 fills, all markets)
    series-records.json             fee type/multiplier + settlement sources (KXFED/KXCPI/KXBTC)
    cutoff.json                     live/historical tier boundary read (2026-07-21T00:00:00Z)
    competition.json                season metadata, markets, verified bar counts, fee model
    trades.json                     every backtest trade (verified entry/exit prices, dates, fees, slippage)
    leaderboard.json                computed standings ($10,000 per account)
    intents.json                    upcoming (proposed) forward trades from the 2026-09-19 live snapshot
    explanations.json               per-strategy "why it worked / didn't" analysis
tests/
  engine.test.mjs                   dependency-free tests for the live-desk engine
  season-memory.test.mjs            invariants of the committed season memory
```

## Season 2026 — committed backtest (verified tape only)

Collected **2026-09-19 (UTC)** from the official, unauthenticated endpoints:

| Market | Window | Bars | Verified outcome |
|---|---|---|---|
| `KXCPI-26AUG-T0.8` — "CPI rise more than 0.8% in Aug 2026?" | 2026-07-24 → 2026-09-12 (daily) | 44 | **no** @ 2026-09-11T13:28:53.706257Z |
| `KXFED-26SEP-T4.75` — "Fed rate above 4.75% after Sep 16, 2026?" | 2026-06-20 → 2026-09-16 (daily) | 72 | **no** @ 2026-09-16T18:20:58.459351Z |
| `KXNFLGAME-26SEP17DETBUF-BUF` — "Buffalo wins" (DET @ BUF) | 2026-09-17 12:00Z → 09-18 01:00Z (hourly) | 14 | **yes** @ 2026-09-18T03:34:55.133992Z |

Committed leaderboard (start $10,000/account; taker fee `0.07·q·p·(1-p)` rounded up to $0.0001;
entries at the bar's verified ask, exits at the verified bid, settlements at the official result):

| Rank | Username | Strategy | Return |
|---|---|---|---|
| 1 | SpikeSurfer | Release Spike Surfer | +2.46% |
| 2 | SureThing | Favorite Holder | +2.46% |
| 3 | YieldSniper | Certainty Carry | +0.002% |
| 4 | DipHunter | Cheap Dip Hunter | −0.013% |
| 5 | CrossChaser | Momentum Cross | −0.493% |

The honest story: the profitable strategies all entered **after** the verified tape confirmed the
move (a 26¢ jump with ~13× volume on the final NFL drive; a liquid 95¢ favorite; a 99¢ NO ask with
18.5 days to settlement). The losers paid the spread on range noise (CrossChaser) or held cheap
lottery tickets on a one-sided book to a zero settlement (DipHunter). Full per-trade detail,
fees and slippage are in `data/season-2026/trades.json`; per-strategy analysis in
`explanations.json`.

### How to reproduce

```bash
python3 scripts/verify_data.py      # 51 assertions; rewrites SHA256SUMS.txt
python3 scripts/build_competition.py # recomputes trades.json / leaderboard.json / intents.json / explanations.json
npm test                            # node --test (engine + season-memory invariants)
```

The site reads the committed JSON/CSV at runtime; if the files are missing it renders an explicit
"committed data unavailable" state instead of inventing rows.

## Live forward-test desk (the separate real-trade section)

- Loads open contracts from `GET https://external-api.kalshi.com/trade-api/v2/markets?status=open`
  (cursor-paginated, capped at 20 pages) and per-market books from
  `GET /markets/{ticker}/orderbook?depth=100`.
- Binary-book mechanics as documented by Kalshi: the book returns YES/NO **bids**; a YES ask is
  derived as `1 − best NO bid` (and NO ask as `1 − best YES bid`) and is visibly labelled "derived".
- **Upcoming trades:** each eligible strategy can emit a *proposed* intent from a fresh verified
  response (price, depth, reason, timestamp, source URL). An intent is **not** a fill.
- **Simulated fills:** only after a fresh order-book read; depth is consumed level by level into a
  VWAP, with slippage vs the displayed quote and the modeled fee. Exits require **full verified
  liquidity**; otherwise the trade stays open with a recorded close error.
- **Settlement** happens only when the official market record returns `result` plus a settlement
  timestamp — never inferred from a final quote.
- Fail-closed: any network/API/CORS failure leaves panels empty and the error visible. No demo
  prices, no localStorage-only "memory" for backtest claims.

Upcoming forward trades **committed** from the 2026-09-19 snapshot (see `intents.json`):
BookRocket → `KXBTC-26SEP2017-B90625` YES @ 0.02 (180 displayed), TailSprint and DipHunter →
`KXBTC-26SEP2017-T90749.99` YES @ 0.01 (5,999 displayed; closes 2026-09-20T21:00:00Z).

## Verification contract

1. **Raw data is stored, hash-bound, and cross-checked.** `SHA256SUMS.txt` + `scripts/verify_data.py`
   (volume sums equal the market-record volume *exactly*; open-interest continuity; monotonic
   bar timestamps; price bounds; settlement fields match the market records).
2. **Fills come from verified quotes only.** Backtest entries use the stored bar's
   `yes_ask_close` (or `1 − yes_bid_close` for NO); exits use `yes_bid_close`; settlements use the
   official result and `settlement_ts`. Midpoints are marks, never fills.
3. **No look-ahead.** Signals are computed from bars up to and including the decision bar; fills
   are timestamped at that bar's `end_period_ts`.
4. **Fees are modeled and labelled.** Standard quadratic taker fee with documented rounding;
   settlement carries no modeled fee (flagged assumption IRR-11); maker-fee differences for
   `quadratic_with_maker_fees` series are not modeled (flagged).
5. **Social sources are discovery-only.** Reddit/YouTube/X/Facebook/contest sites can inspire a
   hypothesis; none of them can verify a Kalshi price, fill, settlement or date.
6. **Transparency of collection method (IRR-12).** This build was collected from a sandbox without
   direct egress to `kalshi.com`; responses were read server-side and transcribed into the committed
   files. The mitigations above are in place, and a future collector with direct API access should
   re-fetch the same URLs and diff byte-for-byte.

## Known limitations and next work (for the next session)

- **Collector with direct egress — RESOLVED 2026-09-20 (IRR-12).** Every `MANIFEST.md` URL was
  re-fetched via the sandbox fetch tool; raw responses are stored verbatim in
  `data/season-2026/raw/` (SHA-256 bound) and all three candle CSVs were field-by-field diffed
  against the raw (`scripts/candles_from_raw.py --diff`). The diff caught one one-bar
  transcription shift in the NFL CSV (IRR-15a), which was regenerated mechanically from the
  canonical raw; PnL/fees/leaderboard are unchanged, and one fill's entry-bar attribution was
  corrected to the bar on which its price was actually verified. FRED/Stooq remain unreachable
  from the sandbox (IRR-13) — Kalshi endpoints work through the fetch tool.
- **Deeper history.** The KXFED full-history fetch is 14 chunks; only the zero-volume prefix
  (chunks 0-1, 2025-08-07→2025-09-28) is archived so far — archive the middle, and page settled
  KXGOLDH markets (cursor pagination) to find the first *liquid* gold event (none traded yet).
  Add more CPI/Fed meetings and settled NFL games to grow the sample.
- **Per-market trade tape — RESOLVED 2026-09-20 (IRR-14).** The filter parameter is `ticker`:
  `GET /markets/trades?ticker=<TICKER>&limit=N`, verified against KXBTC-26SEP2017-T90749.99
  (1 trade = market volume; raw stored).
- **Gold/commodity feed — RESOLVED 2026-09-20 (IRR-13, with gap).** The official LBMA JSON feed
  (`prices.lbma.org.uk/json/gold_am.json`) is reachable; 2026-06-15→2026-09-18 AM fixes are
  stored in `data/season-2026/raw/lbma-gold-am-2026.json`. Kalshi's own gold series `KXGOLDH`
  (hourly 1-minute-candle strike binaries, Pyth-settled, exchange shard 2) was discovered and
  verified — but recent events show zero volume, so MetalMomentum stays gated on Kalshi gold
  liquidity. FRED's CSV endpoint and Stooq are still unreachable from the sandbox.
- **Settlement fee check — RESOLVED 2026-09-20 (IRR-11).** Official docs: "Settlement fees are
  zero for simple yes/no determinations" (sub-cent scalar settlement may differ; payouts rounded
  to whole cents). All backtested markets are simple yes/no → the no-settlement-fee model is
  confirmed. Fee docs: 6-decimal granularity with a per-fill rounding fee onto the member balance
  grid; the model's $0.0001 round-up is the direct-member grid approximation (documented).
- **Exchange sharding (new, 2026-09-20).** Kalshi sharded matching engines by category: shard 2 =
  crypto + commodities (since 2026-09-10). Market-data calls for sharded series require
  `exchange_index=2` (e.g. `KXGOLDH`; `/series/KXGOLDH/markets` 404s without it).
- **Source-gated strategies.** NWS, SEC EDGAR, FDA, NFL/NBA injury, NCAA/NFL/MLB scoreboards and
  SportsPred each need a versioned adapter (primary-source response → exact contract mapping)
  before they can emit a signal. The "CEO" MasterSite reference remains unresolved (IRR-08).
- **Season durability.** The committed `data/season-2026/` is the durable record; browser
  localStorage holds only the live-desk ledger. A scheduled collector should keep extending the
  season archive as markets settle.
- **Reference-competition follow-through.** Trade Ideas, CandleCharts and The Leap remain
  discovery references; no claim here borrows their results.
- **Discovery-only hypothesis log (social research, 2026-09-20).** CEPR's analysis of 300k+ Kalshi
  contracts (favourite-longshot bias: 5–20¢ contracts underperform, 80–95¢ favorites overperform,
  taker longshot losses ~32%) matches the committed results (SureThing/YieldSniper positive,
  DipHunter negative). A community 5,000-strategy run on KXBTC15M found volatility-reversion
  ("panic fade") as the only profitable archetype — the most concrete next backtest candidate once
  KXBTC15M candle history is archived. A "+39% shock-timing bot" claim was refuted by its own
  follow-up out-of-sample backtest. All social numbers stay hypotheses (registry:
  `cepr-kalshi-bias`, `reddit-btc15m-backtest`, `reddit-shock-timing-refuted`,
  `medium-pm-synthesis`, `pith-optimal-mm`).

## Primary review links

- Kalshi Trade API docs index: <https://docs.kalshi.com/llms.txt>
- Live candlesticks: <https://docs.kalshi.com/api-reference/market/get-market-candlesticks>
- Historical data & cutoff: <https://docs.kalshi.com/getting_started/historical_data>
- Orderbook semantics: <https://docs.kalshi.com/getting_started/orderbook_responses>
- Fee rounding: <https://docs.kalshi.com/getting_started/fee_rounding>
- Settlement: <https://docs.kalshi.com/getting_started/market_settlement>
- Exchange sharding (shard 2 = crypto + commodities): <https://docs.kalshi.com/getting_started/exchange_sharding>
- Kalshi gold series (shard 2): `GET /markets?series_ticker=KXGOLDH&exchange_index=2` on <https://external-api.kalshi.com>
- LBMA gold price (official JSON feed): <https://prices.lbma.org.uk/json/gold_am.json>
- Settlement sources for the backtested markets: Fed — <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>, CPI — <https://www.bls.gov/cpi/>, BTC — <https://www.cfbenchmarks.com/data/indices/BRTI>, KXGOLDH — <https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD>
