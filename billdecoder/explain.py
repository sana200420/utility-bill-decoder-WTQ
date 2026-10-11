"""Plain-language bill explanation ("your bill in a nutshell"), grounded in the extracted facts."""
from __future__ import annotations

import json
import logging

from .answer import SYSTEM, _numbers_in, unsupported_numbers
from .facts import build_facts
from .llm import GeminiClient

log = logging.getLogger(__name__)

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "one friendly sentence: what to pay and by when"},
        "points": {"type": "array", "items": {"type": "string"},
                   "description": "4-6 short plain-language bullets explaining the bill"},
        "tips": {"type": "array", "items": {"type": "string"},
                 "description": "1-3 practical tips grounded in this bill (pay on time, usage trend...)"},
    },
    "required": ["headline", "points", "tips"],
}

PROMPT = """Bill facts (amounts in PKR, negative = credit):
{facts}

Explain this electricity bill to the customer as a warm, clear assistant who avoids jargon.
- headline: what they owe (or their credit) and the due date.
- points: where the money goes (energy, fixed, adjustments like FPA/QTA explained in simple words, taxes and
  their share), units used vs their usual usage, any arrears/credit, subsidy, net metering, late-payment cost.
- tips: practical, based only on this bill.
Write credits as 'a credit of PKR 0.53' (never 'PKR -0.53'). Use only numbers present in the facts (precomputed metrics are fine). No personal identifiers. English only."""


def explain_bill(client: GeminiClient, bill_id: str, record: dict) -> dict:
    facts, _ = build_facts(record)
    out = client.generate_json([PROMPT.format(facts=json.dumps(facts, ensure_ascii=False, default=str))],
                               EXPLAIN_SCHEMA, system=SYSTEM, tag=f"{bill_id}/explain")
    allowed = _numbers_in(facts, set())
    # Drop any bullet whose numbers cannot be traced to the bill (same verifier as Level 2).
    for key in ("points", "tips"):
        kept = [p for p in out.get(key, []) if not unsupported_numbers(p, allowed)]
        if len(kept) < len(out.get(key, [])):
            log.info("%s: dropped %d unverified %s", bill_id, len(out[key]) - len(kept), key)
        out[key] = kept
    return out
