# Commodities · Kalshi Research Exchange

An evidence-first paper-trading lab for Kalshi event contracts:

* an **automated forward-test desk** — 24 return-seeking strategy personas that trade open Kalshi
  contracts on paper twice an hour from a scheduled, read-only collector (GitHub Actions), with
  every fill, exit, settlement, intent, mark and order book committed to this repository — including
  HeatConfirm, a socially discovered weather rule tested forward because its backtest claim is
  third-party (discovery-only, never a price source);
* **two backtests on verified official Kalshi price data** — the original hand-collected sample
  (3 settled markets, 130 verified bars, 5 personas) and an **archive backtest** replayed bar by bar
  over the candle archive the collector now stores (63 markets, 2,455 verified bars, 291 trades,
  9 rule sets across 10 series, with per-trade equity curves and a walk-forward stability split);
* an **execution-realism check** that re-reads every simulated fill against Kalshi's own published
  trade tape (`GET /markets/trades`) and reports the difference in cents — committed live result
  (156 fills compared, median |desk − tape| = 1.00¢, 74.36% within 1¢ of a real print);
* a **tape-validated maker model** (`scripts/maker_model.py`) that posts SpreadSmith's simulated
  resting orders as *quote plans* (1¢ inside the touch on captured ladders, spread ≥ 2¢) and later
  matches every posted price against the official trade tape: a print strictly **through** the
  posted price proves the resting order would have filled (price priority), touch-only prints stay
  queue-uncertain, and projected settlement PnL is stated **before maker fees** (IRR-41). Every
  figure is labelled MODELLED and none enters the ranked board;
* a **trades review** (`scripts/trades_review.py` -> `forward/trades-review.md`) that puts every
  placed trade (entry/exit/settlement dates, verified prices, sizes, fees, slippage, PnL, evidence
  SHA-256, ledger line anchors) and every upcoming trade (queued intents, blocked intents and
  maker quote plans) in one readable committed file;
* a **season health audit** (`scripts/audit_season.py`) that checks the schedule, the ledger's
  coverage, storage compaction, signal adapters, the tape summary and — on the runner — re-reads
  settled markets against the official API, then refuses to let `verify_data.py` pass if anything
  disagrees; plus a **monthly full settlement backfill** (`scripts/verify_settlements.py`) that
  re-reads *every* settled market in the ledger from `GET /markets/{ticker}` and commits the
  comparison as a finding (never an adjustment);
* a **browser trade simulator** (the explicit "place simulated trades on open event contracts"
  section) where you can inspect any open contract — live verified pricing, official close and
  settlement dates, displayed liquidity — and simulate a depth-bound fill yourself, and
  **one page per strategy** (`strategy.html?id=…`) with its rule, evidence, every trade, its equity
  curve and an analysis of why it worked or did not.

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
  src/app.js                        browser trade simulator: market list, books, intents, paper fills, local ledger
scripts/
  forward_desk.py                   ONE collector cycle: scan universe -> signals -> intents -> book fills -> settle -> ledger
  forward_strategies.py             the 24 forward personas (rule functions + provenance) and 4 gated ones
  signals.py                        official signal adapters: NWS city gridpoints (Census Gazetteer centroids),
                                    ESPN scoreboards (NFL/NCAAF/NBA/MLB/NHL/WNBA), openFDA Drugs@FDA records
  season.py                         season = UTC year; create/roll over data/season-<year>/ and write data/seasons.json
  compact_storage.py                gzip old evidence/ + quotes/ captures, keep their hashes, verify on every run (IRR-24)
  backtest_archive.py               replay the committed candle archive bar by bar -> data/season-*/backtest-archive/
                                    (--series for analysis of one series; writes curves.json + walkforward.json)
  execution_realism.py              compare each simulated fill with the official trade tape (GET /markets/trades)
  maker_model.py                    SpreadSmith quote plans vs the official tape: a print strictly through the posted
                                    price proves a resting fill (MODELLED), touch prints stay queue-uncertain;
                                    projected PnL is before maker fees (IRR-41) -> forward/execution/maker-model.json
  trades_review.py                  render forward/trades-review.md + .json: every placed trade and every upcoming
                                    trade (queued/blocked intents + maker quote plans) in one readable file
  verify_settlements.py             monthly FULL settlement backfill: re-read every settled market from
                                    GET /markets/{ticker} and compare with the ledger (findings only;
                                    the ledger is never adjusted; writes forward/audit/settlement-backfill.json)
  audit_season.py                   season health: cron-slot delivery, ledger coverage, compaction, signal
                                    adapters, tape summary, live settlement re-read -> forward/audit/latest.json
  archive_history.py                archive a market's full official candle history in bounded, hash-logged chunks
  render_pages.py                   re-render the committed view files (strategy pages, today, season index) offline
  paper_engine.py                   pure fill/fee/mark arithmetic shared by the collector and the tests
  kalshi_client.py                  stdlib read-only client (pacing, backoff, call log) + offline fixture client
  discover_universe.py              weekly GET /series catalog (13,607 series) -> data/universe/
  verify_data.py                    ~3,400 checks: season assertions, forward-ledger invariants, the season
                                    audit file itself, SHA-256 manifest
  build_competition.py              deterministic backtest + committed competition memory
  candles_from_raw.py, regen_candles.py   raw-response -> CSV regeneration and diff guards
