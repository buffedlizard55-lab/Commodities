#!/usr/bin/env python3
"""Regenerate the candle CSVs with guaranteed column structure.

Each bar below was transcribed from the verified Kalshi candlestick responses captured
2026-09-19 (see MANIFEST.md for the exact endpoints). Tuple layout:
(ts, po, ph, pl, pc, pm, pp, vol, oi, bid, ask) - None = field absent in the API response.
"""
import os

BASE = os.path.join(os.path.dirname(__file__), "..", "data", "season-2026")
HEADER = ("end_period_ts,price_open,price_high,price_low,price_close,price_mean,"
          "price_previous,volume,open_interest,yes_bid_close,yes_ask_close\n")

CPI = [
    (1784865600, None, None, None, None, None, None, 0, 0, 0.01, 0.99),
    (1784952000, None, None, None, None, None, None, 0, 0, 0.01, 0.99),
    (1785038400, None, None, None, None, None, None, 0, 0, 0.01, 0.99),
    (1785124800, None, None, None, None, None, None, 0, 0, 0.01, 0.99),
    (1785211200, None, None, None, None, None, None, 0, 0, 0.01, 0.99),
    (1785297600, None, None, None, None, None, None, 0, 0, 0.01, 0.31),
    (1785384000, None, None, None, None, None, None, 0, 0, 0.02, 0.97),
    (1785470400, None, None, None, None, None, None, 0, 0, 0.02, 0.13),
    (1785556800, None, None, None, None, None, None, 0, 0, 0.01, 0.31),
    (1785643200, 0.31, 0.31, 0.31, 0.31, 0.31, None, 13.00, 13.00, 0.01, 0.32),
    (1785729600, None, None, None, None, None, 0.31, 0, 13.00, 0.01, 0.32),
    (1785816000, None, None, None, None, None, 0.31, 0, 13.00, 0.01, 0.21),
    (1785902400, None, None, None, None, None, 0.31, 0, 13.00, 0.02, 0.19),
    (1785988800, 0.01, 0.18, 0.01, 0.18, 0.0656, 0.31, 375.00, 284.00, 0.00, 0.17),
    (1786075200, None, None, None, None, None, 0.18, 0, 284.00, 0.00, 0.18),
    (1786507200, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 27.78, 311.78, 0.00, 0.18),
    (1786593600, None, None, None, None, None, 0.18, 0, 311.78, 0.00, 0.01),
    (1786766400, 0.01, 0.10, 0.01, 0.10, 0.0598, 0.18, 82.78, 332.78, 0.00, 0.10),
    (1786852800, None, None, None, None, None, 0.10, 0, 332.78, 0.00, 0.18),
    (1786939200, 0.18, 0.32, 0.18, 0.32, 0.2628, 0.10, 25.00, 337.78, 0.00, 0.33),
    (1787025600, 0.04, 0.04, 0.04, 0.04, 0.04, 0.32, 1.00, 338.78, 0.00, 0.04),
    (1787112000, None, None, None, None, None, 0.04, 0, 338.78, 0.00, 0.04),
    (1787198400, None, None, None, None, None, 0.04, 0, 338.78, 0.00, 0.04),
    (1787284800, 0.04, 0.04, 0.04, 0.04, 0.04, 0.04, 55.00, 338.78, 0.00, 0.07),
    (1787371200, 0.07, 0.07, 0.07, 0.07, 0.07, 0.04, 3.00, 341.78, 0.00, 0.12),
    (1787457600, 0.33, 0.37, 0.20, 0.37, 0.2971, 0.07, 209.00, 405.78, 0.00, 0.38),
    (1787544000, 0.38, 0.38, 0.38, 0.38, 0.38, 0.37, 45.00, 450.78, 0.00, 0.40),
    (1787630400, 0.04, 0.13, 0.04, 0.13, 0.0522, 0.38, 79.00, 529.78, 0.01, 0.11),
    (1787716800, None, None, None, None, None, 0.13, 0, 529.78, 0.00, 0.40),
    (1787803200, None, None, None, None, None, 0.13, 0, 529.78, 0.00, 0.10),
    (1787889600, 0.04, 0.08, 0.03, 0.08, 0.0618, 0.13, 4572.00, 3051.78, 0.00, 0.08),
    (1787976000, 0.02, 0.02, 0.02, 0.02, 0.02, 0.08, 14.00, 3065.78, 0.00, 0.03),
    (1788062400, None, None, None, None, None, 0.02, 0, 3065.78, 0.00, 0.02),
    (1788235200, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 83.00, 3065.78, 0.00, 0.03),
    (1788321600, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 17.00, 3082.78, 0.00, 0.02),
    (1788408000, None, None, None, None, None, 0.02, 0, 3082.78, 0.00, 0.02),
    (1788580800, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 470.00, 3552.78, 0.00, 0.02),
    (1788667200, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 142.00, 3694.78, 0.00, 0.02),
    (1788753600, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 332.00, 4026.78, 0.00, 0.02),
    (1788840000, 0.02, 0.09, 0.02, 0.02, 0.0482, 0.02, 14177.00, 14303.78, 0.00, 0.02),
    (1788926400, 0.02, 0.02, 0.01, 0.01, 0.0167, 0.02, 135.00, 14393.78, 0.00, 0.01),
    (1789012800, 0.01, 0.03, 0.01, 0.01, 0.0106, 0.01, 1252.00, 15645.78, 0.00, 0.01),
    (1789099200, 0.01, 0.02, 0.01, 0.01, 0.0110, 0.01, 197.00, 15842.78, 0.00, 0.01),
    (1789185600, None, None, None, None, None, 0.01, 0, 15842.78, 0.00, 1.00),
]

