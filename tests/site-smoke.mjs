// Renders index.html + src/app.js under jsdom against the committed data files and fails on any
// console/runtime error.  Optional: `npm i --no-save jsdom` first (the repo ships no node_modules).
//   node tests/site-smoke.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let JSDOM;
try { ({ JSDOM } = await import('jsdom')); } catch { console.log('SKIP site smoke test: jsdom not installed (npm i --no-save jsdom)'); process.exit(0); }

const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'https://example.test/', pretendToBeVisual: true });
const { window } = dom;
globalThis.document = window.document; globalThis.window = window; globalThis.localStorage = window.localStorage;
globalThis.HTMLElement = window.HTMLElement; globalThis.Node = window.Node;
const errors = [];
window.addEventListener('error', (e) => errors.push(`window error: ${e.message}`));
process.on('unhandledRejection', (r) => errors.push(`unhandled: ${r?.stack ?? r}`));
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith('http')) return new Response(JSON.stringify({ markets: [], cursor: '' }), { status: 200, headers: { 'content-type': 'application/json' } });
  const file = path.join(ROOT, u.split('?')[0]);
  if (!fs.existsSync(file)) return new Response('not found', { status: 404 });
  return new Response(fs.readFileSync(file), { status: 200, headers: { 'content-type': 'application/json' } });
};
globalThis.setInterval = () => 0; window.setInterval = () => 0;
try { await import(path.join(ROOT, 'src/app.js')); } catch (e) { errors.push(`import error: ${e.stack}`); }
await new Promise((r) => setTimeout(r, 1500));

