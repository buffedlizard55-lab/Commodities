import strategies from '../data/strategies.json' with { type: 'json' };
import registry from '../data/source-registry.json' with { type: 'json' };
import {
  KALSHI_MARKET_DATA_URL,
  accountSummary,
  affordableContracts,
  bookImbalance,
  closePaperTrade,
  createPaperTrade,
  displayedQuotes,
  executeAgainstBook,
  formatContracts,
  formatDate,
  formatDollars,
  normalizeMarkets,
  normalizeOrderBook,
  settlePaperTrade,
  strategyExplanation,
  tradesToCsv,
} from './engine.js';

const STORAGE_PREFIX = 'research-exchange-paper-v1';
const SEASON_YEAR = new Date().getUTCFullYear();
const SEASON_BASE = 'data/season-2026/';
const state = {
  seasonYear: SEASON_YEAR,
  markets: [],
  books: new Map(),
  settlementChecks: new Map(),
  selectedTicker: null,
  currentSignals: new Map(),
  apiStatus: 'idle',
  lastUpdated: null,
  activeFilter: 'all',
  activeLedgerTab: 'trades',
  trades: [],
  intents: [],
  observations: [],
  season: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));
const sourceForMarkets = `${KALSHI_MARKET_DATA_URL}/markets?status=open`;
// The production response observed on 2026-09-19 returned `status: "active"`
// for rows selected by the documented `status=open` query. Accept both exact
// labels, but never accept a known closed/settled status.
const OPEN_MARKET_STATUSES = new Set(['open', 'active']);

function loadLocalState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(`${STORAGE_PREFIX}-${SEASON_YEAR}`) ?? '{}');
    state.trades = Array.isArray(parsed.trades) ? parsed.trades : [];
    state.intents = Array.isArray(parsed.intents) ? parsed.intents : [];
    state.observations = Array.isArray(parsed.observations) ? parsed.observations : [];
  } catch (error) {
    toast('Local ledger could not be read; starting with an empty ledger.');
  }
}

function saveLocalState() {
  const payload = {
    schemaVersion: 1,
    seasonYear: state.seasonYear,
    savedAt: new Date().toISOString(),
    trades: state.trades,
    intents: state.intents.slice(-250),
    observations: state.observations.slice(-96),
  };
  try {
    localStorage.setItem(`${STORAGE_PREFIX}-${SEASON_YEAR}`, JSON.stringify(payload));
  } catch (error) {
    toast('Browser storage is full. Export the ledger, then clear old records.');
  }
}

function toast(message) {
  const element = $('#toast');
  if (!element) return;
  element.textContent = message;
  element.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove('show'), 4200);
}

function seasonDetails() {
  const now = new Date();
  const start = Date.UTC(SEASON_YEAR, 0, 1);
  const end = Date.UTC(SEASON_YEAR + 1, 0, 1);
  const progress = Math.max(0, Math.min(100, ((now.getTime() - start) / (end - start)) * 100));
  $('#season-year').textContent = String(SEASON_YEAR);
  $('#season-date').textContent = `${formatDate(now)} · live clock`;
  $('#season-progress').style.width = `${progress}%`;
  $('#season-progress-label').textContent = `${progress.toFixed(1)}% elapsed`;
}

function setApiStatus(status, detail = '') {
  state.apiStatus = status;
  const dot = $('#api-dot');
  const label = $('#connection-label');
  if (dot) dot.classList.toggle('live', status === 'live');
  if (!label) return;
  label.classList.toggle('error', status === 'error');
  label.textContent = status === 'live' ? 'Kalshi API connected' : status === 'error' ? 'Official API unavailable' : 'Waiting for API';
  if (detail) label.title = detail;
}

function rawMarketObservation(market) {
  return {
    ticker: market.ticker,
    status: market.status,
    yesBid: market.yesBid,
    yesAsk: market.yesAsk,
    noBid: market.noBid,
    noAsk: market.noAsk,
    last: market.last,
    previous: market.previous,
    volume: market.volume,
    closeTime: market.closeTime,
  };
}

function saveObservation(markets, sourcePages = [sourceForMarkets]) {
  // Keep the snapshot compact, but retain every page URL used to build it.
  const compact = markets.slice(0, 250).map(rawMarketObservation);
  const next = JSON.stringify(compact);
  const previous = state.observations.at(-1);
  if (previous && JSON.stringify(previous.markets) === next) return;
  state.observations.push({ retrievedAt: new Date().toISOString(), source: sourcePages[0], sourcePages, markets: compact });
  if (state.observations.length > 96) state.observations = state.observations.slice(-96);
  saveLocalState();
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store', headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function marketByTicker(ticker) {
  return state.markets.find((market) => market.ticker === ticker) ?? null;
}