FED = [
    (1781928000, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 1.00, 317.00, 0.02, 0.04),
    (1782014400, None, None, None, None, None, 0.02, 0, 317.00, 0.02, 0.04),
    (1782446400, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 113.00, 317.00, 0.01, 0.04),
    (1782964800, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783051200, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783137600, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783224000, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783310400, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783396800, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.04),
    (1783483200, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.02),
    (1783569600, None, None, None, None, None, 0.02, 0, 317.00, 0.01, 0.02),
    (1783656000, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 540.00, 850.00, 0.00, 0.02),
    (1783828800, None, None, None, None, None, 0.01, 0, 850.00, 0.00, 0.02),
    (1783915200, None, None, None, None, None, 0.01, 0, 850.00, 0.00, 0.02),
    (1784001600, None, None, None, None, None, 0.01, 0, 850.00, 0.00, 0.02),
    (1784088000, None, None, None, None, None, 0.01, 0, 850.00, 0.01, 0.02),
    (1784174400, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 6.00, 856.00, 0.00, 0.01),
    (1784260800, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784347200, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784433600, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784520000, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784606400, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784692800, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784779200, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.01),
    (1784865600, None, None, None, None, None, 0.01, 0, 856.00, 0.00, 0.02),
    (1785038400, 0.02, 0.02, 0.02, 0.02, 0.02, 0.01, 27.06, 882.06, 0.00, 0.02),
    (1785211200, None, None, None, None, None, 0.02, 0, 882.06, 0.00, 0.02),
    (1785297600, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 3.00, 885.06, 0.00, 0.01),
    (1785384000, None, None, None, None, None, 0.02, 0, 885.06, 0.00, 0.01),
    (1785470400, None, None, None, None, None, 0.02, 0, 885.06, 0.00, 0.01),
    (1785556800, None, None, None, None, None, 0.02, 0, 885.06, 0.00, 0.01),
    (1785643200, None, None, None, None, None, 0.02, 0, 885.06, 0.00, 0.01),
    (1785729600, None, None, None, None, None, 0.02, 0, 885.06, 0.00, 0.01),
    (1785816000, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 53.33, 938.39, 0.00, 0.01),
    (1785902400, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1785988800, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1786075200, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1786161600, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1786248000, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1786334400, None, None, None, None, None, 0.01, 0, 938.39, 0.00, 0.01),
    (1786420800, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 100.00, 1038.39, 0.00, 0.01),
    (1786507200, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 33.33, 1071.72, 0.00, 0.01),
    (1786593600, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1786680000, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1786766400, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1786852800, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1786939200, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787025600, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787112000, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787198400, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787284800, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787371200, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787630400, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787716800, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787803200, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787889600, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1787976000, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1788148800, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1788235200, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1788321600, None, None, None, None, None, 0.01, 0, 1071.72, 0.00, 0.01),
    (1788408000, 0.01, 0.11, 0.01, 0.01, 0.0450, 0.01, 99807.80, 55960.48, 0.00, 0.01),
    (1788494400, None, None, None, None, None, 0.01, 0, 55960.48, 0.00, 0.01),
    (1788580800, 0.01, 0.04, 0.01, 0.01, 0.0246, 0.01, 296371.69, 207990.11, 0.00, 0.01),
    (1788667200, None, None, None, None, None, 0.01, 0, 207990.11, 0.00, 0.01),
    (1788926400, None, None, None, None, None, 0.01, 0, 207990.11, 0.00, 0.01),
    (1789012800, None, None, None, None, None, 0.01, 0, 207990.11, 0.00, 0.01),
    (1789099200, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 3.00, 207993.11, 0.00, 0.01),
    (1789185600, None, None, None, None, None, 0.01, 0, 207993.11, 0.00, 0.01),
    (1789358400, None, None, None, None, None, 0.01, 0, 207993.11, 0.00, 0.01),
    (1789444800, None, None, None, None, None, 0.01, 0, 207993.11, 0.00, 0.01),
    (1789531200, None, None, None, None, None, 0.01, 0, 207993.11, 0.00, 0.01),
    (1789617600, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 350.67, 208343.78, 0.00, 1.00),
]

