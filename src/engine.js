// Pure, dependency-free accounting and order-book helpers.
// The UI only calls these functions with values returned by Kalshi's public API.

export const KALSHI_MARKET_DATA_URL = 'https://external-api.kalshi.com/trade-api/v2';
export const STARTING_CASH = 10_000;
export const STANDARD_QUADRATIC_MULTIPLIER = 1;

export function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = typeof value === 'number' ? value : Number.parseFloat(String(value));
  return Number.isFinite(number) ? number : null;
}

function firstNumber(...values) {
  for (const value of values) {
    const parsed = numberOrNull(value);
    if (parsed !== null) return parsed;
  }
  return null;
}

export function formatDollars(value, digits = 2) {
  const number = numberOrNull(value);
  return number === null ? '—' : `$${number.toFixed(digits)}`;
}

export function formatContracts(value) {
  const number = numberOrNull(value);
  return number === null ? '—' : number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function parseTimestamp(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number' || (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value))) {
    const numeric = Number(value);
    const milliseconds = numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
    return Number.isFinite(milliseconds) ? milliseconds : null;
  }
  const milliseconds = new Date(value).getTime();
  return Number.isNaN(milliseconds) ? null : milliseconds;
}

export function formatDate(value) {
  if (!value) return '—';
  const timestamp = parseTimestamp(value);
  const date = timestamp === null ? null : new Date(timestamp);
  return !date || Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }) + ' UTC';
}

export function normalizeMarket(raw = {}) {
  const yesBid = firstNumber(raw.yes_bid_dollars, raw.yes_bid);
  const noBid = firstNumber(raw.no_bid_dollars, raw.no_bid);
  const explicitYesAsk = firstNumber(raw.yes_ask_dollars, raw.yes_ask);
  const explicitNoAsk = firstNumber(raw.no_ask_dollars, raw.no_ask);
  const yesAsk = explicitYesAsk ?? (noBid === null ? null : 1 - noBid);
  const noAsk = explicitNoAsk ?? (yesBid === null ? null : 1 - yesBid);
  const yesAskDerived = explicitYesAsk === null && yesAsk !== null;
  const noAskDerived = explicitNoAsk === null && noAsk !== null;
  const closeTimeRaw = raw.close_time ?? raw.close_ts ?? raw.expiration_time ?? raw.expected_expiration_time ?? null;
  const closeTs = parseTimestamp(closeTimeRaw);
  const closeTime = closeTs === null ? closeTimeRaw : new Date(closeTs).toISOString();
  const last = firstNumber(raw.last_price_dollars, raw.last_price, raw.price_dollars, raw.price);
  const previous = firstNumber(raw.previous_price_dollars, raw.previous_price);
  const mid = yesBid !== null && yesAsk !== null ? (yesBid + yesAsk) / 2 : last;
  return {
    ticker: String(raw.ticker ?? raw.market_ticker ?? ''),
    eventTicker: String(raw.event_ticker ?? ''),
    title: String(raw.title ?? raw.subtitle ?? raw.ticker ?? 'Untitled contract'),
    subtitle: String(raw.subtitle ?? ''),
    status: String(raw.status ?? ''),
    yesBid,
    yesAsk,
    noBid,
    noAsk,
    yesAskDerived,
    noAskDerived,
    last,
    previous,
    mid,
    volume: firstNumber(raw.volume_fp, raw.volume),
    openInterest: firstNumber(raw.open_interest_fp, raw.open_interest),
    closeTime,
    closeTs,
    result: raw.result ?? raw.market_result ?? null,
    raw,
  };
}

export function normalizeMarkets(payload) {
  const rows = Array.isArray(payload) ? payload : (payload?.markets ?? []);
  return rows.map(normalizeMarket).filter((market) => market.ticker);
}

function levelArray(book, side) {
  const source = book?.orderbook_fp ?? book?.orderbook ?? book ?? {};
  const values = side === 'yes'
    ? (source.yes_dollars ?? source.yes ?? source.yes_bids ?? [])
    : (source.no_dollars ?? source.no ?? source.no_bids ?? []);
  if (!Array.isArray(values)) return [];
  return values.map((level) => {
    if (Array.isArray(level)) return { price: numberOrNull(level[0]), quantity: numberOrNull(level[1]) };
    return { price: firstNumber(level.price_dollars, level.price), quantity: firstNumber(level.quantity_fp, level.quantity, level.count_fp, level.count) };
  }).filter((level) => level.price !== null && level.quantity !== null && level.quantity > 0).sort((a, b) => a.price - b.price);
}

