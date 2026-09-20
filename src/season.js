// Committed season memory: the automated forward desk (data/season-2026/forward/*) and the
// committed backtest (data/season-2026/*.json).  Everything rendered here is read from files
// the collector or the backtest script wrote; the browser never computes a result of its own.
import { formatContracts, formatDate, formatDollars } from './engine.js';

const FORWARD_BASE = 'data/season-2026/forward/';
const SEASON_BASE = 'data/season-2026/';
const REPO_TREE = 'https://github.com/buffedlizard55-lab/Commodities/blob/main/';
const KALSHI_MARKET_URL = (ticker) => `https://external-api.kalshi.com/trade-api/v2/markets/${encodeURIComponent(ticker)}`;

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const pct = (value) => (value === null || value === undefined || Number.isNaN(value)) ? '—' : `${value > 0 ? '+' : ''}${Number(value).toFixed(3)}%`;
const cls = (value) => (value > 0 ? 'return-positive' : value < 0 ? 'return-negative' : '');
const money = (value, digits = 2) => (value === null || value === undefined) ? '—' : formatDollars(Number(value), digits);
const px = (value) => (value === null || value === undefined) ? '—' : `$${Number(value).toFixed(Number(value) < 0.1 || Number(value) > 0.9 ? 4 : 3)}`;
const shortHash = (hash) => (hash ? `${hash.slice(0, 10)}…` : '—');

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Math.floor(Date.now() / 60000)}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${path}`);
  return response.json();
}

const forward = { leaderboard: null, state: null, recent: null, curves: null, tab: 'positions', error: null };

export async function loadForwardDesk() {
  const label = $('#forward-state');
  try {
    const [leaderboard, state, recent, curves] = await Promise.all([
      fetchJson(`${FORWARD_BASE}leaderboard.json`),
      fetchJson(`${FORWARD_BASE}state.json`),
      fetchJson(`${FORWARD_BASE}recent.json`).catch(() => ({ events: [], intents: [], cycles: [] })),
      fetchJson(`${FORWARD_BASE}curves.json`).catch(() => ({ recent: {}, daily: {} })),
    ]);
    Object.assign(forward, { leaderboard, state, recent, curves, error: null });
    if (label) { label.textContent = `Committed desk state · ${leaderboard.cycles} cycle(s)`; label.classList.add('ok'); }
    const updated = $('#forward-updated');
    if (updated) updated.textContent = `last cycle ${formatDate(state.lastCycle?.at)} · ${state.lastCycle?.apiCalls ?? '—'} official API reads · ${state.lastCycle?.marketsSeen ?? '—'} open contracts scanned`;
  } catch (error) {
    forward.error = error;
    if (label) label.textContent = 'Forward ledger not published yet';
    const updated = $('#forward-updated');
    if (updated) updated.textContent = 'The first collector cycle commits data/season-2026/forward/ (see the Actions link below).';
  }
  renderForwardBoard();
  renderForwardLedger();
  renderMasterSiteMap();
  return forward;
}

export function forwardRowFor(username) {
  return forward.leaderboard?.rows.find((row) => row.username === username) ?? null;
}

export function forwardGated() {
  return forward.leaderboard?.gated ?? [];
}

export function forwardRows() {
  return forward.leaderboard?.rows ?? [];
}

function sparkline(points) {
  if (!points || points.length < 2) return '<span class="spark-empty">—</span>';
  const values = points.map((p) => Number(p[1]));
  const min = Math.min(...values); const max = Math.max(...values);
  const span = max - min || 1;
  const w = 96; const h = 26;
  const coords = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - min) / span) * (h - 2) - 1).toFixed(1)}`);
  const last = values[values.length - 1]; const first = values[0];
  return `<svg class="spark ${last >= first ? 'up' : 'down'}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-label="equity curve"><polyline points="${coords.join(' ')}" fill="none" stroke-width="1.6"/></svg>`;
}

