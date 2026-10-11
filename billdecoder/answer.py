"""Level 2: plan -> compute (Python) -> write -> verify numbers -> (optional) repair."""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from .calc import CalcError, evaluate
from .facts import build_facts
from .llm import GeminiClient

log = logging.getLogger(__name__)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"plans": {"type": "array", "items": {"type": "object", "properties": {
        "question_id": {"type": "string"},
        "interpretation": {"type": "string", "description": "the meaning used; mention alternatives if ambiguous"},
        "expressions": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "expr": {"type": "string"}}, "required": ["name", "expr"]}},
        "missing_info": {"type": ["string", "null"]},
    }, "required": ["question_id", "interpretation", "expressions"]}}},
    "required": ["plans"],
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {"type": "object", "properties": {
        "question_id": {"type": "string"}, "answer": {"type": "string"}},
        "required": ["question_id", "answer"]}}},
    "required": ["answers"],
}

SYSTEM = """You are a friendly, precise assistant that explains a Pakistani electricity bill to its customer in plain English.
Ground every statement in the provided bill facts. Never use outside tariff/rate knowledge. Never include personal
identifiers. Use PKR with thousands separators (e.g. PKR 3,430.24)."""

PLAN_PROMPT = """Bill facts (extracted from the bill image; amounts in PKR; negative = credit):
{facts}

Customer questions:
{questions}

For EACH question, plan the calculations needed. Write arithmetic expressions over the calculator variables
(names listed in facts.calculator_variables and facts.precomputed). Allowed functions: sum, mean, median, min, max,
len, abs, round, pct(a,b)=a/b*100, pct_change(new,old), count_above(list,t), count_at_least(list,t),
count_below(list,t), last(list,n), first(list,n), and month listers months_above(t), months_at_least(t),
months_below(t) which return the previous-history months matching a units threshold as 'Month YYYY (units)'.
Calendar-month selectors (month numbers 1-12): units_for_months([6,7,8]) and months_for([6,7,8]) over previous
history; units_for_months_incl_current([...]) / months_for_incl_current([...]) also include the current month.
For any question that counts or lists months, ALWAYS use the count_* and months_* functions (do not list by eye).
For seasonal comparisons use e.g. mean(units_for_months([6,7,8])) - mean(units_for_months([12,1,2])) and also
list months_for(...) so the answer can name them. For estimates/projections compute them explicitly (e.g. mean
of last 12 months, same month last year, recent 3-month trend, last month's units x current cost per unit).
If the history includes a month twice in different years, note it. Lists: hist_units (previous months, oldest first),
hist_bills, hist_payments, all_units_incl_current, late_amounts. You may use number literals read from the facts.
If something needed is not on the bill, set missing_info. If the question is ambiguous, say so in interpretation
and plan expressions for each reasonable meaning (e.g. with and without the current month)."""

WRITE_PROMPT = """Bill facts:
{facts}

Questions with the plan and the calculator results (computed by code; trust these numbers):
{work}

Write the final answer for every question, like a helpful utility-company agent:
- Yes/No questions: start with the correct 'Yes' or 'No' that matches the numbers (e.g. fewer units than last
  year -> 'No').
- Fuel adjustment month: look in charge labels (e.g. 'FCA :Feb-26'), other_printed_values and notes, and ALWAYS
  state the month in words (e.g. 'FEB-26' -> February 2026, 'جون-26' -> June 2026). Only if no month appears
  anywhere in the facts, say the bill does not show which month it is for.
- Answer directly in the first sentence, then give the supporting breakdown (2 to 4 sentences in total).
- Use specific numbers from the facts/results only (no other arithmetic of your own).
- 'How much / what share' questions: give the amount AND the percentage, plus any split printed on the bill
  (facts.printed_percentages), and name the line items.
- Late payment: mention every late-payment tier printed and the extra cost of each.
- Counts over the history: use the counts and month lists from the calculator results EXACTLY (never add or
  drop months yourself); list the matching months with their values and state whether the current month is
  included (answer both ways if it changes the result).
- Money: 'PKR 3,430.24' (drop '.00'/'.0' for whole amounts). Credits: say 'a credit of PKR 0.53'.
- Net metering: negative units/amounts in the history mean more energy was exported than imported (credit); say so.
- Never mention internal variable/field names (e.g. payable_after, hist_units); describe things as printed on the bill.
- When something is printed as text (e.g. 'NOT TO BE PAID'), quote it from facts.
- Connection type: decide net-metered vs standard from facts.connection_type_evidence, meter_rows labels,
  negative history units, tariff/customer type; cite the evidence. If none, say it looks like a standard connection.
- Payments: use facts.payment_record; a missing payment value may mean not printed rather than unpaid - say so.
- Cite the relevant line items/months by name. Use month names (e.g. July 2025).
- If a value is an estimate/projection, start with 'Estimate:' and explain how it was calculated.
- If the question can be read more than one way, state the interpretation used or answer both.
- If the bill does not show the needed information, clearly say it is not shown on the bill (do not guess).
- English only, even if the bill has Urdu. No names, account, reference, consumer or meter numbers."""

REPAIR_PROMPT = """Some numbers or month names in your answers could not be traced to the bill facts or calculator results:
{issues}
Rewrite ONLY those answers so that every number comes from the facts or results (or remove it).
Bill facts: {facts}
Calculator results: {work}
Original answers: {answers}"""


