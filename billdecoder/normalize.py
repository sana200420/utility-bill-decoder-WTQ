"""Deterministic post-processing: turns raw model readings into the exact Level 1 JSON contract."""
from __future__ import annotations

import re
from datetime import datetime

from .prompts import CHARGE_TYPES, TAX_TYPES

L1_FIELDS = ["provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date", "issue_date", "due_date",
             "previous_reading", "current_reading", "units_consumed", "charges", "total_charges", "taxes",
             "total_taxes", "current_bill", "arrears", "payable_within_due_date", "payable_after_due_date"]

PROVIDER_BY_PREFIX = {"KESC": "KE", "KE": "KE", "LESCO": "LESCO", "IESCO": "IESCO"}


def provider_from_bill_id(bill_id: str) -> str | None:
    return PROVIDER_BY_PREFIX.get(bill_id.split("_")[0].upper())


def _num(x: float) -> int | float:
    x = round(float(x), 2)
    return int(x) if x == int(x) else x


def parse_amount(text) -> int | float | None:
    """'Rs. 19,964' -> 19964, '38209CR' -> -38209, '(12.5)' -> -12.5, 'NOT TO BE PAID' -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return _num(text)
    s = str(text).strip().upper()
    if not s or s in {"-", "--", "N/A", "NA", "NIL"}:
        return None
    credit = bool(re.search(r"\bCR\b|CR$|\(CR\)", s)) or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"\s*/\s*\d+\s*$", "", s)  # LESCO arrears like '39411 / 2' (age count suffix)
    m = re.search(r"-?\s*\d[\d,]*(?:\.\d+)?", s.replace("RS.", "").replace("RS", "").replace("PKR", ""))
    if not m:
        return None
    val = float(m.group(0).replace(",", "").replace(" ", ""))
    if credit and val > 0:
        val = -val
    return _num(val)


_DATE_FORMATS = ["%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y", "%d/%m/%Y", "%d/%m/%y",
                 "%d %B %Y", "%d-%m-%Y", "%d.%m.%Y"]
_MONTH_FORMATS = ["%Y-%m", "%b %Y", "%b-%y", "%b %y", "%B %Y", "%b-%Y", "%m/%y", "%m/%Y"]


def parse_date(text) -> str | None:
    if not text:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(text).strip(), flags=re.I)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in f or "%B" in f else s, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_month(text) -> str | None:
    if not text:
        return None
    s = str(text).strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s[:7]
    for f in _MONTH_FORMATS:
        try:
            return datetime.strptime(s.title(), f).strftime("%Y-%m")
        except ValueError:
            continue
    return None


def _clean_number(v) -> int | float | None:
    if v is None:
        return None
    try:
        return _num(float(v))
    except (TypeError, ValueError):
        return parse_amount(v)


def _lines(items, allowed: list[str], fallback: str) -> list[dict]:
    out = []
    for it in items or []:
        amt = parse_amount(it.get("amount_text", it.get("amount")))
        if amt is None or amt == 0:
            continue  # rule: skip blank lines and lines printed as 0
        t = it.get("type") if it.get("type") in allowed else fallback
        if t == "subsidy":
            amt = -abs(amt)  # rule: subsidies are deductions
        out.append({"type": t, "amount": amt, "label": it.get("label", "")})
    return out


def normalize(raw: dict, bill_id: str) -> dict:
    """Return a dict with the 18 Level 1 fields plus a private '_extras' key used by Level 2 and auditing."""
    provider = provider_from_bill_id(bill_id) or raw.get("provider")
    charges = _lines(raw.get("charges"), CHARGE_TYPES, "other")
    taxes = _lines(raw.get("taxes"), TAX_TYPES, "other_tax")

    late = [a for a in (parse_amount(x.get("amount_text")) for x in raw.get("late_payment_amounts") or [])
            if a is not None]
    tariff = raw.get("tariff")
    out = {
        "provider": provider,
        "tariff": tariff.strip() if isinstance(tariff, str) and tariff.strip() else None,
        "sanctioned_load_kw": _clean_number(raw.get("sanctioned_load_kw")),
        "bill_month": parse_month(raw.get("bill_month")),
        "reading_date": parse_date(raw.get("reading_date")),
        "issue_date": parse_date(raw.get("issue_date")),
        "due_date": parse_date(raw.get("due_date")),
        "previous_reading": _clean_number(raw.get("previous_reading")),
        "current_reading": _clean_number(raw.get("current_reading")),
        "units_consumed": _clean_number(raw.get("units_consumed")),
        "charges": charges,
        "total_charges": parse_amount(raw.get("total_charges_text")),
        "taxes": taxes,
        "total_taxes": parse_amount(raw.get("total_taxes_text")),
        "current_bill": parse_amount(raw.get("current_bill_text")),
        "arrears": parse_amount(raw.get("arrears_text")),
        "payable_within_due_date": parse_amount(raw.get("payable_within_due_date_text")),
        "payable_after_due_date": max(late) if late else None,  # rule: highest late amount
    }
    history = []
    for h in raw.get("usage_history") or []:
        m = parse_month(h.get("month"))
        if m:
            history.append({"month": m, "units": _clean_number(h.get("units")),
                            "bill_amount": _clean_number(h.get("bill_amount")),
                            "payment": _clean_number(h.get("payment"))})
    history.sort(key=lambda r: r["month"])
    out["_extras"] = {
        "late_payment_amounts": [{"label": x.get("label"), "amount": parse_amount(x.get("amount_text"))}
                                 for x in raw.get("late_payment_amounts") or []],
        "late_payment_surcharges": [{"label": x.get("label"), "amount": parse_amount(x.get("amount_text"))}
                                    for x in raw.get("late_payment_surcharge_amounts") or []],
        "payable_after_due_date_note": raw.get("payable_after_due_date_note"),
        "meter_rows": raw.get("meter_rows") or [],
        "usage_history": history,
        "printed_percentages": raw.get("printed_percentages") or {},
        "other_printed_values": raw.get("other_printed_values") or [],
        "notes_english": raw.get("notes_english") or [],
        "connection_type_evidence": raw.get("connection_type_evidence") or [],
    }
    return out


def to_level1(record: dict) -> dict:
    """Strip private keys and labels; keep field order exactly as the contract."""
    out = {}
    for f in L1_FIELDS:
        v = record.get(f)
        if f in ("charges", "taxes"):
            v = [{"type": x["type"], "amount": x["amount"]} for x in (v or [])]
        out[f] = v
    return out