.github/workflows/forward-desk.yml  cron 7,37 * * * * (desk cycle) + Monday 06:17 UTC (universe)
                                    + 1st of month 06:47 UTC (full settlement backfill) + manual dispatch
data/
  strategies.json                   persona roster (browser live-book, backtest, forward-desk, gated)
  source-registry.json              79 official/primary sources + irregularities IRR-01..IRR-42
  seasons.json                      season index the site reads (active season, cycles, fills, frozen flag)
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
      signals/                      point-in-time signal captures (NWS forecast, ESPN snapshots, openFDA records)
      strategies/<id>.json          one committed page file per persona (rule, evidence, trades, curve, analysis)
      summary/<YYYY-MM-DD>.json     the day's roll-up (+ today.json), written every cycle
      execution/                    trade-tape comparisons of the desk's own fills (runner-written)
      candles/                      archived official candlesticks + index.jsonl (url, sha256, result, fees)
      recent.json, curves.json      small windows for the site (latest events/intents/cycles, equity curves)
      audit/                        season health audit: latest.json + append-only audit-history.jsonl + history.json
    backtest-archive/               archive backtest outputs (competition / trades / leaderboard /
                                    explanations / curves / walkforward)
    history/                        full official candle history per market (CSV + deduplicated chunk log + index)
    MANIFEST.md, SHA256SUMS.txt     retrieval log + hash binding for the committed backtest files
    candles-*.csv, raw/, ...        verified backtest inputs (see MANIFEST.md)
    competition.json, trades.json, leaderboard.json, intents.json, explanations.json   backtest outputs
tests/
  test_forward_desk.py              29 offline tests: two-cycle lifecycle, IOC fills, partial-exit refusal,
                                    evidence hashes, HeatConfirm gating (entry needs forecast in bracket, cheap
                                    ask, tight spread; exit only when a fresh forecast leaves the bracket),
                                    EPL/NCAAM universe + ScorePulse thresholds, can-fire proofs for the
                                    zero-intent FdaRecordCheck / WeatherFader rules, and the IRR-40 regression
                                    (a fixture cycle never writes the committed data/universe adapter caches),
                                    SpreadSmith quote-plan emission (plans never fill, dedupe, queue sizing) and the
                                    HalftimeHype recreation (in-game gate, 20-30c band, spread/volume gates, 50c exit)
  test_signals.py                   26 tests: Census gazetteer -> NWS gridpoint (alias/suffix matching),
                                    ESPN scoreboard (incl. the verified nickname rules_primary form, IRR-36,
                                    the added NHL/WNBA leagues and the EPL/NCAAM expansion, IRR-37),
                                    openFDA records incl. 404 = verified absence
  test_verify_settlements.py        12 tests: full settlement backfill - match / mismatch / unreachable /
                                    value-scalar / multi-position dedup / shard routing / limit determinism /
                                    multi-season scan / report + append-only history / fractional-second ts (IRR-39)
  test_compaction.py                9 tests: gzip round-trip, hash check, tamper detection, season rollover,
                                    all-season compaction after a UTC-year rollover
  test_backtest_archive.py          14 tests: fee multipliers, no look-ahead, PnL arithmetic, per-strategy
                                    cash identity, --series filter, curve points, walk-forward fold sums
  test_execution_realism.py         6 tests: tape comparison, no-tape window, optimism verdict (fixtures only)
  test_maker_model.py               12 tests: quote-plan read/filter, through-price proof, touch = queue-uncertain,
                                    pre-post prints ignored, window capped by close, settlement projection only from
                                    official results, labelled MODELLED roll-up, end-to-end offline run
  test_trades_review.py             6 tests: fill+close joins, upcoming excludes fills, totals agree with the ledger,
                                    markdown marks open rows, empty ledger says so explicitly
  test_archive_history.py           4 tests: chunk tiling, per-request hashes, diff detection, failed chunks
  test_verify_data.py               14 tests: canonical CSV invariants, fixture-bundle builder (+gz/sha,
                                    FixtureClient replay), archive competition counts, source-registry
                                    schema, roster/usernames, trades-review ledger agreement, labelled
                                    maker model, and a full verify_data end-to-end zero-failure run
  test_audit_season.py              12 tests: slot on-time/late/missed counts, orphan-fill detection, equity
                                    drift, signal totals (incl. the Central Park capture count, IRR-40), tape
                                    summary, live re-read unavailable offline, audit history, second-precision ts
                                    comparison + fail-closed branches (IRR-39)
  site-smoke.mjs                    jsdom checks: index.html + one mapped and one unmatched strategy artifact against the committed data
                                    (board, filters, ledger tabs, health panel, archive board + walk-forward,
                                    execution realism)
  engine.test.mjs, season-memory.test.mjs, season-rollover-browser.test.mjs   browser engine/memory invariants plus a 2027 active / 2026 frozen season-index render check
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
   `KXNFLGAME KXNBAGAME KXNCAAFGAME KXMLBGAME KXNHLGAME KXWNBAGAME KXEPLGAME KXNCAAMBGAME`,
   plus every Companies series tagged `CEOs` whose ticker names a CEO market and every `KXFDA*`
   series tagged `Medicine` (from the weekly series catalog). Fee type / multiplier / exchange
   shard come from each series record (e.g. `KXMLBGAME` multiplier 0.5, shard 3). `KXNHLGAME` /
   `KXWNBAGAME` were added 2026-09-21 and `KXEPLGAME` / `KXNCAAMBGAME` 2026-09-22 (signal-coverage
   next-work items); their first series records are fetched on the next runner cycle via
   `ensure_series`, and until then the desk simply has no open market to trade.
