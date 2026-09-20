# Commodities · Kalshi Research Exchange

An evidence-first paper-trading lab for Kalshi event contracts:

* an **automated forward-test desk** — 20 return-seeking strategy personas that trade open Kalshi
  contracts on paper twice an hour from a scheduled, read-only collector (GitHub Actions), with
  every fill, exit, settlement, intent, mark and order book committed to this repository;
* a **committed backtest on verified official Kalshi price data** (unchanged: 3 settled markets,
  130 verified bars, 5 personas);
* a **browser live desk** where you can inspect any open contract and simulate a fill yourself.

Everything on the site traces to a stored, hash-bound file or a documented official endpoint.
No synthetic quotes, no reconstructed bars, no silent fallbacks.

> Simulation only. Nothing in this project submits orders to Kalshi; no API key exists anywhere in it.

Site: <https://buffedlizard55-lab.github.io/Commodities/> · Collector runs:
<https://github.com/buffedlizard55-lab/Commodities/actions/workflows/forward-desk.yml>

## What is in the repository

```
index.html, styles.css, src/        GitHub Pages site (published from the repo root)
  src/engine.js                     pure accounting / orderbook / fee / settlement helpers (browser)
  src/season.js                     renders the committed forward desk + committed backtest
  src/app.js                        browser live desk: market list, books, intents, paper fills, local ledger
scripts/
  forward_desk.py                   ONE collector cycle: scan universe -> intents -> book fills -> settle -> ledger
  forward_strategies.py             the 20 forward personas (rule functions + provenance) and 4 gated ones
  paper_engine.py                   pure fill/fee/mark arithmetic shared by the collector and the tests
  kalshi_client.py                  stdlib read-only client (pacing, backoff, call log) + offline fixture client
  discover_universe.py              weekly GET /series catalog (13,607 series) -> data/universe/
  verify_data.py                    51 season assertions + forward-ledger invariants + SHA-256 manifest
  build_competition.py              deterministic backtest + committed competition memory
  candles_from_raw.py, regen_candles.py   raw-response -> CSV regeneration and diff guards
.github/workflows/forward-desk.yml  cron 7,37 * * * * (desk cycle) + Monday 06:17 UTC (universe) + manual dispatch
data/
  strategies.json                   persona roster (browser live-book, backtest, forward-desk, gated)
  source-registry.json              63 official/primary sources + irregularities IRR-01..IRR-24
  universe/                         series-catalog.json (compact, all categories), series-index.json (fees, shards, tags)
  season-2026/                      COMMITTED SEASON MEMORY (durable store)
    forward/                        <- the automated desk writes here every cycle
      state.json                    cash + open positions per strategy, last cycle summary, file index
      leaderboard.json              ranked board with rule / why / provenance / fill & mark policy strings
      trades.jsonl                  append-only fills, bid exits and settlements (evidence-bound)
      intents/YYYY-MM.jsonl         every intended trade with its status (filled / queued / not confirmed / no depth)
      evidence/YYYY-MM-DD.jsonl     verbatim order-book responses (self-hashing) + settlement record projections
      equity/YYYY-MM.csv            per-cycle per-strategy cash, marks, equity, realized, fees, slippage
      quotes/YYYY-MM-DD.csv         quotes seen for held / traded contracts at each cycle
      cycles/YYYY-MM.jsonl          one row per collector run (timing, API calls, counts, errors)
      signals/nws-central-park.jsonl  point-in-time NWS forecast used by the weather personas
      recent.json, curves.json      small windows for the site (latest events/intents/cycles, equity curves)
    MANIFEST.md, SHA256SUMS.txt     retrieval log + hash binding for the committed backtest files
    candles-*.csv, raw/, ...        verified backtest inputs (see MANIFEST.md)
    competition.json, trades.json, leaderboard.json, intents.json, explanations.json   backtest outputs
tests/
  test_forward_desk.py              offline two-cycle lifecycle, IOC-limit fills, partial-exit refusal, evidence hashes
  engine.test.mjs, season-memory.test.mjs   browser engine + committed-memory invariants (node --test)
```

## The forward-test desk (automated, separate section on the site)

**Can a strategy place an upcoming trade and simulate a real trading experience?**
*Yes, on paper and automatically:* each cycle every persona scans the tracked open contracts,
records its **intent** (contract, side, price, limit, reason), the desk fetches the **fresh
official order book** and fills as an immediate-or-cancel limit order against displayed depth.
*No, it is not a live order:* queue position, latency and market impact are not simulated as
facts (IRR-22).

