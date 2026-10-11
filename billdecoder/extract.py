"""Level 1 orchestration: multi-view vision passes, field-level voting, check-guided arbitration."""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from .llm import GeminiClient
from .normalize import L1_FIELDS, normalize, provider_from_bill_id, to_level1
from .preprocess import prepare_views
from .prompts import EXTRACTION_SCHEMA, FOCUS_PROMPT, RECHECK_PROMPT, SYSTEM_EXTRACT, extraction_prompt
from .validate import confusable_digit_repair, run_checks, score

log = logging.getLogger(__name__)


def _key(v):
    """Hashable comparison key for a field value (lists compared by type+amount)."""
    if isinstance(v, list):
        return json.dumps([(x["type"], x["amount"]) for x in v])
    return json.dumps(v)


def _vote(candidates: list[dict], scores: list[float]) -> tuple[dict, list[str]]:
    """Per field: majority value; ties broken by the candidate whose arithmetic checks score best."""
    order = sorted(range(len(candidates)), key=lambda i: -scores[i])
    final, disputed = {}, []
    for f in L1_FIELDS:
        keys = [_key(c.get(f)) for c in candidates]
        counts = Counter(keys)
        top, n = counts.most_common(1)[0]
        if len(counts) > 1:
            disputed.append(f)
        if n > len(candidates) / 2 or len(counts) == 1:
            winner = keys.index(top)
        else:
            winner = order[0]
            # prefer a non-null reading from the best-scoring candidate
            if candidates[winner].get(f) is None:
                winner = next((i for i in order if candidates[i].get(f) is not None), winner)
        final[f] = candidates[winner].get(f)
    best = candidates[order[0]]
    final["_extras"] = best["_extras"]
    return final, disputed


SUM_CHECKS = {"charges_sum", "taxes_sum", "bill_total", "payable"}


def _arbitrate(final: dict, candidates: list[dict], disputed: list[str]) -> tuple[dict, list]:
    """If an alternative reading of a disputed field makes more arithmetic checks pass, prefer it.

    Only values that some pass actually read from the image are considered, so nothing is invented.
    """
    swaps = []
    for f in disputed:
        best_val, best_score = final.get(f), score(run_checks(final))
        for c in candidates:
            alt = c.get(f)
            if _key(alt) == _key(best_val):
                continue
            trial = {**final, f: alt}
            s = score(run_checks(trial))
            if s > best_score:
                best_val, best_score = alt, s
        if _key(best_val) != _key(final.get(f)):
            swaps.append({"field": f, "from": final.get(f), "to": best_val})
            final = {**final, f: best_val}
    return final, swaps


def extract_bill(client: GeminiClient, image_path: Path) -> dict:
    """Return {'level1': {...}, 'record': {... incl. labels and extras}, 'audit': {...}}."""
    bill_id = image_path.stem
    provider = provider_from_bill_id(bill_id) or "KE"
    views = prepare_views(image_path)

    passes = [
        ("A:original", client.cfg.model, [views["original"]],
         "View: the original bill image."),
        ("B:enhanced+zoom", client.cfg.model, [views["enhanced"], views["crop_top"], views["crop_bottom"]],
         "Views: 1) full enhanced bill, 2) zoomed top half, 3) zoomed bottom half of the SAME bill. "
         "Use the zoomed views to read small digits."),
    ]
    candidates, scores, raws, names = [], [], [], []
    for name, model, imgs, note in passes:
        try:
            raw = client.generate_json([*imgs, extraction_prompt(provider, note)], EXTRACTION_SCHEMA,
                                       model=model, system=SYSTEM_EXTRACT, tag=f"{bill_id}/{name}")
        except Exception as e:
            log.error("%s pass %s failed: %s", bill_id, name, e)
            continue
        rec = normalize(raw, bill_id)
        candidates.append(rec)
        scores.append(score(run_checks(rec)))
        raws.append(raw)
        names.append(name)

    if not candidates:
        raise RuntimeError(f"No extraction pass succeeded for {bill_id}")

    _, disputed = _vote(candidates, scores) if len(candidates) > 1 else ({}, [])
    if disputed or len(candidates) < 2:
        # Juror pass: a different allowed model re-reads only the disputed fields.
        fields = disputed or L1_FIELDS
        cand_view = {f: [c.get(f) if f not in ("charges", "taxes") else
                         [(x["type"], x["amount"]) for x in c.get(f) or []] for c in candidates] for f in fields}
        prompt = (extraction_prompt(provider, "Views: full enhanced bill, zoomed top half, zoomed bottom half.") +
                  "\n\n" + FOCUS_PROMPT.format(fields=", ".join(fields), candidates=json.dumps(cand_view)))
        try:
            raw = client.generate_json([views["enhanced"], views["crop_top"], views["crop_bottom"], prompt],
                                       EXTRACTION_SCHEMA, model=client.cfg.fallback_model,
                                       system=SYSTEM_EXTRACT, tag=f"{bill_id}/C:juror")
            rec = normalize(raw, bill_id)
            candidates.append(rec)
            scores.append(score(run_checks(rec)))
            names.append("C:juror")
        except Exception as e:
            log.error("%s juror pass failed: %s", bill_id, e)

    final, still_disputed = _vote(candidates, scores) if len(candidates) > 1 else (candidates[0], [])
    final.setdefault("_extras", candidates[0]["_extras"])
    final, swaps = _arbitrate(final, candidates, still_disputed)
    checks = run_checks(final)

    # Targeted re-read: a failed sum check means some printed value was probably misread.
    failed = [c for c in checks if c["status"] == "fail" and c["check"] in SUM_CHECKS]
    if failed:
        prompt = (extraction_prompt(provider, "Views: full enhanced bill, zoomed top half, zoomed bottom half.") +
                  "\n\n" + RECHECK_PROMPT.format(failures="\n".join(f"- {c['check']}: {c['detail']}" for c in failed)))
        try:
            raw = client.generate_json([views["enhanced"], views["crop_top"], views["crop_bottom"], prompt],
                                       EXTRACTION_SCHEMA, system=SYSTEM_EXTRACT, tag=f"{bill_id}/D:recheck")
            rec = normalize(raw, bill_id)
            names.append("D:recheck")
            final, more = _arbitrate(final, [rec], [f for f in L1_FIELDS if f != "provider"])
            swaps += more
            checks = run_checks(final)
        except Exception as e:
            log.error("%s recheck pass failed: %s", bill_id, e)
        final, fixes = confusable_digit_repair(final)
        swaps += fixes
        checks = run_checks(final)
    agreement = 1 - len(still_disputed) / len(L1_FIELDS)
    audit = {
        "bill_id": bill_id,
        "preprocess": views["meta"],
        "passes": names,
        "pass_scores": scores,
        "disputed_fields_after_pass_B": disputed,
        "fields_without_unanimity": still_disputed,
        "agreement": round(agreement, 3),
        "check_guided_swaps": swaps,
        "checks": checks,
        "charge_labels": [(c.get("label"), c["type"], c["amount"]) for c in final.get("charges") or []],
        "tax_labels": [(t.get("label"), t["type"], t["amount"]) for t in final.get("taxes") or []],
    }
    return {"level1": to_level1(final), "record": final, "audit": audit}


def empty_level1(bill_id: str) -> dict:
    """Schema-valid fallback when a bill cannot be processed at all."""
    rec = {f: None for f in L1_FIELDS}
    rec.update(provider=provider_from_bill_id(bill_id), charges=[], taxes=[])
    return rec
