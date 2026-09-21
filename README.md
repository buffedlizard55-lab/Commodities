# Commodities · Kalshi Research Exchange

An evidence-first paper-trading lab for Kalshi event contracts:

* an **automated forward-test desk** — 23 return-seeking strategy personas that trade open Kalshi
  contracts on paper twice an hour from a scheduled, read-only collector (GitHub Actions), with
  every fill, exit, settlement, intent, mark and order book committed to this repository — including
  HeatConfirm, a socially discovered weather rule tested forward because its backtest claim is
  third-party (discovery-only, never a price source);
* **two backtests on verified official Kalshi price data** — the original hand-collected sample
  (3 settled markets, 130 verified bars, 5 personas) and an **archive backtest** replayed bar by bar
  over the candle archive the collector now stores (45 markets, 1,691 verified bars, 199 trades,
  9 rule sets, with per-trade equity curves and a walk-forward stability split);
* an **execution-realism check** that re-reads every simulated fill against Kalshi's own published
  trade tape (`GET /markets/trades`) and reports the difference in cents — now with a committed
  live result (158 fills compared, median |desk − tape| = 1.00¢);
* a **season health audit** (`scripts/audit_season.py`) that checks the schedule, the ledger's
  coverage, storage compaction, signal adapters, the tape summary and — on the runner — re-reads
  settled markets against the official API, then refuses to let `verify_data.py` pass if anything
  disagrees;
* a **browser live desk** where you can inspect any open contract and simulate a fill yourself, and
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
  src/app.js                        browser live desk: market list, books, intents, paper fills, local ledger
scripts/
  forward_desk.py                   ONE collector cycle: scan universe -> signals -> intents -> book fills -> settle -> ledger
  forward_strategies.py             the 23 forward personas (rule functions + provenance) and 4 gated ones
  signals.py                        official signal adapters: NWS city gridpoints (Census Gazetteer centroids),
                                    ESPN scoreboards (NFL/NCAAF/NBA/MLB), openFDA Drugs@FDA records
  season.py                         season = UTC year; create/roll over data/season-<year>/ and write data/seasons.json
  compact_storage.py                gzip old evidence/ + quotes/ captures, keep their hashes, verify on every run (IRR-24)
  backtest_archive.py               replay the committed candle archive bar by bar -> data/season-*/backtest-archive/
                                    (--series for analysis of one series; writes curves.json + walkforward.json)
  execution_realism.py              compare each simulated fill with the official trade tape (GET /markets/trades)
  audit_season.py                   season health: cron-slot delivery, ledger coverage, compaction, signal
                                    adapters, tape summary, live settlement re-read -> forward/audit/latest.json
  archive_history.py                archive a market's full official candle history in bounded, hash-logged chunks
  render_pages.py                   re-render the committed view files (strategy pages, today, season index) offline
  paper_engine.py                   pure fill/fee/mark arithmetic shared by the collector and the tests
  kalshi_client.py                  stdlib read-only client (pacing, backoff, call log) + offline fixture client
  discover_universe.py              weekly GET /series catalog (13,607 series) -> data/universe/
  verify_data.py                    ~1,950 checks: season assertions, forward-ledger invariants, the season
                                    audit file itself, SHA-256 manifest
  build_competition.py              deterministic backtest + committed competition memory
  candles_from_raw.py, regen_candles.py   raw-response -> CSV regeneration and diff guards
.github/workflows/forward-desk.yml  cron 7,37 * * * * (desk cycle) + Monday 06:17 UTC (universe) + manual dispatch
data/
  strategies.json                   persona roster (browser live-book, backtest, forward-desk, gated)
  source-registry.json              70 official/primary sources + irregularities IRR-01..IRR-34
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
      audit/                        season health audit: latest.json + append-only audit-history.jsonl
    backtest-archive/               archive backtest outputs (competition / trades / leaderboard /
                                    explanations / curves / walkforward)
    history/                        full official candle history per market (CSV + per-request chunk log)
    MANIFEST.md, SHA256SUMS.txt     retrieval log + hash binding for the committed backtest files
    candles-*.csv, raw/, ...        verified backtest inputs (see MANIFEST.md)
    competition.json, trades.json, leaderboard.json, intents.json, explanations.json   backtest outputs
