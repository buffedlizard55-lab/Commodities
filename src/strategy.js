// Per-strategy page: everything on this page is copied from data/season-<year>/forward/strategies/<id>.json,
// which scripts/forward_desk.py writes from the ledger at the end of every cycle.  The page computes
// nothing about trading — it only formats committed rows and links to the artifacts behind them.
import { formatContracts, formatDate, formatDollars, STARTING_CASH } from './engine.js';
import { loadSeasonsIndex, seasonMeta } from './season.js';

const REPO_TREE = 'https://github.com/buffedlizard55-lab/Commodities/blob/main/';
const KALSHI_MARKET_URL = (ticker) => `https://kalshi.com/markets/${encodeURIComponent(ticker)}`;

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const money = (value, digits = 2) => (value === null || value === undefined) ? '—' : formatDollars(Number(value), digits);
const pct = (value) => (value === null || value === undefined) ? '—' : `${value > 0 ? '+' : ''}${Number(value).toFixed(3)}%`;
const cls = (value) => (value > 0 ? 'return-positive' : value < 0 ? 'return-negative' : '');
const px = (value) => (value === null || value === undefined) ? '—' : `$${Number(value).toFixed(Number(value) < 0.1 || Number(value) > 0.9 ? 4 : 3)}`;
const shortHash = (hash) => (hash ? `${hash.slice(0, 10)}…` : '—');

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Math.floor(Date.now() / 60000)}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${path}`);
  return response.json();
}

function curve(rows) {
  // rows: [cycle, equity, cash, openPositions, realizedPnl, feesPaid, slippagePaid]
  if (!rows || rows.length < 2) return '<p class="muted-inline">Not enough committed cycles for a curve yet (one point per cycle is appended to equity/).</p>';
  const values = rows.map((r) => Number(r[1]));
  const min = Math.min(...values, STARTING_CASH);
  const max = Math.max(...values, STARTING_CASH);
  const span = max - min || 1;
  const w = 720; const h = 190;
  const x = (i) => (i / (values.length - 1)) * (w - 8) + 4;
  const y = (v) => h - 14 - ((v - min) / span) * (h - 30);
  const coords = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const baseline = y(STARTING_CASH).toFixed(1);
  const up = values[values.length - 1] >= values[0];
  return `<figure class="equity-chart">
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="equity curve for this strategy">
      <line class="baseline" x1="0" y1="${baseline}" x2="${w}" y2="${baseline}"/>
      <polyline class="${up ? 'up' : 'down'}" points="${coords}" fill="none" stroke-width="2"/>
      <circle class="${up ? 'up' : 'down'}" cx="${x(values.length - 1).toFixed(1)}" cy="${y(values[values.length - 1]).toFixed(1)}" r="3.5"/>
    </svg>
    <figcaption>${rows.length} committed cycle point(s) · first ${money(values[0])} · latest ${money(values[values.length - 1])} · dashed line is the ${money(STARTING_CASH)} start</figcaption>
  </figure>`;
}

function archiveCurve(archive) {
  const points = archive?.curve?.points ?? [];
  if (points.length < 2) return '<p class="muted-inline">No closed-trade curve was produced for the mapped archive rule.</p>';
  const values = points.map(([, pnl]) => Number(pnl));
  const min = Math.min(...values, 0); const max = Math.max(...values, 0); const span = max - min || 1;
  const w = 720; const h = 150; const pad = 8;
  const coords = points.map(([, value], i) => `${(pad + (i / (points.length - 1)) * (w - 2 * pad)).toFixed(1)},${(h - pad - ((Number(value) - min) / span) * (h - 2 * pad)).toFixed(1)}`).join(' ');
  const zero = (h - pad - ((0 - min) / span) * (h - 2 * pad)).toFixed(1);
  return `<figure class="equity-chart archive-curve"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(archive.rule?.username ?? 'archive')} cumulative realized PnL curve"><line class="baseline" x1="0" y1="${zero}" x2="${w}" y2="${zero}"/><polyline class="${values.at(-1) >= 0 ? 'up' : 'down'}" points="${coords}" fill="none" stroke-width="2"/></svg><figcaption>${points.length} closed-trade point(s) · cumulative realized PnL ${money(values.at(-1))}; baseline is $0</figcaption></figure>`;
}

