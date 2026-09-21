import test from 'node:test';
import assert from 'node:assert/strict';

function element(initial = {}) {
  return { textContent: initial.textContent ?? '', innerHTML: '', classList: { add() {}, toggle() {} } };
}

test('browser season renderer follows a 2027 active index and keeps 2026 frozen', async () => {
  const elements = {
    '#season-list': element(),
    '#season-note': element(),
    '#season-state': element(),
    '#season-year': element({ textContent: '—' }),
  };
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  globalThis.document = { querySelector: (selector) => elements[selector] ?? null };
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      activeSeason: '2027',
      rolloverRule: 'A season is one UTC calendar year.',
      seasons: [
        { season: '2026', active: false, frozen: true, cycles: 48, participants: 23, fills: 100, settlements: 50, dir: 'data/season-2026', createdAt: '2026-01-01T00:00:00Z' },
        { season: '2027', active: true, frozen: false, cycles: 0, participants: 0, fills: 0, settlements: 0, dir: 'data/season-2027', createdAt: '2027-01-01T00:00:00Z' },
      ],
    }),
  });
  try {
    const module = await import(`../src/season.js?rollover-browser=${Date.now()}`);
    await module.loadSeasonsIndex();
    assert.equal(module.seasonMeta.activeSeason, '2027');
    assert.match(elements['#season-list'].innerHTML, /Season 2027/);
    assert.match(elements['#season-list'].innerHTML, /Season 2026/);
    assert.match(elements['#season-list'].innerHTML, /frozen/);
    assert.equal(elements['#season-year'].textContent, '2027');
  } finally {
    globalThis.document = originalDocument;
    globalThis.fetch = originalFetch;
  }
});
