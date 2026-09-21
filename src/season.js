// Committed season memory: the automated forward desk (data/season-<UTC year>/forward/*) and
// each season's committed backtest. Everything rendered here is read from files the collector or
// backtest script wrote; the browser never computes a result of its own.
import { formatContracts, formatDate, formatDollars } from './engine.js';

const SEASONS_PATH = 'data/seasons.json';
// Season-aware paths: the desk writes data/season-<UTC year>/, so the site follows data/seasons.json
// (written by scripts/forward_desk.py) and falls back to Season 2026 when the index is absent.
export const seasonMeta = { activeSeason: '2026', seasons: [], loaded: false };
const SEASON_BASE = () => `data/season-${seasonMeta.activeSeason}/`;
const FORWARD_BASE = () => `${SEASON_BASE()}forward/`;
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

const forward = { leaderboard: null, state: null, recent: null, curves: null, execution: null, today: null, audit: null, auditHistory: [], backfill: null, tab: 'positions', error: null };

export async function loadSeasonsIndex() {
  try {
    const index = await fetchJson(SEASONS_PATH);
    if (index && index.activeSeason) {
      seasonMeta.activeSeason = String(index.activeSeason);
      seasonMeta.seasons = index.seasons ?? [];
      seasonMeta.rolloverRule = index.rolloverRule ?? '';
      seasonMeta.generatedAt = index.generatedAt ?? null;
    }
  } catch { /* no index yet: stay on Season 2026 */ }
  seasonMeta.loaded = true;
  renderSeasons();
  return seasonMeta;
}

function renderSeasons() {
  const list = $('#season-list');
  if (!list) return;
  if (!seasonMeta.seasons.length) { list.innerHTML = '<p class="muted-inline">No season index committed yet (data/seasons.json).</p>'; return; }
  list.innerHTML = seasonMeta.seasons.map((row) => `<article class="season-chip ${row.active ? 'active' : ''} ${row.frozen ? 'frozen' : ''}">
      <strong>Season ${esc(row.season)}</strong>
      <span>${row.active ? 'active now' : row.frozen ? 'frozen' : 'pending'}</span>
      <small>${esc(row.cycles ?? 0)} cycle(s) · ${esc(row.participants ?? 0)} account(s) · ${esc(row.fills ?? 0)} fill(s) · ${esc(row.settlements ?? 0)} settlement(s)</small>
      <small>created ${esc(formatDate(row.createdAt))}${row.lastCycleAt ? ` · last cycle ${esc(formatDate(row.lastCycleAt))}` : ''}</small>
      <small class="mono">${esc(row.dir)}/ · backtest ${row.backtest ? 'yes' : 'no'} · archive backtest ${row.archiveBacktest ? 'yes' : 'no'}</small>
    </article>`).join('');
  const note = $('#season-note');
  if (note) note.textContent = seasonMeta.rolloverRule ?? '';
  const statePill = $('#season-state');
  if (statePill) {
    statePill.textContent = `Season ${seasonMeta.activeSeason} active · ${seasonMeta.seasons.length} season(s) committed`;
    statePill.classList.add('ok');
  }
  const year = $('#season-year');
  if (year && year.textContent === '—') year.textContent = seasonMeta.activeSeason;
}

