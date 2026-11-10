#!/usr/bin/env python3
"""Derive data/corrected_labels.csv from the frozen validation rows.

The raw rows are never modified. This file only adds an interpretation of the
raw ``integrity_violation`` flag, explained in ERRATA.md: in runs where the
configured fault never fired, the flag records that the contract was not
completed, not that a recovery from the fault failed.

Usage:
  python3 scripts/build_corrected_labels.py           # write the CSV
  python3 scripts/build_corrected_labels.py --check   # fail if the CSV differs
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROWS = ROOT / "artifacts" / "row-evidence" / "validation" / "rows.jsonl"
OUTPUT = ROOT / "data" / "corrected_labels.csv"

COLUMNS = (
    "row_id",
    "aggregate_run_id",
    "replicate_index",
    "scenario_id",
    "expected_fault_count",
    "actual_fault_count",
    "table_valid_run",
    "table_safe_handoff",
    "strict_clean",
    "integrity_violation",
    "corrected_label",
    "counts_as_integrity_failure_after_fault",
)


def corrected_label(row: dict) -> str:
    if row["actual_fault_count"] == 0:
        if row["table_safe_handoff"]:
            return "fault_not_fired_handoff_before_exposure"
        return "fault_not_fired_invalid_exposure"
    if row["integrity_violation"]:
        return "integrity_violation_after_fault"
    return "no_integrity_violation_after_fault"


def render() -> str:
    rows = [json.loads(line) for line in ROWS.read_text(encoding="utf-8").splitlines() if line.strip()]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in sorted(rows, key=lambda item: item["row_id"]):
        label = corrected_label(row)
        writer.writerow(
            [
                row["row_id"],
                row["aggregate_run_id"],
                row["replicate_index"],
                row["scenario_id"],
                row["expected_fault_count"],
                row["actual_fault_count"],
                str(bool(row["table_valid_run"])).lower(),
                str(bool(row["table_safe_handoff"])).lower(),
                str(bool(row["strict_clean"])).lower(),
                str(bool(row["integrity_violation"])).lower(),
                label,
                str(label == "integrity_violation_after_fault").lower(),
            ]
        )
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        ok = OUTPUT.is_file() and OUTPUT.read_text(encoding="utf-8") == text
        print(json.dumps({"ok": ok, "path": OUTPUT.relative_to(ROOT).as_posix()}))
        return 0 if ok else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(json.dumps({"ok": True, "rows": text.count("\n") - 1}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
