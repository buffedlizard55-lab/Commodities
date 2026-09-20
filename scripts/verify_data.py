#!/usr/bin/env python3
"""Verify every stored data file in data/season-2026 against its documented cross-checks.

Run: python3 scripts/verify_data.py
Exit code 0 = all checks pass; non-zero = at least one check failed.
Writes data/season-2026/SHA256SUMS.txt binding each stored file.
"""
import csv
import hashlib
import io
import json
import os
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "data", "season-2026")
FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(f"PASS {name}")
    else:
        FAILURES.append(f"FAIL {name} {detail}")
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")


def read_csv(path):
    rows = []
    with open(os.path.join(BASE, path), newline="") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            rows.append(next(csv.reader(io.StringIO(line))))
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:]]


def fnum(cell):
    cell = cell.strip()
    if cell == "":
        return None
    return float(cell)


def main():
    # ---- KXCPI daily candles ----
    cpi = read_csv("candles-KXCPI-26AUG-T0.8-daily.csv")
    check("cpi.rows==44", len(cpi) == 44, f"(got {len(cpi)})")
    ts = [int(r["end_period_ts"]) for r in cpi]
    check("cpi.ts_strictly_increasing", all(b > a for a, b in zip(ts, ts[1:])))
    check("cpi.first_ts", ts[0] == 1784865600, f"(got {ts[0]})")
    check("cpi.last_ts", ts[-1] == 1789185600, f"(got {ts[-1]})")
    ok_bounds = all(
        all((fnum(r[c]) is None or 0.0 <= fnum(r[c]) <= 1.0) for c in
            ["price_open", "price_high", "price_low", "price_close", "yes_bid_close", "yes_ask_close"])
        for r in cpi
    )
    check("cpi.prices_in_[0,1]", ok_bounds)
    vol_sum = sum(fnum(r["volume"]) or 0.0 for r in cpi)
    check("cpi.volume_sum==22306.56", abs(vol_sum - 22306.56) < 1e-6, f"(got {vol_sum})")
    last_oi = fnum(cpi[-1]["open_interest"])
    check("cpi.last_oi==15842.78", abs(last_oi - 15842.78) < 1e-6, f"(got {last_oi})")

    # ---- KXFED 90-day candles ----
    fed = read_csv("candles-KXFED-26SEP-T4.75-daily.csv")
    check("fed.rows==72", len(fed) == 72, f"(got {len(fed)})")
    fts = [int(r["end_period_ts"]) for r in fed]
    check("fed.ts_strictly_increasing", all(b > a for a, b in zip(fts, fts[1:])))
    check("fed.first_ts", fts[0] == 1781928000, f"(got {fts[0]})")
    check("fed.last_ts", fts[-1] == 1789617600, f"(got {fts[-1]})")
    first = fed[0]
    check("fed.first_vol==1.00", abs(fnum(first["volume"]) - 1.0) < 1e-6)
    check("fed.first_oi==317.00", abs(fnum(first["open_interest"]) - 317.0) < 1e-6)
    last = fed[-1]
    check("fed.last_oi==208343.78", abs(fnum(last["open_interest"]) - 208343.78) < 1e-6,
          f"(got {fnum(last['open_interest'])})")
    spike = next(r for r in fed if r["end_period_ts"] == "1788580800")
    check("fed.spike_bar_vol==296371.69", abs(fnum(spike["volume"]) - 296371.69) < 1e-6)
    ok_bounds = all(
        all((fnum(r[c]) is None or 0.0 <= fnum(r[c]) <= 1.0) for c in
            ["price_open", "price_high", "price_low", "price_close", "yes_bid_close", "yes_ask_close"])
        for r in fed
    )
    check("fed.prices_in_[0,1]", ok_bounds)

    # ---- KXFED cold-open context ----
    cold = read_csv("candles-KXFED-26SEP-T4.75-cold-open-2025-08.csv")
    check("cold.rows==27", len(cold) == 27, f"(got {len(cold)})")
    check("cold.all_zero_volume", all((fnum(r["volume"]) or 0.0) == 0.0 for r in cold))
    check("cold.first_ts", int(cold[0]["end_period_ts"]) == 1754539200)

    # ---- NFL BUF hourly candles ----
    nfl = read_csv("candles-KXNFLGAME-26SEP17DETBUF-BUF-hourly.csv")
    check("nfl.rows==14", len(nfl) == 14, f"(got {len(nfl)})")
    nts = [int(r["end_period_ts"]) for r in nfl]
    check("nfl.ts_strictly_increasing", all(b > a for a, b in zip(nts, nts[1:])))
    check("nfl.first_ts", nts[0] == 1789646400)
    check("nfl.last_ts", nts[-1] == 1789693200)
    nfl_vol = sum(fnum(r["volume"]) or 0.0 for r in nfl)
    check("nfl.volume_sum==8989894.63", abs(nfl_vol - 8989894.63) < 1e-4, f"(got {nfl_vol})")
    check("nfl.last_close==0.94", abs(fnum(nfl[-1]["price_close"]) - 0.94) < 1e-9)
    check("nfl.last_oi==6349995.83", abs(fnum(nfl[-1]["open_interest"]) - 6349995.83) < 1e-4)
    check("nfl.ask_ge_bid_every_bar",
          all(fnum(r["yes_ask_close"]) >= fnum(r["yes_bid_close"]) for r in nfl))
    check("nfl.jump_bar", abs(fnum(nfl[12]["volume"]) - 3727803.14) < 1e-4 and
          abs(fnum(nfl[12]["price_close"]) - 0.95) < 1e-9)

    # ---- Order book ----
    book = read_csv("orderbook-KXBTC-26SEP2017-T90749.99.csv")
    check("book.levels==15", len(book) == 15, f"(got {len(book)})")
    check("book.all_no_side", all(r["side"] == "no" for r in book))
    depth = sum(fnum(r["size_fp"]) or 0.0 for r in book)
    check("book.total_no_depth==53827.00", abs(depth - 53827.0) < 1e-6, f"(got {depth})")

    # ---- Trade tape sample ----
    tape = read_csv("trade-tape-sample.csv")
    check("tape.rows==25", len(tape) == 25, f"(got {len(tape)})")
    check("tape.all_2026_09_19", all(r["created_time"].startswith("2026-09-19") for r in tape))
    check("tape.yes_no_complement",
          all(abs((fnum(r["yes_price_dollars"]) or 0) + (fnum(r["no_price_dollars"]) or 0) - 1.0) < 1e-6
              for r in tape))

    # ---- Market records ----
    with open(os.path.join(BASE, "market-records.json")) as fh:
        rec = json.load(fh)
    by_ticker = {m["ticker"]: m for m in rec["markets"]}
    c = by_ticker["KXCPI-26AUG-T0.8"]
    check("rec.cpi_result_no", c["result"] == "no")
    check("rec.cpi_settlement_ts", c["settlement_ts"] == "2026-09-11T13:28:53.706257Z")
    check("rec.cpi_volume", c["volume_fp"] == "22306.56")
    check("rec.cpi_oi", c["open_interest_fp"] == "15842.78")
    f = by_ticker["KXFED-26SEP-T4.75"]
    check("rec.fed_result_no", f["result"] == "no")
    check("rec.fed_settlement_ts", f["settlement_ts"] == "2026-09-16T18:20:58.459351Z")
    check("rec.fed_oi", f["open_interest_fp"] == "208343.78")
    b = by_ticker["KXNFLGAME-26SEP17DETBUF-BUF"]
    d = by_ticker["KXNFLGAME-26SEP17DETBUF-DET"]
    check("rec.buf_result_yes", b["result"] == "yes" and b["settlement_value_dollars"] == "1.0000")
    check("rec.det_result_no", d["result"] == "no" and d["settlement_value_dollars"] == "0.0000")
    check("rec.nfl_settlement_ts_match",
          b["settlement_ts"] == d["settlement_ts"] == "2026-09-18T03:34:55.133992Z")
    x = by_ticker["KXBTC-26SEP2017-T90749.99"]
    check("rec.btc_open", x["status"] == "active" and x["close_time"] == "2026-09-20T21:00:00Z")
    check("rec.btc_yes_ask", x["yes_ask_dollars"] == "0.0100" and x["yes_ask_size_fp"] == "5999.00")

    # ---- Cutoff + series records ----
    with open(os.path.join(BASE, "cutoff.json")) as fh:
        cut = json.load(fh)
    check("cutoff.value", cut["response"]["market_settled_ts"] == "2026-07-21T00:00:00Z")
    with open(os.path.join(BASE, "series-records.json")) as fh:
        ser = json.load(fh)
    fees = {s["ticker"]: (s["fee_type"], s["fee_multiplier"]) for s in ser["series"]}
    check("series.fed_fee", fees["KXFED"] == ("quadratic_with_maker_fees", 1))
    check("series.cpi_fee", fees["KXCPI"] == ("quadratic_with_maker_fees", 1))
    check("series.btc_fee", fees["KXBTC"] == ("quadratic", 1))
    check("series.gold_fee", fees["KXGOLDH"] == ("quadratic", 1))

    # ---- Hashes (recursive: raw/ holds the verbatim API responses) ----
    lines = []
    for root, dirs, files in os.walk(BASE):
        dirs.sort()
        for name in sorted(files):
            if name == "SHA256SUMS.txt":
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, BASE)
            with open(path, "rb") as fh:
                lines.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {rel}")
    with open(os.path.join(BASE, "SHA256SUMS.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"WROTE SHA256SUMS.txt ({len(lines)} files)")

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print("  " + f)
        sys.exit(1)


if __name__ == "__main__":
    main()
