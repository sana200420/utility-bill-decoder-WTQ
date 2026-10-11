"""Pre-submission validator: checks the generated CSVs against the templates and the Level 1 contract.

    python check_outputs.py --templates ../test/csv --out ../output
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from billdecoder.normalize import L1_FIELDS
from billdecoder.prompts import CHARGE_TYPES, TAX_TYPES

NUMERIC = {"sanctioned_load_kw", "previous_reading", "current_reading", "units_consumed", "total_charges",
           "total_taxes", "current_bill", "arrears", "payable_within_due_date", "payable_after_due_date"}
DATES = {"reading_date", "issue_date", "due_date"}


def rows(p: Path):
    with open(p, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t, o = Path(a.templates), Path(a.out)
    errors: list[str] = []

    for name, keep in (("level1.csv", ["bill_id"]), ("level2.csv", ["bill_id", "question_id", "question"])):
        tf, tr = rows(t / name)
        of, orows = rows(o / name)
        if tf != of:
            errors.append(f"{name}: columns {of} != template {tf}")
        if len(tr) != len(orows):
            errors.append(f"{name}: {len(orows)} rows != template {len(tr)}")
        for i, (x, y) in enumerate(zip(tr, orows)):
            for k in keep:
                if x[k] != y[k]:
                    errors.append(f"{name} row {i + 2}: {k} changed")
            val = y.get("json" if name == "level1.csv" else "answer", "")
            if not val.strip():
                errors.append(f"{name} row {i + 2}: empty output cell")
        print(f"{name}: {len(orows)} rows checked")

    _, l1 = rows(o / "level1.csv")
    for r in l1:
        try:
            d = json.loads(r["json"])
        except json.JSONDecodeError as e:
            errors.append(f"{r['bill_id']}: invalid JSON ({e})")
            continue
        if list(d) != L1_FIELDS:
            errors.append(f"{r['bill_id']}: fields/order mismatch {set(L1_FIELDS) ^ set(d)}")
        for k in NUMERIC:
            if d.get(k) is not None and not isinstance(d[k], (int, float)):
                errors.append(f"{r['bill_id']}: {k} is not a number")
        for k in DATES:
            if d.get(k) is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d[k]):
                errors.append(f"{r['bill_id']}: {k} bad date {d[k]}")
        if d.get("bill_month") is not None and not re.fullmatch(r"\d{4}-\d{2}", d["bill_month"]):
            errors.append(f"{r['bill_id']}: bad bill_month")
        if d.get("provider") not in ("KE", "LESCO", "IESCO"):
            errors.append(f"{r['bill_id']}: bad provider")
        for c in d.get("charges", []):
            if c.get("type") not in CHARGE_TYPES or not isinstance(c.get("amount"), (int, float)):
                errors.append(f"{r['bill_id']}: bad charge {c}")
        for c in d.get("taxes", []):
            if c.get("type") not in TAX_TYPES or not isinstance(c.get("amount"), (int, float)):
                errors.append(f"{r['bill_id']}: bad tax {c}")
        nulls = [k for k in L1_FIELDS if d.get(k) is None]
        print(f"  {r['bill_id']}: ok{' (null: ' + ', '.join(nulls) + ')' if nulls else ''}")

    if errors:
        print("\nPROBLEMS:")
        for e in errors:
            print(" -", e)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