2. **Signals.** Kalshi market fields (asks/bids, last, *last trade a day ago*, volume, close time,
   strike bounds); **NWS point forecasts** — the Census Gazetteer interior point of each city named
   by a `KXHIGH*` series resolves `api.weather.gov/points/{lat},{lon}` to the right gridpoint (up to
   12 cities a cycle); **ESPN scoreboards** for NFL/college football/NBA/MLB/NHL/WNBA/EPL/NCAAM (matched to a
   market by the team names in `rules_primary` — Kalshi's nickname form like "DET Lions vs BUF
   Bills" is handled (IRR-36) — plus the scheduled date, unique match or abstain);
   **openFDA Drugs@FDA** records for `KXFDA*` markets whose title names a drug; official **daily
   candlesticks** (SMA persona) and **1-minute candlesticks** of open 15-minute markets (panic fade).
   Every capture is stored with its URL and SHA-256 before a rule reads it, and an adapter that
   answers nothing records an error note instead of a default.
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

### Personas (24 active, 4 gated)

| Username | Rule (short) | Source of the idea |
|---|---|---|
| BookRocket, TickChaser, TailSprint, DepthDiver | cheap-side sweep · 24h momentum · ≤15¢ tails inside 48h · depth imbalance | exchange mechanics (prior roster, now automated) |
| DipHunter, SpikeSurfer, SureThing, YieldSniper | ≤5¢ tickets · 20¢ 24h jumps · 90–97¢ favourites (vol ≥100k) · 97–99¢ carry ≤21 days | committed backtest personas, forward twins |
| PinePilotX | SMA5/SMA10 cross on official daily candles | MasterSite **PinePilot** |
| CEOExitFav | favourite 80–96¢ on Kalshi CEO series | MasterSite has **no CEO project** (verified) → Kalshi CEO series |
| WeatherCatalyst, WeatherFader | YES on the NWS-forecast bracket ≤70¢ · NO on brackets ≥4°F away ≤92¢ | MasterSite **SFWeather** → NWS API → `KXHIGH*` (city resolved per series) |
| FDAReaction | favourite 85–97¢ on FDA drug-decision series | MasterSite **DrugAnalysis** → `KXFDA*` (Medicine) |
| GridironPulse, SportsPredLab | 80–95¢ game favourite within 12h · 2–20¢ underdog sweep | MasterSite **NFL/NBA injury, NCAA/NFL/MLB scoreboards, SportsPred** → `KX*GAME` |
| ScorePulse | live: buy the side the **ESPN scoreboard** shows ahead by ≥8 pts (football) / 10 pts (NBA/WNBA/NCAAM) / 3 runs (MLB) / 2 goals (NHL/EPL) at ≤85¢ | ESPN scoreboard API → `KX*GAME` (ESPN is a listed settlement source for NCAAF/MLB/NBA/NHL/WNBA/EPL/NCAAM per the committed series catalog) |
| FdaRecordCheck | YES ≤97¢ when **openFDA Drugs@FDA** already lists an approved ORIG application for the named drug | openFDA `drugsfda` → `KXFDA*` (Medicine) |
| MetalMomentum | 60–80¢ leader with 5–12 min left on gold 15-minute markets | MasterSite **GOLD** is a ring-buyer directory → Kalshi `KXGOLD15M` (Pyth-settled) |
| TailSprint15, HighProbScalp, PanicFader | ≤10¢ 15-minute tails · 75–80¢ in / 95¢ out · 1-minute-candle panic fade | r/KalshiBTCUporDown15, r/PredictionsMarkets (discovery only) |
| LongshotFader | buy the 80–95¢ side against a 5–20¢ longshot (vol ≥5k) | CEPR favourite-longshot evidence |
| HeatConfirm | buy YES on a daily-high market when the **NWS forecast high is inside the bracket**, the ask ≤ 42¢ and the spread ≤ 8¢; exit only when a fresh forecast leaves the bracket | r/PredictionsMarkets 500-weather-bots backtest post (discovery only — testing its claim forward IS the experiment) |
| HalftimeHype | in-game 20–30¢ underdog (spread ≤ 5¢, vol ≥ 10k) while ESPN shows the mapped game live; exit at the first 50¢ bid | r/Kalshi "buy 20–30%, sell near 50%" tip (discovery only, recreated mechanically 2026-09-22; the halftime timing is folded into the 50¢ target and the rule text says so) |
| *gated:* Form4Flash, LeapMapper, SpreadSmith, TipoffTriage | — | no Kalshi mapping / maker fills unprovable from REST (quote plans + tape-validated maker model instead) / no official feed |

Full rule text, "why it should (or should not) work", provenance links and live results are in
`data/season-2026/forward/leaderboard.json`, per persona in
`data/season-2026/forward/strategies/<id>.json`, and on the site's Season board — every board row
links to that persona's own page. The site's "Where the ideas came from" table maps each requested
MasterSite source to what was built.

ScorePulse and FdaRecordCheck have not traded yet — and now we know exactly why for ScorePulse
(MetalMomentum left the zero-fill group on 2026-09-22 with its first gold fill; HalftimeHype starts
with its first runner cycle after this merge):
its adapter runs every cycle with live egress, but line-by-line review of the committed official
responses showed Kalshi writes game matchups in nickname form ("DET Lions vs BUF Bills"), which the
old parser truncated — no market ever matched (IRR-36, fixed 2026-09-21 with regression tests).
Both appear unranked with zero fills rather than with a synthetic start; ScorePulse's first possible
fill now depends only on an in-progress game crossing its lead threshold at an executable price.
The EPL (`KXEPLGAME`, 2-goal threshold) and men's college basketball (`KXNCAAMBGAME`, 10-point
threshold) mappings were added 2026-09-22 after direct endpoint reads confirmed the same scoreboard
shape (IRR-37); Kalshi's `rules_primary` phrasing for those two series gets its first confirmation
on the runner, and NCAAM tip-off is in November, so ScorePulse abstains there until games exist.
FdaRecordCheck's and WeatherFader's rules now have offline can-fire proofs (`ZeroFillRuleTests`):
their zero-intent ledgers mean the rules never triggered, not that the rules cannot fire.