COLD = [
    (1754539200, 0.06), (1754625600, 0.05), (1754712000, 0.05), (1754798400, 0.05),
    (1754884800, 0.05), (1754971200, 0.05), (1755057600, 0.05), (1755144000, 0.05),
    (1755230400, 0.05), (1755316800, 0.05), (1755403200, 0.05), (1755489600, 0.05),
    (1755576000, 0.05), (1755662400, 0.05), (1755748800, 0.05), (1755835200, 0.05),
    (1755921600, 0.05), (1756008000, 0.05), (1756094400, 0.05), (1756180800, 0.06),
    (1756267200, 0.06), (1756353600, 0.06), (1756440000, 0.06), (1756526400, 0.06),
    (1756612800, 0.06), (1756699200, 0.06), (1756785600, 0.06),
]

HEADER_COLD = ("end_period_ts,price_open,price_high,price_low,price_close,price_mean,"
               "price_previous,volume,open_interest,yes_bid_close,yes_ask_close\n")


def fmt(v):
    # None = field absent from the API response (e.g. no trades that bar);
    # an explicit zero is stored as "0.00", exactly as the API returns it.
    if v is None:
        return ""
    if isinstance(v, float) and v == 0:
        return "0.00"
    if isinstance(v, int) and v == 0:
        return "0.00"
    return str(v)


def write_bars(path, bars, header, comments):
    lines = [f"# {c}" for c in comments]
    lines.append(header.rstrip("\n"))
    for b in bars:
        ts, po, ph, pl, pc, pm, pp, vol, oi, bid, ask = b
        lines.append(",".join([str(ts)] + [fmt(x) for x in (po, ph, pl, pc, pm, pp, vol, oi, bid, ask)]))
    with open(os.path.join(BASE, path), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"WROTE {path} ({len(bars)} bars)")


if __name__ == "__main__":
    write_bars("candles-KXCPI-26AUG-T0.8-daily.csv", CPI, HEADER, [
        "Source: GET https://external-api.kalshi.com/trade-api/v2/series/KXCPI/markets/KXCPI-26AUG-T0.8/candlesticks?start_ts=1784505600&end_ts=1789257600&period_interval=1440",
        "Market: KXCPI-26AUG-T0.8 \"Will CPI rise more than 0.8% in August 2026?\" result=no, settlement_ts=2026-09-11T13:28:53.706257Z",
        "Retrieved 2026-09-19 (UTC). 44 daily bars; bars end at 04:00 UTC (US-market day boundary).",
        "price_* = executed trade prices; yes_bid_close/yes_ask_close = closing bid/ask quotes for the bar.",
        "Cross-check: SUM(volume) = 22306.56 == market record volume_fp; last open_interest 15842.78 == market record OI.",
    ])
    write_bars("candles-KXFED-26SEP-T4.75-daily.csv", FED, HEADER, [
        "Source: GET https://external-api.kalshi.com/trade-api/v2/series/KXFED/markets/KXFED-26SEP-T4.75/candlesticks?start_ts=1781913600&end_ts=1789776000&period_interval=1440",
        "Market: KXFED-26SEP-T4.75 \"Fed rate above 4.75% after Sep 16, 2026 meeting?\" result=no, settlement_ts=2026-09-16T18:20:58.459351Z",
        "Retrieved 2026-09-19 (UTC). 72 daily bars, window 2026-06-20 -> 2026-09-16 (bars end at 04:00 UTC).",
        "Cross-check: last open_interest 208343.78 == market record OI; first bar ts 1781928000 vol 1.00 OI 317.00.",
    ])
    lines = [
        "# Source: GET https://external-api.kalshi.com/trade-api/v2/series/KXFED/markets/KXFED-26SEP-T4.75/candlesticks?start_ts=1754006400&end_ts=1789776000&period_interval=1440",
        "# CONTEXT ONLY - cold-open sample (first 27 bars returned by the API, market open 2025-08-06).",
        "# Not used in backtest computations (zero volume, zero OI; quote-only evidence that the market",
        "# opened pricing a \"rate above 4.75%\" outcome at ~4-6 cents).",
        "# Bar 27 (1756785600) yes_ask low was truncated in the captured chunk; all other fields verified.",
        HEADER_COLD.rstrip("\n"),
    ]
    for ts, ask in COLD:
        lines.append(f"{ts},,,,,,,0.00,0.00,0.00,{ask:.2f}")
    with open(os.path.join(BASE, "candles-KXFED-26SEP-T4.75-cold-open-2025-08.csv"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"WROTE candles-KXFED-26SEP-T4.75-cold-open-2025-08.csv ({len(COLD)} bars)")