function renderForwardBoard() {
  const metrics = $('#forward-metrics');
  const body = $('#forward-leaderboard tbody');
  const policies = $('#forward-policies');
  if (!body) return;
  if (!forward.leaderboard) {
    body.innerHTML = `<tr><td colspan="10" class="empty-cell">${forward.error ? `Could not read the committed forward ledger (${esc(forward.error.message)}). No placeholder rows are shown.` : 'Loading…'}</td></tr>`;
    return;
  }
  const { leaderboard, state } = forward;
  const totals = leaderboard.rows.reduce((acc, row) => ({ fills: acc.fills + row.fills, open: acc.open + row.openPositions, settled: acc.settled + row.settlements, exits: acc.exits + row.exits }), { fills: 0, open: 0, settled: 0, exits: 0 });
  if (metrics) metrics.innerHTML = [
    ['Desk cycles', String(leaderboard.cycles), `since ${formatDate(state.createdAt)} · every 30 min`],
    ['Verified fills', String(totals.fills), `${totals.exits} bid exits · ${leaderboard.ranked}/${leaderboard.participants} strategies ranked`],
    ['Open positions', String(totals.open), 'marked at bid, else last trade'],
    ['Settlements', String(totals.settled), 'official result + settlement_ts'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  body.innerHTML = leaderboard.rows.map((row) => {
    const curve = forward.curves?.recent?.[row.strategyId] ?? [];
    const evidence = row.fills ? ['verified forward fills', ''] : ['unranked · no fill yet', 'none'];
    return `<tr class="board-row" data-strategy="${esc(row.strategyId)}">
      <td class="rank">${row.rank ?? '—'}</td>
      <td><div class="persona-cell"><span class="avatar">${esc(row.username.slice(0, 2).toUpperCase())}</span><div><strong>${esc(row.username)}</strong><small>${esc(row.name)} · <a class="text-link" href="${esc(row.source?.url ?? '#')}" target="_blank" rel="noreferrer">${esc(row.source?.kind ?? 'source')} ↗</a></small></div></div></td>
      <td><b>${money(row.equity)}</b></td>
      <td class="${cls(row.returnPct)}">${pct(row.returnPct)}</td>
      <td class="${cls(row.liquidationReturnPct)}"><small>${money(row.liquidationEquity)}<br>${pct(row.liquidationReturnPct)}</small></td>
      <td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td>
      <td><small>${money(row.feesPaid)} · ${money(row.slippagePaid)}</small></td>
      <td><small>${row.fills} / ${row.openPositions} / ${row.settlements}${row.wins + row.losses ? ` · ${row.wins}W ${row.losses}L` : ''}</small></td>
      <td>${sparkline(curve)}</td>
      <td><span class="evidence-pill ${evidence[1]}">${esc(evidence[0])}</span></td>
    </tr>
    <tr class="board-detail" data-strategy-detail="${esc(row.strategyId)}" hidden><td colspan="10"><div class="detail-grid"><div><span>Rule</span><p>${esc(row.rule)}</p></div><div><span>Why it should (or should not) work</span><p>${esc(row.why)}</p></div><div><span>Provenance</span><p>${esc(row.source?.label ?? '')}</p></div><div class="span-2"><span>Result analysis (from the ledger)</span><p>${esc(row.analysis ?? '')}</p></div><div><span>Ledger</span><p><a class="text-link" href="${REPO_TREE}data/season-2026/forward/trades.jsonl" target="_blank" rel="noreferrer">trades.jsonl ↗</a> · <a class="text-link" href="${REPO_TREE}data/season-2026/forward/state.json" target="_blank" rel="noreferrer">state.json ↗</a> · unfilled remainder ${formatContracts(row.unfilledContracts)} contracts</p></div></div></td></tr>`;
  }).join('') || '<tr><td colspan="10" class="empty-cell">No strategies in the committed board.</td></tr>';
  if (policies) policies.innerHTML = [
    ['Fill policy', leaderboard.fillPolicy],
    ['Mark policy', leaderboard.markPolicy],
  ].map(([label, text]) => `<div class="policy-card"><span>${esc(label)}</span><p>${esc(text ?? '')}</p></div>`).join('');
}

function evidenceLink(evidence) {
  if (!evidence) return '—';
  const file = evidence.file ? `<a class="text-link" href="${REPO_TREE}data/season-2026/forward/${esc(evidence.file)}" target="_blank" rel="noreferrer">${esc(evidence.file.split('/').pop())} ↗</a>` : '';
  const url = evidence.url ? `<a class="text-link" href="${esc(evidence.url)}" target="_blank" rel="noreferrer">official ↗</a>` : '';
  return `${file}${file && url ? ' · ' : ''}${url}<br><small class="mono">sha256 ${esc(shortHash(evidence.sha256))} · ${esc(formatDate(evidence.retrievedAt))}</small>`;
}

function renderForwardLedger() {
  const head = $('#forward-ledger-table thead');
  const body = $('#forward-ledger-table tbody');
  if (!head || !body) return;
  const positions = Object.values(forward.state?.accounts ?? {}).flatMap((account) => account.positions.map((p) => ({ ...p, username: account.username })));
  const events = forward.recent?.events ?? [];
  const intents = forward.recent?.intents ?? [];
  const cycles = forward.recent?.cycles ?? [];
  $('#fwd-positions-count').textContent = String(positions.length);
  $('#fwd-events-count').textContent = String(events.length);
  $('#fwd-intents-count').textContent = String(intents.length);
  $('#fwd-cycles-count').textContent = String(cycles.length);
  document.querySelectorAll('[data-forward-tab]').forEach((button) => button.classList.toggle('active', button.dataset.forwardTab === forward.tab));
  if (!forward.state) {
    head.innerHTML = '';
    body.innerHTML = '<tr><td class="empty-cell">The forward ledger has not been published yet. Nothing is shown until the collector commits real data.</td></tr>';
    return;
  }
  if (forward.tab === 'positions') {
    head.innerHTML = '<tr><th>Strategy</th><th>Contract</th><th>Side · size</th><th>Entry (VWAP · touch · limit)</th><th>Fee · slippage</th><th>Mark</th><th>Unrealized</th><th>Closes</th><th>Evidence</th></tr>';
    positions.sort((a, b) => (b.entryAt ?? '').localeCompare(a.entryAt ?? ''));
    body.innerHTML = positions.length ? positions.map((p) => {
      const markValue = p.lastMark?.markValue ?? null;
      const unrealized = markValue === null ? null : markValue - p.entryNotional - p.entryFee;
      return `<tr><td><div class="persona-cell"><span class="avatar">${esc(p.username.slice(0, 2).toUpperCase())}</span><div><strong>${esc(p.username)}</strong><small>${esc(formatDate(p.entryAt))}</small></div></div></td>
        <td><b>${esc(p.title)}</b><br><small>${esc(p.subtitle ? `${p.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(p.ticker)}" target="_blank" rel="noreferrer">${esc(p.ticker)} ↗</a></small></td>
        <td><b>${esc(p.side.toUpperCase())}</b><br><small>${formatContracts(p.contracts)} filled${p.unfilledContracts ? ` · ${formatContracts(p.unfilledContracts)} unfilled` : ''}</small></td>
        <td><b>${px(p.entryPrice)}</b><br><small>touch ${px(p.entryTouch)} · limit ${px(p.limitPrice)} · ${p.levelsConsumed} level(s)</small></td>
        <td><small>${money(p.entryFee, 4)} · ${money(p.entrySlippage, 4)}</small></td>
        <td><small>${p.lastMark?.markPrice === null || p.lastMark?.markPrice === undefined ? 'no bid / no trade' : `${px(p.lastMark.markPrice)} (${esc(p.lastMark.bid !== null && p.lastMark.bid !== undefined ? 'bid' : 'last')})`}<br>${esc(formatDate(p.lastMark?.at))}</small></td>
        <td class="${cls(unrealized)}">${unrealized === null ? '—' : money(unrealized)}</td>
        <td><small>${esc(formatDate(p.closeTime))}</small></td>
        <td><small>${evidenceLink(p.evidence)}</small><br><small class="reason" title="${esc(p.entryReason)}">${esc(p.entryReason)}</small></td></tr>`;
    }).join('') : '<tr><td colspan="9" class="empty-cell">No open paper position. Positions appear only after a rule was confirmed on a fresh official order book.</td></tr>';
  } else if (forward.tab === 'events') {
    head.innerHTML = '<tr><th>When</th><th>Strategy</th><th>Event</th><th>Contract</th><th>Side · size</th><th>Entry</th><th>Exit</th><th>Fees · slippage</th><th>PnL</th><th>Evidence</th></tr>';
    body.innerHTML = events.length ? events.map((e) => {
      const kind = e.kind === 'fill' ? 'fill' : e.kind === 'exit' ? 'bid exit' : `settled ${String(e.result ?? '').toUpperCase()}`;
      const exit = e.kind === 'fill' ? '<span class="evidence-pill none">open</span>' : `<b>${px(e.exitPrice)}</b><br><small>${esc(formatDate(e.exitAt))}${e.exitReason ? ` · ${esc(e.exitReason)}` : ''}</small>`;
      const fees = e.kind === 'fill' ? `${money(e.entryFee, 4)} · ${money(e.slippageEntry, 4)}` : `${money(e.feesTotal, 4)} · ${money((e.slippageEntry ?? 0) + (e.slippageExit ?? 0), 4)}`;
      return `<tr><td><small>${esc(formatDate(e.at))}<br>cycle ${esc(e.cycle)}</small></td>
        <td><b>${esc(e.username)}</b></td>
        <td><span class="evidence-pill ${e.kind === 'fill' ? '' : e.pnl > 0 ? 'win' : 'loss'}">${esc(kind)}</span></td>
        <td><b>${esc(e.title)}</b><br><small>${esc(e.subtitle ? `${e.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(e.ticker)}" target="_blank" rel="noreferrer">${esc(e.ticker)} ↗</a></small></td>
        <td><b>${esc(e.side.toUpperCase())}</b><br><small>${formatContracts(e.contracts)}${e.unfilledContracts ? ` · ${formatContracts(e.unfilledContracts)} unfilled` : ''}</small></td>
        <td><b>${px(e.entryPrice)}</b><br><small>${e.kind === 'fill' ? `touch ${px(e.entryTouch)} · limit ${px(e.limitPrice)} · ${e.levelsConsumed} lvl` : esc(formatDate(e.entryAt))}</small></td>
        <td>${exit}</td>
        <td><small>${fees}</small></td>
        <td class="${cls(e.pnl)}">${e.kind === 'fill' ? '—' : money(e.pnl)}</td>
        <td><small>${evidenceLink(e.evidence)}</small>${e.reason ? `<br><small class="reason" title="${esc(e.reason)}">${esc(e.reason)}</small>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="10" class="empty-cell">No fill, exit or settlement has been recorded yet.</td></tr>';
  } else if (forward.tab === 'intents') {
    head.innerHTML = '<tr><th>Cycle</th><th>Strategy</th><th>Contract</th><th>Side · quote</th><th>Rule reason</th><th>Book check</th><th>Status</th></tr>';
    const label = { filled: ['filled', ''], queued: ['queued (next in line)', 'none'], not_confirmed_on_book: ['not confirmed on fresh book', 'blocked'], no_liquidity_or_cash: ['no executable depth / cash', 'blocked'], no_book: ['book unavailable', 'blocked'], skipped_position_cap: ['position cap reached', 'none'], proposed: ['proposed', 'none'] };
    body.innerHTML = intents.length ? intents.map((i) => {
      const [text, pill] = label[i.status] ?? [i.status, 'none'];
      const book = i.bookQuotes ? `YES ${px(i.bookQuotes.yes_bid)} / ${px(i.bookQuotes.yes_ask)} · NO ${px(i.bookQuotes.no_bid)} / ${px(i.bookQuotes.no_ask)}<br>${esc(formatDate(i.bookAt))}` : 'not fetched';
      return `<tr><td><small>${esc(formatDate(i.at))}<br>${esc(i.cycle)}</small></td><td><b>${esc(i.username)}</b></td><td><b>${esc(i.title)}</b><br><small>${esc(i.subtitle ? `${i.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(i.ticker)}" target="_blank" rel="noreferrer">${esc(i.ticker)} ↗</a> · closes ${esc(formatDate(i.closeTime))}</small></td><td><b>${esc(i.side.toUpperCase())}</b> @ ${px(i.quotePrice)}<br><small>limit ${px(i.limit)} · vol ${formatContracts(i.volume)}</small></td><td><small>${esc(i.reason)}</small></td><td><small>${book}</small></td><td><span class="evidence-pill ${pill}">${esc(text)}</span>${i.positionId ? `<br><small>${formatContracts(i.contracts)} @ ${px(i.fillPrice)}</small>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="7" class="empty-cell">No intent recorded in the current window.</td></tr>';
  } else if (forward.tab === 'cycles') {
    head.innerHTML = '<tr><th>Cycle (UTC)</th><th>Duration</th><th>API reads</th><th>Series · markets</th><th>Books</th><th>Intents · fills · exits · settlements</th><th>Signals</th><th>Errors</th></tr>';
    body.innerHTML = cycles.length ? cycles.map((c) => `<tr><td><b>${esc(c.cycle)}</b><br><small>${esc(formatDate(c.at))}</small></td><td>${esc(c.durationSec)} s</td><td>${esc(c.apiCalls)}${c.apiErrors ? ` <small>(${esc(c.apiErrors)} non-200)</small>` : ''}</td><td>${esc(c.seriesTracked)} · ${esc(c.marketsSeen)}</td><td>${esc(c.booksFetched)}</td><td>${esc(c.intents)} · ${esc(c.fills)} · ${esc(c.exits)} · ${esc(c.settlements)}</td><td><small>NWS ${c.nwsCaptured ? 'captured' : 'not captured'} · ${esc(c.candleMarkets)} candle market(s)</small></td><td><small>${c.errorCount ? esc((c.errors ?? []).slice(0, 3).join(' | ')) + (c.errorCount > 3 ? ` (+${c.errorCount - 3})` : '') : 'none'}</small></td></tr>`).join('') : '<tr><td colspan="8" class="empty-cell">No cycle recorded.</td></tr>';
  } else {
    head.innerHTML = '<tr><th>Signal</th><th>Captured</th><th>Source</th><th>Values used</th></tr>';
    const nws = forward.recent?.nws;
    const rows = [];
    if (nws) rows.push(`<tr><td><b>NWS point forecast · Central Park (OKX/34,45)</b><br><small>drives WeatherCatalyst / WeatherFader on KXHIGHNY</small></td><td><small>${esc(formatDate(nws.at))}<br>forecast updateTime ${esc(nws.updateTime ?? '—')}</small></td><td><a class="text-link" href="${esc(nws.source)}" target="_blank" rel="noreferrer">api.weather.gov ↗</a><br><small>archived in signals/nws-central-park.jsonl</small></td><td><small>${esc((nws.daytime ?? []).map((d) => `${d.date} ${d.name}: ${d.high_f}°F`).join(' · '))}<br>mapped: ${esc(Object.entries(nws.mapped ?? {}).map(([k, v]) => `${k} → ${v}°F`).join(', ') || 'no open KXHIGHNY event matched')}</small></td></tr>`);
    rows.push(`<tr><td><b>Official candlesticks</b><br><small>daily bars for PinePilotX (SMA cross); 1-minute bars for PanicFader</small></td><td><small>each cycle</small></td><td><small>GET /series/{series}/markets/{ticker}/candlesticks</small></td><td><small>${esc(forward.state?.lastCycle?.candleMarkets ?? 0)} daily-candle market(s) in the last cycle</small></td></tr>`);
    rows.push(`<tr><td><b>Kalshi market fields</b><br><small>last / previous (a day ago) / volume / close_time / strike bounds</small></td><td><small>each cycle</small></td><td><small>GET /markets?series_ticker=…&status=open</small></td><td><small>${esc(forward.state?.lastCycle?.marketsSeen ?? 0)} open contracts across ${esc(forward.state?.lastCycle?.seriesTracked ?? 0)} tracked series</small></td></tr>`);
    body.innerHTML = rows.join('');
  }
}

export function bindForwardEvents() {
  document.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-forward-tab]');
    if (tab) { forward.tab = tab.dataset.forwardTab; renderForwardLedger(); return; }
    const row = event.target.closest('.board-row');
    if (row && !event.target.closest('a')) {
      const detail = document.querySelector(`[data-strategy-detail="${row.dataset.strategy}"]`);
      if (detail) detail.hidden = !detail.hidden;
    }
  });
}

const MASTERSITE_MAP = [
  ['CEO', 'none (verified: no CEO project in the 2026-09-20 MasterSite data file)', '—', "Kalshi Companies series tagged 'CEOs' whose ticker names a CEO market (e.g. KXTESLACEOCHANGE, KXAAPLCEOCHANGE)", 'CEOExitFav'],
  ['Weather', 'SFWeather (NWS pipeline)', 'NWS gridded forecasts, nightly', 'KXHIGHNY daily-high brackets; NWS point forecast for Central Park archived at decision time', 'WeatherCatalyst · WeatherFader'],
  ['Insider trades', 'Insider-trades (SEC Form 4 dashboard)', 'Form 4 filings, no point-in-time archive', 'no Kalshi contract settles on a filing; SEC hosts unreachable from shared runners (sibling IR-76/77)', 'Form4Flash (gated)'],
  ['TheLeap', 'TradingViewTheLeap (contest research layer)', 'futures contest research', 'Kalshi index/commodity range series must be enumerated first (series catalog has 1,040 Financials rows)', 'LeapMapper (gated)'],
  ['NFL Injury', 'NFLInjuryReport (Actions cron every 10 min)', 'NFL.com injury rows', 'official game price on KXNFLGAME; injury rows are review links only', 'GridironPulse'],
  ['NBA Injury', 'NBAInjuryReport (ESPN injuries endpoint)', 'ESPN injury rows', 'no machine-readable official NBA feed (project finding); KXNBAGAME price traded instead', 'GridironPulse · TipoffTriage (gated)'],
  ['FDA Decisions Drug Analysis', 'DrugAnalysis (PDUFA calendar)', '32-entry decision calendar', "KXFDA* series tagged 'Medicine' (approval / approval-date markets)", 'FDAReaction'],
  ['NCAA Scoreboard', 'Ncaa-football-alerts', 'live college football alerts', 'KXNCAAFGAME (ESPN is listed as a settlement source on the series record)', 'GridironPulse · SportsPredLab'],
  ['NFL scoreboard', 'NFL-scoreboard', 'live NFL scores', 'KXNFLGAME game markets', 'GridironPulse · SportsPredLab'],
  ['MLB Scoreboard', 'MLB-Live-PBP · MLB-PBP · MLB-Prediction-model-backtest', 'live play-by-play, model backtest', 'KXMLBGAME game markets (shard 3, fee multiplier 0.5 per series record)', 'GridironPulse · SportsPredLab'],
  ['Sports Pred', 'SportsPred (22 sports, 95 leagues)', 'scores and model predictions', 'underdog convexity sweep on KX*GAME markets (model output is not evidence)', 'SportsPredLab'],
  ['Gold', 'GOLD (ring buyer directory — not a price signal)', 'jewelry buyer listings', 'Kalshi KXGOLD15M (Pyth-settled; ~140k contracts per 15-minute market) and KXGOLDH', 'MetalMomentum · TailSprint15 · PanicFader'],
  ['PinePilot', 'Tradingview-pinescript-editor (18 Pine v6 modes)', 'Pine Script strategy lab', 'SMA5/SMA10 cross replayed on official daily candlesticks with real fees', 'PinePilotX'],
];

function renderMasterSiteMap() {
  const body = $('#mastersite-map tbody');
  if (!body) return;
  body.innerHTML = MASTERSITE_MAP.map(([source, project, publishes, mapping, persona]) => `<tr><td><b>${esc(source)}</b></td><td><small>${esc(project)}</small></td><td><small>${esc(publishes)}</small></td><td><small>${esc(mapping)}</small></td><td><small>${esc(persona)}</small></td></tr>`).join('');
}

// ---------------------------------------------------------------- committed backtest (03)
export async function loadSeasonMemory(state, afterLoad) {
  const label = $('#season-memory-state');
  try {
    const [competition, leaderboard, trades, intents, explanations] = await Promise.all([
      fetchJson(`${SEASON_BASE}competition.json`), fetchJson(`${SEASON_BASE}leaderboard.json`), fetchJson(`${SEASON_BASE}trades.json`),
      fetchJson(`${SEASON_BASE}intents.json`), fetchJson(`${SEASON_BASE}explanations.json`),
    ]);
    state.season = { competition, leaderboard, trades, intents, explanations };
    if (label) { label.textContent = `Committed memory loaded · ${competition.verifiedBars} verified bars`; label.classList.add('ok'); }
  } catch (error) {
    if (label) label.textContent = `Committed memory unavailable (${error.message})`;
    return null;
  }
  renderSeasonMemory(state.season);
  if (typeof afterLoad === 'function') afterLoad();
  return state.season;
}

function renderSeasonMemory(season) {
  const { competition, leaderboard, trades, intents, explanations } = season;
  const markets = Object.entries(competition.markets ?? {});
  $('#bt-collected').textContent = competition.dataCollectedAt ?? '—';
  $('#bt-markets').textContent = `${markets.length} settled`;
  $('#bt-bars').textContent = `${competition.verifiedBars} (+${competition.coldOpenContextBars ?? 0} context)`;
  $('#bt-board-note').textContent = `data/season-2026/leaderboard.json · ${competition.sizingRule}`;
  $('#bt-intents-note').textContent = `${intents.length} proposed from the committed ${competition.dataCollectedAt} live snapshot — not fills`;
  $('#backtest-leaderboard tbody').innerHTML = leaderboard.map((row) => `<tr><td class="rank">${row.rank}</td><td><div class="persona-cell"><span class="avatar">${esc(row.username.slice(0, 2).toUpperCase())}</span><div><strong>${esc(row.username)}</strong><small>${esc(row.name)}</small></div></div></td><td><b>${money(row.equity)}</b></td><td class="${cls(row.returnPct)}">${pct(row.returnPct)}</td><td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td><td>${row.trades}</td><td><span class="evidence-pill">${esc(row.evidenceState)}</span></td></tr>`).join('');
  $('#backtest-intents tbody').innerHTML = intents.length ? intents.map((i) => `<tr><td><b>${esc(i.username)}</b><br><small>${esc(i.strategyName)}</small></td><td><b>${esc(i.ticker)}</b><br><small>${esc(i.title)}</small></td><td>${esc(i.side.toUpperCase())}</td><td>${px(i.proposedPrice)}</td><td>${formatContracts(i.maxDisplayDepth)}</td><td><small>${esc(i.reason)}</small></td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No intent stored with the snapshot.</td></tr>';
  $('#backtest-trades tbody').innerHTML = trades.map((t) => {
    const username = leaderboard.find((row) => row.strategyId === t.strategyId)?.username ?? t.strategyId;
    return `<tr><td><b>${esc(username)}</b></td><td><b>${esc(t.market)}</b><br><small>${esc(t.marketTitle)} · result ${esc(String(t.result).toUpperCase())}</small></td><td>${esc(t.side.toUpperCase())}</td><td>${formatContracts(t.contracts)}</td><td><b>${px(t.entryPrice)}</b><br><small>${esc(formatDate(t.entryAt))} · bar bid ${px(t.entryBar?.yes_bid_close)} / ask ${px(t.entryBar?.yes_ask_close)}</small></td><td><b>${px(t.exitPrice)}</b><br><small>${esc(formatDate(t.exitAt))} · ${esc(t.exitType)}</small></td><td>${money(t.feesTotal, 4)}</td><td>${money((t.slippageEntry ?? 0) + (t.slippageExit ?? 0), 4)}</td><td class="${cls(t.pnl)}">${money(t.pnl)}</td></tr>`;
  }).join('');
  $('#backtest-explanations').innerHTML = explanations.map((e) => `<article class="explain-card"><strong>@${esc(e.username)}</strong><p>${esc(e.explanation)}</p></article>`).join('');
  const files = ['MANIFEST.md', 'SHA256SUMS.txt', 'competition.json', 'leaderboard.json', 'trades.json', 'intents.json', 'explanations.json', 'market-records.json', 'series-records.json', 'cutoff.json', 'candles-KXCPI-26AUG-T0.8-daily.csv', 'candles-KXFED-26SEP-T4.75-daily.csv', 'candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv', 'raw/'];
  $('#backtest-files').innerHTML = files.map((f) => `<a class="file-chip" href="${REPO_TREE}data/season-2026/${esc(f)}" target="_blank" rel="noreferrer">${esc(f)} ↗</a>`).join('');
}
