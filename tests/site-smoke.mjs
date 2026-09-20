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
  check('forward board rows', count('#forward-leaderboard tbody tr.board-row') === 20, String(count('#forward-leaderboard tbody tr.board-row')));
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
check('no runtime errors', errors.length === 0, errors.join(' | ').slice(0, 400));
const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