def _numbers_in(obj, acc: set[float]) -> set[float]:
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, acc)
    elif isinstance(obj, str):
        for m in re.findall(r"-?\d[\d,]*\.?\d*", obj):
            try:
                acc.add(float(m.replace(",", "")))
            except ValueError:
                pass
    return acc


MONTH_RE = (r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r" \d{4}\b")


def _month_label(iso: str) -> str:
    return date(int(iso[:4]), int(iso[5:7]), 1).strftime("%B %Y")


def unsupported_numbers(answer: str, allowed: set[float]) -> list[str]:
    bad = []
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", answer):
        try:
            x = float(tok.replace(",", ""))
        except ValueError:
            continue
        if x <= 31 or 1990 <= x <= 2100:  # small counts, day numbers, years
            continue
        if any(abs(abs(x) - abs(a)) <= max(0.51, abs(a) * 0.006) for a in allowed):
            continue
        bad.append(tok)
    return bad


def answer_questions(client: GeminiClient, bill_id: str, record: dict, questions: list[tuple[str, str]]) -> dict:
    """questions: [(question_id, question_text)] -> {'answers': {qid: text}, 'audit': {...}}"""
    facts, variables = build_facts(record)
    facts_json = json.dumps(facts, ensure_ascii=False, default=str)
    q_text = "\n".join(f"{qid}: {q}" for qid, q in questions)

    plans = {}
    try:
        plan = client.generate_json([PLAN_PROMPT.format(facts=facts_json, questions=q_text)], PLAN_SCHEMA,
                                    system=SYSTEM, tag=f"{bill_id}/L2-plan")
        plans = {p["question_id"]: p for p in plan.get("plans", [])}
    except Exception as e:
        log.error("%s plan step failed: %s", bill_id, e)

    work = []
    for qid, q in questions:
        p = plans.get(qid, {})
        results = []
        for ex in p.get("expressions", [])[:8]:
            try:
                results.append({"name": ex["name"], "expr": ex["expr"], "value": evaluate(ex["expr"], variables)})
            except CalcError as ce:
                results.append({"name": ex["name"], "expr": ex["expr"], "error": str(ce)})
                continue
            # Counting months? Attach the exact matching months so the writer never lists them by eye.
            for kind, t in re.findall(r"count_(above|at_least|below)\(\s*hist_units\s*,\s*([\d.]+)\s*\)", ex["expr"]):
                expr = f"months_{kind}({t})"
                if not any(r.get("expr") == expr for r in results):
                    results.append({"name": f"matching_months_{kind}_{t}", "expr": expr,
                                    "value": evaluate(expr, variables)})
        work.append({"question_id": qid, "question": q, "interpretation": p.get("interpretation"),
                     "missing_info": p.get("missing_info"), "results": results})
    work_json = json.dumps(work, ensure_ascii=False, default=str)

    answers: dict[str, str] = {}
    try:
        out = client.generate_json([WRITE_PROMPT.format(facts=facts_json, work=work_json)], ANSWER_SCHEMA,
                                   system=SYSTEM, tag=f"{bill_id}/L2-write")
        answers = {a["question_id"]: a["answer"].strip() for a in out.get("answers", []) if a.get("answer")}
    except Exception as e:
        log.error("%s write step failed: %s", bill_id, e)

    # Verify: every number in an answer must be traceable to facts, results or the question.
    allowed = _numbers_in(facts, set())
    _numbers_in(work, allowed)
    known_months = {h["month_name"] for h in facts["history_previous_months"]}
    for key in ("bill_month", "reading_date", "issue_date", "due_date"):
        if facts.get(key):
            known_months.add(_month_label(facts[key]))
    issues = {}
    for qid, q in questions:
        if qid in answers:
            bad = unsupported_numbers(answers[qid], allowed | _numbers_in(q, set()))
            bad += [m for m in re.findall(MONTH_RE, answers[qid]) if m not in known_months]
            if bad:
                issues[qid] = bad
    repaired = []
    if issues:
        try:
            fix = client.generate_json([REPAIR_PROMPT.format(
                issues=json.dumps(issues), facts=facts_json, work=work_json,
                answers=json.dumps({k: answers[k] for k in issues}, ensure_ascii=False))],
                ANSWER_SCHEMA, system=SYSTEM, tag=f"{bill_id}/L2-repair")
            for a in fix.get("answers", []):
                if a.get("question_id") in issues and a.get("answer"):
                    answers[a["question_id"]] = a["answer"].strip()
                    repaired.append(a["question_id"])
        except Exception as e:
            log.error("%s repair step failed: %s", bill_id, e)

    for qid in answers:  # tidy '267.0' / '3,430.00' -> '267' / '3,430'
        answers[qid] = re.sub(r"(\d)\.0{1,2}(?!\d)", lambda m: m.group(1), answers[qid])
    for qid, _ in questions:  # never leave an answer cell empty
        if not answers.get(qid):
            answers[qid] = ("I could not reliably determine this from the bill; the information needed is not "
                            "clearly shown on the bill.")
    return {"answers": answers, "audit": {"work": work, "unverified_numbers": issues, "repaired": repaired}}