function renderArchiveDetail(page, base) {
  const panel = $('#strategy-archive');
  const state = $('#strategy-archive-state');
  if (!panel) return;
  const archive = page.archiveBacktest;
  const archiveBase = base.replace(/forward\/$/, '');
  if (!archive?.available) {
    if (state) { state.textContent = 'not available'; state.classList.add('blocked'); }
    panel.innerHTML = `<div class="empty-state"><h3>No archive backtest is committed for Season ${esc(page.season)}</h3><p>${esc(archive?.note ?? 'The collector has not attached archive results for this season.')}</p></div>`;
    return;
  }
  if (!archive.matched) {
    if (state) { state.textContent = 'unmatched rule'; state.classList.add('blocked'); }
    const catalog = (archive.catalog ?? []).map((row) => `<li><b>${esc(row.username)}</b> · ${esc(row.name)} · ${esc(row.trades ?? 0)} trade(s) · ${pct(row.returnPct)}</li>`).join('');
    panel.innerHTML = `<div class="notice notice-amber"><div class="notice-icon">!</div><div><strong>No archive rule is claimed as a match for ${esc(page.strategy.username)}.</strong><p>${esc(archive.note ?? 'A shared source, topic, or market is not evidence that two rules are the same.')}</p><p>Available archive rule IDs: ${esc((archive.catalog ?? []).map((row) => row.strategyId).join(', ') || 'none')}.</p></div></div><details><summary>Available official-candle archive rules (${archive.catalog?.length ?? 0})</summary><ul>${catalog || '<li>none</li>'}</ul></details><p class="micro-note"><span>Archive files</span><a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.leaderboard ?? 'backtest-archive/leaderboard.json')}" target="_blank" rel="noreferrer">leaderboard ↗</a> · <a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.trades ?? 'backtest-archive/trades.json')}" target="_blank" rel="noreferrer">trades ↗</a></p>`;
    return;
  }
  const metric = archive.metrics ?? {};
  if (state) { state.textContent = `matched ${archive.archiveStrategyId}`; state.classList.add('ok'); }
  const cards = [
    ['Mapped rule', `${archive.rule?.username ?? archive.archiveStrategyId}`, archive.mappingNote],
    ['Archive return', pct(metric.returnPct), `${money(metric.realizedPnl)} realized on $10,000`],
    ['Archive trades', String(metric.trades ?? archive.trades?.length ?? 0), `${metric.wins ?? 0}W · ${metric.losses ?? 0}L · ${metric.marketsTraded ?? 0} markets`],
    ['Archive costs', `${money(metric.feesPaid)} fees`, `${money(metric.slippagePaid)} slippage · ${metric.skippedNoLiquidity ?? 0} skipped for no depth`],
    ['Verified archive', `${archive.competition?.verifiedBars ?? '—'} bars`, `${archive.competition?.marketCount ?? '—'} markets · generated ${formatDate(archive.competition?.generatedAt)}`],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note ?? '')}</small></div>`).join('');
  const trades = (archive.trades ?? []).map((trade) => `<tr><td><small>${esc(trade.ticker)}</small><br><small>${esc(trade.series)} · ${esc(trade.period)} min</small></td><td>${esc(String(trade.side).toUpperCase())} · ${formatContracts(trade.contracts)}</td><td><b>${px(trade.entryPrice)}</b><br><small>${esc(formatDate(trade.entryAt))}</small></td><td><b>${px(trade.exitPrice)}</b><br><small>${esc(trade.exitType)} · ${esc(formatDate(trade.exitAt))}</small></td><td>${money(trade.feesTotal, 4)}<br>${money((trade.slippageEntry ?? 0) + (trade.slippageExit ?? 0), 4)} slippage</td><td class="${cls(trade.pnl)}">${money(trade.pnl)}</td><td><a class="text-link" href="${esc(trade.url)}" target="_blank" rel="noreferrer">official candle request ↗</a><br><small class="mono">${esc(shortHash(trade.sha256))}</small></td></tr>`).join('');
  const folds = (archive.walkForward ?? []).map((row) => `<tr><td>${esc(row.fold)}</td><td>${esc(row.trades)} · ${esc(row.wins)}W / ${esc(row.losses)}L</td><td class="${cls(row.realizedPnl)}">${money(row.realizedPnl)}</td><td class="${cls(row.returnPct)}">${pct(row.returnPct)}</td></tr>`).join('');
  panel.innerHTML = `<div class="metrics-grid">${cards}</div><div class="rule-grid"><div class="rule-card"><span>Matched archive identity</span><p><b>${esc(archive.archiveStrategyId)}</b> · ${esc(archive.rule?.name)}<br>${esc(archive.rule?.text)}<br>${esc(archive.rule?.explanation ?? '')}</p></div><div class="rule-card"><span>Mapping boundary</span><p>${esc(archive.mappingNote)} This is a family attachment, not a claim that forward and archive trades are the same trades.</p></div></div>${archiveCurve(archive)}<div class="panel"><div class="panel-header"><div><h3>Archive trade details</h3><p>official archived bars only; entry and exit timestamps remain in the committed replay</p></div><span class="source-badge">${archive.trades?.length ?? 0} trade(s)</span></div><div class="table-shell"><table><thead><tr><th>Market</th><th>Side · size</th><th>Entry</th><th>Exit</th><th>Fees · slippage</th><th>PnL</th><th>Evidence</th></tr></thead><tbody>${trades || '<tr><td colspan="7" class="empty-cell">No archive trades.</td></tr>'}</tbody></table></div></div><div class="panel"><div class="panel-header"><div><h3>Walk-forward context</h3><p>same continuous replay, reset per fold; not an independent train/test claim</p></div><span class="source-badge">${archive.walkForward?.length ?? 0} fold row(s)</span></div><div class="table-shell"><table><thead><tr><th>Fold</th><th>Trades</th><th>Realized</th><th>Return</th></tr></thead><tbody>${folds || '<tr><td colspan="4" class="empty-cell">No fold row for this rule.</td></tr>'}</tbody></table></div><p class="micro-note"><span>Caveat</span>${esc(archive.walkForwardCaveat ?? '')}</p></div><p class="micro-note"><span>Archive evidence</span><a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.competition ?? 'backtest-archive/competition.json')}" target="_blank" rel="noreferrer">competition ↗</a> · <a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.trades ?? 'backtest-archive/trades.json')}" target="_blank" rel="noreferrer">all archive trades ↗</a> · <a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.curves ?? 'backtest-archive/curves.json')}" target="_blank" rel="noreferrer">curves ↗</a> · <a class="text-link" href="${REPO_TREE}${archiveBase}${esc(archive.files?.walkForward ?? 'backtest-archive/walkforward.json')}" target="_blank" rel="noreferrer">walk-forward ↗</a></p>`;
}