tests/
  test_forward_desk.py              12 offline tests: two-cycle lifecycle, IOC fills, partial-exit refusal,
                                    evidence hashes, HeatConfirm gating (entry needs forecast in bracket, cheap
                                    ask, tight spread; exit only when a fresh forecast leaves the bracket)
  test_signals.py                   18 tests: Census gazetteer -> NWS gridpoint (alias/suffix matching),
                                    ESPN scoreboard, openFDA records incl. the 404 = verified-absence path
  test_compaction.py                8 tests: gzip round-trip, hash check, tamper detection, season rollover
  test_backtest_archive.py          14 tests: fee multipliers, no look-ahead, PnL arithmetic, per-strategy
                                    cash identity, --series filter, curve points, walk-forward fold sums
  test_execution_realism.py         6 tests: tape comparison, no-tape window, optimism verdict (fixtures only)
  test_archive_history.py           4 tests: chunk tiling, per-request hashes, diff detection, failed chunks
  test_audit_season.py              7 tests: slot on-time/late/missed counts, orphan-fill detection, equity
                                    drift, signal totals, tape summary, live re-read unavailable offline
  site-smoke.mjs                    40 jsdom checks: index.html + one strategy page against the committed data
                                    (board, filters, ledger tabs, health panel, archive board + walk-forward,
                                    execution realism)
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
   strike bounds); **NWS point forecasts** — the Census Gazetteer interior point of each city named
   by a `KXHIGH*` series resolves `api.weather.gov/points/{lat},{lon}` to the right gridpoint (up to
   12 cities a cycle); **ESPN scoreboards** for NFL/college football/NBA/MLB (matched to a market by
   the team display names in `rules_primary` + the scheduled date, unique match or abstain);
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

### Personas (23 active, 4 gated)

| Username | Rule (short) | Source of the idea |
|---|---|---|
| BookRocket, TickChaser, TailSprint, DepthDiver | cheap-side sweep · 24h momentum · ≤15¢ tails inside 48h · depth imbalance | exchange mechanics (prior roster, now automated) |
| DipHunter, SpikeSurfer, SureThing, YieldSniper | ≤5¢ tickets · 20¢ 24h jumps · 90–97¢ favourites (vol ≥100k) · 97–99¢ carry ≤21 days | committed backtest personas, forward twins |
| PinePilotX | SMA5/SMA10 cross on official daily candles | MasterSite **PinePilot** |
| CEOExitFav | favourite 80–96¢ on Kalshi CEO series | MasterSite has **no CEO project** (verified) → Kalshi CEO series |
| WeatherCatalyst, WeatherFader | YES on the NWS-forecast bracket ≤70¢ · NO on brackets ≥4°F away ≤92¢ | MasterSite **SFWeather** → NWS API → `KXHIGH*` (city resolved per series) |
| FDAReaction | favourite 85–97¢ on FDA drug-decision series | MasterSite **DrugAnalysis** → `KXFDA*` (Medicine) |
| GridironPulse, SportsPredLab | 80–95¢ game favourite within 12h · 2–20¢ underdog sweep | MasterSite **NFL/NBA injury, NCAA/NFL/MLB scoreboards, SportsPred** → `KX*GAME` |
| ScorePulse | live: buy the side the **ESPN scoreboard** shows ahead by ≥8 pts / 10 pts / 3 runs at ≤85¢ | ESPN scoreboard API → `KX*GAME` (ESPN is a listed settlement source for NCAAF/MLB/NBA) |
| FdaRecordCheck | YES ≤97¢ when **openFDA Drugs@FDA** already lists an approved ORIG application for the named drug | openFDA `drugsfda` → `KXFDA*` (Medicine) |
| MetalMomentum | 60–80¢ leader with 5–12 min left on gold 15-minute markets | MasterSite **GOLD** is a ring-buyer directory → Kalshi `KXGOLD15M` (Pyth-settled) |
| TailSprint15, HighProbScalp, PanicFader | ≤10¢ 15-minute tails · 75–80¢ in / 95¢ out · 1-minute-candle panic fade | r/KalshiBTCUporDown15, r/PredictionsMarkets (discovery only) |
| LongshotFader | buy the 80–95¢ side against a 5–20¢ longshot (vol ≥5k) | CEPR favourite-longshot evidence |
| HeatConfirm | buy YES on a daily-high market when the **NWS forecast high is inside the bracket**, the ask ≤ 42¢ and the spread ≤ 8¢; exit only when a fresh forecast leaves the bracket | r/PredictionsMarkets 500-weather-bots backtest post (discovery only — testing its claim forward IS the experiment) |
| *gated:* Form4Flash, LeapMapper, SpreadSmith, TipoffTriage | — | no Kalshi mapping / unverifiable maker fills / no official feed |