function marketSort(a, b) {
  const aTime = a.closeTs ?? Number.POSITIVE_INFINITY;
  const bTime = b.closeTs ?? Number.POSITIVE_INFINITY;
  return aTime - bTime || a.title.localeCompare(b.title);
}

async function fetchOpenMarkets() {
  const all = [];
  const sourcePages = [];
  let cursor = '';
  // Kalshi documents cursor pagination. Keep a defensive page cap so a broken
  // cursor cannot make a static page issue an unbounded number of requests.
  for (let page = 0; page < 20; page += 1) {
    const params = new URLSearchParams({ status: 'open', limit: '100' });
    if (cursor) params.set('cursor', cursor);
    const url = `${KALSHI_MARKET_DATA_URL}/markets?${params.toString()}`;
    sourcePages.push(url);
    const payload = await fetchJson(url);
    all.push(...normalizeMarkets(payload));
    const nextCursor = String(payload?.cursor ?? '');
    if (!nextCursor || nextCursor === cursor) break;
    cursor = nextCursor;
    if (!Array.isArray(payload?.markets) || payload.markets.length === 0) break;
  }
  const unique = new Map();
  for (const market of all) unique.set(market.ticker, market);
  return { markets: [...unique.values()], sourcePages };
}

async function reconcileSettlements() {
  const activeTrades = state.trades.filter((trade) => trade.status === 'open' || trade.status === 'partially-closed');
  const missing = [...new Set(activeTrades.map((trade) => trade.ticker))].filter((ticker) => !marketByTicker(ticker));
  let changed = false;
  for (const ticker of missing.slice(0, 10)) {
    const checkedAt = state.settlementChecks.get(ticker) ?? 0;
    if (Date.now() - checkedAt < 300_000) continue;
    state.settlementChecks.set(ticker, Date.now());
    try {
      const payload = await fetchJson(`${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(ticker)}`);
      const market = normalizeMarket(payload?.market ?? payload);
      if (!['yes', 'no'].includes(String(market.result ?? '').toLowerCase())) continue;
      state.trades = state.trades.map((trade) => {
        if (trade.ticker !== ticker || !['open', 'partially-closed'].includes(trade.status)) return trade;
        const settled = settlePaperTrade(trade, market);
        if (settled.status === 'settled') changed = true;
        return settled;
      });
    } catch (error) {
      // A missing/temporarily unavailable settlement record is never converted
      // into an inferred result or a guessed exit.
    }
  }
  if (changed) saveLocalState();
  return changed;
}

async function loadMarkets({ announce = true } = {}) {
  setApiStatus('loading');
  try {
    const result = await fetchOpenMarkets();
    const markets = result.markets.filter((market) => !market.status || OPEN_MARKET_STATUSES.has(market.status)).sort(marketSort);
    state.markets = markets;
    await reconcileSettlements();
    state.lastUpdated = new Date().toISOString();
    saveObservation(markets, result.sourcePages);
    setApiStatus('live');
    $('#last-updated').textContent = `Observed ${formatDate(state.lastUpdated)}`;
    if (!state.selectedTicker && markets[0]) state.selectedTicker = markets[0].ticker;
    if (state.selectedTicker && !marketByTicker(state.selectedTicker) && markets[0]) state.selectedTicker = markets[0].ticker;
    generateIntents();
    renderAll();
    if (state.selectedTicker) await loadOrderBook(state.selectedTicker, { announce: false });
    if (announce) toast(`${markets.length.toLocaleString()} open contracts observed from Kalshi.`);
  } catch (error) {
    setApiStatus('error', error.message);
    state.markets = [];
    state.currentSignals.clear();
    renderAll();
    toast(`No verified market data loaded: ${error.message}.`);
  }
}

async function loadOrderBook(ticker, { announce = true } = {}) {
  if (!ticker) return;
  try {
    const payload = await fetchJson(`${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(ticker)}/orderbook?depth=100`);
    const book = normalizeOrderBook(payload);
    state.books.set(ticker, book);
    generateIntents();
    renderAll();
    if (announce) toast(`Fresh order book loaded for ${ticker}.`);
    return book;
  } catch (error) {
    state.books.delete(ticker);
    renderSelectedMarket();
    toast(`No verified order book loaded for ${ticker}: ${error.message}.`);
    return null;
  }
}