export async function loadForwardDesk() {
  const label = $('#forward-state');
  if (!seasonMeta.loaded) await loadSeasonsIndex();
  try {
    const [leaderboard, state, recent, curves, execution, today, audit, auditHistory, backfill] = await Promise.all([
      fetchJson(`${FORWARD_BASE()}leaderboard.json`),
      fetchJson(`${FORWARD_BASE()}state.json`),
      fetchJson(`${FORWARD_BASE()}recent.json`).catch(() => ({ events: [], intents: [], cycles: [] })),
      fetchJson(`${FORWARD_BASE()}curves.json`).catch(() => ({ recent: {}, daily: {} })),
      fetchJson(`${FORWARD_BASE()}execution/summary.json`).catch(() => null),
      fetchJson(`${FORWARD_BASE()}summary/today.json`).catch(() => null),
      fetchJson(`${FORWARD_BASE()}audit/latest.json`).catch(() => null),
      fetchJson(`${FORWARD_BASE()}audit/history.json`).catch(() => []),
      fetchJson(`${FORWARD_BASE()}audit/settlement-backfill.json`).catch(() => null),
    ]);
    forward.execution = execution; forward.today = today; forward.audit = audit; forward.backfill = backfill;
    forward.auditHistory = Array.isArray(auditHistory) ? auditHistory : [];
    Object.assign(forward, { leaderboard, state, recent, curves, error: null });
    if (label) { label.textContent = `Committed desk state · Season ${esc(leaderboard.season ?? seasonMeta.activeSeason)} · ${leaderboard.cycles} cycle(s)`; label.classList.add('ok'); }
    const updated = $('#forward-updated');
    if (updated) updated.textContent = `last cycle ${formatDate(state.lastCycle?.at)} · ${state.lastCycle?.apiCalls ?? '—'} official API reads · ${state.lastCycle?.marketsSeen ?? '—'} open contracts scanned`;
  } catch (error) {
    forward.error = error;
    if (label) label.textContent = 'Forward ledger not published yet';
    const updated = $('#forward-updated');
    if (updated) updated.textContent = `The first collector cycle commits data/season-${seasonMeta.activeSeason}/forward/ (see the Actions link below).`;
  }
  renderForwardBoard();
  renderForwardLedger();
  renderExecutionRealism();
  renderSeasonHealth();
  renderToday();
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

function auditRateSparkline(rows) {
  const points = (rows ?? []).filter((row) => row && Number(row.expectedSlots) > 0 && Number.isFinite(Number(row.slotRatePct)))
    .map((row) => ({ at: row.generatedAt, value: Number(row.slotRatePct) }));
  if (!points.length) return '<span class="spark-empty">No audited scheduled slots to plot</span>';
  const values = points.map((point) => point.value);
  const w = 560; const h = 110; const pad = 12;
  const min = 0; const max = 100;
  const denominator = Math.max(1, points.length - 1);
  const coords = points.map((point, i) => `${(pad + (i / denominator) * (w - 2 * pad)).toFixed(1)},${(h - pad - ((point.value - min) / (max - min)) * (h - 2 * pad)).toFixed(1)}`);
  const last = values.at(-1);
  const series = points.length > 1
    ? `<polyline points="${coords.join(' ')}" fill="none" stroke="var(--accent, #54c98e)" stroke-width="2.4"/>`
    : `<circle cx="${coords[0].split(',')[0]}" cy="${coords[0].split(',')[1]}" r="4" fill="var(--accent, #54c98e)"/>`;
  const note = points.length > 1 ? '' : ' · one point, trend pending';
  return `<div class="audit-rate-chart"><svg class="audit-rate-sparkline" viewBox="0 0 ${w} ${h}" role="img" aria-label="scheduled cron slots matched rate over time, latest ${last.toFixed(2)} percent${note}"><line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="rgba(255,255,255,.18)"/><line x1="${pad}" y1="${pad}" x2="${w - pad}" y2="${pad}" stroke="rgba(255,255,255,.12)" stroke-dasharray="4 4"/>${series}</svg><div class="audit-rate-labels"><span>0%</span><b>${last.toFixed(2)}% latest${points.length > 1 ? '' : ' · pending trend'}</b><span>100%</span></div></div>`;
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
    const searchTerm = `${row.username} ${row.name} ${row.strategyId} ${row.group ?? ''} ${row.rule ?? ''}`.toLowerCase();
    return `<tr class="board-row" data-strategy="${esc(row.strategyId)}" data-search="${esc(searchTerm)}">
      <td class="rank">${row.rank ?? '—'}</td>
      <td><div class="persona-cell"><span class="avatar">${esc(row.username.slice(0, 2).toUpperCase())}</span><div><strong><a class="text-link" href="strategy.html?id=${esc(row.strategyId)}">${esc(row.username)}</a></strong><small>${esc(row.name)} · <a class="text-link" href="${esc(row.source?.url ?? '#')}" target="_blank" rel="noreferrer">${esc(row.source?.kind ?? 'source')} ↗</a> · <a class="text-link" href="strategy.html?id=${esc(row.strategyId)}">strategy page</a></small></div></div></td>
      <td><b>${money(row.equity)}</b></td>
      <td class="${cls(row.returnPct)}">${pct(row.returnPct)}</td>
      <td class="${cls(row.liquidationReturnPct)}"><small>${money(row.liquidationEquity)}<br>${pct(row.liquidationReturnPct)}</small></td>
      <td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td>
      <td><small>${money(row.feesPaid)} · ${money(row.slippagePaid)}</small></td>
      <td><small>${row.fills} / ${row.openPositions} / ${row.settlements}${row.wins + row.losses ? ` · ${row.wins}W ${row.losses}L` : ''}</small></td>
      <td>${sparkline(curve)}</td>
      <td><span class="evidence-pill ${evidence[1]}">${esc(evidence[0])}</span></td>
    </tr>
    <tr class="board-detail" data-strategy-detail="${esc(row.strategyId)}" hidden><td colspan="10"><div class="detail-grid"><div><span>Rule</span><p>${esc(row.rule)}</p></div><div><span>Why it should (or should not) work</span><p>${esc(row.why)}</p></div><div><span>Provenance</span><p>${esc(row.source?.label ?? '')}</p></div><div class="span-2"><span>Result analysis (from the ledger)</span><p>${esc(row.analysis ?? '')}</p></div><div><span>Ledger</span><p><a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/trades.jsonl" target="_blank" rel="noreferrer">trades.jsonl ↗</a> · ${ledgerLineLink(row.firstFill, 'first fill')} ${ledgerLineLink(row.latestFill, 'latest fill')} · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/state.json" target="_blank" rel="noreferrer">state.json ↗</a> · <a class="text-link" href="strategy.html?id=${esc(row.strategyId)}">full strategy page</a> · unfilled remainder ${formatContracts(row.unfilledContracts)} contracts</p></div></div></td></tr>`;
  }).join('') || '<tr><td colspan="10" class="empty-cell">No strategies in the committed board.</td></tr>';
  if (policies) policies.innerHTML = [
    ['Fill policy', leaderboard.fillPolicy],
    ['Mark policy', leaderboard.markPolicy],
  ].map(([label, text]) => `<div class="policy-card"><span>${esc(label)}</span><p>${esc(text ?? '')}</p></div>`).join('');
  applyBoardFilter();
}

// Shared client-side filter for the board and the ledger tables: a substring match on the row's
// own rendered text (username, strategy id, series, ticker).  It hides rows; it never removes data.
function applyTextFilter(tableSelector, inputSelector, countSelector) {
  const input = document.querySelector(inputSelector);
  const term = (input?.value ?? '').trim().toLowerCase();
  const rows = document.querySelectorAll(`${tableSelector} tbody tr`);
  let shown = 0;
  rows.forEach((tr) => {
    if (tr.classList.contains('board-detail')) return; // detail rows follow their board row
    const searchable = (tr.dataset.search ?? tr.textContent ?? '').toLowerCase();
    const hit = !term || searchable.includes(term);
    tr.hidden = !hit;
    if (hit) shown += 1;
    const detail = document.querySelector(`${tableSelector} [data-strategy-detail="${tr.dataset.strategy ?? '__'}"]`);
    if (detail && tr.classList.contains('board-row') && !hit) detail.hidden = true;
  });
  const note = document.querySelector(countSelector);
  if (note) note.textContent = term ? `${shown} / ${[...rows].filter((r) => !r.classList.contains('board-detail')).length} shown` : '';
}

function applyBoardFilter() { applyTextFilter('#forward-leaderboard', '#board-filter', '#board-filter-count'); }
function applyLedgerFilter() { applyTextFilter('#forward-ledger-table', '#ledger-filter', '#ledger-filter-count'); }

function ledgerLineLink(ref, label = 'ledger line') {
  if (!ref || !ref.ledgerFile || !Number.isInteger(Number(ref.ledgerLine))) return '';
  const file = String(ref.ledgerFile);
  const line = Number(ref.ledgerLine);
  return `<a class="text-link ledger-line-link" href="${REPO_TREE}${SEASON_BASE()}forward/${esc(file)}#L${line}" target="_blank" rel="noreferrer">${esc(label)} #L${line} ↗</a>`;
}

