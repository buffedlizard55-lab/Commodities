# Research Exchange · Commodities

Evidence-first Kalshi paper-trading competition and forward-testing lab. The published site is a dependency-free static app in `index.html`, `styles.css`, `src/`, and `data/`. `.github/workflows/pages.yml` publishes the repository root through the official GitHub Pages artifact/deploy actions after Pages is enabled for the repository.

## What is implemented

- **Live public market-data read:** the browser requests Kalshi's documented REST endpoints for open markets and a selected market's order book. The getting-started orderbook guide says that read needs no authentication; the current API-reference page displays auth headers, so this app uses no key and visibly fail-closes if production rejects the public read.
- **Fail-closed data boundary:** if the official endpoint cannot be reached, the site shows an empty state. It never inserts demo prices, invented market rows, or a synthetic leaderboard result.
- **Upcoming trade desk:** live-book strategies can create a timestamped proposed intent from observed market fields. A paper fill consumes the current YES/NO order-book depth level by level, recording requested size, filled size, VWAP, source levels, liquidity consumed, reciprocal-ask derivation, and modeled slippage.
- **Explicit real-trade simulation section:** the Live Desk explains exactly what can be simulated with a public read and what cannot: authentication, order submission, queue position, maker fills, and a real Kalshi fill are not claimed.
- **Competition leaderboard:** 2026 UTC-season accounts start with $10,000 of paper cash. Equity is calculated from realized PnL plus a conservative live-bid liquidation mark. The roster sorts by return, but a strategy with no verified fill has no return claim.
- **Strategy roster:** 17 named usernames cover live order-book hypotheses plus source-gated research ideas for weather, SEC Form 4, FDA, NFL/NBA injuries, NCAA/NFL/MLB scores, SportsPred, gold, PinePilot, The Leap, and market making. Source-gated ideas cannot fabricate signals when their primary-source adapter is absent.
- **Ledger and memory:** fills, exits, proposed intents, and compact verified market observations are stored under a versioned browser `localStorage` key. JSON and CSV exports are available. This is browser-local memory, not a shared database.
- **Research boundary:** this build does not bundle a verified historical tape and therefore makes no backtest claim. It forward-tests runtime observations and links to Kalshi's official historical endpoints for the next server-side collection/replay pass.
- **Manual-review sources:** `data/source-registry.json` lists official Kalshi docs, government/sports primary sources, the MasterSite project links, competition references, and explicit irregularities. User project pages and social/community posts are never treated as exchange-price or settlement evidence.

## Run locally

A static server is required because the site uses ES modules and JSON modules:

```bash
python3 -m http.server 4173
# open http://localhost:4173/
```

The official Kalshi API may be unreachable from a local network or a GitHub Pages browser because of network policy or CORS. That is an expected, visible failure state; it is not replaced with fake data.

## Tests

The pure accounting/order-book helpers have a dependency-free Node test suite:

```bash
npm test
```

The tests cover reciprocal YES/NO asks, depth consumption, VWAP/slippage, fee arithmetic, affordability, and closed-trade PnL. UI/API integration still needs to run in a browser against the official endpoint.

## Verification contract

1. A market row is accepted only from a successful `GET /trade-api/v2/markets?status=open` response. A live response read on 2026-09-19 used `status: "active"` for those rows, so the app accepts only the explicit `open` or `active` labels and keeps the raw status.
2. A paper fill is accepted only after a fresh `GET /trade-api/v2/markets/{ticker}/orderbook` response.
3. A displayed midpoint is a mark; it is never logged as an entry or exit.
4. A YES ask is `1 - best NO bid`; a NO ask is `1 - best YES bid`, matching Kalshi's documented binary-book representation. Derived values are visibly labelled.
5. The standard quadratic taker-fee estimate is implemented from the published formula and centicent rounding documentation. Series-specific overrides and membership-specific balance rounding remain flagged for review.
6. An exit is recorded only when the current verified bid book can fully consume the open position. Partial liquidity is not silently treated as a full exit.
7. No result is marked settled from a quote. When an open paper position disappears from the open-market list, the app checks the official market endpoint and settles only on an explicit YES/NO result with a returned date field.

## Three review passes completed

- **Pass 1 — implementation:** created the static site, public market/order-book adapter, paper execution engine, strategy roster, browser ledger, exports, source registry, and tests.
- **Pass 2 — bug/edge review:** added fail-closed API handling, XSS-safe rendering, reciprocal order-book asks, quantity-aware depth consumption, affordability including fees, conservative live-bid marks, partial-exit protection, local-storage caps, and unresolved-source flags.
- **Pass 3 — requirements re-check:** separated hypothesis/source gates from exchange evidence, removed any implied backtest result, made the real-trade simulation boundary explicit, included all requested project references, and documented the one-year/shared-database/authentication limitations.

## Known limitations and next work

- **No live order entry:** real orders require authenticated RSA-PSS requests and secure server-side key handling. Do not put a private key in a GitHub Page. A future backend must add explicit paper/live mode separation, auth, rate limits, audit logs, and user consent.
- **No shared one-year competition database:** browser storage is per device. A durable competition needs a scheduled collector or server with raw response retention, immutable timestamps, schema migrations, deduplication, and public export snapshots.
- **No historical replay in this release:** the next research pass should collect official Kalshi candlesticks/trades, honor the historical cutoff, model maker queue/fill rules, and publish a replay report only after raw files and hashes are committed.
- **Market-specific fees:** the default estimate is not a promise of the fee for every series. Fetch and retain series fee changes before a future exact-fee comparison.
- **Market making is only a quote plan:** a public REST snapshot cannot prove queue priority or a maker fill. A future authenticated WebSocket/collector should record book deltas and actual public trades before counting a maker fill.
- **External signal adapters are gated:** NWS, SEC EDGAR, FDA, NCAA, MLB, NFL and NBA inputs need source-specific timestamped adapters and exact contract joins. Until then, the roster is honest about being a research hypothesis.
- **The requested “CEO” label was not an identifiable entry in the retrieved MasterSite directory.** It is recorded as unresolved instead of guessing a URL or strategy.

## Primary review links

- [Kalshi market-data quick start](https://docs.kalshi.com/getting_started/quick_start_market_data)
- [Kalshi market order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook)
- [Kalshi unauthenticated orderbook response guide](https://docs.kalshi.com/getting_started/orderbook_responses)
- [Kalshi historical data](https://docs.kalshi.com/getting_started/historical_data)
- [Kalshi historical candlesticks](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks)
- [Kalshi fee schedule](https://kalshi.com/fee-schedule)
- [Kalshi authenticated requests](https://docs.kalshi.com/getting_started/quick_start_authenticated_requests)
- [MasterSite directory](https://buffedlizard55-lab.github.io/MasterSite/)

This project is a paper-trading research tool, not financial advice. It does not send live orders.