function currentCashFor(strategyId) {
  const strategyTrades = state.trades.filter((trade) => trade.strategyId === strategyId);
  const summary = accountSummary(strategyTrades, new Map(state.markets.map((market) => [market.ticker, market])), state.books);
  return summary.startingCash + summary.realized - summary.committed;
}

function askFor(market, side, book = null) {
  const quotes = displayedQuotes(market, book);
  return side === 'yes' ? quotes.yesAsk : quotes.noAsk;
}

function signalFor(strategy, market, book = null) {
  if (!market || strategy.mode !== 'live-book') return null;
  const quotes = displayedQuotes(market, book);
  const yesSpread = quotes.yesBid !== null && quotes.yesAsk !== null ? quotes.yesAsk - quotes.yesBid : null;
  const noSpread = quotes.noBid !== null && quotes.noAsk !== null ? quotes.noAsk - quotes.noBid : null;
  const pickCheaper = (maximum, reason) => {
    const candidates = [{ side: 'yes', price: quotes.yesAsk, spread: yesSpread }, { side: 'no', price: quotes.noAsk, spread: noSpread }]
      .filter((candidate) => candidate.price !== null && candidate.price <= maximum)
      .sort((a, b) => a.price - b.price);
    if (!candidates.length) return null;
    const candidate = candidates[0];
    return { ...candidate, strategyId: strategy.id, reason, observedAt: new Date().toISOString(), ticker: market.ticker };
  };

  if (strategy.signal === 'book-edge') {
    const candidate = pickCheaper(0.45, `Executable ${strategy.tag} ask is at or below $0.45.`);
    return candidate && (candidate.spread ?? 0) >= 0.02 ? candidate : null;
  }
  if (strategy.signal === 'momentum') {
    if (market.last === null || market.previous === null) return null;
    const move = market.last - market.previous;
    if (Math.abs(move) < 0.03) return null;
    const side = move > 0 ? 'yes' : 'no';
    const price = side === 'yes' ? quotes.yesAsk : quotes.noAsk;
    return price === null ? null : { strategyId: strategy.id, ticker: market.ticker, side, price, spread: side === 'yes' ? yesSpread : noSpread, reason: `Observed last/previous move of ${(move * 100).toFixed(1)}¢ in the ${side.toUpperCase()} direction.`, observedAt: new Date().toISOString() };
  }
  if (strategy.signal === 'tail') {
    const hoursToClose = market.closeTs === null ? null : (market.closeTs - Date.now()) / 3_600_000;
    if (hoursToClose === null || hoursToClose < 0 || hoursToClose > 48) return null;
    return pickCheaper(0.15, `Market closes in ${hoursToClose.toFixed(1)} hours and a side is quoted at or below $0.15.`);
  }
  if (strategy.signal === 'imbalance') {
    if (!book) return null;
    const imbalance = bookImbalance(book);
    if (imbalance === null || Math.abs(imbalance) < 0.35) return null;
    const side = imbalance > 0 ? 'yes' : 'no';
    const price = askFor(market, side, book);
    return price === null ? null : { strategyId: strategy.id, ticker: market.ticker, side, price, spread: side === 'yes' ? yesSpread : noSpread, reason: `Displayed depth imbalance is ${(imbalance * 100).toFixed(1)}% toward ${side.toUpperCase()}.`, observedAt: new Date().toISOString() };
  }
  return null;
}

function intentFingerprint(intent) {
  return `${intent.strategyId}|${intent.ticker}|${intent.side}|${Math.round(intent.price * 100)}`;
}

function generateIntents() {
  state.currentSignals.clear();
  for (const strategy of strategies) {
    const candidates = [];
    for (const market of state.markets) {
      const book = state.books.get(market.ticker) ?? null;
      const signal = signalFor(strategy, market, book);
      if (signal) candidates.push({ ...signal, strategyName: strategy.name, username: strategy.username });
    }
    if (!candidates.length) continue;
    candidates.sort((a, b) => a.price - b.price);
    state.currentSignals.set(strategy.id, candidates[0]);
    const top = candidates[0];
    const fingerprint = intentFingerprint(top);
    const recent = state.intents.find((intent) => intent.fingerprint === fingerprint && (Date.now() - new Date(intent.observedAt).getTime()) < 900_000 && intent.status === 'proposed');
    if (!recent) {
      state.intents.push({
        id: `intent-${strategy.id}-${top.ticker}-${Date.now()}`.replace(/[^A-Za-z0-9_-]/g, '-'),
        fingerprint,
        strategyId: strategy.id,
        username: strategy.username,
        strategyName: strategy.name,
        ticker: top.ticker,
        title: marketByTicker(top.ticker)?.title ?? top.ticker,
        side: top.side,
        proposedPrice: top.price,
        reason: top.reason,
        observedAt: top.observedAt,
        source: `${KALSHI_MARKET_DATA_URL}/markets?status=open`,
        status: 'proposed',
        tradeId: null,
      });
    }
  }
  if (state.intents.length > 250) state.intents = state.intents.slice(-250);
  saveLocalState();
}