function ledgerLineLink(ref, base, label = 'ledger line') {
  if (!ref || !ref.ledgerFile || !Number.isInteger(Number(ref.ledgerLine))) return '';
  return `<a class="text-link ledger-line-link" href="${REPO_TREE}${base}${esc(ref.ledgerFile)}#L${Number(ref.ledgerLine)}" target="_blank" rel="noreferrer">${esc(label)} #L${Number(ref.ledgerLine)} ↗</a>`;
}

function evidenceLink(evidence, base) {
  if (!evidence) return '—';
  const storedFile = evidence.compacted && evidence.compressedFile ? evidence.compressedFile : evidence.file;
  const file = storedFile ? `<a class="text-link" href="${REPO_TREE}${base}${esc(storedFile)}" target="_blank" rel="noreferrer">${esc(evidence.compacted ? `${evidence.file} (gz)` : String(evidence.file).split('/').pop())} ↗</a>` : '';
  const url = evidence.url ? `<a class="text-link" href="${esc(evidence.url)}" target="_blank" rel="noreferrer">official ↗</a>` : '';
  const compactNote = evidence.compacted ? ` · original bytes in ${esc(evidence.compressedFile)}; hash preserved in COMPRESSED.json` : '';
  return `${file}${file && url ? ' · ' : ''}${url}<br><small class="mono">sha256 ${esc(shortHash(evidence.sha256))} · ${esc(formatDate(evidence.retrievedAt))}${compactNote}</small>`;
}

const INTENT_LABEL = {
  filled: ['filled', ''],
  queued: ['queued (next in line)', 'none'],
  not_confirmed_on_book: ['not confirmed on a fresh book', 'blocked'],
  no_liquidity_or_cash: ['no executable depth / cash', 'blocked'],
  no_book: ['book unavailable', 'blocked'],
  skipped_position_cap: ['position cap reached', 'none'],
  skipped_new_fill_cap: ['new-fill cap reached this cycle', 'none'],
  proposed: ['proposed', 'none'],
};