Each cycle (`scripts/forward_desk.py --live`, ~100–150 official reads, ~30 s):

1. **Universe.** Open markets of the tracked series — econ `KXFED KXCPI KXCPIYOY KXFEDDECISION`,
   crypto `KXBTC KXBTC15M KXETH15M`, gold `KXGOLD15M KXGOLDH`, weather `KXHIGHNY`, games
   `KXNFLGAME KXNBAGAME KXNCAAFGAME KXMLBGAME`, plus every Companies series tagged `CEOs` whose
   ticker names a CEO market and every `KXFDA*` series tagged `Medicine` (from the weekly series
   catalog). Fee type / multiplier / exchange shard come from each series record (e.g.
   `KXMLBGAME` multiplier 0.5, shard 3).
2. **Signals.** Kalshi market fields (asks/bids, last, *last trade a day ago*, volume, close time,
   strike bounds), the **NWS point forecast for Central Park** (archived with its hash), official
   **daily candlesticks** (SMA persona) and **1-minute candlesticks** of open 15-minute markets
   (panic-fade persona).
3. **Reconcile.** Positions whose market shows an official `result` + `settlement_ts` settle at
   $1/$0 with no fee; rule exits sell the **whole** position into the bid ladder within 3¢ of the
   best bid or not at all (partial exits are refused and logged).
4. **Enter.** Ranked candidates per persona (most active first); the rule must hold again on the
   fresh book; size = 50% of free cash, IOC limit = min(rule bound, touch + 5¢); the ladder is
   consumed level by level (VWAP, levels, slippage vs touch, exact fee, unfilled remainder). At most
   2 new fills per persona per cycle and 8 open positions per persona (operational caps, not risk
   management).
5. **Mark & record.** Equity = cash + Σ contracts × (best bid, else last official trade, else 0);
   bid-only liquidation equity is reported alongside (IRR-23). Personas without a fill are
   *unranked*. Everything is appended to the ledger files above and committed by the workflow.

Fee model: `0.07 · q · p · (1 − p) · series multiplier`, rounded up to $0.0001 (documented
approximation, IRR-11). Series with a non-quadratic fee type are never traded.

Rules may be tightened during the season when the ledger exposes a flaw (e.g. IRR-25: favourite
bands now also require a displayed spread ≤ 5¢). Every change is a commit to
`scripts/forward_strategies.py`; positions opened under an earlier rule stay in the ledger and the
cycle ids make before/after results distinguishable. Nothing is ever re-simulated or deleted.

### Personas (20 active, 4 gated)

| Username | Rule (short) | Source of the idea |
|---|---|---|
| BookRocket, TickChaser, TailSprint, DepthDiver | cheap-side sweep · 24h momentum · ≤15¢ tails inside 48h · depth imbalance | exchange mechanics (prior roster, now automated) |
| DipHunter, SpikeSurfer, SureThing, YieldSniper | ≤5¢ tickets · 20¢ 24h jumps · 90–97¢ favourites (vol ≥100k) · 97–99¢ carry ≤21 days | committed backtest personas, forward twins |
| PinePilotX | SMA5/SMA10 cross on official daily candles | MasterSite **PinePilot** |
| CEOExitFav | favourite 80–96¢ on Kalshi CEO series | MasterSite has **no CEO project** (verified) → Kalshi CEO series |
| WeatherCatalyst, WeatherFader | YES on the NWS-forecast bracket ≤70¢ · NO on brackets ≥4°F away ≤92¢ | MasterSite **SFWeather** → NWS API → `KXHIGHNY` |
| FDAReaction | favourite 85–97¢ on FDA drug-decision series | MasterSite **DrugAnalysis** → `KXFDA*` (Medicine) |
| GridironPulse, SportsPredLab | 80–95¢ game favourite within 12h · 2–20¢ underdog sweep | MasterSite **NFL/NBA injury, NCAA/NFL/MLB scoreboards, SportsPred** → `KX*GAME` |
| MetalMomentum | 60–80¢ leader with 5–12 min left on gold 15-minute markets | MasterSite **GOLD** is a ring-buyer directory → Kalshi `KXGOLD15M` (Pyth-settled) |
| TailSprint15, HighProbScalp, PanicFader | ≤10¢ 15-minute tails · 75–80¢ in / 95¢ out · 1-minute-candle panic fade | r/KalshiBTCUporDown15, r/PredictionsMarkets (discovery only) |
| LongshotFader | buy the 80–95¢ side against a 5–20¢ longshot (vol ≥5k) | CEPR favourite-longshot evidence |
| *gated:* Form4Flash, LeapMapper, SpreadSmith, TipoffTriage | — | no Kalshi mapping / unverifiable maker fills / no official feed |