export function normalizeOrderBook(payload) {
  return {
    yes: levelArray(payload, 'yes'),
    no: levelArray(payload, 'no'),
    fetchedAt: new Date().toISOString(),
    raw: payload,
  };
}

export function bestBid(book, side) {
  const levels = book?.[side] ?? [];
  return levels.length ? levels[levels.length - 1].price : null;
}

export function displayedQuotes(market, book = null) {
  const yesBid = book ? bestBid(book, 'yes') : market.yesBid;
  const noBid = book ? bestBid(book, 'no') : market.noBid;
  const yesAsk = yesBid === null && noBid === null ? market.yesAsk : (noBid === null ? market.yesAsk : 1 - noBid);
  const noAsk = yesBid === null && noBid === null ? market.noAsk : (yesBid === null ? market.noAsk : 1 - yesBid);
  return { yesBid, yesAsk, noBid, noAsk };
}

export function bookImbalance(book) {
  const yes = (book?.yes ?? []).reduce((total, level) => total + level.quantity, 0);
  const no = (book?.no ?? []).reduce((total, level) => total + level.quantity, 0);
  if (yes + no === 0) return null;
  return (yes - no) / (yes + no);
}

/**
 * Consume the public bid book as a taker. YES asks are the reciprocal of NO bids;
 * NO asks are the reciprocal of YES bids. This is the documented binary-book rule.
 */
export function executeAgainstBook(book, side, action, requestedContracts) {
  const requested = numberOrNull(requestedContracts);
  if (!book || !['yes', 'no'].includes(side) || !['buy', 'sell'].includes(action) || requested === null || requested <= 0) {
    return { fills: [], requestedContracts: requested ?? 0, filledContracts: 0, notional: 0, vwap: null, remainingContracts: requested ?? 0, slippage: null };
  }
  const sourceSide = action === 'sell' ? side : (side === 'yes' ? 'no' : 'yes');
  const source = [...(book[sourceSide] ?? [])].sort((a, b) => b.price - a.price);
  const bestSource = source[0]?.price ?? null;
  const bestExecutable = bestSource === null ? null : (action === 'sell' ? bestSource : 1 - bestSource);
  let remaining = requested;
  let notional = 0;
  const fills = [];
  for (const level of source) {
    if (remaining <= 0) break;
    const quantity = Math.min(remaining, level.quantity);
    const price = action === 'sell' ? level.price : 1 - level.price;
    fills.push({ sourcePrice: level.price, price, quantity });
    remaining -= quantity;
    notional += quantity * price;
  }
  const filledContracts = requested - remaining;
  const vwap = filledContracts > 0 ? notional / filledContracts : null;
  const slippage = vwap !== null && bestExecutable !== null ? (action === 'buy' ? vwap - bestExecutable : bestExecutable - vwap) : null;
  return { fills, requestedContracts: requested, filledContracts, notional, vwap, remainingContracts: remaining, slippage };
}

/** Standard quadratic taker fee from Kalshi's published fee schedule. */
export function standardQuadraticFee(price, contracts, multiplier = STANDARD_QUADRATIC_MULTIPLIER) {
  const p = numberOrNull(price);
  const quantity = numberOrNull(contracts);
  const feeMultiplier = numberOrNull(multiplier) ?? STANDARD_QUADRATIC_MULTIPLIER;
  if (p === null || quantity === null || p < 0 || p > 1 || quantity <= 0) return 0;
  // Kalshi's price is in dollars, so p * (1-p) is the expected-earnings component.
  const rawFee = 0.07 * quantity * p * (1 - p) * feeMultiplier;
  // The API documentation describes trade-fee rounding up to the nearest $0.0001.
  return Math.ceil((rawFee - 1e-10) * 10_000) / 10_000;
}