function renderMissing(id, message, board) {
  const state = $('#strategy-state');
  if (state) { state.textContent = message; state.classList.add('blocked'); }
  $('#strategy-title').textContent = id ? `Strategy “${id}” has no committed page yet` : 'No strategy selected';
  const panel = $('#strategy-evidence');
  const rows = (board ?? []).map((row) => `<a class="file-chip" href="strategy.html?id=${esc(row.strategyId)}">${esc(row.username)} ↗</a>`).join(' ');
  if (panel) panel.innerHTML = `<p class="muted-inline">${esc(message)}</p>${rows ? `<p><b>Committed strategy pages:</b></p><p>${rows}</p>` : ''}`;
}

async function main() {
  const id = new URLSearchParams(window.location.search).get('id');
  await loadSeasonsIndex();
  const base = `data/season-${seasonMeta.activeSeason}/forward/`;
  if (!id) {
    let board = null;
    try { board = (await fetchJson(`${base}leaderboard.json`)).rows; } catch { /* nothing committed */ }
    renderMissing('', 'Add ?id=<strategyId> to the URL. Every row on the season board links here.', board);
    return;
  }
  let page;
  try { page = await fetchJson(`${base}strategies/${id}.json`); }
  catch (error) {
    let board = null;
    try { board = (await fetchJson(`${base}leaderboard.json`)).rows; } catch { /* nothing committed */ }
    renderMissing(id, `No committed strategy page for “${id}” in Season ${seasonMeta.activeSeason} (${error.message}). The collector writes these files at the end of every cycle.`, board);
    return;
  }

  const s = page.strategy; const a = page.account;
  $('#strategy-avatar').textContent = String(s.username).slice(0, 2).toUpperCase();
  $('#strategy-title').textContent = s.username;
  $('#strategy-subtitle').innerHTML = `${esc(s.name)} · Season ${esc(page.season)} · cycle ${esc(page.cycle ?? '—')} · generated ${esc(formatDate(page.generatedAt))}`;
  const state = $('#strategy-state');
  state.textContent = `${esc(page.eventCount ?? 0)} ledger event(s) · ${esc((page.equity ?? []).length)} committed cycle point(s)`;
  state.classList.add('ok');

  $('#strategy-rule').innerHTML = [
    ['Entry rule', s.rule],
    ['Why it should work', s.why],
    ['Provenance', `${s.source?.label ?? ''} — ${s.source?.kind ?? 'source'}`],
    ['Group', s.group],
  ].map(([label, text]) => `<div class="rule-card"><span>${esc(label)}</span><p>${esc(text ?? '')}</p></div>`).join('')
    + `<div class="rule-card"><span>Source link</span><p><a class="text-link" href="${esc(s.source?.url ?? '#')}" target="_blank" rel="noreferrer">${esc(s.source?.url ?? '—')} ↗</a></p></div>`;

  const requested = a.requestedContracts ?? 0;
  const filled = a.filledContracts ?? 0;
  const fillRate = requested ? ((filled / requested) * 100).toFixed(1) : '—';
  $('#strategy-metrics').innerHTML = [
    ['Return', pct(page.returnPct), `on ${money(STARTING_CASH)} of paper capital`],
    ['Equity (mark-to-bid)', money(a.lastEquity), `marked ${esc(formatDate(a.lastMarkedAt))}`],
    ['Equity (last trade)', money(a.lastLiquidationEquity), pct(page.liquidationReturnPct)],
    ['Realized PnL', money(a.realizedPnl), `${esc(a.settlements ?? 0)} official settlement(s)`],
    ['Fees paid', money(a.feesPaid, 4), 'quadratic taker fees on every fill'],
    ['Slippage paid', money(a.slippagePaid, 4), 'touch → execution VWAP'],
    ['Fills / exits', `${esc(a.fills ?? 0)} / ${esc(a.exits ?? 0)}`, `${esc(a.settlements ?? 0)} settled · ${esc(a.wins ?? 0)}W ${esc(a.losses ?? 0)}L`],
    ['Contracts filled', `${formatContracts(filled)}`, `requested ${formatContracts(requested)} · ${fillRate}% filled`],
    ['Unfilled remainder', formatContracts(a.unfilledContracts), 'depth the book did not have'],
    ['Best trade', money(a.bestTradePnl), 'largest single closed PnL'],
    ['Worst trade', money(a.worstTradePnl), 'smallest single closed PnL'],
    ['Cash', money(a.cash), 'available for the next rule signal'],
  ].map(([label, value, note]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');

  $('#strategy-curve').innerHTML = curve(page.equity ?? []);
  renderArchiveDetail(page, base);

  const analysis = $('#strategy-analysis');
  analysis.innerHTML = Array.isArray(page.analysis)
    ? page.analysis.map((p) => `<p>${esc(p)}</p>`).join('')
    : `<p>${esc(page.analysis ?? 'No analysis committed yet.')}</p>`;

  const posHead = $('#strategy-positions-table thead');
  const posBody = $('#strategy-positions-table tbody');
  const positions = page.positions ?? [];
  posHead.innerHTML = '<tr><th>Contract</th><th>Side · size</th><th>Entry</th><th>Fee · slippage</th><th>Mark</th><th>Unrealized</th><th>Closes</th><th>Evidence</th></tr>';
  posBody.innerHTML = positions.length ? positions.map((p) => {
    const markValue = p.lastMark?.markValue ?? null;
    const unrealized = markValue === null ? null : markValue - p.entryNotional - p.entryFee;
    return `<tr><td><b>${esc(p.title)}</b><br><small>${esc(p.subtitle ? `${p.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(p.ticker)}" target="_blank" rel="noreferrer">${esc(p.ticker)} ↗</a></small></td>
      <td><b>${esc(String(p.side).toUpperCase())}</b><br><small>${formatContracts(p.contracts)} filled${p.unfilledContracts ? ` · ${formatContracts(p.unfilledContracts)} unfilled` : ''}</small></td>
      <td><b>${px(p.entryPrice)}</b><br><small>touch ${px(p.entryTouch)} · limit ${px(p.limitPrice)} · ${esc(p.levelsConsumed)} level(s)</small></td>
      <td><small>${money(p.entryFee, 4)} · ${money(p.entrySlippage, 4)}</small></td>
      <td><small>${p.lastMark?.markPrice === null || p.lastMark?.markPrice === undefined ? 'no bid / no trade' : `${px(p.lastMark.markPrice)} (${esc(p.lastMark.bid !== null && p.lastMark.bid !== undefined ? 'bid' : 'last')})`}<br>${esc(formatDate(p.lastMark?.at))}</small></td>
      <td class="${cls(unrealized)}">${unrealized === null ? '—' : money(unrealized)}</td>
      <td><small>${esc(formatDate(p.closeTime))}</small></td>
      <td><small>${ledgerLineLink({ ledgerFile: p.ledger?.fillFile, ledgerLine: p.ledger?.fillLine }, base, 'fill')} ${ledgerLineLink({ ledgerFile: p.ledger?.closeFile, ledgerLine: p.ledger?.closeLine }, base, 'close')}</small><br><small>${evidenceLink(p.evidence, base)}</small></td></tr>`;
  }).join('') : '<tr><td colspan="8" class="empty-cell">No open position. A position appears only after the rule fired and a fresh official order book confirmed it.</td></tr>';

  const evHead = $('#strategy-events-table thead');
  const evBody = $('#strategy-events-table tbody');
  const events = page.events ?? [];
  $('#strategy-events-count').textContent = `${events.length} shown of ${page.eventCount ?? events.length}`;
  evHead.innerHTML = '<tr><th>When</th><th>Event</th><th>Contract</th><th>Side · size</th><th>Entry</th><th>Exit</th><th>Fees · slippage</th><th>PnL</th><th>Evidence</th></tr>';
  evBody.innerHTML = events.length ? events.map((e) => {
    const kind = e.kind === 'fill' ? 'fill' : e.kind === 'exit' ? 'bid exit' : `settled ${String(e.result ?? '').toUpperCase()}`;
    const exit = e.kind === 'fill' ? '<span class="evidence-pill none">open</span>' : `<b>${px(e.exitPrice)}</b><br><small>${esc(formatDate(e.exitAt))}${e.exitReason ? ` · ${esc(e.exitReason)}` : ''}</small>`;
    const fees = e.kind === 'fill' ? `${money(e.entryFee, 4)} · ${money(e.slippageEntry, 4)}` : `${money(e.feesTotal, 4)} · ${money((e.slippageEntry ?? 0) + (e.slippageExit ?? 0), 4)}`;
    return `<tr><td><small>${esc(formatDate(e.at))}<br>${esc(e.cycle)}</small></td>
      <td><span class="evidence-pill ${e.kind === 'fill' ? '' : e.pnl > 0 ? 'win' : 'loss'}">${esc(kind)}</span></td>
      <td><b>${esc(e.title)}</b><br><small>${esc(e.subtitle ? `${e.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(e.ticker)}" target="_blank" rel="noreferrer">${esc(e.ticker)} ↗</a></small></td>
      <td><b>${esc(String(e.side).toUpperCase())}</b><br><small>${formatContracts(e.contracts)}${e.unfilledContracts ? ` · ${formatContracts(e.unfilledContracts)} unfilled` : ''}</small></td>
      <td><b>${px(e.entryPrice)}</b><br><small>${e.kind === 'fill' ? `touch ${px(e.entryTouch)} · limit ${px(e.limitPrice)} · ${esc(e.levelsConsumed)} lvl` : esc(formatDate(e.entryAt))}</small></td>
      <td>${exit}</td><td><small>${fees}</small></td>
      <td class="${cls(e.pnl)}">${e.kind === 'fill' ? '—' : money(e.pnl)}</td>
      <td><small>${ledgerLineLink(e, base, e.kind === 'fill' ? 'fill' : 'event')}</small><br><small>${evidenceLink(e.evidence, base)}</small>${e.reason ? `<br><small class="reason" title="${esc(e.reason)}">${esc(e.reason)}</small>` : ''}</td></tr>`;
  }).join('') : '<tr><td colspan="9" class="empty-cell">No fill, exit or settlement recorded for this strategy yet.</td></tr>';

  const inHead = $('#strategy-intents-table thead');
  const inBody = $('#strategy-intents-table tbody');
  const intents = page.intents ?? [];
  inHead.innerHTML = '<tr><th>Cycle</th><th>Contract</th><th>Side · quote</th><th>Rule reason</th><th>Book check</th><th>Status</th></tr>';
  inBody.innerHTML = intents.length ? intents.map((i) => {
    const [text, pill] = INTENT_LABEL[i.status] ?? [i.status, 'none'];
    const book = i.bookQuotes ? `YES ${px(i.bookQuotes.yes_bid)} / ${px(i.bookQuotes.yes_ask)} · NO ${px(i.bookQuotes.no_bid)} / ${px(i.bookQuotes.no_ask)}<br>${esc(formatDate(i.bookAt))}` : 'not fetched';
    return `<tr><td><small>${esc(formatDate(i.at))}<br>${esc(i.cycle)}<br>${ledgerLineLink(i, base, 'intent')}</small></td>
      <td><b>${esc(i.title)}</b><br><small>${esc(i.subtitle ? `${i.subtitle} · ` : '')}<a class="text-link" href="${KALSHI_MARKET_URL(i.ticker)}" target="_blank" rel="noreferrer">${esc(i.ticker)} ↗</a> · closes ${esc(formatDate(i.closeTime))}</small></td>
      <td><b>${esc(String(i.side).toUpperCase())}</b> @ ${px(i.quotePrice)}<br><small>limit ${px(i.limit)} · vol ${formatContracts(i.volume)}</small></td>
      <td><small>${esc(i.reason)}</small></td><td><small>${book}</small></td>
      <td><span class="evidence-pill ${pill}">${esc(text)}</span>${i.positionId ? `<br><small>${formatContracts(i.contracts)} @ ${px(i.fillPrice)}</small>` : ''}</td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty-cell">No intent recorded for this strategy in the committed window.</td></tr>';

  const files = Object.entries(page.ledger ?? {}).map(([label, path]) => `<a class="file-chip" href="${REPO_TREE}${base}${esc(path)}" target="_blank" rel="noreferrer">${esc(label)}: ${esc(path)} ↗</a>`).join(' ');
  const evidenceFiles = (page.evidenceFiles ?? []).map((f) => `<a class="file-chip" href="${REPO_TREE}${base}${esc(f)}" target="_blank" rel="noreferrer">${esc(String(f).split('/').pop())} ↗</a>`).join(' ');
  $('#strategy-evidence').innerHTML = `<p><b>Ledger</b></p><p>${files || '—'}</p>
    <p><b>Captured order books used by this strategy</b></p><p>${evidenceFiles || 'none captured yet'}</p>
    <p><b>This page's own data file</b></p><p><a class="file-chip" href="${REPO_TREE}${base}strategies/${esc(id)}.json" target="_blank" rel="noreferrer">strategies/${esc(id)}.json ↗</a></p>`;
}

main().catch((error) => {
  renderMissing(new URLSearchParams(window.location.search).get('id'), `Could not load this page: ${error.message}`, null);
});
