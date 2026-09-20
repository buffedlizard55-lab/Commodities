import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const SEASON_DIR = join(new URL('..', import.meta.url).pathname, 'data', 'season-2026');
const readJson = (name) => JSON.parse(readFileSync(join(SEASON_DIR, name), 'utf8'));

const files = ['competition.json', 'leaderboard.json', 'trades.json', 'intents.json', 'explanations.json', 'market-records.json', 'SHA256SUMS.txt', 'MANIFEST.md'];

test('committed season memory exists with every documented file', () => {
  for (const name of files) {
    assert.ok(existsSync(join(SEASON_DIR, name)), `missing committed file: ${name}`);
  }
});

test('competition metadata pins the verified tape and the fee model', () => {
  const competition = readJson('competition.json');
  assert.equal(competition.season, '2026');
  assert.equal(competition.startingCashPerAccount, 10000);
  assert.ok(competition.verifiedBars >= 100, 'expected a substantial verified bar count');
  assert.equal(competition.markets['KXCPI-26AUG-T0.8'].result, 'no');
  assert.equal(competition.markets['KXNFLGAME-26SEP17DETBUF-BUF'].result, 'yes');
  assert.match(competition.takerFeeModel, /0\.07/);
});

test('every backtest trade is internally consistent (prices, fees, PnL, no look-ahead)', () => {
  const trades = readJson('trades.json');
  assert.ok(trades.length > 0);
  for (const trade of trades) {
    assert.ok(trade.entryPrice > 0 && trade.entryPrice <= 1, `entry price in (0,1]: ${trade.id}`);
    assert.ok(trade.exitPrice >= 0 && trade.exitPrice <= 1, `exit price in [0,1]: ${trade.id}`);
    assert.ok(trade.contracts > 0, 'positive contract count');
    assert.ok(trade.entryFee >= 0 && trade.exitFee >= 0, 'fees are never negative');
    const expected = trade.exitPrice * trade.contracts - trade.exitFee - (trade.entryPrice * trade.contracts + trade.entryFee);
    assert.ok(Math.abs(trade.pnl - expected) < 1e-4, `pnl matches entry/exit math: ${trade.id}`);
    assert.ok(trade.entryAt < trade.exitAt, `entry strictly before exit: ${trade.id}`);
    if (trade.exitType === 'settlement') {
      assert.ok(trade.exitPrice === 0 || trade.exitPrice === 1, 'settlement exits are binary');
    }
  }
});

test('leaderboard is recomputable from the trade ledger and sorted', () => {
  const trades = readJson('trades.json');
  const leaderboard = readJson('leaderboard.json');
  const byStrategy = new Map();
  for (const trade of trades) {
    const entry = byStrategy.get(trade.strategyId) ?? { pnl: 0, count: 0 };
    entry.pnl += trade.pnl;
    entry.count += 1;
    byStrategy.set(trade.strategyId, entry);
  }
  assert.equal(leaderboard.length, byStrategy.size);
  for (const row of leaderboard) {
    const entry = byStrategy.get(row.strategyId);
    assert.ok(entry, `leaderboard row has trades: ${row.strategyId}`);
    assert.ok(Math.abs(row.realizedPnl - entry.pnl) < 0.01, `realizedPnl matches ledger: ${row.strategyId}`);
    assert.ok(Math.abs(row.equity - (10000 + entry.pnl)) < 0.01, `equity = starting cash + pnl: ${row.strategyId}`);
    assert.equal(row.trades, entry.count);
  }
  for (let i = 1; i < leaderboard.length; i += 1) {
    assert.ok(leaderboard[i - 1].returnPct >= leaderboard[i].returnPct, 'leaderboard sorted by return');
  }
});

test('upcoming intents are proposals only, priced against the committed book', () => {
  const intents = readJson('intents.json');
  assert.ok(intents.length > 0);
  for (const intent of intents) {
    assert.equal(intent.status, 'proposed', 'intents never claim a fill');
    assert.ok(intent.proposedPrice > 0 && intent.proposedPrice < 1);
    assert.ok(intent.maxDisplayDepth > 0, 'depth recorded from the verified book');
    assert.ok(Array.isArray(intent.evidence) && intent.evidence.length > 0);
  }
});

test('every strategy has a committed explanation of its outcome', () => {
  const explanations = readJson('explanations.json');
  const leaderboard = readJson('leaderboard.json');
  assert.equal(explanations.length, leaderboard.length);
  for (const item of explanations) {
    assert.ok(item.explanation.length > 40, 'explanation is substantive');
  }
});

test('SHA-256 manifest binds at least the raw data files', () => {
  const hashes = readFileSync(join(SEASON_DIR, 'SHA256SUMS.txt'), 'utf8').trim().split('\n');
  const bound = new Set(hashes.map((line) => line.trim().split(/\s+/).at(-1)));
  for (const name of [
    'candles-KXCPI-26AUG-T0.8-daily.csv',
    'candles-KXFED-26SEP-T4.75-daily.csv',
    'candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv',
    'orderbook-KXBTC-26SEP2017-T90749.99.csv',
    'market-records.json',
    'cutoff.json',
  ]) {
    assert.ok(bound.has(name), `manifest binds ${name}`);
  }
});