export function affordableContracts(cash, executionPrice, feeMultiplier = 1) {
  const availableCash = numberOrNull(cash) ?? 0;
  const price = numberOrNull(executionPrice);
  if (availableCash <= 0 || price === null || price <= 0) return 0;
  // Maximize deployable paper capital while leaving enough for the standard fee.
  let low = 0;
  let high = Math.floor(availableCash / price);
  while (high - low > 1) {
    const mid = Math.floor((low + high) / 2);
    if (mid * price + standardQuadraticFee(price, mid, feeMultiplier) <= availableCash) low = mid;
    else high = mid;
  }
  if (high * price + standardQuadraticFee(price, high, feeMultiplier) <= availableCash) return high;
  return low;
}

export function markPosition(trade, market, book = null) {
  const quotes = displayedQuotes(market, book);
  const bid = trade.side === 'yes' ? quotes.yesBid : quotes.noBid;
  const mid = trade.side === 'yes' ? quotes.yesAsk !== null && quotes.yesBid !== null ? (quotes.yesAsk + quotes.yesBid) / 2 : market.mid : quotes.noAsk !== null && quotes.noBid !== null ? (quotes.noAsk + quotes.noBid) / 2 : market.mid;
  const liquidationValue = bid === null ? null : trade.contracts * bid;
  const markValue = mid === null ? liquidationValue : trade.contracts * mid;
  return { bid, mid, liquidationValue, markValue };
}

export function buildTradeId(strategyId, ticker, timestamp = Date.now()) {
  return `${strategyId}-${ticker}-${timestamp}`.replace(/[^A-Za-z0-9_-]/g, '-');
}

export function createPaperTrade({ strategy, market, book, side, requestedContracts, now = new Date(), feeMultiplier = 1 }) {
  const execution = executeAgainstBook(book, side, 'buy', requestedContracts);
  if (execution.filledContracts <= 0 || execution.vwap === null) return null;
  const entryFee = standardQuadraticFee(execution.vwap, execution.filledContracts, feeMultiplier);
  const at = now instanceof Date ? now.toISOString() : new Date(now).toISOString();
  const quotes = displayedQuotes(market, book);
  return {
    id: buildTradeId(strategy.id, market.ticker, new Date(at).getTime()),
    strategyId: strategy.id,
    username: strategy.username,
    ticker: market.ticker,
    eventTicker: market.eventTicker,
    title: market.title,
    side,
    action: 'buy',
    status: 'open',
    contracts: execution.filledContracts,
    requestedContracts: execution.requestedContracts,
    entryPrice: execution.vwap,
    entryNotional: execution.notional,
    entryFee,
    entryAt: at,
    marketCloseTime: market.closeTime,
    slippage: execution.slippage ?? 0,
    bestExecutablePrice: side === 'yes' ? quotes.yesAsk : quotes.noAsk,
    liquidityConsumed: execution.fills,
    source: {
      endpoint: `${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(market.ticker)}/orderbook`,
      marketEndpoint: `${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(market.ticker)}`,
      retrievedAt: book.fetchedAt ?? at,
      status: market.status,
      marketRaw: market.raw,
      orderBookRaw: book.raw,
    },
    exitAt: null,
    exitPrice: null,
    exitNotional: null,
    exitFee: null,
    pnl: null,
  };
}

export function settlePaperTrade(trade, market) {
  if (!trade || !['open', 'partially-closed'].includes(trade.status)) return trade;
  const result = String(market?.result ?? '').toLowerCase();
  if (!['yes', 'no'].includes(result)) return trade;
  const payoutPerContract = trade.side === result ? 1 : 0;
  const payout = payoutPerContract * trade.contracts;
  const raw = market.raw ?? {};
  const settlementDateField = raw.settlement_ts ?? raw.settled_time ?? raw.settlement_time ?? raw.close_time ?? raw.expiration_time ?? null;
  const settlementAt = parseTimestamp(settlementDateField);
  // A result without an official date is insufficient for this ledger.
  if (settlementAt === null) return trade;
  const exitAt = new Date(settlementAt).toISOString();
  const pnl = payout - (trade.entryNotional + trade.entryFee);
  return {
    ...trade,
    status: 'settled',
    exitAt,
    exitPrice: payoutPerContract,
    exitNotional: payout,
    exitFee: 0,
    pnl,
    settlementResult: result,
    settlementDateField: settlementDateField === null ? null : String(settlementDateField),
    exitSource: {
      endpoint: `${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(market.ticker)}`,
      retrievedAt: new Date().toISOString(),
      marketRaw: raw,
      settlement: 'official market result; simple binary settlement fee assumed zero per Kalshi settlement documentation',
    },
  };
}