Full rule text, "why it should (or should not) work", provenance links and live results are in
`data/season-2026/forward/leaderboard.json`, per persona in
`data/season-2026/forward/strategies/<id>.json`, and on the site's Season board — every board row
links to that persona's own page. The site's "Where the ideas came from" table maps each requested
MasterSite source to what was built.

ScorePulse and FdaRecordCheck have not traded yet: their adapters need live egress, which a sandbox
session does not have (IRR-31). They appear unranked with zero fills rather than with a synthetic
start, and they join on the first runner cycle.

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
python3 -m unittest discover -s tests -p 'test_*.py'    # 69 offline tests
python3 scripts/discover_universe.py                    # refresh data/universe/
python3 scripts/backtest_archive.py --dry-run           # what the archive replay would trade
python3 scripts/compact_storage.py --check              # verify compacted evidence still matches its hash
python3 scripts/execution_realism.py --live             # compare this season's fills with the trade tape
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
the board every cycle as the archive grows; this is the 2026-09-21 run, committed in
`data/season-2026/backtest-archive/`:

**45 markets · 1,691 verified bars · 199 trades · 9 rule sets** · $10,000 start · 50% of cash per entry,
capped at 25% of the bar's verified volume · entry at the bar's verified ask · exit at a later
verified bid or at the official result.

| Rank | Rule set | Entry rule | Return | Trades | W/L |
|---|---|---|---|---|---|
| 1 | ArchiveLongshot | verified ask 5–20¢, hold to the official result | **+462.52%** | 18 | 3 / 15 |
| 2 | ArchiveSMA | SMA5/SMA10 cross on daily bars | +207.23% | 71 | 20 / 51 |
| 3 | ArchiveCloser | last-20% favourite at ≥ 90¢, hold to settlement | +28.55% | 12 | 12 / 0 |
| 4 | ArchiveGapFade | fade a bar open ≥ 10¢ from the prior close | −2.36% | 6 | 3 / 3 |
| 5 | ArchivePanicFade | 1-minute-candle panic fade | −4.32% | 11 | 6 / 5 |
| 6 | ArchiveFavourite | verified ask 85–97¢, spread ≤ 5¢ | −28.31% | 14 | 13 / 1 |
| 7 | ArchiveMomentum | ≥ 3¢ day-over-day move, +5¢ take-profit | −30.53% | 35 | 25 / 10 |
| 8 | ArchiveFader | buy NO when YES is 5–20¢ on a tight spread | −75.00% | 18 | 15 / 3 |
| 9 | ArchiveFavStop | ArchiveFavourite entry, stop at −10¢ | −97.24% | 14 | 7 / 7 |