Full rule text, "why it should (or should not) work", provenance links and live results are in
`data/season-2026/forward/leaderboard.json` and on the site's Season board. The site's
"Where the ideas came from" table maps each requested MasterSite source to what was built.

### Reading the ledger

* `trades.jsonl` — one line per **fill** (`entryPrice` VWAP, `entryTouch`, `limitPrice`,
  `levelsConsumed`, `unfilledContracts`, `entryFee`, `slippageEntry`, `reason`, `evidence`),
  **exit** (`exitPrice`, `exitReason`, `exitFee`, `pnl`) or **settlement** (`result`, `exitAt` =
  official `settlement_ts`, `pnl`). `evidence.file` + `evidence.sha256` locate the verbatim
  order-book response (or the settlement-record projection) in `evidence/YYYY-MM-DD.jsonl`;
  `evidence.url` is the official endpoint.
* `intents/` — every intended trade, including the ones that did **not** fill and why
  (`not_confirmed_on_book`, `no_liquidity_or_cash`, `queued`, `skipped_position_cap`).
* `state.json` — current positions with `lastMark` (bid, last, mark value) and `quoteAtEntry`.
* `scripts/verify_data.py` re-checks on every run: cash identity
  (`cash = start + realized − open cost`), event counts, realized/fees vs events, evidence hash
  binding and order-book self-hashes.

### Run it yourself

```bash
python3 scripts/forward_desk.py --live                 # one real cycle (needs network egress)
python3 scripts/forward_desk.py --fixtures DIR --now 2026-09-20T15:00:00Z --out /tmp/out   # offline replay
python3 -m unittest tests.test_forward_desk             # 8 offline tests
python3 scripts/discover_universe.py                    # refresh data/universe/
python3 scripts/verify_data.py && npm test              # invariants + hashes + browser engine tests
```

## Season 2026 — committed backtest (verified tape only)

Collected **2026-09-19 (UTC)** from the official, unauthenticated endpoints (re-fetched verbatim
2026-09-20, `data/season-2026/raw/`):

| Market | Window | Bars | Verified outcome |
|---|---|---|---|
| `KXCPI-26AUG-T0.8` — "CPI rise more than 0.8% in Aug 2026?" | 2026-07-24 → 2026-09-12 (daily) | 44 | **no** @ 2026-09-11T13:28:53.706257Z |
| `KXFED-26SEP-T4.75` — "Fed rate above 4.75% after Sep 16, 2026?" | 2026-06-20 → 2026-09-16 (daily) | 72 | **no** @ 2026-09-16T18:20:58.459351Z |
| `KXNFLGAME-26SEP17DETBUF-BUF` — "Buffalo wins" (DET @ BUF) | 2026-09-17 12:00Z → 09-18 01:00Z (hourly) | 14 | **yes** @ 2026-09-18T03:34:55.133992Z |

| Rank | Username | Strategy | Return |
|---|---|---|---|
| 1 | SpikeSurfer | Release Spike Surfer | +2.46% |
| 2 | SureThing | Favorite Holder | +2.46% |
| 3 | YieldSniper | Certainty Carry | +0.002% |
| 4 | DipHunter | Cheap Dip Hunter | −0.013% |
| 5 | CrossChaser | Momentum Cross | −0.493% |

The profitable strategies all entered **after** the verified tape confirmed the move; the losers
paid the spread on range noise or held cheap tickets on a one-sided book to a zero settlement.
This pass fixed the informational slippage column only (IRR-20: settlement exits no longer carry a
fake "slippage"; the entry half-spread is side-aware) — equity, returns and PnL are unchanged.

```bash
python3 scripts/verify_data.py       # 51 assertions + forward ledger; rewrites SHA256SUMS.txt
python3 scripts/build_competition.py # recomputes trades / leaderboard / intents / explanations
npm test                             # node --test (engine + season-memory invariants)
```

## Browser live desk (manual)

Loads open contracts from `GET /markets?status=open`, per-market books from
`GET /markets/{ticker}/orderbook`, lets you simulate a taker fill for any live-book persona, exits
only with full verified liquidity, settles only from the official record, and keeps its ledger in
this browser's local storage (separate from the committed season). Fail-closed on any API error.

## Verification contract

