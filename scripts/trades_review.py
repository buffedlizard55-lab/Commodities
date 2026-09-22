#!/usr/bin/env python3
"""Trades review: every placed trade and every upcoming trade, in one readable committed file.

The request this serves is exact: "We should be able to read and review all placed trades and all
upcoming trades the strategies want to place."  This script re-renders the append-only ledger into

    forward/trades-review.md     readable review (this is the manual-review artifact)
    forward/trades-review.json  the same content, machine-readable

Section 1 walks `trades.jsonl` and joins each fill with its later exit/settlement by positionId:
entry date/price/limit, exit date/price/type or "open", contracts requested/filled/unfilled, fees,
slippage, PnL, the evidence SHA-256 and the ledger line anchors.  Section 2 walks every intent row
whose status means the strategy still WANTS the trade (queued / proposed / quote_plan) or wanted it
and could not place it (not_confirmed_on_book / no_book / no_liquidity_or_cash /
skipped_position_cap): contract, side, intended price, close date, rule reason and current status.
Section 3 is the SpreadSmith quote-plan book (maker plans, explicitly not fills).

Nothing is invented: a missing exit is "open", a missing result is "unsettled".  Regenerate with
`python3 scripts/trades_review.py`; the collector calls it after every cycle so the file on the
site is never stale.

Usage:
  python3 scripts/trades_review.py                  # newest committed season
  python3 scripts/trades_review.py --season 2026
  python3 scripts/trades_review.py --forward-dir data/season-2026/forward
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import forward_desk as FD  # noqa: E402

UPCOMING_STATUSES = ("queued", "proposed", "quote_plan", "not_confirmed_on_book", "no_book",
                     "no_liquidity_or_cash", "skipped_position_cap")


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def money(value, digits: int = 2) -> str:
    return "—" if value is None else f"${value:,.{digits}f}"


def price(value) -> str:
    return "—" if value is None else f"${value:.4f}"


def pct(value) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def cell(value) -> str:
    """Keep free text from breaking markdown tables."""
    return "—" if value is None else str(value).replace("|", "/").replace("\n", " ")


def build_review(forward_dir: str) -> dict:
    events = read_jsonl(os.path.join(forward_dir, "trades.jsonl"))
    intents: list[dict] = []
    intent_dir = os.path.join(forward_dir, "intents")
    for name in sorted(os.listdir(intent_dir)) if os.path.isdir(intent_dir) else []:
        if name.endswith(".jsonl"):
            for row in read_jsonl(os.path.join(intent_dir, name)):
                row["_file"] = os.path.join("intents", name)
                intents.append(row)

    fills = [e for e in events if e.get("kind") == "fill"]
    closes: dict[str, dict] = {}
    for event in events:
        if event.get("kind") in ("exit", "settlement") and event.get("positionId"):
            closes[event["positionId"]] = event

    placed = []
    for fill in fills:
        close = closes.get(fill.get("positionId"))
        placed.append({
            "positionId": fill.get("positionId"),
            "username": fill.get("username"), "strategyId": fill.get("strategyId"),
            "ticker": fill.get("ticker"), "title": fill.get("title"), "series": fill.get("series"),
            "side": fill.get("side"),
            "requestedContracts": fill.get("requestedContracts"),
            "contracts": fill.get("contracts"), "unfilledContracts": fill.get("unfilledContracts"),
            "entryPrice": fill.get("entryPrice"), "entryTouch": fill.get("entryTouch"),
            "limitPrice": fill.get("limitPrice"), "entryAt": fill.get("entryAt"),
            "closeTime": fill.get("closeTime"),
            "entryFee": fill.get("entryFee"), "slippageEntry": fill.get("slippageEntry"),
            "exitPrice": close.get("exitPrice") if close else None,
            "exitAt": close.get("exitAt") if close else None,
            "exitType": close.get("exitType") if close else None,
            "exitReason": close.get("exitReason") if close else None,
            "result": close.get("result") if close and close.get("kind") == "settlement" else None,
            "exitFee": close.get("exitFee") if close and close.get("kind") == "exit" else None,
            "feesTotal": close.get("feesTotal") if close else fill.get("entryFee"),
            "pnl": close.get("pnl") if close else None,
            "status": (close.get("kind") if close else "open"),
            "slippageExit": close.get("slippageExit") if close else None,
            "reason": fill.get("reason"),
            "evidenceSha256": (fill.get("evidence") or {}).get("sha256"),
            "ledgerFile": fill.get("ledgerFile"), "ledgerLine": fill.get("ledgerLine"),
        })

    upcoming = [{
        "at": intent.get("at"), "cycle": intent.get("cycle"),
        "username": intent.get("username"), "strategyId": intent.get("strategyId"),
        "ticker": intent.get("ticker"), "title": intent.get("title"), "series": intent.get("series"),
        "side": intent.get("side"), "quotePrice": intent.get("quotePrice"), "limit": intent.get("limit"),
        "closeTime": intent.get("closeTime"), "volume": intent.get("volume"),
        "reason": intent.get("reason"), "status": intent.get("status"),
        "isQuotePlan": intent.get("status") == "quote_plan",
        "postedPrice": intent.get("postedPrice"), "postedContracts": intent.get("postedContracts"),
        "bookQuotes": intent.get("bookQuotes"),
        "ledgerFile": intent.get("_file") or intent.get("ledgerFile"), "ledgerLine": intent.get("ledgerLine"),
    } for intent in intents if intent.get("status") in UPCOMING_STATUSES]

    quote_plans = [row for row in upcoming if row["isQuotePlan"]]
    totals = {
        "events": len(events),
        "fills": len(fills),
        "exits": sum(1 for e in events if e.get("kind") == "exit"),
        "settlements": sum(1 for e in events if e.get("kind") == "settlement"),
        "openPositions": sum(1 for p in placed if p["status"] == "open"),
        "realizedPnl": round(sum(p["pnl"] or 0.0 for p in placed if p["pnl"] is not None), 4),
        "feesPaid": round(sum(p["feesTotal"] or 0.0 for p in placed), 4),
        "slippage": round(sum((e.get("slippageEntry") or 0.0) + (e.get("slippageExit") or 0.0)
                              for e in events if e.get("kind") in ("fill", "exit")), 4),
        "upcomingTotal": len(upcoming),
        "upcomingByStatus": dict(Counter(row["status"] for row in upcoming)),
        "quotePlans": len(quote_plans),
    }
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forwardDir": os.path.relpath(forward_dir, FD.ROOT),
        "totals": totals,
        "placed": placed,
        "upcoming": upcoming,
        "method": "Rendered from forward/trades.jsonl and forward/intents/*.jsonl (append-only, "
                  "evidence-bound). No row is synthesized; open/unsettled states are stated as such.",
    }


def render_markdown(review: dict) -> str:
    totals = review["totals"]
    lines = [
        "# Trades review — every placed trade and every upcoming trade",
        "",
        f"Generated `{review['generatedAt']}` from `{review['forwardDir']}/trades.jsonl` and "
        f"`{review['forwardDir']}/intents/*.jsonl`. Regenerate with `python3 scripts/trades_review.py`.",
        "",
        f"**Totals:** {totals['fills']} fills · {totals['exits']} exits · {totals['settlements']} settlements · "
        f"{totals['openPositions']} open · realized PnL {money(totals['realizedPnl'])} · "
        f"fees {money(totals['feesPaid'], 4)} · slippage {money(totals['slippage'], 4)} · "
        f"{totals['upcomingTotal']} upcoming/wanted rows (statuses: "
        f"{', '.join(f'{k}={v}' for k, v in sorted(totals['upcomingByStatus'].items())) or 'none'}) · "
        f"{totals['quotePlans']} maker quote plans (modelled, not fills).",
        "",
        "## 1. Placed trades (verified fills against captured order books)",
        "",
        "| # | Username | Contract | Side | Size (filled / requested) | Entry (price · date · limit) | Exit (price · date · type) | Result | Fees · slippage | PnL | Evidence SHA-256 (first 12) | Ledger |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, trade in enumerate(review["placed"], 1):
        exit_cell = (f"{price(trade['exitPrice'])} · {trade['exitAt'] or '—'} · {trade['exitType'] or '—'}"
                     if trade["exitAt"] else "**open** (marked each cycle)")
        size = f"{trade['contracts']:,} / {trade['requestedContracts'] or 0:,}"
        if trade.get("unfilledContracts"):
            size += f" ({trade['unfilledContracts']:,} unfilled: depth/limit)"
        fees = f"{money(trade['feesTotal'], 4)} · {money((trade['slippageEntry'] or 0.0) + (trade['slippageExit'] or 0.0), 4)}"
        sha = (trade.get("evidenceSha256") or "—")
        sha = sha[:12] if isinstance(sha, str) else "—"
        ledger = f"{trade.get('ledgerFile') or 'trades.jsonl'}:{trade.get('ledgerLine')}"
        lines.append(
            f"| {index} | @{trade['username']} | {trade['ticker']} | {str(trade['side']).upper()} | {size} | "
            f"{price(trade['entryPrice'])} · {trade['entryAt']} · {price(trade['limitPrice'])} | {exit_cell} | "
            f"{str(trade['result'] or trade['status']).upper()} | {fees} | {money(trade['pnl'], 4)} | `{sha}` | {ledger} |")
    if not review["placed"]:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | no fill recorded yet |")

    lines += [
        "",
        "## 2. Upcoming trades (what the strategies still want to place, and what they wanted and could not)",
        "",
        "| Observed | Username | Contract | Side · intended price | Closes (UTC) | Status | Reason | Ledger |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in review["upcoming"]:
        if row["isQuotePlan"]:
            continue  # section 3
        price_cell = f"{str(row['side']).upper()} @ {price(row['quotePrice'])} (limit {price(row['limit'])})"
        ledger = f"{row.get('ledgerFile') or '—'}:{row.get('ledgerLine')}"
        lines.append(
            f"| {row['at']} | @{row['username']} | {row['ticker']} | {price_cell} | {row['closeTime'] or '—'} | "
            f"{row['status']} | {cell(row['reason'])} | {ledger} |")

    lines += [
        "",
        "## 3. SpreadSmith maker quote plans (MODELLED — plans, never fills)",
        "",
        "| Posted | Contract | Side | Posted price · size | Spread at post | Closes (UTC) | Reason | Ledger |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in [r for r in review["upcoming"] if r.get("isQuotePlan")]:
        ledger = f"{row.get('ledgerFile') or '—'}:{row.get('ledgerLine')}"
        lines.append(
            f"| {row['at']} | {row['ticker']} | {str(row['side']).upper()} | "
            f"{price(row.get('postedPrice'))} × {row.get('postedContracts') or 0:,} | "
            f"{row.get('bookQuotes') and 'see bookQuotes' or '—'} | {row['closeTime'] or '—'} | "
            f"{cell(row['reason'])} | {ledger} |")
    if not [r for r in review["upcoming"] if r.get("isQuotePlan")]:
        lines.append("| — | — | — | — | — | — | no quote plan recorded yet | — |")

    lines += [
        "",
        "---",
        f"Method: {review['method']}",
        "",
    ]
    return "\n".join(lines)


def write_review(forward_dir: str) -> dict:
    review = build_review(forward_dir)
    FD.write_json(os.path.join(forward_dir, "trades-review.json"), review, compact=False)
    with open(os.path.join(forward_dir, "trades-review.md"), "w") as fh:
        fh.write(render_markdown(review))
    return review


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", help="season to render (default: the newest data/season-*)")
    parser.add_argument("--forward-dir", help="explicit forward directory (overrides --season)")
    args = parser.parse_args(argv)
    if args.forward_dir:
        forward_dir = args.forward_dir
    else:
        seasons = sorted(d.replace("season-", "") for d in os.listdir(FD.DATA_DIR)
                         if d.startswith("season-") and os.path.isdir(os.path.join(FD.DATA_DIR, d)))
        season = args.season or (seasons[-1] if seasons else None)
        if not season:
            print("no data/season-* directory found")
            return 1
        forward_dir = os.path.join(FD.DATA_DIR, f"season-{season}", "forward")
    if not os.path.isdir(forward_dir):
        print(f"no forward ledger at {forward_dir}")
        return 1
    review = write_review(forward_dir)
    print(json.dumps({"placed": len(review["placed"]), "upcoming": len(review["upcoming"]),
                      "quotePlans": review["totals"]["quotePlans"], "totals": review["totals"],
                      "wrote": [os.path.join(review["forwardDir"], "trades-review.md"),
                                os.path.join(review["forwardDir"], "trades-review.json")]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