### Reading the ledger

* `trades.jsonl` — one line per **fill** (`entryPrice` VWAP, `entryTouch`, `limitPrice`,
  `levelsConsumed`, `unfilledContracts`, `entryFee`, `slippageEntry`, `reason`, `evidence`),
  **exit** (`exitPrice`, `exitReason`, `exitFee`, `pnl`) or **settlement** (`result`, `exitAt` =
  official `settlement_ts`, `pnl`). `evidence.file` + `evidence.sha256` locate the verbatim
  order-book response (or the settlement-record projection) in `evidence/YYYY-MM-DD.jsonl`;
  `evidence.url` is the official endpoint. Every rendered event also carries `ledgerFile` + 1-based
  `ledgerLine`, so the board and strategy pages deep-link to `trades.jsonl#L<n>`; those append-only
  line anchors remain valid when evidence files are gzipped. If an evidence capture is compacted,
  `COMPRESSED.json` and its `.gz` path preserve the original bytes and hash.
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
python3 -m unittest discover -s tests -p 'test_*.py'    # 144 offline tests
python3 scripts/verify_settlements.py                   # offline plan: what the monthly backfill would re-read
python3 scripts/verify_settlements.py --live            # full settlement backfill (runner; official re-read)
python3 scripts/discover_universe.py                    # refresh data/universe/
python3 scripts/backtest_archive.py --dry-run           # what the archive replay would trade
python3 scripts/compact_storage.py --all-seasons --check # verify compacted evidence in every frozen/active season
python3 scripts/execution_realism.py --live             # compare this season's fills with the trade tape
python3 scripts/maker_model.py --live                   # match SpreadSmith quote plans against the trade tape
python3 scripts/archive_history.py --ticker KXFED-26SEP-T4.75 --live   # full official candle history
python3 scripts/render_pages.py                         # re-render strategy pages / today / season index
python3 scripts/verify_data.py && npm test && node tests/site-smoke.mjs
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
python3 scripts/verify_data.py       # ~1,950 checks + forward ledger; rewrites SHA256SUMS.txt
python3 scripts/build_competition.py # recomputes trades / leaderboard / intents / explanations
npm test                             # node --test (engine + season-memory invariants)
```

## Archive backtest — the collector's own candle archive (verified bars only)

The desk archives the official candlesticks of every market it trades (`forward/candles/`, each row
bound to the response URL and SHA-256 in `candles/index.jsonl`). `scripts/backtest_archive.py`
replays that archive bar by bar with the same accounting as the forward desk. The workflow rebuilds
the board every cycle as the archive grows; this is the **2026-09-22T14:10:56Z** run, committed in
`data/season-2026/backtest-archive/`:

**63 markets · 2,455 verified bars · 291 trades · 9 rule sets · 10 series** · $10,000 start · 50% of cash per entry,
capped at 25% of the bar's verified volume · entry at the bar's verified ask · exit at a later
verified bid or at the official result.

| Rank | Rule set | Entry rule | Return | Trades | W/L |
|---|---|---|---|---|---|
| 1 | ArchiveLongshot | verified ask 5–20¢, hold to the official result | **+363.49%** | 30 | 6 / 24 |
| 2 | ArchiveSMA | SMA5/SMA10 cross on daily bars | +206.43% | 107 | 27 / 80 |
| 3 | ArchiveCloser | last-20% favourite at ≥ 90¢, hold to settlement | +29.88% | 14 | 14 / 0 |
| 4 | ArchiveGapFade | fade a bar open ≥ 10¢ from the prior close | −2.19% | 7 | 4 / 3 |
| 5 | ArchiveFavourite | verified ask 85–97¢, spread ≤ 5¢ | −33.73% | 17 | 15 / 2 |
| 6 | ArchiveMomentum | ≥ 3¢ day-over-day move, +5¢ take-profit | −37.07% | 54 | 40 / 14 |
| 7 | ArchivePanicFade | 1-minute-candle panic fade | −57.72% | 14 | 6 / 8 |
| 8 | ArchiveFader | buy NO when YES is 5–20¢ on a tight spread | −69.63% | 30 | 24 / 6 |
| 9 | ArchiveFavStop | ArchiveFavourite entry, stop at −10¢ | −99.77% | 18 | 7 / 11 |

The 363% winner is **6 wins and 24 losses**: it bought cheap tickets, bled small losses and got paid
big — convexity, not skill, and exactly what IRR-30 warns about reading into a 63-market month. The
negative side is just as instructive: ArchiveFader won 24 of 30 trades and is still −69.63%, and
ArchiveFavourite won 15 of 17 and is −33.73% — win rate without payoff asymmetry loses here.
`backtest-archive/curves.json` carries every trade-by-trade realized curve behind that sentence (the
site draws them), and `backtest-archive/walkforward.json` splits the same replay into two equal-duration windows
(each fold resets to $10,000): ArchiveLongshot made its entire return in the second fold, and only
ArchiveSMA, ArchiveMomentum and ArchiveGapFade traded in both windows (each around flat in fold 1);
the other six traded only in fold 2. The split measures stability across time, not a train/test
edge — none of these rules has fitted parameters. `--series KXXX` re-runs one series as analysis only (it refuses to
overwrite the committed board). A market is never traded before it has an official `result`; series
with a non-quadratic fee type are skipped; the fee multiplier comes from the series record
(`KXMLBGAME` 0.5). `scripts/verify_data.py` re-checks the bar count against the index, the official
results, the absence of look-ahead, every PnL line, the per-strategy cash identity, that the number
of curve points equals the number of trades, and that the walk-forward fold totals match the board.

```bash
python3 scripts/backtest_archive.py --dry-run     # report only
python3 scripts/backtest_archive.py               # writes data/season-*/backtest-archive/
python3 scripts/archive_history.py --ticker KXFED-26SEP-T4.75 --period 1440 --live   # full history
python3 scripts/archive_history.py --ticker KXFED-26SEP-T4.75 --diff                 # detect drift
```

### KXFED full-life request archive

`data/season-2026/history/KXFED-26SEP-T4.75-p1440.csv` currently contains **327 official daily
bars from five distinct, hash-logged request windows**. The companion
`KXFED-26SEP-T4.75-p1440.index.json` records the requested window, market result and settlement,
first/last returned bar, CSV/chunk hashes, and the important boundary: every requested window was
attempted, but an absent official bar is not synthesized as a zero. The chunk log is rewritten
idempotently, so retries do not repeat identical request records; a changed official response is kept
as a separate hash-bearing record. This is market-history evidence, not a claim that every calendar
day had a returned candle.

## Execution realism — our fills versus the official trade tape

Simulated fills are only credible if the tape says someone actually traded there.
`scripts/execution_realism.py` re-reads `GET /markets/trades?ticker=…&min_ts=…&max_ts=…` around each
fill and writes `forward/execution/<date>.jsonl` + `summary.json`: tape VWAP and best, the difference
in cents, whether the desk's price sat inside the tape's range, the response URL and SHA-256. A
window with no print is recorded as `no_tape_in_window`; a fetch failure as `tape_fetch_failed`.
Neither is ever turned into a price.

The verdict is deliberately blunt: a median |desk − tape| above 1¢ is reported as *optimistic — treat
the desk's returns as an upper bound*. **The first live comparison ran on the runner on 2026-09-21**
(closed IRR-29); the committed `execution/summary.json` as re-read on 2026-09-22 compares **156
fills**: median |desk − tape| = **1.00¢**, 74.36% within 1¢, 69.23% inside the tape's real range,
65.38% covered by tape volume, 44 windows with no print recorded as such. Per-strategy medians in
that file range from 0.00¢ (dip-hunter) to 7.08¢ (weather-fade), and the workflow re-runs the
comparison every cycle, so this number is a moving artifact, not a one-off claim.
Queue position, latency and market impact are still not modelled: every fill remains an upper bound
on what a taker order would have received, and where the desk price beats the whole tape window
that shows up as a below-100% "inside range" percentage, not as a corrected fill.

## Seasons and storage

* **Season health audit (every cycle).** `scripts/audit_season.py` writes
  `forward/audit/latest.json` plus append-only `audit-history.jsonl` and the browser view `history.json`:
  which `:07/:37` cron slots were executed, late or missed (median delay in minutes — GitHub's
  scheduler is best-effort, IRR-34), and the truthful scheduled-slot rate trend (on-time + late
  matched slots divided by expected slots; manual extras do not inflate it); whether
  every fill in the ledger still has a position or a recorded close; whether the equity CSV still
  matches account state; signal adapter totals (captured vs. abstained, with the last cycles' error
  lines); what the compactor squeezed and whether its hashes verify; the execution-realism summary;
  and — with `--live`, on the runner — a re-read of sampled settled markets against
  `GET /markets/{ticker}`, storing each response's SHA-256 and flagging any ledger-vs-API mismatch.
  Timestamps compare at second precision (`paper_engine.settlement_ts_equal`, IRR-39): the ledger
  stores exitAt truncated to whole seconds while the API returns fractional seconds, so a strict
  string comparison flagged every real settlement and froze the ledger for ~22 h before it was
  fixed. The audit never adjusts the ledger: a mismatch is a finding, and `verify_data.py` fails
  the run on it. The site renders this as the "season health & storage" section, including the
  trend only from stored audit rows (one point is shown as insufficient for a line). Eleven tests
  in `tests/test_audit_season.py` pin the slot accounting, orphan detection, the second-precision
  timestamp comparison (including its fail-closed missing-timestamp branch), and the rule that an
  offline run records "live re-read unavailable" instead of faking one.
* **Full settlement backfill (monthly).** The audit above only *samples* the most recent settled
  positions (`--samples`, default 8). `scripts/verify_settlements.py --live` is the exhaustive
  version: every unique settled ticker across every committed season is re-read from
  `GET /markets/{ticker}` (targeting each series' exchange shard) and its official `result` +
  `settlement_ts` are compared with the ledger's settlement event, each response recorded with its
  URL and SHA-256. It writes `forward/audit/settlement-backfill.json` plus an append-only
  `settlement-backfill-history.jsonl`, runs on the 1st of each month (06:47 UTC cron) and on demand
  (`workflow_dispatch` mode `settlements`). It never adjusts the ledger: a mismatch is a committed
  finding with a non-zero exit code and a banner on the site's season-health panel; `verify_data.py`
  surfaces it as a warning (a monthly artifact deliberately does not block the twice-hourly commit).
  Unreachable markets are counted as such — never as a match or a mismatch. Offline runs print the
  plan and write nothing. Eleven tests in `tests/test_verify_settlements.py` pin the behaviour.
* **One-year competition, keyed by UTC year.** `scripts/season.py` decides the season from the
  cycle's own timestamp: the first cycle of a new year creates `data/season-<year>/` with every
  persona back at $10,000 and marks the finished season `frozen` in `data/seasons.json`. Nothing is
  overwritten, so a finished season stays readable (the rollover tests also assert 2027 starts with
  fresh accounts and the 2026 ledger remains frozen).
* **Storage compaction (IRR-24, addressed).** `scripts/compact_storage.py` gzips `evidence/` and
  `quotes/` captures older than N days and records each file's SHA-256 in `COMPRESSED.json`.
  Append-only ledger files (`trades.jsonl`, `intents/`, `equity/`, `cycles/`) are **never**
  compressed — they are the audit trail. `--check` re-hashes every compacted file and exits 1 on a
  mismatch; `--restore` brings a file back; `verify_data.py` reads compressed evidence transparently
  and runs the check on every pass. The workflow checks and compacts **all** `season-*/forward/`
  directories after each cycle, so a 2027 rollover does not strand frozen 2026 captures.

## Browser trade simulator (manual)

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
7. **Maker fills are never invented.** A resting-order fill is claimed only when the official tape
   printed strictly through the posted price (price-priority proof); touch prints stay
   queue-uncertain and quote plans never touch the ranked board (scripts/maker_model.py).
8. **Signal adapters are context, never settlement.** NWS, ESPN and openFDA can time an entry or an
   exit; only Kalshi's official `result` + `settlement_ts` settles a position. Where a series names a
   different settlement source than the signal (24 of the 26 daily-temperature series settle on The
   Weather Company, not NWS), the registry says so (IRR-26).
9. **Regenerable caches are gitignored (with one provenance-bearing exception).**
   `data/universe/fda-records.json` and `data/universe/place-centroids.json` are rebuilt from the
   official endpoint on demand; a cache written by a fixture run would look identical to verified
   data, so neither is ever committed — and since IRR-40 the desk writes those caches under a
   universe dir that `set_paths` redirects for test output, so a fixture cycle physically cannot
   create or modify them. The deliberate exception is `data/universe/nws-gridpoints.json`, which IS
   committed: every entry carries its resolution proof (Census place name, NWS points URL + SHA-256,
   grid id/x/y, retrieval time), so it is evidence of the city→gridpoint chain, not a regenerable
   lookup.

## Known limitations and next work (for the next session)

**Done in this session (2026-09-22)** — the previous session's open list, worked first,
then the session request (trade-simulator section, trades review, maker model, MasterSite map,
social sweep). Line by line:

* **Maker behaviour — the named missing piece is built.** `scripts/maker_model.py` posts
  SpreadSmith's quote plans (1¢ inside the touch on captured ladders, spread ≥ 2¢, size = the queue
  resting at the best bid, max 3/cycle) as intents with status `quote_plan` and later matches each
  posted price against `GET /markets/trades`: a print **strictly through** the posted price proves
  the resting order would have filled (price priority — queue position is irrelevant in that case);
  touch-only prints are `queue_uncertain` and never counted; projected settlement PnL is stated
  **before maker fees** (the maker side of the fee schedule is unverified here — IRR-41). 27 new
  offline tests (12 maker model + 6 trades review + 5 quote-plan cycle + 4 HalftimeHype);
  `verify_data.py` now fails if a quote plan ever looks like a fill. SpreadSmith stays unranked: its
  numbers are MODELLED evidence, not fills.
* **Trades review — "read and review all placed trades and all upcoming trades" ships.**
  `scripts/trades_review.py` renders `forward/trades-review.md` + `.json` after every cycle: 267
  placed trades (entry/exit/settlement dates, verified prices, fill/requested/unfilled sizes, fees,
  slippage, PnL, evidence SHA-256, ledger line anchors) and 58 upcoming/wanted rows (16 queued,
  32 not-confirmed-on-book, 10 position-capped; quote plans listed separately). Site link in
  section 02.
* **Explicit trade-simulator section.** Section 08 is retitled "Simulate placing real trades on
  open Kalshi contracts" with the pricing/dates/liquidity/slippage facts spelled out; the section-2
  callout now names queued intents and maker quote plans as the upcoming trades.
* **MasterSite map re-read line by line against `MasterSite/data/sites.js`** (52 sites; audit
  2026-09-21T23:07:59Z): every requested project carries its official link and its mapping; **CEO
  is still a verified negative** (no such project in the directory — IRR-08/IRR-19 hold); GOLD is a
  ring-buyer directory and stays mapped to Kalshi's own gold series; the competition mechanics
  reverse-engineered from KalshiPaperSim (Live Desk + trades review schema) are now named in the
  map as the source of this site's mechanics.
* **Social sweep (Reddit, YouTube, X, Facebook) — five Reddit threads carry falsifiable rules,
  logged discovery-only (IRR-42):** the r/Kalshi "buy 20-30%, sell near 50%" tip is **recreated as
  `HalftimeHype`** (in-game ESPN gate, 20-30c underdog, spread ≤ 5¢, volume ≥ 10k, exit at a 50c
  bid — the unverifiable "halftime" timing is folded into the 50c target and the rule text says
  so); the 1,000-strategy KXBTC15M run corroborates PanicFader's archetype (panic_fade profitable
  on all 150 variants in that run); the "Shock-Timing" World Cup bot stays logged **with its
  refutation in the same entry**; the r/passive_income econ/weather bots are skeleton-only
  (their Cleveland-Fed-Nowcast → CPI-bracket mapping is underspecified and was not guessed);
  Prevayo / botforkalshi blogs were evaluated and **not adopted** (no falsifiable parameters).
  Targeted searches for X, Facebook and YouTube posts returned only secondary blogs/Reddit
  reposts — no recreatable rule was located on those three platforms (honest negative, logged).
* **HeatConfirm — judged honestly with what exists (readout refreshed).** As of the
  2026-09-22T14:08Z cycle: **5 fills, 1,283 of 139,516 requested contracts (liquidity-bound), 0
  settlements** — all five positions settle 2026-09-23, so the experiment still has no closed
  trade. The rule stays frozen; the first settlements are the first honest datapoints.
* **Season audit read (not just the board).** `forward/audit/latest.json` (2026-09-22T14:10:56Z):
  **13 cycles over 118 expected `:07/:37` slots — 1 on-time, 9 late, 108 missed, 3 manual extras;
  median delay 49.5 min; longest gap 28.09 h (the IRR-39 freeze)**. Live settlement re-read 8/8
  match; ledger 448 evidence-bound events (267 fills, 3 exits, 178 settlements). Portfolio equity
  **$175,817.94 (−23.56%)** vs $230,000 start over 23 accounts / 13 cycles; LongshotFader +46.14%
  (lead), GridironPulse +28.73%, SureThing +14.77%; TailSprint −99.85% is the ranked bottom.
* **Zero-fill personas, refreshed:** ScorePulse and FdaRecordCheck remain 0-fill (can-fire-tested,
  "never triggered", not dead code); **MetalMomentum is no longer zero-fill — 1 fill, 1 open gold
  position** as of the 14:08Z cycle. HalftimeHype starts with its first runner cycle after this
  merge.

### Open items for the next session

* **First settlements of HeatConfirm (2026-09-23) and MetalMomentum's gold position** — judge at
  settlement, never mid-flight; no threshold retuning either way.
* **First live settlement backfill.** `verify_settlements.py --live` has still never run against
  every settled ticker (`settlement-backfill.json` absent; the monthly cron is the 1st at 06:47
  UTC). This session's merge will dispatch `mode=settlements` manually — review
  `forward/audit/settlement-backfill.json` afterwards (mismatches are findings only).
* **HalftimeHype + ScorePulse need live games at cycle time.** Both gates are verified-data gates
  (mapped in-progress game on the ESPN scoreboard); twice-hourly snapshots can only catch games in
  progress at :07/:37 — cadence is the binding constraint for every in-game rule.
* **Cron cadence (IRR-34) remains the biggest evidence bottleneck** (13/118 slots). Options, both
  allowed: an external **free** uptime ping to `workflow_dispatch` (a trigger, never a data
  source), or accept ~5–10 cycles/day and measure in weeks.
* **Maker fees (IRR-41):** find a primary fee-schedule source for the maker side before any
  SpreadSmith PnL number can leave the "before maker fees" label. More quote plans will accumulate
  fill evidence automatically each cycle.
* **Cleveland Fed Inflation Nowcast candidate** (from the r/post skeleton): archive the nowcast
  point-in-time first (official clevelandfed.org feed), then state a bracket-mapping rule against
  KXCPI-family markets — do not guess the mapping.
* **LeapMapper** still needs the universe job to enumerate index/commodity range series (the
  catalog has 1,040 Financials rows) with fee types and tick grids before a rule is stated.
* **FDA target dates remain unverifiable** (Drugs@FDA publishes no PDUFA date, IRR-27) — date-driven
  FDA personas stay out.

### Standing limitations (in the way of the full vision)

1. **No live order entry, by design** — no API key exists in the project; everything is paper.
2. **Sandbox sessions cannot reach kalshi.com** (IRR-12): collection and tape reads happen on
   GitHub's runners only; sessions verify from the committed responses and via GitHub APIs.
3. **Maker fills cannot be proven from REST snapshots** — queue position and latency are not
   observable; the maker model bounds them (through-price proof / touch uncertainty) instead of
   inventing fills.
4. **Maker fees are unverified** (IRR-41) — SpreadSmith PnL is pre-fee and stays labelled.
5. **Some requested signals have no official machine-readable feed** — NBA injuries (project
   finding), SEC EDGAR from shared runner IPs (sibling IR-76/77), FDA decision dates (IRR-27).
   Those personas stay gated rather than fed by unofficial scrapes.
6. **Signal sources are context, never settlement** (24 of 26 daily-temperature series settle on
   The Weather Company, not the NWS feed the adapter reads — IRR-26).
7. **GitHub `schedule` is best-effort** — missed cron slots are recorded, not hidden; there is no
   guarantee of twice-hourly execution without an external pinger.
8. **The forward sample is days old, not years** — 13 cycles and 181 closed positions cannot
   confirm or refute a strategy's edge; the archive backtest (63 markets, 291 trades) is the only
   long-horizon evidence and it covers 9 rule sets, not the live roster.

## Primary review links

* Kalshi Trade API docs index: <https://docs.kalshi.com/llms.txt>
* Series list: <https://docs.kalshi.com/api-reference/market/get-series-list> · Market record: <https://docs.kalshi.com/api-reference/market/get-market>
* Order book semantics: <https://docs.kalshi.com/getting_started/orderbook_responses>
* Candlesticks: <https://docs.kalshi.com/api-reference/market/get-market-candlesticks>
* Fee rounding: <https://docs.kalshi.com/getting_started/fee_rounding> · Settlement: <https://docs.kalshi.com/getting_started/market_settlement>
* Rate limits: <https://docs.kalshi.com/getting_started/rate_limits> · Sharding: <https://docs.kalshi.com/getting_started/exchange_sharding>
* NWS API (Central Park point): <https://api.weather.gov/points/40.7789,-73.9692>
* Kalshi gold 15-minute (shard 2): `GET /markets?series_ticker=KXGOLD15M&exchange_index=2` on <https://external-api.kalshi.com>
* Trade tape (execution realism): <https://docs.kalshi.com/api-reference/trade/get-trades>
* ESPN public scoreboards (signal only): <https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard>
* ESPN NHL / WNBA scoreboards (added 2026-09-21, IRR-35): <https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard> · <https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard>
* ESPN EPL / men's college basketball scoreboards (added 2026-09-22, IRR-37): <https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard> · <https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard>
* openFDA Drugs@FDA (signal only): <https://api.fda.gov/drug/drugsfda.json>
* Census Gazetteer city centroids: <https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html>
* MasterSite directory reviewed for strategy sources: <https://buffedlizard55-lab.github.io/MasterSite/>
* Settlement sources for the backtested markets: Fed — <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>, CPI — <https://www.bls.gov/cpi/>, BTC — <https://www.cfbenchmarks.com/data/indices/BRTI>, gold — <https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD>
* Trades review (all placed + all upcoming trades, regenerated per cycle): [`data/season-2026/forward/trades-review.md`](data/season-2026/forward/trades-review.md)
* Maker quote-plan evidence (MODELLED vs the tape): [`data/season-2026/forward/execution/maker-model.json`](data/season-2026/forward/execution/maker-model.json)
* MasterSite structured catalog re-read 2026-09-22 (52 sites): <https://buffedlizard55-lab.github.io/MasterSite/> · data file: <https://github.com/buffedlizard55-lab/MasterSite/blob/main/data/sites.js>
* Competition mechanics reverse-engineered from KalshiPaperSim: <https://buffedlizard55-lab.github.io/KalshiPaperSim/>
* Community sources (discovery-only, never price evidence): r/Kalshi winners thread (HalftimeHype) — <https://www.reddit.com/r/Kalshi/comments/1qd4ubf/people_who_actually_win_money_on_kalshi_whats/> · 1,000-strategy KXBTC15M run (PanicFader corroboration) — <https://www.reddit.com/r/PredictionsMarkets/comments/1srphtq/backtested_1000_strategies_on_kalshi_15min_btc/> · 5,000-strategy run — <https://www.reddit.com/r/PredictionsMarkets/comments/1szxy8h/backtested_5000_strategies_on_kalshi_15min_btc/> · Shock-timing bot + refutation — <https://www.reddit.com/r/PredictionsMarkets/comments/1u3rn8s/i_built_a_39_kalshi_trading_bot_to_exploit_world/> · r/passive_income econ/weather bots (skeleton only) — <https://www.reddit.com/r/passive_income/comments/1saogus/automating_a_side_hustle_how_i_make_money_trading/>