export function closePaperTrade(trade, market, book, now = new Date(), feeMultiplier = 1) {
  if (!trade || trade.status !== 'open') return trade;
  const execution = executeAgainstBook(book, trade.side, 'sell', trade.contracts);
  if (execution.filledContracts <= 0 || execution.vwap === null) return { ...trade, closeError: 'No bid liquidity available at exit.' };
  // Do not silently turn a partial bid-book match into a complete exit. The
  // ledger must keep the open position until a later verified book can close it.
  if (execution.filledContracts < trade.contracts) {
    return { ...trade, closeError: `Only ${execution.filledContracts} of ${trade.contracts} contracts had verified exit liquidity.` };
  }
  const exitFee = standardQuadraticFee(execution.vwap, execution.filledContracts, feeMultiplier);
  const exitAt = now instanceof Date ? now.toISOString() : new Date(now).toISOString();
  const pnl = execution.notional - exitFee - (trade.entryNotional + trade.entryFee);
  return {
    ...trade,
    status: 'closed',
    exitAt,
    exitPrice: execution.vwap,
    exitNotional: execution.notional,
    exitFee,
    exitLiquidityConsumed: execution.fills,
    pnl,
    exitSource: {
      endpoint: `${KALSHI_MARKET_DATA_URL}/markets/${encodeURIComponent(market.ticker)}/orderbook`,
      retrievedAt: book.fetchedAt ?? exitAt,
      marketRaw: market.raw,
      orderBookRaw: book.raw,
    },
  };
}

export function accountSummary(trades, marketsByTicker = new Map(), booksByTicker = new Map(), startingCash = STARTING_CASH) {
  let realized = 0;
  let committed = 0;
  let markValue = 0;
  let liquidationValue = 0;
  let openTrades = 0;
  for (const trade of trades) {
    if (trade.pnl !== null && trade.pnl !== undefined) realized += trade.pnl;
    if (trade.status === 'open' || trade.status === 'partially-closed') {
      openTrades += 1;
      committed += (trade.entryNotional ?? 0) + (trade.entryFee ?? 0);
      const market = marketsByTicker.get(trade.ticker);
      if (market) {
        const mark = markPosition(trade, market, booksByTicker.get(trade.ticker));
        if (mark.markValue !== null) markValue += mark.markValue;
        if (mark.liquidationValue !== null) liquidationValue += mark.liquidationValue;
      }
    }
  }
  // Realized PnL is net of entry and exit fees. Open-equity uses a conservative liquidation mark.
  const equity = startingCash + realized + liquidationValue - trades.filter((t) => t.status === 'open' || t.status === 'partially-closed').reduce((sum, t) => sum + (t.entryNotional ?? 0) + (t.entryFee ?? 0), 0);
  return { startingCash, realized, committed, markValue, liquidationValue, equity, returnPct: (equity / startingCash - 1) * 100, openTrades };
}

export function strategyExplanation(summary, strategy) {
  if (!summary.trades) return 'No verified paper fills; this strategy has no return claim.';
  if (summary.realized === 0 && summary.openTrades === 0) return 'No closed result yet; observed quotes remain research evidence, not PnL.';
  if (summary.realized > 0) return `Positive net PnL from ${summary.trades} recorded fill${summary.trades === 1 ? '' : 's'}; the observed cause is the entry/exit price path after modeled fees.`;
  if (summary.realized < 0) return `Negative net PnL from ${summary.trades} recorded fill${summary.trades === 1 ? '' : 's'}; the observed cause is the entry/exit price path plus modeled fees and liquidity slippage.`;
  return `${strategy?.name ?? 'Strategy'} has live positions but no verified closed outcome yet; mark-to-book is not realized PnL.`;
}

export function csvEscape(value) {
  const string = value === null || value === undefined ? '' : String(value);
  return `"${string.replaceAll('"', '""')}"`;
}

export function tradesToCsv(trades) {
  const headers = ['id', 'strategyId', 'username', 'ticker', 'eventTicker', 'side', 'status', 'contracts', 'entryPrice', 'entryFee', 'entryAt', 'exitPrice', 'exitFee', 'exitAt', 'pnl', 'marketCloseTime'];
  const rows = trades.map((trade) => headers.map((header) => csvEscape(trade[header])).join(','));
  return [headers.join(','), ...rows].join('\n');
}
