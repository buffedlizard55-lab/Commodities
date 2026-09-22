#!/usr/bin/env python3
"""Market-making quote-plan model: match SpreadSmith's *posted* quotes against the official tape.

This is the missing piece named in the README's open list ("maker behaviour: the missing piece is
matching posted quotes against it").  The forward desk emits SpreadSmith quote plans (a simulated
resting order posted one cent inside the touch on a captured ladder) as intents with status
`quote_plan`.  A plan is NOT a fill and never opens a position.  This script measures, afterwards,
whether the official trade tape proves such a resting order would have traded:

    GET /markets/trades?ticker=<t>&min_ts=<post>&max_ts=<post+window>

Fill evidence on the quoted side (price p is the tape price of the quoted outcome):

  tape_traded_through   a print at p < our posted price after the post.  In a price-priority book
                        a trade strictly through our level can only print after bids at our price
                        were consumed, so this PROVES the resting order would have filled (at our
                        price, full posted size) provided the order rested - queue position is
                        irrelevant here.  This is the model's only "filled" state.
  queue_uncertain       prints at p == our posted price only.  A touch print needs queue position
                        (FIFO) that a REST snapshot cannot prove - reported separately, never
                        counted as filled.
  no_fill_evidence      no print at or through our price inside the window.

Everything the roll-up reports is MODELLED evidence about hypothetical resting orders: no ledger
fill, position or PnL is created here.  Projected settlement PnL (only for `tape_traded_through`
plans on markets whose official result is already in the ledger) is reported both gross and net of
the maker fee, now that the maker side has a primary source (IRR-41, closed 2026-09-22):
Kalshi's published fee schedule states `maker fees = round up(M x 0.0175 x C x P x (1-P))` with M
defaulting to 0, and the series' own fee_type (GET /series) says whether the series carries maker
fees at all, so a `quadratic` series pays 0 and a `quadratic_with_maker_fees` series pays the
0.0175 rate (one quarter of the taker rate) on each executed contract.

Usage:
  python3 scripts/maker_model.py --live                 # official tape (GitHub runner)
  python3 scripts/maker_model.py --fixtures DIR         # offline replay for tests
  python3 scripts/maker_model.py --window 3600 --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from kalshi_client import KalshiClient, FixtureClient, KalshiError  # noqa: E402
from paper_engine import fnum, parse_ts, iso, maker_fee, maker_multiplier  # noqa: E402
from forward_desk import FORWARD_DIR as DEFAULT_FORWARD_DIR, set_paths  # noqa: E402

MODEL_LABEL = "MODELLED - tape evidence about a hypothetical resting order; never a ledger fill"
DEFAULT_WINDOW = 3600  # the plan rests up to 1h or until market close (whichever is first)


def read_quote_plans(forward_dir: str) -> list[dict]:
    """Every SpreadSmith quote plan from the append-only intent ledger (oldest first)."""
    plans = []
    intent_dir = os.path.join(forward_dir, "intents")
    for name in sorted(os.listdir(intent_dir)) if os.path.isdir(intent_dir) else []:
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(intent_dir, name)) as fh:
            for line in fh:
                if not line.strip():
                    continue
                intent = json.loads(line)
                if intent.get("status") == "quote_plan" and intent.get("strategyId") == "spread-smith":
                    intent["_ledgerFile"] = os.path.join("intents", name)
                    plans.append(intent)
    plans.sort(key=lambda row: (str(row.get("at", "")), str(row.get("ticker", ""))))
    return plans


def tape_price_for(trade: dict, side: str) -> float | None:
    return fnum(trade.get("yes_price_dollars" if side == "yes" else "no_price_dollars"))


def load_results(forward_dir: str) -> dict:
    """Official results already reconciled into the ledger: ticker -> {result, settlementTs}.

    A market without a settlement event in trades.jsonl stays unknown here; the model never
    guesses a result (verification contract rule 3).
    """
    results = {}
    path = os.path.join(forward_dir, "trades.jsonl")
    if not os.path.exists(path):
        return results
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("kind") == "settlement" and event.get("ticker") and event.get("result"):
                results[event["ticker"]] = {"result": event["result"], "settlementTs": event.get("exitAt")}
    return results


def project_settlement(plan: dict, posted_price: float, contracts: float, results: dict,
                       fee_type: str | None = None, fee_multiplier: float | None = 1.0) -> dict:
    """Settlement PnL for a proven fill, gross and net of the official maker fee (IRR-41).

    The fee is charged on the executed resting contracts (Kalshi's fee schedule: maker fees apply
    to orders that rest and are ultimately executed, and nothing is charged for a cancellation),
    priced at our posted level with the series' own maker multiplier.
    """
    record = results.get(plan.get("ticker"))
    multiplier = maker_multiplier(fee_type, fee_multiplier)
    fee = maker_fee(posted_price, contracts, multiplier) if multiplier else 0.0
    if not record:
        return {"settled": False, "result": None, "pnlBeforeMakerFees": None,
                "makerFeeMultiplier": multiplier, "makerFee": fee}
    result = str(record["result"]).lower()
    side = plan.get("side")
    won = (result == side)
    payout = contracts * (1.0 if won else 0.0)
    pnl = round(payout - contracts * posted_price, 6)
    return {"settled": True, "result": result, "won": won,
            "settlementTs": record.get("settlementTs"), "pnlBeforeMakerFees": pnl,
            "makerFeeMultiplier": multiplier, "makerFee": round(fee, 6),
            "pnlNetOfMakerFees": round(pnl - fee, 6)}


def compare(plan: dict, client, window: int, results: dict, fee_records: dict | None = None) -> dict:
    """One posted quote plan vs the official tape after its post time."""
    side = plan.get("side")
    posted_price = fnum(plan.get("postedPrice"))
    series_fee = (fee_records or {}).get(plan.get("series")) or {}
    fee_type, fee_multiplier_value = series_fee.get("fee_type"), series_fee.get("fee_multiplier")
    posted_contracts = fnum(plan.get("postedContracts")) or 0.0
    posted_at = parse_ts(plan.get("bookAt") or plan.get("at"))
    close_ts = parse_ts(plan.get("closeTime"))
    row = {
        "kind": "maker-model-comparison", "at": iso(int(time.time())), "modelLabel": MODEL_LABEL,
        "strategyId": plan.get("strategyId"), "username": plan.get("username"), "ticker": plan.get("ticker"),
        "series": plan.get("series"), "side": side, "postedPrice": posted_price,
        "postedContracts": posted_contracts, "spreadAtPost": plan.get("spreadAtPost"),
        "improvementCents": plan.get("improvementCents"), "postedAt": plan.get("bookAt") or plan.get("at"),
        "planReason": plan.get("reason"), "planLedgerFile": plan.get("_ledgerFile"),
        "planLedgerLine": plan.get("ledgerLine"), "windowSeconds": window,
        "seriesFeeType": fee_type, "seriesMakerMultiplier": maker_multiplier(fee_type, fee_multiplier_value),
    }
    if posted_price is None or posted_at is None or side not in ("yes", "no"):
        row["status"] = "skipped_incomplete_plan"
        return row
    window_end = posted_at + window
    if close_ts is not None and close_ts > posted_at:
        window_end = min(window_end, close_ts)
    row["windowEnd"] = iso(window_end)
    try:
        payload, raw, url = client.trades(ticker=plan["ticker"], limit=1000, min_ts=posted_at, max_ts=window_end)
    except KalshiError as error:
        row["status"] = "tape_fetch_failed"
        row["error"] = str(error)[:200]
        return row
    row["tapeUrl"] = url
    row["tapeSha256"] = hashlib.sha256(raw).hexdigest()
    prints = [t for t in (payload.get("trades") or [])
              if tape_price_for(t, side) is not None and (parse_ts(t.get("created_time")) or 0) > posted_at]
    row["tapePrints"] = len(prints)
    if not prints:
        row["status"] = "compared"
        row["fillEvidence"] = "no_fill_evidence"
        row["filledContracts"] = 0.0
        row["settlement"] = {"settled": False, "result": None, "pnlBeforeMakerFees": None,
                             "makerFeeMultiplier": maker_multiplier(fee_type, fee_multiplier_value),
                             "makerFee": (maker_fee(posted_price, posted_contracts,
                                                    maker_multiplier(fee_type, fee_multiplier_value))
                                          if posted_price is not None else None)}
        return row
    prices = [tape_price_for(t, side) for t in prints]
    through = [t for t in prints if tape_price_for(t, side) < posted_price - 1e-9]
    touch = [t for t in prints if abs(tape_price_for(t, side) - posted_price) <= 1e-9]
    touch_volume = sum(fnum(t.get("count_fp")) or 0.0 for t in touch)
    row.update({
        "status": "compared", "tapeMin": min(prices), "tapeMax": max(prices),
        "throughPrints": len(through), "touchPrints": len(touch),
        "touchVolume": round(touch_volume, 2),
        "firstThroughTs": through[0].get("created_time") if through else None,
    })
    if through:
        # Sufficient condition: the tape traded strictly through our level -> the resting order
        # at our price was consumed first (price priority).  Full posted size fills at our price.
        row["fillEvidence"] = "tape_traded_through"
        row["filledContracts"] = posted_contracts
        row["settlement"] = project_settlement(plan, posted_price, posted_contracts, results,
                                               fee_type, fee_multiplier_value)
    elif touch:
        row["fillEvidence"] = "queue_uncertain"
        row["filledContracts"] = 0.0
        row["queueUncertainContracts"] = min(posted_contracts, touch_volume) if touch_volume else posted_contracts
        row["settlement"] = {"settled": False, "result": None, "pnlBeforeMakerFees": None,
                             "makerFeeMultiplier": maker_multiplier(fee_type, fee_multiplier_value),
                             "note": "touch prints only; a REST snapshot cannot prove FIFO queue position"}
    else:
        # Prints exist but none reached our price (all better for the counterparty than our bid
        # means they never sold down to us): no fill evidence at all.
        row["fillEvidence"] = "no_fill_evidence"
        row["filledContracts"] = 0.0
        row["settlement"] = {"settled": False, "result": None, "pnlBeforeMakerFees": None,
                             "makerFeeMultiplier": maker_multiplier(fee_type, fee_multiplier_value),
                             "makerFee": (maker_fee(posted_price, posted_contracts,
                                                    maker_multiplier(fee_type, fee_multiplier_value))
                                          if posted_price is not None else None)}
    return row


def load_series_fees(path: str | None = None) -> dict:
    """Series records with their official fee_type / fee_multiplier (GET /series evidence).

    Missing or unreadable file -> {} and every series is modelled with maker multiplier 0, i.e.
    the conservative default of the published fee schedule.  Never a guessed fee type.
    """
    path = path or os.path.join(os.path.dirname(__file__), "..", "data", "universe", "series-index.json")
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    return payload.get("series") or {}


def summarize(rows: list[dict], generated_at: str) -> dict:
    compared = [r for r in rows if r.get("status") == "compared"]
    proven = [r for r in compared if r.get("fillEvidence") == "tape_traded_through"]
    uncertain = [r for r in compared if r.get("fillEvidence") == "queue_uncertain"]
    no_evidence = [r for r in compared if r.get("fillEvidence") == "no_fill_evidence"]
    failed = [r for r in rows if r.get("status") == "tape_fetch_failed"]
    settled = [r for r in proven if (r.get("settlement") or {}).get("settled")]
    pnls = [(r["settlement"]["pnlBeforeMakerFees"]) for r in settled
            if r["settlement"].get("pnlBeforeMakerFees") is not None]
    nets = [r["settlement"]["pnlNetOfMakerFees"] for r in settled
            if r["settlement"].get("pnlNetOfMakerFees") is not None]
    fees = [r["settlement"]["makerFee"] for r in settled
            if r["settlement"].get("makerFee") is not None]
    wins = sum(1 for r in settled if (r["settlement"] or {}).get("won"))
    verdict = (f"{len(proven)} of {len(compared)} compared quote plan(s) have tape proof of a fill "
               f"(the tape traded strictly through the posted price); {len(uncertain)} more have "
               f"touch-only prints and stay queue-uncertain; {len(no_evidence)} have no print at or "
               f"through the posted price. "
               + (f"Of the proven fills, {len(settled)} settled so far: {wins} win(s), "
                  f"projected settlement PnL {sum(pnls):+.2f} USD gross and {sum(nets):+.2f} USD net "
                  f"of modelled maker fees ({sum(fees):.2f} USD). "
                  if settled else "No proven fill has settled yet. ")
               + "All figures are MODELLED evidence about hypothetical resting orders.")
    return {
        "schemaVersion": 1, "generatedAt": generated_at, "modelLabel": MODEL_LABEL,
        "plans": len(rows), "compared": len(compared), "tapeTradedThrough": len(proven),
        "queueUncertain": len(uncertain), "noFillEvidence": len(no_evidence),
        "tapeFetchFailed": len(failed),
        "settledProvenFills": len(settled), "settledWins": wins,
        "projectedPnlBeforeMakerFees": round(sum(pnls), 6) if pnls else None,
        "projectedMakerFees": round(sum(fees), 6) if fees else None,
        "projectedPnlNetOfMakerFees": round(sum(nets), 6) if nets else None,
        "makerFeesModelled": True,
        "makerFeeSource": "Kalshi fee schedule (July 2026 update) - https://kalshi.com/docs/kalshi-fee-schedule.pdf",
        "makerFeeNote": "Maker fees follow the official schedule: round_up(M x 0.0175 x C x P x "
                        "(1-P)) for a resting order that ultimately executes, with M = the series' "
                        "maker multiplier (0 for a series whose GET /series fee_type carries no "
                        "maker fees, i.e. plain 'quadratic'; the series' recorded multiplier "
                        "otherwise). IRR-41 closed 2026-09-22; gross and net numbers are both "
                        "reported so historical runs stay comparable.",
        "rows": rows, "verdict": verdict,
        "method": "Posted quote plans (forward/intents status=quote_plan) matched against "
                  "GET /markets/trades?ticker=&min_ts=&max_ts= after each plan's post time. "
                  "tape_traded_through requires a strictly better counterparty print (price "
                  "priority proof); touch prints are queue-uncertain and never counted as fills.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixtures", help="directory of recorded responses (offline replay)")
    parser.add_argument("--forward-dir", help="season forward directory (default: data/season-2026/forward)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help="seconds a plan is assumed to rest (or until market close)")
    parser.add_argument("--limit", type=int, default=100, help="maximum plans to compare in one run")
    parser.add_argument("--series-index", help="series-index.json holding fee_type per series "
                                               "(default: data/universe/series-index.json)")
    args = parser.parse_args(argv)
    if not args.live and not args.fixtures:
        parser.error("choose --live or --fixtures DIR")
    forward_dir = args.forward_dir or DEFAULT_FORWARD_DIR
    if args.forward_dir:
        set_paths(forward_dir=args.forward_dir)
    if args.fixtures:
        client = FixtureClient(json.load(open(os.path.join(args.fixtures, "kalshi.json"))))
    else:
        client = KalshiClient()
    plans = read_quote_plans(forward_dir)[-args.limit:]
    results = load_results(forward_dir)
    fee_records = load_series_fees(args.series_index)
    rows = [compare(plan, client, args.window, results, fee_records) for plan in plans]
    day = datetime_day()
    out_dir = os.path.join(forward_dir, "execution")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"maker-{day}.jsonl"), "a") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    summary = summarize(rows, iso(int(time.time())))
    with open(os.path.join(out_dir, "maker-model.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    return 0


def datetime_day() -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(time.time()), tz=timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    sys.exit(main())
