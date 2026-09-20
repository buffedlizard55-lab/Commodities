#!/usr/bin/env python3
"""Storage compaction for the forward-desk ledger (closes IRR-24).

The desk appends 10-30 KB per cycle; a full year at 48 cycles/day is hundreds of MB in git and in
the Pages artifact.  This job gzips the *dated* evidence and quote files once they are older than
N days and records, in `forward/COMPRESSED.json`, everything needed to prove nothing changed:

    originalSha256  sha256 of the file contents before compression (the same bytes the ledger's
                    evidence rows reference)
    gzSha256        sha256 of the .gz file that replaced it
    lines / bytes   line and byte counts, so a restore can be checked
    compactedAt     when the job ran

Nothing is ever dropped: `--restore` puts the original bytes back and `--check` re-verifies every
entry (gzip hash + decompressed hash + line count).  trades.jsonl, intents/, cycles/, equity/ and
candles/ are append-only analysis files and are deliberately NOT compacted.

Usage:
  python3 scripts/compact_storage.py --check
  python3 scripts/compact_storage.py --days 30 --apply
  python3 scripts/compact_storage.py --restore evidence/2026-09-20.jsonl
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(ROOT, "data")
COMPACTABLE = ("evidence", "quotes")
DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
INDEX_NAME = "COMPRESSED.json"


def iso(ts=None) -> str:
    return datetime.fromtimestamp(ts or int(__import__("time").time()), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def default_forward_dir(now_ts: int | None = None) -> str:
    """data/season-<UTC year of now>/forward (the same rule the desk uses)."""
    now_ts = now_ts or int(__import__("time").time())
    return os.path.join(DATA_DIR, f"season-{datetime.fromtimestamp(now_ts, tz=timezone.utc).year}", "forward")


def load_index(forward_dir: str) -> dict:
    path = os.path.join(forward_dir, INDEX_NAME)
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return {"schemaVersion": 1, "files": {}}


def save_index(forward_dir: str, index: dict) -> None:
    index["updatedAt"] = iso()
    with open(os.path.join(forward_dir, INDEX_NAME), "w") as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
        fh.write("\n")


def file_date(rel: str) -> str | None:
    match = DATE_IN_NAME.search(os.path.basename(rel))
    return match.group(1) if match else None


def candidates(forward_dir: str, older_than_days: int, now_ts: int) -> list[str]:
    """Dated evidence/quote files that are old enough to compact and not already compressed."""
    cutoff = datetime.fromtimestamp(now_ts, tz=timezone.utc).timestamp() - older_than_days * 86400
    out = []
    for sub in COMPACTABLE:
        directory = os.path.join(forward_dir, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not (name.endswith(".jsonl") or name.endswith(".csv")):
                continue
            rel = f"{sub}/{name}"
            day = file_date(rel)
            if not day:
                continue
            try:
                stamp = datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
            if stamp >= cutoff:
                continue
            out.append(rel)
    return out


def compact_file(forward_dir: str, rel: str, index: dict) -> dict:
    path = os.path.join(forward_dir, rel)
    with open(path, "rb") as fh:
        raw = fh.read()
    lines = raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)
    entry = {"originalSha256": sha256_bytes(raw), "bytes": len(raw), "lines": lines,
             "compactedAt": iso(), "compression": "gzip"}
    gz_path = path + ".gz"
    with gzip.open(gz_path, "wb", compresslevel=9) as fh:
        fh.write(raw)
    with open(gz_path, "rb") as fh:
        gz = fh.read()
    entry["gzSha256"] = sha256_bytes(gz)
    entry["gzBytes"] = len(gz)
    os.remove(path)
    index["files"][rel] = entry
    return entry


def read_maybe_compressed(forward_dir: str, rel: str) -> bytes:
    """Original bytes for a ledger file, transparently decompressing when it was compacted.

    Used by scripts/verify_data.py so the evidence-hash binding keeps working after compaction.
    """
    path = os.path.join(forward_dir, rel)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    gz_path = path + ".gz"
    if not os.path.exists(gz_path):
        raise FileNotFoundError(rel)
    with gzip.open(gz_path, "rb") as fh:
        return fh.read()


def restore_file(forward_dir: str, rel: str, index: dict) -> dict:
    entry = index["files"].get(rel)
    if not entry:
        raise KeyError(f"{rel} is not in {INDEX_NAME}")
    raw = read_maybe_compressed(forward_dir, rel)
    if sha256_bytes(raw) != entry["originalSha256"]:
        raise ValueError(f"{rel}: decompressed hash does not match the recorded originalSha256")
    path = os.path.join(forward_dir, rel)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(raw)
    return entry


def check_index(forward_dir: str) -> tuple[int, list[str]]:
    """Verify every compacted file: gzip hash, decompressed hash and line count."""
    index = load_index(forward_dir)
    passed, failures = 0, []
    for rel, entry in sorted(index.get("files", {}).items()):
        gz_path = os.path.join(forward_dir, rel + ".gz")
        if not os.path.exists(gz_path):
            failures.append(f"{rel}: .gz missing")
            continue
        with open(gz_path, "rb") as fh:
            gz = fh.read()
        if sha256_bytes(gz) != entry["gzSha256"]:
            failures.append(f"{rel}: gzSha256 mismatch")
            continue
        try:
            raw = gzip.decompress(gz)
        except OSError as error:
            failures.append(f"{rel}: cannot decompress ({error})")
            continue
        if sha256_bytes(raw) != entry["originalSha256"]:
            failures.append(f"{rel}: originalSha256 mismatch after decompression")
            continue
        if entry.get("bytes") is not None and len(raw) != entry["bytes"]:
            failures.append(f"{rel}: byte count mismatch")
            continue
        lines = raw.count(b"\n") + (1 if raw and not raw.endswith(b"\n") else 0)
        if entry.get("lines") is not None and lines != entry["lines"]:
            failures.append(f"{rel}: line count mismatch ({lines} vs {entry['lines']})")
            continue
        passed += 1
    return passed, failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forward-dir", help="season forward directory (default: the current season)")
    parser.add_argument("--days", type=int, default=30, help="compact files older than this many days")
    parser.add_argument("--apply", action="store_true", help="actually compress (default: report only)")
    parser.add_argument("--check", action="store_true", help="verify every entry in COMPRESSED.json")
    parser.add_argument("--restore", help="restore one compacted file (path relative to the forward dir)")
    parser.add_argument("--now", type=int, help="epoch seconds to use as 'now' (tests)")
    args = parser.parse_args(argv)
    now_ts = args.now or int(__import__("time").time())
    forward_dir = args.forward_dir or default_forward_dir(now_ts)
    if not os.path.isdir(forward_dir):
        print(f"no forward directory at {forward_dir}")
        return 1
    index = load_index(forward_dir)
    if args.check:
        passed, failures = check_index(forward_dir)
        print(f"COMPRESSED.json: {passed} verified, {len(failures)} failed")
        for failure in failures:
            print("  FAIL " + failure)
        return 1 if failures else 0
    if args.restore:
        entry = restore_file(forward_dir, args.restore, index)
        print(json.dumps({"restored": args.restore, "entry": entry}, indent=1))
        return 0
    todo = candidates(forward_dir, args.days, now_ts)
    compacted = []
    if args.apply:
        for rel in todo:
            compacted.append({"file": rel, **compact_file(forward_dir, rel, index)})
        save_index(forward_dir, index)
    saved = sum(c["gzBytes"] for c in compacted)
    original = sum(c["bytes"] for c in compacted)
    print(json.dumps({"forwardDir": os.path.relpath(forward_dir, ROOT), "mode": "apply" if args.apply else "dry-run",
                      "days": args.days, "candidates": [os.path.relpath(os.path.join(forward_dir, r), forward_dir) for r in todo],
                      "compacted": compacted, "bytesBefore": original, "bytesAfter": saved,
                      "ratio": round(saved / original, 4) if original else None,
                      "indexEntries": len(index.get("files", {}))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
