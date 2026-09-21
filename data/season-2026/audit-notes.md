# Season 2026 — manual audit notes

Human-readable review record for the running season. The machine artifact is
`forward/audit/latest.json` (rewritten every collector cycle by `scripts/audit_season.py` and
checked by `scripts/verify_data.py`); this file records what a person verified by hand and why
nothing here was smoothed away. Dates are UTC.

## 2026-09-21 — first full audit pass

**State reviewed:** audit `2026-09-21T04:13Z` (status PASS), ledger through cycle
`20260921T002604Z`, leaderboard after 10 cycles: 22 accounts, 211 fills, 147 settlements,
61 open positions, equity CSV matching `state.json` exactly (0 drift rows), 0 orphan fills.

### Schedule (expected, not feared)

10 cycles delivered over 42 scheduled `:07/:37` slots: 1 on-time, 6 late (median 45.9 min),
35 missed, 3 extra manual runs, longest gap 5.56 h. This is GitHub Actions' best-effort
scheduler under load (IRR-34), not a desk bug; the desk refuses to pretend a slot ran. Every
committed cycle row in `forward/cycles/*.jsonl` carries its own start/end timestamps, so the
audit's slot accounting can be recomputed by hand at any time.

### Settlement spot-checks against the official API

| Market | Ledger says | Official record (verified by hand, then automated) |
|---|---|---|
| `KXFED-26SEP-T4.75` | result **no**, settled 2026-09-16T18:20:58.459351Z | <https://external-api.kalshi.com/trade-api/v2/markets/KXFED-26SEP-T4.75> — `result: "no"`, `settlement_ts` identical. ✓ |
| `KXNFLGAME-26SEP17DETBUF-BUF` | result **yes** @ 2026-09-18T03:34:55.133992Z | committed tape + <https://docs.kalshi.com/getting_started/market_settlement> semantics. ✓ |

From now on this check is not manual: `audit_season.py --live` re-reads a sample of settled
markets every cycle, stores the response SHA-256, and `verify_data.py` fails the run on any
mismatch (`live_settlements_match_official_api`). Sample size: `--samples N` (default 8).

### Intents review — why personas abstain

* 26 intents `not_confirmed_on_book` + 15 `queued`: every one of them names a captured order
  book whose touch or depth failed the rule's own cap; none was filled at a guessed price.
* 4 `skipped_position_cap` (book-edge, tail-sprint) — position cap is part of the published rule.
* Zero-intent personas, each verified as a fact rather than a silence:
  * **ScorePulse** — ESPN adapter captured 45 signals in the last window but no mapped game was
    in progress at cycle time; when one was live (MLB, Top 8th, leader +6) the leader was already
    priced above the 85¢ cap, so the rule correctly declined. `matchedVia` and the score line are
    stored per signal.
  * **FdaRecordCheck** — openFDA returned 404 for RETATRUTIDE (its documented zero-results
    response; IRR-33). A verified absence is recorded with URL and date; it is not an error.
  * **WeatherFader / HeatConfirm (other cities)** — centroid lookups had been failing on gazetteer
    name matching; fixed 2026-09-21 with alias/suffix normalisation (IRR-32), 18 tests in
    `tests/test_signals.py`. Until the next runner cycles land, these rows remain 0 on purpose.

### Execution realism — read and relayed

`forward/execution/summary.json` (runner, 2026-09-21T00:27:26Z): 158 fills compared against
`GET /markets/trades`; median |desk − tape| 1.00¢; 82.28% within 1¢; 77.22% of desk prices inside
the window's real range; 42 windows with no print at all (recorded, never invented). Per-strategy
medians 0.08¢ (dip-hunter) … 5.60¢ (panic-fade). Verdict stands as written: "The desk is close to
the tape." — IRR-29 closed with the artifact, not with prose.

### Archive backtest board (rebuilt 2026-09-21)

45 markets / 1,691 verified bars / 199 trades / 9 rule sets, +462.52% (ArchiveLongshot, **3W/15L**
— convexity) to −97.24% (ArchiveFavStop). Walk-forward (2 folds, stability not train/test): the
leader's entire return sits in fold 2. Do not believe any of it yet (IRR-30); the board re-runs
every cycle so the sample grows honestly.

## Open items for the next manual pass (suggested, ~weekly)

1. Re-read this file top-to-bottom against `forward/audit/latest.json` — if the audit says PASS but
   a number moved the wrong way (missed slots, orphans, equity drift, tape medians), write a dated
   note here; findings go here, not into the ledger.
2. HeatConfirm: first entries/exits should appear once KXHIGH* books with forecast-inside-bracket
   pricing exist; record the first five and check each exit against the stored forecast snapshot.
3. Grep `intents/` for any rule that has produced zero intents for 500+ cycles while its signal
   adapter reports captures — silence with data is a bug, silence without data is honest.
4. One `audit_season.py --live --samples 25` backfill pass after two weeks of settlements.