const count = (sel) => document.querySelectorAll(sel).length;
const text = (sel) => (document.querySelector(sel)?.textContent ?? '').replace(/\s+/g, ' ').trim();
const checks = [];
const check = (name, ok, detail = '') => { checks.push([name, ok, detail]); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name} ${detail}`); };
const forwardPublished = fs.existsSync(path.join(ROOT, 'data/season-2026/forward/leaderboard.json'));
if (forwardPublished) {
  // the committed board has one row per funded account; 22 personas exist and 2 join on the next runner cycle
  check('forward board rows', count('#forward-leaderboard tbody tr.board-row') >= 20, String(count('#forward-leaderboard tbody tr.board-row')));
  check('board rows link to a strategy page', document.querySelectorAll('#forward-leaderboard tbody a[href^="strategy.html?id="]').length >= 20,
    String(document.querySelectorAll('#forward-leaderboard tbody a[href^="strategy.html?id="]').length));
  check('forward metrics rendered', /Desk cycles\s*\d+/.test(text('#forward-metrics')), text('#forward-metrics').slice(0, 60));
  check('forward policies rendered', text('#forward-policies').includes('immediate-or-cancel'));
  for (const tab of ['positions', 'events', 'intents', 'cycles', 'signals']) {
    document.querySelector(`[data-forward-tab="${tab}"]`).click();
    await new Promise((r) => setTimeout(r, 30));
    check(`forward tab ${tab} renders`, count('#forward-ledger-table tbody tr') >= 1 && text('#forward-ledger-table thead').length > 0);
  }
} else {
  check('forward board empty-state', text('#forward-leaderboard tbody').length > 0);
}
check('backtest board rows', count('#backtest-leaderboard tbody tr') === 5, String(count('#backtest-leaderboard tbody tr')));
check('backtest trades rows', count('#backtest-trades tbody tr') === 9, String(count('#backtest-trades tbody tr')));
check('backtest intents rows', count('#backtest-intents tbody tr') === 3);
check('backtest explanations', count('#backtest-explanations article') === 5);
check('backtest facts', text('#bt-bars').startsWith('130'), text('#bt-bars'));
check('strategy cards', count('#strategy-grid article') >= 20, String(count('#strategy-grid article')));
check('mastersite map rows', count('#mastersite-map tbody tr') === 13);
check('sources rendered', count('#source-list a') >= 50 && count('#irregularity-list li') >= 20, `${count('#source-list a')} / ${count('#irregularity-list li')}`);
for (const f of ['forward', 'live-book', 'backtest', 'blocked', 'all']) { document.querySelector(`[data-filter="${f}"]`).click(); await new Promise((r) => setTimeout(r, 20)); }
check('filters keep cards', count('#strategy-grid article') >= 20);
// --- new committed sections: today's roll-up, archive backtest, execution realism, seasons ---
if (fs.existsSync(path.join(ROOT, 'data/season-2026/forward/summary/today.json'))) {
  check('daily summary rendered', /Equity/.test(text('#daily-summary')) && count('#daily-summary .metric-card') >= 6, text('#daily-summary').slice(0, 70));
} else {
  check('daily summary empty-state', text('#daily-summary').length > 0);
}
if (fs.existsSync(path.join(ROOT, 'data/season-2026/backtest-archive/competition.json'))) {
  const archive = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/season-2026/backtest-archive/competition.json'), 'utf8'));
  const boardTable = document.querySelector('#archive-backtest .table-shell table tbody');
  const boardRows = boardTable ? boardTable.querySelectorAll('tr').length : 0;
  const archiveBoard = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/season-2026/backtest-archive/leaderboard.json'), 'utf8'));
  const archiveRules = Array.isArray(archiveBoard) ? archiveBoard : (archiveBoard.rows ?? archiveBoard.ranked ?? []);
  check('archive backtest board', boardRows === archiveRules.length, `${boardRows} rows vs ${archive.marketCount} markets / ${archiveRules.length} rule sets`);
  const walk = fs.existsSync(path.join(ROOT, 'data/season-2026/backtest-archive/walkforward.json')) ? JSON.parse(fs.readFileSync(path.join(ROOT, 'data/season-2026/backtest-archive/walkforward.json'), 'utf8')) : null;
  check('archive walk-forward fold rows', !walk || count('#archive-backtest tbody tr') - boardRows === (walk.rows ?? []).length, String(count('#archive-backtest tbody tr') - boardRows));
  const archiveCards = [...document.querySelectorAll('#archive-backtest .metric-card')].map((c) => `${c.querySelector('span').textContent}=${c.querySelector('strong').textContent}`);
  check('archive backtest facts', archiveCards.some((c) => c.startsWith(`Archived markets=${archive.marketCount}`)) && archiveCards.some((c) => c.startsWith(`Verified bars=${archive.verifiedBars}`)), archiveCards.slice(0, 3).join(' '));
  check('archive backtest explanations column', text('#archive-backtest tbody tr td:last-child').length > 40, text('#archive-backtest tbody tr td:last-child').slice(0, 60));
  check('archive equity curves drawn', count('#archive-backtest .curve-legend-item') === Object.keys(JSON.parse(fs.readFileSync(path.join(ROOT, 'data/season-2026/backtest-archive/curves.json'), 'utf8')).strategies ?? {}).length, String(count('#archive-backtest .curve-legend-item')));
} else {
  check('archive backtest empty-state', text('#archive-backtest').length > 0);
}
check('execution realism section renders', text('#execution-realism').length > 0, text('#execution-realism').slice(0, 60));
check('execution realism shows the committed comparison', text('#execution-realism').includes('fills compared') && !text('#execution-realism').includes('Not compared yet'), text('#execution-state').slice(0, 40));
check('season health panel renders', /audit (PASS|FAIL)/.test(text('#health-state')) && text('#season-health-panel').includes('Cron slots executed'), `${text('#health-state').slice(0, 30)} | ${text('#season-health-panel').slice(0, 40)}`);
{
  const totalBoardRows = count('#forward-leaderboard tbody tr.board-row');
  const bf = document.querySelector('#board-filter');
  bf.value = 'longshot'; bf.dispatchEvent(new window.Event('input'));
  const shown = [...document.querySelectorAll('#forward-leaderboard tbody tr.board-row')].filter((tr) => !tr.hidden).length;
  check('board filter narrows rows', shown >= 1 && shown < totalBoardRows && document.querySelector('#board-filter-count').textContent.includes('shown'), `${shown}/${totalBoardRows}`);
  bf.value = ''; bf.dispatchEvent(new window.Event('input'));
  check('board filter reset', [...document.querySelectorAll('#forward-leaderboard tbody tr.board-row')].every((tr) => !tr.hidden));
  document.querySelector('[data-forward-tab=\"positions\"]').click();
  await new Promise((r) => setTimeout(r, 30));
  const lf = document.querySelector('#ledger-filter');
  lf.value = 'zzz-no-such-ticker'; lf.dispatchEvent(new window.Event('input'));
  const ledShown = [...document.querySelectorAll('#forward-ledger-table tbody tr')].filter((tr) => !tr.hidden).length;
  check('ledger filter hides non-matching rows', ledShown === 0, String(ledShown));
  lf.value = ''; lf.dispatchEvent(new window.Event('input'));
  check('ledger filter reset', [...document.querySelectorAll('#forward-ledger-table tbody tr')].every((tr) => !tr.hidden));
}
check('season index rendered', count('#season-list .season-chip') >= 1 || text('#season-list').length > 0, text('#season-list').slice(0, 60));
check('no runtime errors', errors.length === 0, errors.join(' | ').slice(0, 400));
// --- per-strategy page: renders one committed strategy file end to end ---
const strategyFiles = fs.existsSync(path.join(ROOT, 'data/season-2026/forward/strategies'))
  ? fs.readdirSync(path.join(ROOT, 'data/season-2026/forward/strategies')).filter((f) => f.endsWith('.json'))
  : [];
if (strategyFiles.length) {
  const id = strategyFiles[0].replace(/\.json$/, '');
  const expectedUsername = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/season-2026/forward/strategies', strategyFiles[0]), 'utf8')).strategy.username;
  const strategyHtml = fs.readFileSync(path.join(ROOT, 'strategy.html'), 'utf8');
  const strategyDom = new JSDOM(strategyHtml, { url: `https://example.test/strategy.html?id=${id}`, pretendToBeVisual: true });
  globalThis.document = strategyDom.window.document; globalThis.window = strategyDom.window;
  const strategyErrors = [];
  strategyDom.window.addEventListener('error', (e) => strategyErrors.push(`strategy window error: ${e.message}`));
  try { await import(`${path.join(ROOT, 'src/strategy.js')}?id=${id}`); } catch (e) { strategyErrors.push(`strategy import error: ${e.stack}`); }
  await new Promise((r) => setTimeout(r, 1200));
  const sdoc = strategyDom.window.document;
  const scount = (sel) => sdoc.querySelectorAll(sel).length;
  const stext = (sel) => (sdoc.querySelector(sel)?.textContent ?? '').replace(/\s+/g, ' ').trim();
  // strategy files are named by strategy id (a slug); the page shows the persona username
  check('strategy page identity', stext('#strategy-title') === expectedUsername, `${stext('#strategy-title')} vs ${expectedUsername}`);
  check('strategy page rule cards', scount('#strategy-rule .rule-card') >= 4, String(scount('#strategy-rule .rule-card')));
  check('strategy page metrics', scount('#strategy-metrics .metric-card') >= 10, String(scount('#strategy-metrics .metric-card')));
  check('strategy page analysis', stext('#strategy-analysis').length > 20, stext('#strategy-analysis').slice(0, 60));
  check('strategy page tables have headers', scount('#strategy-events-table thead th') === 9 && scount('#strategy-positions-table thead th') === 8 && scount('#strategy-intents-table thead th') === 6,
    `${scount('#strategy-events-table thead th')}/${scount('#strategy-positions-table thead th')}/${scount('#strategy-intents-table thead th')}`);
  check('strategy page evidence links', scount('#strategy-evidence a') >= 3, String(scount('#strategy-evidence a')));
  check('strategy page no runtime errors', strategyErrors.length === 0, strategyErrors.join(' | ').slice(0, 300));
} else {
  check('strategy pages committed', false, 'data/season-2026/forward/strategies/*.json missing — run scripts/render_pages.py');
}

const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
