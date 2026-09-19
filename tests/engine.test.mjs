import test from 'node:test';
import assert from 'node:assert/strict';
import {
  affordableContracts,
  createPaperTrade,
  executeAgainstBook,
  normalizeMarket,
  normalizeOrderBook,
  standardQuadraticFee,
  closePaperTrade,
  settlePaperTrade,
} from '../src/engine.js';

const strategy = { id: 'book-edge', username: 'BookRocket', name: 'Book Edge Sweep' };
const market = normalizeMarket({
  ticker: 'KXTEST-YES',
  event_ticker: 'KXTEST',
  title: 'Test contract',
  status: 'open',
  yes_bid_dollars: '0.40',
  no_bid_dollars: '0.55',
  close_time: '2026-12-31T00:00:00Z',
});
const rawBook = { orderbook_fp: { yes_dollars: [['0.4000', '3.00'], ['0.3900', '9.00']], no_dollars: [['0.5500', '2.00'], ['0.5400', '8.00']] } };
const book = normalizeOrderBook(rawBook);

 test('normalizes reciprocal asks from opposing bids and seconds timestamps', () => {
  const secondsMarket = normalizeMarket({ ticker: 'KXTIME', status: 'open', close_ts: 1798675200 });
  assert.equal(secondsMarket.closeTs, 1798675200000);
  assert.ok(Math.abs(market.yesAsk - 0.45) < 1e-9);
  assert.ok(Math.abs(market.noAsk - 0.6) < 1e-9);
  assert.equal(market.yesAskDerived, true);
  assert.equal(market.noAskDerived, true);
});

test('consumes YES ask from NO bids in best-price order', () => {
  const result = executeAgainstBook(book, 'yes', 'buy', 3);
  assert.equal(result.filledContracts, 3);
  assert.equal(result.fills.length, 2);
  assert.ok(Math.abs(result.fills[0].price - 0.45) < 1e-9);
  assert.equal(result.fills[0].quantity, 2);
  assert.ok(Math.abs(result.fills[1].price - 0.46) < 1e-9);
  assert.ok(Math.abs(result.notional - 1.36) < 1e-9);
  assert.ok(result.slippage > 0);
});

test('does not invent liquidity when the request is larger than the book', () => {
  const result = executeAgainstBook(book, 'no', 'buy', 20);
  assert.equal(result.filledContracts, 12);
  assert.equal(result.remainingContracts, 8);
});

test('uses the published standard quadratic fee shape', () => {
  assert.equal(standardQuadraticFee(0.5, 100), 1.75);
  assert.equal(standardQuadraticFee(0.5, 1), 0.0175);
});

test('affordability includes the modeled fee', () => {
  const contracts = affordableContracts(10, 0.5);
  assert.equal(contracts, 19);
  assert.ok(contracts * 0.5 + standardQuadraticFee(0.5, contracts) <= 10);
  assert.ok((contracts + 1) * 0.5 + standardQuadraticFee(0.5, contracts + 1) > 10);
});

test('official binary result settles an open paper trade with a verified date', () => {
  const trade = createPaperTrade({ strategy, market, book, side: 'yes', requestedContracts: 3, now: new Date('2026-09-19T00:00:00Z') });
  const settled = settlePaperTrade(trade, normalizeMarket({ ...market.raw, result: 'yes', status: 'settled', settlement_ts: 1798675200 }));
  assert.equal(settled.status, 'settled');
  assert.equal(settled.exitPrice, 1);
  assert.equal(settled.exitAt, '2026-12-31T00:00:00.000Z');
  assert.equal(settled.exitFee, 0);
  assert.ok(Number.isFinite(settled.pnl));
});

test('paper fill records verified entry evidence and closes only with full liquidity', () => {
  const trade = createPaperTrade({ strategy, market, book, side: 'yes', requestedContracts: 3, now: new Date('2026-09-19T00:00:00Z') });
  assert.ok(trade);
  assert.equal(trade.contracts, 3);
  assert.ok(Math.abs(trade.entryPrice - (1.36 / 3)) < 1e-9);
  assert.equal(trade.source.endpoint.includes('/orderbook'), true);
  const closed = closePaperTrade(trade, market, book, new Date('2026-09-19T01:00:00Z'));
  assert.equal(closed.status, 'closed');
  assert.ok(Number.isFinite(closed.pnl));
  const blocked = closePaperTrade({ ...trade, contracts: 999 }, market, book);
  assert.equal(blocked.status, 'open');
  assert.match(blocked.closeError, /verified exit liquidity/);
});