function selectedBook() {
  return state.selectedTicker ? state.books.get(state.selectedTicker) ?? null : null;
}

function summaryForStrategy(strategy) {
  const trades = state.trades.filter((trade) => trade.strategyId === strategy.id);
  const summary = accountSummary(trades, new Map(state.markets.map((market) => [market.ticker, market])), state.books);
  return { ...summary, trades: trades.length };
}

function formatReturn(value) {
  if (!Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function renderCompetition() {
  const rows = strategies.map((strategy) => ({ strategy, summary: summaryForStrategy(strategy) })).sort((a, b) => b.summary.returnPct - a.summary.returnPct || a.strategy.username.localeCompare(b.strategy.username));
  const totalTrades = state.trades.length;
  const openTrades = state.trades.filter((trade) => trade.status === 'open' || trade.status === 'partially-closed').length;
  const totalEquity = rows.reduce((sum, row) => sum + row.summary.equity, 0);
  const metricCards = $('#competition-metrics');
  const hasVerifiedEvidence = state.observations.length > 0 || state.trades.length > 0;
  if (metricCards) metricCards.innerHTML = [
    [hasVerifiedEvidence ? 'Season equity' : 'Starting paper capital', formatDollars(totalEquity), hasVerifiedEvidence ? `${strategies.length} paper accounts` : 'no verified fills yet'],
    ['Simulated trades', String(totalTrades), 'verified book fills only'],
    ['Open contracts', String(openTrades), 'marked at live bid'],
    ['Data snapshots', String(state.observations.length), 'compact browser memory'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`).join('');
  const body = $('#leaderboard-table tbody');
  if (!body) return;
  body.innerHTML = rows.map((row, index) => {
    const { strategy, summary } = row;
    const btRow = strategy.mode === 'backtest' ? seasonBacktestSummary(strategy) : null;
    const evidence = summary.trades ? ['live fill', ''] : strategy.mode === 'backtest' ? (btRow ? ['committed backtest', ''] : ['backtest row missing', 'none']) : strategy.mode === 'live-book' ? ['awaiting fill', 'none'] : ['source-gated', 'blocked'];
    const returnClass = summary.returnPct > 0 ? 'return-positive' : summary.returnPct < 0 ? 'return-negative' : '';
    return `<tr><td class="rank">${index + 1}</td><td><div class="persona-cell"><span class="avatar">${escapeHtml(strategy.username.slice(0, 2).toUpperCase())}</span><div><strong>${escapeHtml(strategy.username)}</strong><small>${escapeHtml(strategy.name)}</small></div></div></td><td><b>${formatDollars(summary.equity)}</b></td><td class="${returnClass}">${formatReturn(summary.returnPct)}</td><td class="${summary.realized > 0 ? 'return-positive' : summary.realized < 0 ? 'return-negative' : ''}">${formatDollars(summary.realized)}</td><td>${summary.trades}</td><td><span class="evidence-pill ${evidence[1]}">${escapeHtml(evidence[0])}</span></td></tr>`;
  }).join('');
}

function renderMarketList() {
  const container = $('#market-list');
  const search = ($('#market-search')?.value ?? '').trim().toLowerCase();
  const markets = state.markets.filter((market) => `${market.title} ${market.ticker} ${market.eventTicker}`.toLowerCase().includes(search));
  $('#market-count').textContent = state.markets.length ? `${markets.length.toLocaleString()} shown · ${state.markets.length.toLocaleString()} verified open` : 'Waiting for a live response';
  if (!markets.length) {
    container.innerHTML = state.apiStatus === 'error' ? '<div class="empty-state"><div class="empty-icon">!</div><strong>Official data unavailable</strong><p>No fallback data is used. Check the endpoint link or retry when the API/network is available.</p></div>' : '<div class="empty-state"><div class="empty-icon">◎</div><strong>No matching live contract</strong><p>Change the filter or load the open-market list.</p></div>';
    return;
  }
  container.innerHTML = markets.slice(0, 100).map((market) => {
    const quotes = displayedQuotes(market);
    const selected = market.ticker === state.selectedTicker ? ' selected' : '';
    const title = escapeHtml(market.title);
    const close = market.closeTime ? `closes ${escapeHtml(formatDate(market.closeTime))}` : 'close date not returned';
    const quote = quotes.yesAsk === null ? 'quote unavailable' : `YES ${formatDollars(quotes.yesAsk)}`;
    return `<button class="market-row${selected}" data-action="select-market" data-ticker="${escapeHtml(market.ticker)}" type="button"><span class="market-symbol">KX</span><span class="market-main"><strong title="${title}">${title}</strong><small>${escapeHtml(market.ticker)} · ${close}</small></span><span class="market-quote"><strong>${escapeHtml(quote)}</strong><small>${market.volume === null ? 'volume —' : `vol ${escapeHtml(formatContracts(market.volume))}`}</small></span></button>`;
  }).join('');
}

function renderSelectedMarket() {
  const container = $('#selected-market');
  const market = marketByTicker(state.selectedTicker);
  if (!market) {
    $('#selected-status').textContent = 'Select a live market to inspect it.';
    container.innerHTML = '<div class="empty-state compact"><div class="empty-icon">↗</div><strong>No contract selected</strong><p>The order book, strategy intents and fill controls will appear here.</p></div>';
    return;
  }
  const book = selectedBook();
  const quotes = displayedQuotes(market, book);
  const signalEntries = strategies.filter((strategy) => strategy.mode === 'live-book').map((strategy) => ({ strategy, signal: signalFor(strategy, market, book) })).filter((entry) => entry.signal);
  const relevantIntents = state.intents.filter((intent) => intent.ticker === market.ticker && intent.status === 'proposed').slice(-8);
  $('#selected-status').textContent = book ? `Book captured ${formatDate(book.fetchedAt)}` : 'Market metadata captured; order book not loaded';
  const quote = (label, value, derived = false) => `<div class="quote-box"><span>${label}${derived ? ' · derived' : ''}</span><strong>${value === null ? '—' : formatDollars(value)}</strong></div>`;
  const intentHtml = relevantIntents.length ? relevantIntents.map((intent) => {
    const strategy = strategies.find((item) => item.id === intent.strategyId);
    return `<div class="intent-row"><div><strong>${escapeHtml(intent.username)} · ${escapeHtml(intent.side.toUpperCase())}</strong><small>${escapeHtml(intent.reason)}<br>observed ${escapeHtml(formatDate(intent.observedAt))}</small></div>${book && strategy ? `<button class="button button-small button-primary" data-action="fill-intent" data-intent-id="${escapeHtml(intent.id)}" type="button">Simulate fill</button>` : '<span class="evidence-pill none">book needed</span>'}</div>`;
  }).join('') : '<div class="empty-state compact"><strong>No upcoming live-book intent on this contract</strong><p>Source-gated strategies remain visible in the roster but cannot create a signal without their primary-source adapter.</p></div>';
  const strategyHtml = signalEntries.length ? signalEntries.map(({ strategy, signal }) => `<div class="intent-row"><div><strong>${escapeHtml(strategy.username)} · ${escapeHtml(signal.side.toUpperCase())} @ ${escapeHtml(formatDollars(signal.price))}</strong><small>${escapeHtml(signal.reason)}</small></div><button class="button button-small button-primary" data-action="fill-signal" data-strategy-id="${escapeHtml(strategy.id)}" type="button">Simulate fill</button></div>`).join('') : '<div class="empty-state compact"><strong>No eligible signal for this live book</strong><p>A quote alone is not treated as a strategy edge.</p></div>';
  const bookLines = book ? `<div class="detail-meta"><div><span>YES depth</span><b>${formatContracts(book.yes.reduce((sum, level) => sum + level.quantity, 0))} contracts / ${book.yes.length} levels</b></div><div><span>NO depth</span><b>${formatContracts(book.no.reduce((sum, level) => sum + level.quantity, 0))} contracts / ${book.no.length} levels</b></div><div><span>Order-book imbalance</span><b>${bookImbalance(book) === null ? '—' : `${(bookImbalance(book) * 100).toFixed(1)}% YES`}</b></div><div><span>Book source</span><b>public REST · depth 100</b></div></div>` : '<div class="detail-meta"><div><span>Order book</span><b>Not loaded</b></div><div><span>Paper fill</span><b>Disabled until book read</b></div></div>';
  container.innerHTML = `<div class="detail-head"><h3 class="market-title">${escapeHtml(market.title)}</h3><p class="market-subtitle">${escapeHtml(market.ticker)} · event ${escapeHtml(market.eventTicker || 'not returned')}</p></div><div class="quote-grid">${quote('YES bid', quotes.yesBid)}${quote('YES ask', quotes.yesAsk, market.yesAskDerived && !book)}${quote('NO bid', quotes.noBid)}${quote('NO ask', quotes.noAsk, market.noAskDerived && !book)}</div>${bookLines}<div class="subsection-title">Upcoming strategy intents</div><div class="intent-list">${intentHtml}</div><div class="subsection-title">Current live-book signals</div><div class="intent-list">${strategyHtml}</div><div class="detail-meta" style="margin-top:13px"><div><span>Close time</span><b>${escapeHtml(formatDate(market.closeTime))}</b></div><div><span>Volume</span><b>${escapeHtml(formatContracts(market.volume))}</b></div></div>`;
}

function seasonBacktestSummary(strategy) {
  if (!state.season) return null;
  return state.season.leaderboard.find((row) => row.username === strategy.username) ?? null;
}

function renderStrategies() {
  const matches = (strategy) => {
    if (state.activeFilter === 'all') return true;
    if (state.activeFilter === 'live-book') return strategy.mode === 'live-book';
    if (state.activeFilter === 'backtest') return strategy.mode === 'backtest';
    return strategy.mode !== 'live-book' && strategy.mode !== 'backtest';
  };
  const filtered = strategies.filter(matches);
  $('#strategy-count').textContent = String(strategies.length);
  $('#strategy-grid').innerHTML = filtered.map((strategy) => {
    const isBacktest = strategy.mode === 'backtest';
    const isLive = strategy.mode === 'live-book';
    const status = isLive ? 'live-book eligible' : isBacktest ? 'backtested · committed' : 'source-gated';
    const pillClass = isLive || isBacktest ? '' : 'blocked';
    let foot;
    if (isBacktest) {
      const bt = seasonBacktestSummary(strategy);
      const note = bt ? `${bt.trades} verified trade(s) · $${bt.feesPaid.toFixed(2)} fees + $${bt.slippagePaid.toFixed(2)} spread` : 'no committed backtest row';
      const ret = bt ? formatReturn(bt.returnPct) : '—';
      const retClass = bt ? (bt.returnPct > 0 ? 'return-positive' : bt.returnPct < 0 ? 'return-negative' : '') : '';
      foot = `<div class="strategy-foot"><small>${escapeHtml(note)}</small><b class="${retClass}">${escapeHtml(ret)}</b></div>`;
    } else {
      const summary = summaryForStrategy(strategy);
      foot = `<div class="strategy-foot"><small>${escapeHtml(strategyExplanation(summary, strategy))}</small><b>${formatReturn(summary.returnPct)}</b></div>`;
    }
    return `<article class="strategy-card ${isLive || isBacktest ? 'is-live' : 'is-blocked'}"><div class="strategy-top"><span class="strategy-avatar">${escapeHtml(strategy.username.slice(0, 2).toUpperCase())}</span><span class="evidence-pill ${pillClass}">${escapeHtml(status)}</span></div><h3>${escapeHtml(strategy.name)}</h3><div class="strategy-username">@${escapeHtml(strategy.username)}</div><p><b>Rule:</b> ${escapeHtml(strategy.rule)}<br><br><b>Why:</b> ${escapeHtml(strategy.why)}</p>${foot}</article>`;
  }).join('');
}

function renderLedger() {
  $('#trade-count').textContent = String(state.trades.length);
  $('#intent-count').textContent = String(state.intents.filter((intent) => intent.status === 'proposed').length);
  $('#observation-count').textContent = String(state.observations.length);
  const head = $('#ledger-table thead');
  const body = $('#ledger-table tbody');
  if (state.activeLedgerTab === 'trades') {
    head.innerHTML = '<tr><th>Strategy / ticker</th><th>Side & size</th><th>Entry (verified)</th><th>Exit (verified)</th><th>Fees / slippage</th><th>Net PnL</th><th>Actions</th></tr>';
    if (!state.trades.length) { body.innerHTML = '<tr><td colspan="7" class="empty-cell">No records yet. Use “Simulate fill” on a live order book. No synthetic trades are inserted.</td></tr>'; return; }
    body.innerHTML = [...state.trades].reverse().map((trade) => `<tr><td><div class="persona-cell"><span class="avatar">${escapeHtml(trade.username.slice(0, 2).toUpperCase())}</span><div><strong>${escapeHtml(trade.username)}</strong><small title="${escapeHtml(trade.title)}">${escapeHtml(trade.ticker)}</small></div></div></td><td><b>${escapeHtml(trade.side.toUpperCase())}</b><br><small>${escapeHtml(formatContracts(trade.contracts))} contracts</small></td><td><b>${formatDollars(trade.entryPrice, 4)}</b><br><small>${escapeHtml(formatDate(trade.entryAt))}</small></td><td>${trade.exitAt ? `<b>${formatDollars(trade.exitPrice, 4)}</b><br><small>${escapeHtml(formatDate(trade.exitAt))}</small>` : '<span class="evidence-pill none">open · mark live</span>'}</td><td>${formatDollars((trade.entryFee ?? 0) + (trade.exitFee ?? 0), 4)}<br><small>slip ${formatDollars(trade.slippage ?? 0, 4)}</small></td><td class="${(trade.pnl ?? 0) > 0 ? 'return-positive' : (trade.pnl ?? 0) < 0 ? 'return-negative' : ''}">${trade.pnl === null ? '—' : formatDollars(trade.pnl, 4)}</td><td>${trade.status === 'open' ? `<button class="button button-small button-ghost" data-action="close-trade" data-trade-id="${escapeHtml(trade.id)}" type="button">Exit at book</button>` : `<span class="evidence-pill">${escapeHtml(trade.status)}</span>`}</td></tr>`).join('');
  } else if (state.activeLedgerTab === 'upcoming') {
    head.innerHTML = '<tr><th>Observed</th><th>Strategy</th><th>Contract</th><th>Proposed side</th><th>Price</th><th>Reason / evidence</th><th>Status</th></tr>';
    const intents = [...state.intents].reverse().filter((intent) => intent.status === 'proposed');
    body.innerHTML = intents.length ? intents.map((intent) => `<tr><td>${escapeHtml(formatDate(intent.observedAt))}</td><td><b>${escapeHtml(intent.username)}</b><br><small>${escapeHtml(intent.strategyName)}</small></td><td><b>${escapeHtml(intent.ticker)}</b><br><small>${escapeHtml(intent.title)}</small></td><td>${escapeHtml(intent.side.toUpperCase())}</td><td>${formatDollars(intent.proposedPrice, 4)}</td><td>${escapeHtml(intent.reason)}<br><small>${escapeHtml(intent.source)}</small></td><td><span class="evidence-pill none">proposed</span></td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">No upcoming intent has been generated from a live response.</td></tr>';
  } else {
    head.innerHTML = '<tr><th>Retrieved</th><th>Source</th><th>Markets captured</th><th>Sample evidence</th></tr>';
    body.innerHTML = state.observations.length ? [...state.observations].reverse().map((observation) => { const sample = observation.markets[0]; return `<tr><td>${escapeHtml(formatDate(observation.retrievedAt))}</td><td><a class="text-link" href="${escapeHtml(observation.source)}" target="_blank" rel="noreferrer">Kalshi markets API ↗</a></td><td>${observation.markets.length}</td><td>${sample ? `<b>${escapeHtml(sample.ticker)}</b><br><small>YES bid ${escapeHtml(formatDollars(sample.yesBid, 4))} · close ${escapeHtml(formatDate(sample.closeTime))}</small>` : '—'}</td></tr>`; }).join('') : '<tr><td colspan="4" class="empty-cell">No verified observations yet.</td></tr>';
  }
}

function renderSources() {
  $('#source-list').innerHTML = registry.sources.map((source) => `<a class="source-card" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer"><strong>${escapeHtml(source.name)} ↗</strong><small>${escapeHtml(source.use)}<br>${escapeHtml(source.note)}</small><span class="source-type">${escapeHtml(source.kind)} · ${escapeHtml(source.status)}</span></a>`).join('');
  $('#irregularity-list').innerHTML = registry.irregularities.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  $('#registry-date').textContent = escapeHtml(registry.registryDate);
}

function renderAll() {
  seasonDetails();
  renderCompetition();
  renderMarketList();
  renderSelectedMarket();
  renderStrategies();
  renderLedger();
}

async function fillIntent(intent) {
  const market = marketByTicker(intent.ticker);
  const strategy = strategies.find((item) => item.id === intent.strategyId);
  const book = state.books.get(intent.ticker);
  if (!market || !strategy || !book) return toast('A fresh official order book is required before a paper fill.');
  await fillStrategy(strategy, market, book, intent);
}

async function fillStrategy(strategy, market, book, intent = null) {
  const signal = intent ? { side: intent.side, price: intent.proposedPrice, reason: intent.reason } : signalFor(strategy, market, book);
  if (!signal) return toast('This strategy has no current verified signal for the selected book.');
  const availableCash = currentCashFor(strategy.id);
  const requested = affordableContracts(availableCash, signal.price);
  if (requested <= 0) return toast('The strategy has no paper cash available at this quote.');
  const preview = executeAgainstBook(book, signal.side, 'buy', requested);
  if (preview.filledContracts <= 0) return toast('No displayed liquidity is available on that side.');
  const trade = createPaperTrade({ strategy, market, book, side: signal.side, requestedContracts: requested, now: new Date() });
  if (!trade) return toast('No verified contracts could be filled from the current book.');
  state.trades.push(trade);
  if (intent) {
    const stored = state.intents.find((item) => item.id === intent.id);
    if (stored) { stored.status = 'filled'; stored.tradeId = trade.id; }
  } else {
    const matching = state.intents.find((item) => item.strategyId === strategy.id && item.ticker === market.ticker && item.status === 'proposed');
    if (matching) { matching.status = 'filled'; matching.tradeId = trade.id; }
  }
  saveLocalState();
  renderAll();
  toast(`Paper fill recorded: ${formatContracts(trade.contracts)} ${trade.side.toUpperCase()} @ ${formatDollars(trade.entryPrice, 4)}. No live order was sent.`);
}

async function closeTrade(tradeId) {
  const trade = state.trades.find((item) => item.id === tradeId);
  const market = trade ? marketByTicker(trade.ticker) : null;
  if (!trade || !market) return toast('The current market is not in the verified open list; exit is not guessed.');
  const book = await loadOrderBook(trade.ticker, { announce: false });
  if (!book) return;
  const updated = closePaperTrade(trade, market, book, new Date());
  if (updated === trade || updated.status === 'open') return toast('The current bid book cannot fully exit this position; no partial result was recorded.');
  state.trades = state.trades.map((item) => item.id === tradeId ? updated : item);
  saveLocalState();
  renderAll();
  toast(`Exit recorded from the verified bid book. Net PnL: ${formatDollars(updated.pnl, 4)}.`);
}

function download(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  URL.revokeObjectURL(url);
}

function exportJson() {
  download(`research-exchange-${SEASON_YEAR}.json`, JSON.stringify({ schemaVersion: 1, seasonYear: SEASON_YEAR, exportedAt: new Date().toISOString(), trades: state.trades, intents: state.intents, observations: state.observations }, null, 2), 'application/json');
}

function exportCsv() {
  download(`research-exchange-trades-${SEASON_YEAR}.csv`, tradesToCsv(state.trades), 'text/csv');
}

function clearLedger() {
  if (!window.confirm('Clear this browser’s paper ledger for the current season? Export first if it matters.')) return;
  state.trades = []; state.intents = []; state.observations = [];
  saveLocalState(); renderAll(); toast('Local ledger cleared.');
}

function handleClick(event) {
  const target = event.target.closest('[data-action], [data-filter], [data-ledger-tab]');
  if (!target) return;
  const action = target.dataset.action;
  if (action === 'select-market') {
    state.selectedTicker = target.dataset.ticker;
    renderMarketList(); renderSelectedMarket();
    loadOrderBook(state.selectedTicker);
  } else if (action === 'fill-intent') {
    const intent = state.intents.find((item) => item.id === target.dataset.intentId);
    if (intent) fillIntent(intent);
  } else if (action === 'fill-signal') {
    const strategy = strategies.find((item) => item.id === target.dataset.strategyId);
    const market = marketByTicker(state.selectedTicker);
    const book = selectedBook();
    if (strategy && market && book) fillStrategy(strategy, market, book);
  } else if (action === 'close-trade') {
    closeTrade(target.dataset.tradeId);
  } else if (target.dataset.filter) {
    state.activeFilter = target.dataset.filter;
    $$('.filter-button').forEach((button) => button.classList.toggle('active', button === target));
    renderStrategies();
  } else if (target.dataset.ledgerTab) {
    state.activeLedgerTab = target.dataset.ledgerTab;
    $$('.ledger-tab').forEach((button) => button.classList.toggle('active', button.dataset.ledgerTab === state.activeLedgerTab));
    renderLedger();
  }
}

function bindEvents() {
  document.addEventListener('click', handleClick);
  $('#refresh-button').addEventListener('click', () => loadMarkets());
  $('#load-open-button').addEventListener('click', () => loadMarkets());
  $('#market-search').addEventListener('input', renderMarketList);
  $('#export-json').addEventListener('click', exportJson);
  $('#export-csv').addEventListener('click', exportCsv);
  $('#clear-ledger').addEventListener('click', clearLedger);
}

loadLocalState();
renderSources();
renderAll();
bindEvents();
loadSeasonMemory();
loadMarkets({ announce: false });
window.setInterval(() => { if (state.apiStatus !== 'loading') loadMarkets({ announce: false }); }, 30_000);
