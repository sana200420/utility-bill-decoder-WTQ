"""Utility Bill Decoder - command line entry point.

Batch mode (generates both competition CSVs from the templates):
    python run.py batch --bills ../test/bills --level1 ../test/csv/level1.csv --level2 ../test/csv/level2.csv --out ../output

Single bill (demo / debugging):
    python run.py bill ../train/bills/KESC_0008.png -q "How much of my bill is taxes?"
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from billdecoder.answer import answer_questions
from billdecoder.config import Config
from billdecoder.extract import empty_level1, extract_bill
from billdecoder.llm import GeminiClient

log = logging.getLogger("billdecoder")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def find_image(bills_dir: Path, bill_id: str) -> Path | None:
    for ext in IMAGE_EXTS:
        for cand in (bills_dir / f"{bill_id}{ext}", bills_dir / f"{bill_id}{ext.upper()}"):
            if cand.exists():
                return cand
    return None


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)


def process_bill(client: GeminiClient, bills_dir: Path, bill_id: str, questions: list[tuple[str, str]],
                 audit_dir: Path) -> tuple[dict, dict]:
    t0 = time.time()
    img = find_image(bills_dir, bill_id)
    audit: dict = {"bill_id": bill_id}
    record = None
    if img is None:
        log.error("%s: image not found in %s", bill_id, bills_dir)
        level1 = empty_level1(bill_id)
    else:
        try:
            res = extract_bill(client, img)
            level1, record, audit["level1"] = res["level1"], res["record"], res["audit"]
        except Exception as e:
            log.exception("%s: extraction failed: %s", bill_id, e)
            level1 = empty_level1(bill_id)
    answers = {}
    if questions:
        try:
            l2 = answer_questions(client, bill_id, record or {**level1, "_extras": {}}, questions)
            answers, audit["level2"] = l2["answers"], l2["audit"]
        except Exception as e:
            log.exception("%s: answering failed: %s", bill_id, e)
    for qid, _ in questions:
        answers.setdefault(qid, "Sorry, this bill could not be read reliably enough to answer this question.")
    audit["seconds"] = round(time.time() - t0, 1)
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"{bill_id}.json").write_text(json.dumps(audit, indent=1, ensure_ascii=False, default=str),
                                               encoding="utf-8")
    checks = audit.get("level1", {}).get("checks", [])
    log.info("%s done in %ss | checks pass=%d fail=%d | agreement=%s", bill_id, audit["seconds"],
             sum(c["status"] == "pass" for c in checks), sum(c["status"] == "fail" for c in checks),
             audit.get("level1", {}).get("agreement"))
    return level1, answers


def cmd_batch(args) -> int:
    client = GeminiClient(Config.from_env())
    bills_dir, out_dir = Path(args.bills), Path(args.out)
    l1_fields, l1_rows = read_csv(Path(args.level1))
    l2_fields, l2_rows = read_csv(Path(args.level2)) if args.level2 else ([], [])

    questions: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for r in l2_rows:
        questions.setdefault(r["bill_id"], []).append((r["question_id"], r["question"]))
    bill_ids = list(OrderedDict.fromkeys([r["bill_id"] for r in l1_rows] + list(questions)))

    log.info("Processing %d bills with %s (fallback %s)", len(bill_ids), client.cfg.model, client.cfg.fallback_model)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {b: pool.submit(process_bill, client, bills_dir, b, questions.get(b, []), Path(args.audit))
                   for b in bill_ids}
        results = {b: f.result() for b, f in futures.items()}

    for r in l1_rows:
        r["json"] = json.dumps(results[r["bill_id"]][0], ensure_ascii=False, separators=(",", ":"))
    write_csv(out_dir / "level1.csv", l1_fields, l1_rows)
    if l2_rows:
        for r in l2_rows:
            r["answer"] = results[r["bill_id"]][1][r["question_id"]]
        write_csv(out_dir / "level2.csv", l2_fields, l2_rows)
    log.info("Wrote %s (API calls this run: %d)", out_dir, client.calls_made)
    return 0


def cmd_bill(args) -> int:
    client = GeminiClient(Config.from_env())
    img = Path(args.image)
    res = extract_bill(client, img)
    print(json.dumps(res["level1"], indent=2, ensure_ascii=False))
    print("\nChecks:")
    for c in res["audit"]["checks"]:
        print(f"  [{c['status']:>4}] {c['check']}: {c['detail']}")
    if args.question:
        qs = [(f"Q{i + 1}", q) for i, q in enumerate(args.question)]
        l2 = answer_questions(client, img.stem, res["record"], qs)
        for qid, q in qs:
            print(f"\n{qid}: {q}\n-> {l2['answers'][qid]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Utility Bill Decoder (KE / LESCO / IESCO)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batch", help="fill level1.csv / level2.csv templates for a folder of bills")
    b.add_argument("--bills", required=True)
    b.add_argument("--level1", required=True)
    b.add_argument("--level2")
    b.add_argument("--out", default="../output")
    b.add_argument("--audit", default="../audit")
    b.add_argument("--workers", type=int, default=3)
    s = sub.add_parser("bill", help="extract one bill and optionally answer questions")
    s.add_argument("image")
    s.add_argument("-q", "--question", action="append")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return cmd_batch(args) if args.cmd == "batch" else cmd_bill(args)
    except ValueError as e:  # configuration problems
        log.error(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