The 462% winner is **3 wins and 15 losses**: it bought cheap tickets, bled small losses and got paid
big once — convexity, not skill, and exactly what IRR-30 warns about reading into a 45-market month.
`backtest-archive/curves.json` carries every trade-by-trade realized curve behind that sentence (the
site draws them), and `backtest-archive/walkforward.json` splits the same replay into two windows:
ArchiveLongshot made its entire return in the second fold, and seven of the nine rule sets flip or
survive — the split measures stability across time, not a train/test edge, because none of these
rules has fitted parameters. `--series KXXX` re-runs one series as analysis only (it refuses to
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

## Execution realism — our fills versus the official trade tape

Simulated fills are only credible if the tape says someone actually traded there.
`scripts/execution_realism.py` re-reads `GET /markets/trades?ticker=…&min_ts=…&max_ts=…` around each
fill and writes `forward/execution/<date>.jsonl` + `summary.json`: tape VWAP and best, the difference
in cents, whether the desk's price sat inside the tape's range, the response URL and SHA-256. A
window with no print is recorded as `no_tape_in_window`; a fetch failure as `tape_fetch_failed`.
Neither is ever turned into a price.

The verdict is deliberately blunt: a median |desk − tape| above 1¢ is reported as *optimistic — treat
the desk's returns as an upper bound*. **The first live comparison ran on the runner on 2026-09-21**
(closed IRR-29): 158 desk fills compared, median |desk − tape| = **1.00¢**, 82.28% within 1¢, 77.22%
inside the tape's real range, 42 windows with no print recorded as such — "The desk is close to the
tape." Per-strategy medians range from 0.08¢ (dip-hunter) to 5.60¢ (panic-fade), and the workflow
re-runs the comparison every cycle, so this number is a moving artifact, not a one-off claim.
Queue position, latency and market impact are still not modelled: every fill remains an upper bound
on what a taker order would have received, and where the desk price beats the whole tape window
that shows up as a below-100% "inside range" percentage, not as a corrected fill.

## Seasons and storage

* **Season health audit (every cycle).** `scripts/audit_season.py` writes
  `forward/audit/latest.json` + `audit-history.jsonl`: which `:07/:37` cron slots were executed,
  late or missed (median delay in minutes — GitHub's scheduler is best-effort, IRR-34); whether
  every fill in the ledger still has a position or a recorded close; whether the equity CSV still
  matches account state; signal adapter totals (captured vs. abstained, with the last cycles' error
  lines); what the compactor squeezed and whether its hashes verify; the execution-realism summary;
  and — with `--live`, on the runner — a re-read of sampled settled markets against
  `GET /markets/{ticker}`, storing each response's SHA-256 and flagging any ledger-vs-API mismatch.
  The audit never adjusts the ledger: a mismatch is a finding, and `verify_data.py` fails the run on
  it. The site renders this as the "season health & storage" section. Seven tests in
  `tests/test_audit_season.py` pin the slot accounting, orphan detection and the rule that an
  offline run records "live re-read unavailable" instead of faking one.
* **One-year competition, keyed by UTC year.** `scripts/season.py` decides the season from the
  cycle's own timestamp: the first cycle of a new year creates `data/season-<year>/` with every
  persona back at $10,000 and marks the finished season `frozen` in `data/seasons.json`. Nothing is
  overwritten, so a finished season stays readable (8 tests in `tests/test_compaction.py` cover the
  boundary, the rollover and the index).
* **Storage compaction (IRR-24, addressed).** `scripts/compact_storage.py` gzips `evidence/` and
  `quotes/` captures older than N days and records each file's SHA-256 in `COMPRESSED.json`.
  Append-only ledger files (`trades.jsonl`, `intents/`, `equity/`, `cycles/`) are **never**
  compressed — they are the audit trail. `--check` re-hashes every compacted file and exits 1 on a
  mismatch; `--restore` brings a file back; `verify_data.py` reads compressed evidence transparently
  and runs the check on every pass. The workflow compacts after each cycle and checks before it
  commits.

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
7. **Signal adapters are context, never settlement.** NWS, ESPN and openFDA can time an entry or an
   exit; only Kalshi's official `result` + `settlement_ts` settles a position. Where a series names a
   different settlement source than the signal (24 of the 26 daily-temperature series settle on The
   Weather Company, not NWS), the registry says so (IRR-26).
8. **Regenerable caches are gitignored.** `data/universe/fda-records.json`,
   `data/universe/place-centroids.json` and `forward/signals/gridpoints.json` are rebuilt from the
   official endpoint on demand; a cache written by a fixture run would look identical to verified
   data, so none of them is ever committed.

## Known limitations and next work (for the next session)

**Done this session** (previously on the open list): the first live execution-realism comparison and
its committed verdict (IRR-29 closed); the season health audit with cron-delivery accounting
(IRR-34 quantified) wired into the workflow and checked by `verify_data.py`; a rebuilt archive
backtest on the grown archive (45 markets, curves + walk-forward + `--series` filter); the Census
Gazetteer centroid fix for the other KXHIGH* cities (IRR-32); openFDA 404 = verified absence
documented (IRR-33); the HeatConfirm persona from the weather-bot discovery post; and the site's
board/ledger text filters, archive equity curves and season-health panel.

* **Let HeatConfirm run and judge it honestly.** Its entire premise is a third-party backtest claim
  (discovery-only). After ~2–4 weeks of cycles, read `forward/strategies/heat-confirm.json`: did a
  fresh forecast inside the bracket + cheap tight ask actually settle YES above breakeven? If the
  sample is still thin, say so; do not promote, demote or retune the bracket thresholds mid-season.
* **Read the season audit, not just the board.** `forward/audit/latest.json` now quantifies what the
  runner actually achieved (last audit: 10 cycles over 42 expected slots, 1 on-time / 6 late / 35
  missed, longest gap 5.56 h). Cadence, not correctness, is the binding constraint on how fast this
  experiment accumulates evidence. Options if that matters: an external free ping to
  `workflow_dispatch` is allowed (it is not a data source); simply accept ~5–10 cycles/day.
* **The live settlement re-read is now automatic but sampled.** `audit_season.py --live --samples N`
  spot-checks the most recent finalized positions each cycle; a full backfill over every settled
  market of the season is cheap to script and worth one run per month (the ledger is never adjusted
  automatically — mismatches stay findings until reviewed by a human).
* **KXFED history still only covers 90 days.** The workflow re-runs `archive_history.py` for
  `KXFED-26SEP-T4.75` every cycle, so the archive grows on its own; the committed hand-collected
  backtest (3 markets / 130 bars) is intentionally frozen and should stay that way — extend the
  *archive* backtest instead of editing history.
* **ScorePulse / FdaRecordCheck are still untested, not refuted.** The adapters now run every cycle
  (45 ESPN signals in the last window; 0 needed corrections since the openFDA lookups only fire
  when a KXFDA market with a named drug is open). Zero fills is the honest state (IRR-31 closed).
  Review intents weekly; do not loosen the 85¢ cap to manufacture trades.
* **FDA target dates remain unverifiable.** Drugs@FDA publishes no PDUFA action date (IRR-27), so
  date-driven FDA personas stay out. `KXFDAAPPROVE` volume is small (76k) — check whether the series
  is even worth trading before adding rules.
* **Maker behaviour.** SpreadSmith stays gated until a defensible resting-fill model exists. The
  trade tape is wired up and the desk's taker fills now have a measured error (1.00¢ median), so the
  missing piece is matching *posted* quotes against it — still not a verified fill, and it should
  stay labelled as a model.
* **Signal coverage.** The ESPN adapter covers four leagues; Kalshi also lists soccer, tennis, F1 and
  hockey series in `data/universe/series-catalog.json`. LeapMapper still needs index/commodity range
  series. Adding a league is a `SERIES_*` entry plus a scoreboard URL, not new machinery.
* **Site polish left:** season-health sparkline history (append `audit-history.jsonl` rows per audit
  and draw the executed-slot rate over time), a per-strategy archive-backtest page section mirroring
  the forward one, and an evidence drill-down that opens the exact `trades.jsonl` line for a board
  fill (deep-linking by line offset).

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
* openFDA Drugs@FDA (signal only): <https://api.fda.gov/drug/drugsfda.json>
* Census Gazetteer city centroids: <https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html>
* MasterSite directory reviewed for strategy sources: <https://buffedlizard55-lab.github.io/MasterSite/>
* Settlement sources for the backtested markets: Fed — <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>, CPI — <https://www.bls.gov/cpi/>, BTC — <https://www.cfbenchmarks.com/data/indices/BRTI>, gold — <https://app.pyth.com/explore/Metal.Index.1OZGOLD%2FUSD>