function evidenceLink(evidence) {
  if (!evidence) return '—';
  const storedFile = evidence.compacted && evidence.compressedFile ? evidence.compressedFile : evidence.file;
  const file = storedFile ? `<a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/${esc(storedFile)}" target="_blank" rel="noreferrer">${esc(evidence.compacted ? `${evidence.file} (gz)` : evidence.file.split('/').pop())} ↗</a>` : '';
  const url = evidence.url ? `<a class="text-link" href="${esc(evidence.url)}" target="_blank" rel="noreferrer">official ↗</a>` : '';
  const compactNote = evidence.compacted ? ` · original bytes in ${esc(evidence.compressedFile)}; hash preserved in COMPRESSED.json` : '';
  return `${file}${file && url ? ' · ' : ''}${url}<br><small class="mono">sha256 ${esc(shortHash(evidence.sha256))} · ${esc(formatDate(evidence.retrievedAt))}${compactNote}</small>`;
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
        <td><small>${ledgerLineLink({ ledgerFile: p.ledger?.fillFile, ledgerLine: p.ledger?.fillLine }, 'fill')} ${ledgerLineLink({ ledgerFile: p.ledger?.closeFile, ledgerLine: p.ledger?.closeLine }, 'close')}</small><br><small>${evidenceLink(p.evidence)}</small><br><small class="reason" title="${esc(p.entryReason)}">${esc(p.entryReason)}</small></td></tr>`;
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
        <td><small>${ledgerLineLink(e, e.kind === 'fill' ? 'fill' : 'event')}</small><br><small>${evidenceLink(e.evidence)}</small>${e.reason ? `<br><small class="reason" title="${esc(e.reason)}">${esc(e.reason)}</small>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="10" class="empty-cell">No fill, exit or settlement has been recorded yet.</td></tr>';
  } else if (forward.tab === 'intents') {
    head.innerHTML = '<tr><th>Cycle</th><th>Strategy</th><th>Contract</th><th>Side · quote</th><th>Rule reason</th><th>Book check</th><th>Status</th></tr>';
    const label = { filled: ['filled', ''], queued: ['queued (next in line)', 'none'], not_confirmed_on_book: ['not confirmed on fresh book', 'blocked'], no_liquidity_or_cash: ['no executable depth / cash', 'blocked'], no_book: ['book unavailable', 'blocked'], skipped_position_cap: ['position cap reached', 'none'], proposed: ['proposed', 'none'] };
    body.innerHTML = intents.length ? intents.map((i) => {
      const [text, pill] = label[i.status] ?? [i.status, 'none'];
      const book = i.bookQuotes ? `YES ${px(i.bookQuotes.yes_bid)} / ${px(i.bookQuotes.yes_ask)} · NO ${px(i.bookQuotes.no_bid)} / ${px(i.bookQuotes.no_ask)}<br>${esc(formatDate(i.bookAt))}` : 'not fetched';
      return `<tr><td><small>${esc(formatDate(i.at))}<br>${esc(i.cycle)}<br>${ledgerLineLink(i, 'intent')}</small></td><td><b>${esc(i.username)}</b></td><td><b>${esc(i.title)}</b><br><small>${esc(i.subtitle ? `${i.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(i.ticker)}" target="_blank" rel="noreferrer">${esc(i.ticker)} ↗</a> · closes ${esc(formatDate(i.closeTime))}</small></td><td><b>${esc(i.side.toUpperCase())}</b> @ ${px(i.quotePrice)}<br><small>limit ${px(i.limit)} · vol ${formatContracts(i.volume)}</small></td><td><small>${esc(i.reason)}</small></td><td><small>${book}</small></td><td><span class="evidence-pill ${pill}">${esc(text)}</span>${i.positionId ? `<br><small>${formatContracts(i.contracts)} @ ${px(i.fillPrice)}</small>` : ''}</td></tr>`;
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
    const espn = forward.recent?.espn ?? [];
    const espnSignals = Object.entries(forward.recent?.espnSignals ?? {});
    const liveSignals = espnSignals.filter(([, s]) => s.state === 'in');
    rows.push(`<tr><td><b>ESPN scoreboards · NFL / NCAAF / NBA / MLB / NHL / WNBA</b><br><small>drives ScorePulse (live scoreboard leader) and the game-persona review links; NHL/WNBA added 2026-09-21 (IRR-35), team names matched from Kalshi's nickname-form rules_primary (IRR-36)</small></td><td><small>${espn.length} snapshot(s) in the last window</small></td><td><small>site.api.espn.com scoreboard JSON (public)</small></td><td><small>${esc(espn.map((s) => `${s.league} ${s.date}: ${s.events} event(s)`).join(' · ') || 'none fetched')}<br>${esc(liveSignals.length ? `${liveSignals.length} live mapped: ${liveSignals.slice(0, 3).map(([t, s]) => `${t} ${s.detail ?? ''} ${s.awayScore ?? '?'}-${s.homeScore ?? '?'} (${s.matchedVia ?? ''})`).join('; ')}` : 'no in-progress mapped game at the cycle time - ScorePulse abstains unless a side leads past its threshold while still <= 85c')}<br>every snapshot archived with its URL + SHA-256 in signals/espn-scoreboard.jsonl</small></td></tr>`);
    const cities = forward.recent?.nwsCities ?? [];
    rows.push(`<tr><td><b>NWS city gridpoints · other KXHIGH* series</b><br><small>Census Gazetteer place - api.weather.gov /points - forecast</small></td><td><small>${cities.length ? `${cities.length} city capture(s) in the window` : 'none in the window'}</small></td><td><small>see the season-health abstentions below when the gazetteer step fails</small></td><td><small>${esc(cities.slice(0, 4).map((c) => `${c.series} (${c.city}): ${c.highF ?? '?'} F`).join(' - ') || '-')}</small></td></tr>`);
    const fda = forward.recent?.openfda ?? [];
    rows.push(`<tr><td><b>openFDA Drugs@FDA lookups</b><br><small>drives FdaRecordCheck; an openFDA 404 is recorded as a verified absence, never turned into a price</small></td><td><small>${fda.length} lookup record(s) in the window</small></td><td><small>api.fda.gov/drug/drugsfda.json (public, no key)</small></td><td><small>${esc(fda.slice(0, 4).map((r) => `${r.drug}: ${r.approvedRecord ? `record (${(r.applications ?? [])[0]?.application_number ?? ''})` : `no record as of ${r.retrievedAt ?? ''}`}`).join(' - ') || 'no FDA-decision market with a named drug was open at the cycle time')}</small></td></tr>`);
    const sigErrors = forward.recent?.signalErrors ?? [];
    if (sigErrors.length) {
      rows.push(`<tr><td><b>Adapter abstentions (last window)</b><br><small>fail-closed notes - no default value is ever invented</small></td><td><small>${esc(forward.state?.lastCycle?.signalErrorCount ?? sigErrors.length)} line(s) recorded</small></td><td><small>forward/cycles/*.jsonl</small></td><td><small class="mono">${esc(sigErrors.slice(0, 8).join(' | '))}</small></td></tr>`);
    }
    body.innerHTML = rows.join('');
  }
  applyLedgerFilter();
}


function renderExecutionRealism() {
  const panel = $('#execution-realism');
  if (!panel) return;
  const summary = forward.execution;
  const status = $('#execution-state');
  if (!summary || !summary.compared) {
    panel.innerHTML = '<div class="empty-state"><span class="empty-icon">◎</span><h3>Not compared yet</h3><p><code>scripts/execution_realism.py --live</code> writes <code>forward/execution/summary.json</code> after a runner cycle. The desk fills stay what they always were — verified order-book fills — this file only measures them against the official tape.</p></div>';
    if (status) { status.textContent = 'not compared yet (runner-only)'; status.classList.add('error'); }
    return;
  }
  const median = summary.medianAbsCentsDiff;
  if (status) {
    status.textContent = median === null ? `${summary.compared} fill(s) compared` : `median |desk − tape| = ${Number(median).toFixed(2)}¢`;
    status.classList.toggle('ok', median !== null && median <= 1);
    status.classList.toggle('error', median !== null && median > 1);
  }
  const cards = [
    ['Desk fills compared', String(summary.compared ?? 0), 'against GET /markets/trades on the same ticker'],
    ['Covered by the tape', `${summary.tapeCoveredPct ?? 0}%`, `${summary.noTapeInWindow ?? 0} window(s) had no print; ${summary.tapeFetchFailed ?? 0} fetch failure(s)`],
    ['Within 1¢ of the tape', `${summary.withinOneCentPct ?? 0}%`, 'median |desk − tape| = ' + (median === null || median === undefined ? 'n/a' : `${Number(median).toFixed(2)}¢`)],
    ['Desk price inside the tape range', `${summary.insideTapeRangePct ?? 0}%`, 'the desk never claims a better print than happened'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  const byStrategy = Object.entries(summary.byStrategy ?? {}).sort((a, b) => a[0].localeCompare(b[0]))
    .map(([id, row]) => `<tr><td><a class="text-link" href="strategy.html?id=${esc(id)}">${esc(id)}</a></td><td>${esc(row.compared)}</td><td>${row.medianAbsCentsDiff === null || row.medianAbsCentsDiff === undefined ? '—' : Number(row.medianAbsCentsDiff).toFixed(2) + '¢'}</td></tr>`).join('');
  panel.innerHTML = `<div class="metrics-grid">${cards}</div>
    <p class="verdict">${esc(summary.verdict ?? '')}</p>
    <div class="table-shell"><table><thead><tr><th>Strategy</th><th>Fills compared</th><th>Median |desk − tape|</th></tr></thead><tbody>${byStrategy || '<tr><td colspan=\"3\" class=\"empty-cell\">No fill has been compared yet.</td></tr>'}</tbody></table></div>
    <p class="micro-note"><span>Evidence</span><a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/execution/summary.json" target="_blank" rel="noreferrer">execution/summary.json ↗</a> · <a class="text-link" href="https://docs.kalshi.com/api-reference/trade/get-trades" target="_blank" rel="noreferrer">official trade tape docs ↗</a> · window ±120s · compared at ${esc(formatDate(summary.generatedAt))} · ${esc(summary.method ?? '')}</p>`;
}

function renderSeasonHealth() {
  const panel = $('#season-health-panel');
  if (!panel) return;
  const status = $('#health-state');
  const audit = forward.audit;
  if (!audit) {
    if (status) { status.textContent = 'awaiting first runner audit'; status.classList.add('error'); }
    panel.innerHTML = '<div class="empty-state"><span class="empty-icon">◎</span><h3>No season audit committed yet</h3><p><code>scripts/audit_season.py --live</code> runs on every collector cycle (schedule gaps, ledger coverage, storage compaction, signal coverage, tape summary, and a re-read of settled markets against <code>GET /markets/{ticker}</code>) and commits <code>forward/audit/latest.json</code>. Until that file exists this panel stays empty rather than showing a guess.</p></div>';
    return;
  }
  if (status) {
    status.textContent = `audit ${audit.status} · ${formatDate(audit.generatedAt)}`;
    status.classList.toggle('ok', audit.status === 'PASS');
    status.classList.toggle('error', audit.status !== 'PASS');
  }
  const sched = audit.schedule ?? {};
  const ledger = audit.ledger ?? {};
  const storage = audit.storage ?? {};
  const signals = audit.signals ?? {};
  const tape = audit.tape ?? {};
  const live = audit.live ?? {};
  const cards = [
    ['Cron slots executed', `${sched.executed ?? 0} / ${sched.expectedSlots ?? 0}`, `on-time ${sched.onTime ?? 0} · late ${sched.late ?? 0} · missed ${sched.missed ?? 0} · extra manual ${sched.extraManual ?? 0}`],
    ['Longest gap', sched.maxGapHours === undefined ? '—' : `${sched.maxGapHours} h`, 'hours between consecutive cycles'],
    ['Ledger events', String(ledger.events ?? 0), `${ledger.eventKinds?.fill ?? 0} fills · ${ledger.eventKinds?.exit ?? 0} exits · ${ledger.eventKinds?.settlement ?? 0} settlements · ${ledger.openPositions ?? 0} open`],
    ['Ledger coverage', ledger.fillsWithoutExitOrPosition === 0 ? 'complete' : `${ledger.fillsWithoutExitOrPosition} orphan fill(s)`, 'every fill has a position or a recorded close'],
    ['Storage compaction', `${storage.compactedFiles ?? 0} file(s)`, `${((storage.bytesBefore ?? 0) / 1024).toFixed(0)} KB → ${((storage.bytesAfter ?? 0) / 1024).toFixed(0)} KB gz · hashes verify: ${storage.hashVerifyPass ?? 0} ok / ${(storage.hashVerifyFailures ?? []).length} bad`],
    ['Tape comparison', tape.available ? `${tape.compared ?? 0} fills` : 'pending', tape.available ? `median ${tape.medianAbsCentsDiff}¢ · ${tape.withinOneCentPct}% within 1¢` : 'execution/summary.json not written yet'],
    ['Live settlement re-read', live.available ? `${live.matches ?? 0}/${live.attempts ?? 0} match` : 'pending (runner)', live.available ? `${(live.mismatches ?? []).length} mismatch(es) - every comparison re-issued GET /markets/{ticker}` : 'the offline audit never fakes this line'],
    ['Settlement backfill (full)', forward.backfill?.mode === 'live' ? `${forward.backfill.matches ?? 0}/${forward.backfill.attempted ?? 0} match` : 'not run yet', forward.backfill?.mode === 'live' ? `every settled market re-read ${formatDate(forward.backfill.generatedAt)} · ${(forward.backfill.mismatches ?? []).length} mismatch(es) · ${(forward.backfill.unreachable ?? []).length} unreachable` : 'monthly: scripts/verify_settlements.py --live'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  const sig = signals.totals ?? {};
  const signalChips = [
    ['NWS Central Park captures', sig.nwsCentralPark], ['NWS city forecasts', sig.nwsCityForecasts],
    ['ESPN signals (last window)', sig.espnSignals], ['ESPN live-game signals', sig.espnLiveSignals],
    ['openFDA lookups', sig.fdaSignals], ['openFDA verified absences', sig.fdaNoRecord],
    ['archived candle files', sig.candlesArchived], ['signal error lines', sig.signalErrors],
  ].map(([label, value]) => `<span class="evidence-pill ${(String(label).startsWith('signal error') && Number(value) > 0) ? 'blocked' : ''}">${esc(label)}: ${esc(value ?? 0)}</span>`).join(' ');
  const errorLines = (signals.latestSignalErrors ?? []).slice(0, 10)
    .map((e) => `<li class="mono">${esc(e)}</li>`).join('');
  const liveRows = (live.samples ?? []).map((s) => `<tr>
      <td><a class="text-link" href="${KALSHI_MARKET_URL(s.ticker)}" target="_blank" rel="noreferrer">${esc(s.ticker)} ↗</a></td>
      <td><small>${esc(s.strategyId ?? '')}</small></td>
      <td><small>${esc(String(s.ledgerResult ?? ''))} @ ${esc(s.ledgerSettlementTs ?? '')}</small></td>
      <td><small>${esc(String(s.apiResult ?? s.status ?? 'fetch failed'))}${s.apiSettlementTs ? ` @ ${esc(s.apiSettlementTs)}` : ''}</small></td>
      <td>${s.status === 'unreachable' ? '<span class="evidence-pill none">unreachable</span>' : (s.resultMatches && s.settlementMatches ? '<span class="evidence-pill">match</span>' : '<span class="evidence-pill blocked">MISMATCH</span>')}</td>
      <td><small class="mono">${esc(shortHash(s.responseSha256))}</small></td></tr>`).join('');
  const mismatchBanner = (live.mismatches ?? []).length
    ? `<div class="notice notice-amber"><div class="notice-icon">!</div><div><strong>${live.mismatches.length} settled market(s) where the ledger and the live official record disagree</strong><p>The audit never adjusts the ledger: the mismatch stands here, and the affected positions are named in the table below.</p></div></div>` : '';
  const backfill = forward.backfill;
  const backfillBanner = backfill?.mode === 'live' && (backfill.mismatches ?? []).length
    ? `<div class="notice notice-amber"><div class="notice-icon">!</div><div><strong>Full settlement backfill (${esc(formatDate(backfill.generatedAt))}): ${backfill.mismatches.length} ledger-vs-API mismatch(es) across ${esc(backfill.settledTickers ?? 0)} settled markets</strong><p>${esc(backfill.mismatches.map((m) => `${m.ticker}: ledger ${m.ledgerResult ?? ''}@${m.ledgerSettlementTs ?? ''} vs API ${m.apiResult ?? ''}@${m.apiSettlementTs ?? ''}`).join(' · '))}. The ledger was NOT adjusted - this is a finding for manual review (<a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/audit/settlement-backfill.json" target="_blank" rel="noreferrer">settlement-backfill.json ↗</a>).</p></div></div>` : '';
  const details = [
    `median cycle start delay ${sched.medianDelayMin ?? '—'} min`,
    (sched.missedSlotsSample ?? []).length ? `missed slot sample: ${sched.missedSlotsSample.slice(0, 6).join(', ')}${sched.missedSlotsSample.length > 6 ? ` (+${sched.missedSlotsSample.length - 6} more)` : ''}` : 'no missed cron slots',
    (ledger.orphanFillSample ?? []).length ? `orphan fills: ${ledger.orphanFillSample.join(', ')}` : 'no orphan fills',
    (ledger.equityCsvVsStateMismatches ?? 0) ? `${ledger.equityCsvVsStateMismatches} equity CSV drift row(s)` : 'equity CSV matches account state',
    live.available ? 'live settlement re-read attempted' : esc(live.note ?? 'live re-read pending (needs runner network)'),
  ].join(' · ');
  const historyRows = forward.auditHistory ?? [];
  const historyChart = `<div class="health-trend"><div class="panel-header"><div><h3>Scheduled-slot execution trend</h3><p>matched scheduled cron slots ÷ expected slots, from append-only audit rows; manual extra cycles do not inflate this rate</p></div><span class="source-badge">${historyRows.length} audit point(s)</span></div>${auditRateSparkline(historyRows)}<p class="micro-note"><span>Definition</span>Only <code>on-time + late</code> scheduled slots count in the numerator. Missed slots remain visible; an unavailable or zero-slot audit is not plotted.</p></div>`;
  panel.innerHTML = `${mismatchBanner}${backfillBanner}<div class="metrics-grid">${cards}</div>
    ${historyChart}
    <p class="micro-note"><span>Signal coverage</span>${signalChips}</p>
    ${errorLines ? `<details class="health-errors"><summary>Latest cycle's signal abstentions (${sig.signalErrors ?? 0} line(s)) - a source that answers nothing makes its personas abstain, never default</summary><ul>${errorLines}</ul></details>` : ''}
    ${liveRows ? `<div class="table-shell"><table><thead><tr><th>Settled market</th><th>Strategy</th><th>Ledger says</th><th>Official API now</th><th>Verdict</th><th>Response hash</th></tr></thead><tbody>${liveRows}</tbody></table></div>` : ''}
    <p class="micro-note"><span>Coverage details</span>${esc(details)}</p>
    <p class="micro-note"><span>Audit files</span><a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/audit/latest.json" target="_blank" rel="noreferrer">forward/audit/latest.json ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/audit/audit-history.jsonl" target="_blank" rel="noreferrer">audit-history.jsonl ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/audit/history.json" target="_blank" rel="noreferrer">history.json ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/audit/settlement-backfill.json" target="_blank" rel="noreferrer">settlement-backfill.json ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/COMPRESSED.json" target="_blank" rel="noreferrer">COMPRESSED.json ↗</a> · cron ${esc(sched.cron ?? '—')} · cycles ${esc(sched.cycles ?? 0)} · ${esc(sched.note ?? '')}</p>`;
}

function renderToday() {
  const panel = $('#daily-summary');
  if (!panel) return;
  const day = forward.today;
  if (!day) { panel.innerHTML = '<p class="muted-inline">No daily summary committed yet.</p>'; return; }
  const eq = day.equity ?? {};
  const cards = [
    ['Accounts', String(day.accounts ?? 0), `each starts at ${money(STARTING_CASH_LABEL)}`],
    ['Equity (mark-to-bid)', money(eq.total), `${pct(eq.returnPct)} across ${esc(eq.ranked ?? 0)} ranked`],
    ['Equity (last trade)', money(eq.liquidationTotal), 'conservative mark, no bid assumed'],
    ['Realized PnL', money(eq.realizedPnl), `${money(eq.feesPaid)} fees · ${money(eq.slippagePaid)} slippage`],
    [`Today (${esc(day.fills ?? 0)} fills)`, money(day.realizedToday), `${esc(day.exits ?? 0)} exits · ${esc(day.settlements ?? 0)} settlements · ${money(day.feesToday)} fees`],
    ['Ledger', `${formatContracts(day.ledger?.contracts)} contracts filled`, `${esc(day.ledger?.markets ?? 0)} distinct contracts touched`],
    ['Open positions', `${esc(day.positions?.open ?? 0)} (${formatContracts(day.positions?.contracts)} ct)`, `entry notional ${money(day.positions?.entryNotional)}`],
    ['Top strategy', day.leaders?.[0] ? pct(day.leaders[0].returnPct) : '—', day.leaders?.[0] ? `${esc(day.leaders[0].username)} · ${esc(day.leaders[0].fills ?? 0)} fill(s) today` : 'none ranked yet'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  const movers = (day.rows ?? []).slice(0, 12).map((m) => `<tr><td><a class="text-link" href="strategy.html?id=${esc(m.strategyId)}">${esc(m.username)}</a></td>
      <td>${money(m.equity)}</td><td class="${cls(m.returnPct)}">${pct(m.returnPct)}</td>
      <td class="${cls(m.pnlToday)}">${money(m.pnlToday)}</td><td>${esc(m.fillsToday)} / ${esc(m.closesToday)}</td><td>${esc(m.openPositions)}</td></tr>`).join('');
  const signals = (day.signals ?? []).map((sig) => `<span class="evidence-pill ${sig.ok ? '' : 'blocked'}">${esc(sig.source)}: ${sig.ok ? (sig.captured ? `${esc(sig.captured)} captured` : 'captured') : 'not captured'}</span>`).join(' ');
  panel.innerHTML = `<div class="section-head"><h3>${esc(day.date ?? '')} <span class="pill">${esc(day.weekday ?? '')}</span></h3>
      <p class="muted-inline">${esc(day.cycles ?? 0)} cycle(s) committed today · ${esc(day.apiCalls ?? 0)} official API reads (${esc(day.apiErrors ?? 0)} non-200) · ${esc(day.errors ?? 0)} recorded error(s)${(day.errorSamples ?? []).length ? `: ${esc(day.errorSamples.join(' | '))}` : ''}</p></div>
    <div class="metric-grid">${cards}</div>
    <div class="two-col"><div><h4>Board at the close of the day</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>Strategy</th><th>Equity</th><th>Return</th><th>PnL today</th><th>Fills / closes</th><th>Open</th></tr></thead><tbody>${movers || '<tr><td colspan="6" class="empty-cell">No account in this season yet.</td></tr>'}</tbody></table></div></div>
    <div><h4>Signal adapters</h4><p class="signal-chips">${signals || 'none captured'}</p>
      <p class="note">Best move today: <b>${esc(day.bestMove?.username ?? '—')}</b> ${day.bestMove ? money(day.bestMove.pnlToday) : ''} · worst: <b>${esc(day.worstMove?.username ?? '—')}</b> ${day.worstMove ? money(day.worstMove.pnlToday) : ''}</p>
      <p class="note">${esc(day.note ?? '')}</p></div></div>
    <p class="note"><a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/summary/${esc(day.date ?? 'today')}.json" target="_blank" rel="noreferrer">summary/${esc(day.date ?? 'today')}.json ↗</a> · written by <code>scripts/forward_desk.py</code> every cycle</p>`;
}

const STARTING_CASH_LABEL = 10000;

export async function loadArchiveBacktest() {
  const panel = $('#archive-backtest');
  const statePill = $('#archive-state');
  if (!panel) return null;
  let archive = null; let board = null; let explanations = [];
  try {
    [archive, board] = await Promise.all([
      fetchJson(`${SEASON_BASE()}backtest-archive/competition.json`),
      fetchJson(`${SEASON_BASE()}backtest-archive/leaderboard.json`),
    ]);
  } catch { /* not built yet */ }
  try { explanations = await fetchJson(`${SEASON_BASE()}backtest-archive/explanations.json`); } catch { /* optional */ }
  let curves = null; let walk = null;
  try { curves = await fetchJson(`${SEASON_BASE()}backtest-archive/curves.json`); } catch { /* optional */ }
  try { walk = await fetchJson(`${SEASON_BASE()}backtest-archive/walkforward.json`); } catch { /* optional */ }
  if (!archive || !Array.isArray(board) || !board.length) {
    panel.innerHTML = '<div class="empty-state"><span class="empty-icon">◎</span><h3>No archive backtest for this season yet</h3><p><code>python3 scripts/backtest_archive.py</code> replays the committed candle archive and writes <code>backtest-archive/</code>. Nothing is shown until those files exist.</p></div>';
    if (statePill) { statePill.textContent = 'not built for this season'; statePill.classList.add('error'); }
    return null;
  }
  const byId = Object.fromEntries((explanations ?? []).map((row) => [row.strategyId, row]));
  const rows = board.map((row) => `<tr><td class="rank">${esc(row.rank)}</td>
    <td><b>${esc(row.username)}</b><br><small>${esc(row.name)}</small></td>
    <td class="${cls(row.returnPct)}"><b>${pct(row.returnPct)}</b><br><small>${money(row.realizedPnl)}</small></td>
    <td><small>${esc(row.trades)} trade(s) · ${esc(row.wins ?? 0)}W ${esc(row.losses ?? 0)}L · ${esc(row.marketsTraded)} market(s) · ${formatContracts(row.contracts)} ct</small></td>
    <td><small>${money(row.feesPaid)} fees · ${money(row.slippagePaid)} slippage · ${esc(row.skippedNoLiquidity ?? 0)} skipped for no depth</small></td>
    <td><small>${esc(row.rule)}</small></td>
    <td><small>${esc((byId[row.strategyId] ?? {}).explanation ?? row.why ?? '')}</small></td></tr>`).join('');
  const facts = [
    ['Archived markets', String(archive.marketCount ?? 0), `${esc((archive.series ?? []).length)} series · min ${esc(archive.minBars ?? 0)} bars`],
    ['Verified bars', String(archive.verifiedBars ?? 0), `periods ${(archive.periods ?? []).join(' / ')} min`],
    ['Sizing', archive.sizingRule ?? '—', 'no entry without bar volume'],
    ['Exits', archive.exitRule ?? '—', 'official result at the archived settlement_ts'],
    ['Fees', archive.feeRule ?? '—', 'quadratic taker fee, series multiplier'],
    ['Provenance', 'candles/index.jsonl', 'every bar bound to its response URL + SHA-256'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  if (statePill) { statePill.textContent = `${archive.marketCount} markets · ${archive.verifiedBars} bars · ${board.length} rule sets`; statePill.classList.add('ok'); }
  panel.innerHTML = `<div class="metrics-grid">${facts}</div>
    <div class="table-shell"><table><thead><tr><th>#</th><th>Rule set</th><th>Return on $10,000</th><th>Trading profile</th><th>Cost of trading</th><th>Entry rule</th><th>Why it won or lost</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${renderArchiveCurves(curves, board)}
    ${renderArchiveFolds(walk)}
    <p class="micro-note"><span>Evidence</span>Replayed bar by bar by <code>scripts/backtest_archive.py</code> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}backtest-archive/trades.json" target="_blank" rel="noreferrer">every trade ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}backtest-archive/explanations.json" target="_blank" rel="noreferrer">per-rule explanations ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}backtest-archive/curves.json" target="_blank" rel="noreferrer">curve data ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}backtest-archive/walkforward.json" target="_blank" rel="noreferrer">walk-forward data ↗</a> · <a class="text-link" href="${REPO_TREE}${SEASON_BASE()}forward/candles/index.jsonl" target="_blank" rel="noreferrer">candle index ↗</a> · a market is never traded before it has an official result.</p>`;
  return { archive, board, explanations };
}

const CURVE_COLORS = ['#54c98e', '#e0b13c', '#4ea1ff', '#e06c75', '#b48ead', '#56b6c2', '#d19a66', '#7f9f7f', '#c678dd', '#61afef', '#98c379'];

function renderArchiveCurves(curves, board) {
  if (!curves || !curves.strategies) return '';
  const series = Object.entries(curves.strategies)
    .map(([id, row]) => ({ id, username: row.username, points: row.points ?? [], startTs: row.startTs }))
    .filter((row) => row.points.length);
  if (!series.length) {
    return '<div class="panel"><div class="panel-header"><div><h3>Archive-backtest equity curves</h3>'
      + '<p>every rule set abstained; no curve to draw</p></div><span class="source-badge">realized only</span></div></div>';
  }
  const returnById = Object.fromEntries((board ?? []).map((row) => [row.strategyId, row]));
  const xs = series.flatMap((row) => [row.startTs ?? row.points[0][0], ...row.points.map(([ts]) => ts)]);
  const ys = series.flatMap((row) => row.points.map(([, cum]) => (curves.startingCash ?? 10000) + cum));
  const x0 = Math.min(...xs); const x1 = Math.max(...xs);
  const y0 = Math.min(...ys, curves.startingCash ?? 10000); const y1 = Math.max(...ys, curves.startingCash ?? 10000);
  const W = 720; const H = 220; const pad = 8;
  const sx = (t) => pad + ((t - x0) / Math.max(1, x1 - x0)) * (W - 2 * pad);
  const sy = (v) => H - pad - ((v - y0) / Math.max(1e-9, y1 - y0)) * (H - 2 * pad);
  const zeroY = sy(curves.startingCash ?? 10000);
  const lines = series.map((row, i) => {
    const pts = [[row.startTs ?? row.points[0][0], curves.startingCash ?? 10000], ...row.points]
      .map(([t, v]) => `${sx(t).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
    return `<polyline fill="none" stroke="${CURVE_COLORS[i % CURVE_COLORS.length]}" stroke-width="2" points="${pts}"/>`;
  }).join('');
  const legend = series.map((row, i) => {
    const final = returnById[row.id] ?? {};
    return `<span class="curve-legend-item"><i style="background:${CURVE_COLORS[i % CURVE_COLORS.length]}"></i>${esc(row.username)} <b class="${cls(final.returnPct)}">${esc(pct(final.returnPct))}</b></span>`;
  }).join('');
  return `<div class="panel"><div class="panel-header"><div><h3>Realized-equity curves — one point per closed trade</h3>
      <p>entry fills at the bar's verified ask, exits at a later verified bid or the official result; positions carry at cost until closed (no unrealized mark)</p></div>
      <span class="source-badge">backtest-archive/curves.json</span></div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="archive backtest realized equity curves" class="curve-svg">
      <line x1="${pad}" y1="${zeroY}" x2="${W - pad}" y2="${zeroY}" stroke="rgba(255,255,255,.18)" stroke-dasharray="4 4"/>
      ${lines}
    </svg>
    <div class="curve-legend">${legend}</div>
    <p class="micro-note"><span>Note</span>${esc(curves.method ?? '')} · the curve ends exactly at the leaderboard number; scripts/verify_data.py checks that identity.</p></div>`;
}

function renderArchiveFolds(walk) {
  if (!walk || !Array.isArray(walk.rows) || !walk.rows.length) return '';
  const windows = Object.fromEntries((walk.windows ?? []).map((w) => [w.fold, w]));
  const rows = walk.rows.map((r) => `<tr>
      <td><b>${esc(r.username)}</b></td>
      <td><small>fold ${esc(r.fold)} · ${esc(windows[r.fold] ? `${windows[r.fold].startAt} → ${windows[r.fold].endAt}` : `#${r.fold}`)}</small></td>
      <td><small>${esc(r.trades)} trade(s) · ${esc(r.wins)}W ${esc(r.losses)}L</small></td>
      <td class="${cls(r.realizedPnl)}"><small>${esc(money(r.realizedPnl))}</small></td>
      <td class="${cls(r.returnPct)}"><b>${esc(pct(r.returnPct))}</b></td>
    </tr>`).join('');
  return `<div class="panel"><div class="panel-header"><div><h3>Walk-forward windows — stability, not a train/test split</h3>
      <p>the replay horizon cut into ${esc(walk.folds ?? '?')} equal windows by entry time; exits and settlements still use the real later bars; each window restarts from $10,000</p></div>
      <span class="source-badge">backtest-archive/walkforward.json</span></div>
    <div class="table-shell"><table><thead><tr><th>Rule set</th><th>Window</th><th>Entries</th><th>Realized</th><th>Return (reset per window)</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="micro-note"><span>Caveat</span>${esc(walk.caveat ?? '')}</p></div>`;
}

export async function loadStrategyDetail(strategyId) {
  const base = `${FORWARD_BASE()}strategies/${strategyId}.json`;
  try { return await fetchJson(base); } catch { return null; }
}

export function bindForwardEvents() {
  for (const [inputSelector, apply] of [['#board-filter', applyBoardFilter], ['#ledger-filter', applyLedgerFilter]]) {
    const input = document.querySelector(inputSelector);
    if (input) input.addEventListener('input', apply);
  }
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
      fetchJson(`${SEASON_BASE()}competition.json`), fetchJson(`${SEASON_BASE()}leaderboard.json`), fetchJson(`${SEASON_BASE()}trades.json`),
      fetchJson(`${SEASON_BASE()}intents.json`), fetchJson(`${SEASON_BASE()}explanations.json`),
    ]);
    state.season = { competition, leaderboard, trades, intents, explanations };
    loadArchiveBacktest();
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
  $('#bt-board-note').textContent = `${SEASON_BASE()}leaderboard.json · ${competition.sizingRule}`;
  $('#bt-intents-note').textContent = `${intents.length} proposed from the committed ${competition.dataCollectedAt} live snapshot — not fills`;
  $('#backtest-leaderboard tbody').innerHTML = leaderboard.map((row) => `<tr><td class="rank">${row.rank}</td><td><div class="persona-cell"><span class="avatar">${esc(row.username.slice(0, 2).toUpperCase())}</span><div><strong>${esc(row.username)}</strong><small>${esc(row.name)}</small></div></div></td><td><b>${money(row.equity)}</b></td><td class="${cls(row.returnPct)}">${pct(row.returnPct)}</td><td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td><td>${row.trades}</td><td><span class="evidence-pill">${esc(row.evidenceState)}</span></td></tr>`).join('');
  $('#backtest-intents tbody').innerHTML = intents.length ? intents.map((i) => `<tr><td><b>${esc(i.username)}</b><br><small>${esc(i.strategyName)}</small></td><td><b>${esc(i.ticker)}</b><br><small>${esc(i.title)}</small></td><td>${esc(i.side.toUpperCase())}</td><td>${px(i.proposedPrice)}</td><td>${formatContracts(i.maxDisplayDepth)}</td><td><small>${esc(i.reason)}</small></td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No intent stored with the snapshot.</td></tr>';
  $('#backtest-trades tbody').innerHTML = trades.map((t) => {
    const username = leaderboard.find((row) => row.strategyId === t.strategyId)?.username ?? t.strategyId;
    return `<tr><td><b>${esc(username)}</b></td><td><b>${esc(t.market)}</b><br><small>${esc(t.marketTitle)} · result ${esc(String(t.result).toUpperCase())}</small></td><td>${esc(t.side.toUpperCase())}</td><td>${formatContracts(t.contracts)}</td><td><b>${px(t.entryPrice)}</b><br><small>${esc(formatDate(t.entryAt))} · bar bid ${px(t.entryBar?.yes_bid_close)} / ask ${px(t.entryBar?.yes_ask_close)}</small></td><td><b>${px(t.exitPrice)}</b><br><small>${esc(formatDate(t.exitAt))} · ${esc(t.exitType)}</small></td><td>${money(t.feesTotal, 4)}</td><td>${money((t.slippageEntry ?? 0) + (t.slippageExit ?? 0), 4)}</td><td class="${cls(t.pnl)}">${money(t.pnl)}</td></tr>`;
  }).join('');
  $('#backtest-explanations').innerHTML = explanations.map((e) => `<article class="explain-card"><strong>@${esc(e.username)}</strong><p>${esc(e.explanation)}</p></article>`).join('');
  const files = ['MANIFEST.md', 'SHA256SUMS.txt', 'competition.json', 'leaderboard.json', 'trades.json', 'intents.json', 'explanations.json', 'market-records.json', 'series-records.json', 'cutoff.json', 'candles-KXCPI-26AUG-T0.8-daily.csv', 'candles-KXFED-26SEP-T4.75-daily.csv', 'candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv', 'raw/'];
  $('#backtest-files').innerHTML = files.map((f) => `<a class="file-chip" href="${REPO_TREE}${SEASON_BASE()}${esc(f)}" target="_blank" rel="noreferrer">${esc(f)} ↗</a>`).join('');
}