1. **Raw data is stored, hash-bound and cross-checked** (`SHA256SUMS.txt`, `evidence/*.jsonl`
   self-hashes, `verify_data.py`).
2. **Fills come from verified quotes only** — a captured order book (forward), a stored bar's
   verified ask/bid (backtest); settlements from the official `result` + `settlement_ts`.
   Midpoints and last trades are marks, never fills.
3. **No look-ahead; no inferred results.** A finalized market without a yes/no result is held and
   flagged, never guessed.
4. **Fees are modeled and labelled**; series with unknown fee types are not traded.
5. **Social sources are discovery-only.** Reddit/YouTube/X/Facebook/contest sites can inspire a
   hypothesis; none of them verifies a Kalshi price, fill, settlement or date.
6. **Method transparency.** Sandbox sessions have no direct egress to kalshi.com; the collector runs
   on GitHub's runners with direct API access and stores what it received verbatim (IRR-12 closed).

## Known limitations and next work (for the next session)

* **Previous-session items still open.** KXFED full-history chunks 2–13 are not archived; the
  committed backtest sample is unchanged (3 markets) — the collector's `forward/candles/` archive
  is now the path to a larger verified sample (it already holds the LSU/Ole Miss game candles).
* **Season rollover.** `state.json` is Season 2026; on 2027-01-01 start `data/season-2027/`
  (new accounts at $10,000) and keep 2026 frozen.
* **Let the season run and audit it.** The desk started counting at cycle `20260920T032126Z`
  (IRR-17). After a few days: compare settlements against `GET /markets/{ticker}` by hand for a
  sample, check `cycles/*.jsonl` for skipped/late schedules (GitHub cron is best-effort and stops
  after 60 days without repository activity), and review `intents/` for rules that never confirm on
  the book.
* **Storage compaction (IRR-24).** Add a monthly job that gzips `evidence/` and `quotes/` files
  older than 30 days (keeping the hashes in place) so the repo and Pages artifact stay small.
* **More archived history for backtests.** Extend `build_competition.py` to run over any set of
  archived candle CSVs, and let the collector archive candlesticks for markets it traded once they
  settle (`KXGOLD15M`, `KXBTC15M`, game markets) — that turns the forward sample into a
  reproducible backtest set.
* **Signal adapters still missing.** ESPN scoreboard snapshots for in-game score triggers (ESPN is
  a listed settlement source for `KXNCAAFGAME`); openFDA for PDUFA dates; other NWS cities
  (`KXHIGHCHI/LAX/MIA/…`) after reading each series' station in `rules_primary`; index/commodity
  range series for LeapMapper from `data/universe/series-catalog.json`.
* **Maker behaviour.** SpreadSmith stays gated until a defensible resting-fill model exists (would
  need trade-tape matching against posted quotes, still not a verified fill).
* **Execution realism.** IOC limits at touch + 5¢ and displayed depth are assumptions (IRR-22);
  compare the desk's fills with the official trade tape (`/markets/trades?ticker=`) at the same
  timestamps to estimate how optimistic they are.
* **Site polish.** Per-strategy pages (full history from `equity/` and `trades.jsonl`), filters
  by series, and a daily summary generated by the collector.

## Primary review links

* Kalshi Trade API docs index: <https://docs.kalshi.com/llms.txt>
* Series list: <https://docs.kalshi.com/api-reference/market/get-series-list> · Market record: <https://docs.kalshi.com/api-reference/market/get-market>
* Order book semantics: <https://docs.kalshi.com/getting_started/orderbook_responses>
* Candlesticks: <https://docs.kalshi.com/api-reference/market/get-market-candlesticks>
* Fee rounding: <https://docs.kalshi.com/getting_started/fee_rounding> · Settlement: <https://docs.kalshi.com/getting_started/market_settlement>
* Rate limits: <https://docs.kalshi.com/getting_started/rate_limits> · Sharding: <https://docs.kalshi.com/getting_started/exchange_sharding>
* NWS API (Central Park point): <https://api.weather.gov/points/40.7789,-73.9692>
* Kalshi gold 15-minute (shard 2): `GET /markets?series_ticker=KXGOLD15M&exchange_index=2` on <https://external-api.kalshi.com>
* MasterSite directory reviewed for strategy sources: <https://buffedlizard55-lab.github.io/MasterSite/>
* Settlement sources for the backtested markets: Fed — <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>, CPI — <https://www.bls.gov/cpi/>, BTC — <https://www.cfbenchmarks.com/data/indices/BRTI>, gold — <https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD>
