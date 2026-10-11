"""Builds the grounded fact sheet and calculator variables for Level 2 from the extracted record."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date


def _d(s):
    return date.fromisoformat(s) if s else None


def _days(a, b):
    return (_d(b) - _d(a)).days if a and b else None


def _month_name(ym: str) -> str:
    y, m = ym.split("-")
    return date(int(y), int(m), 1).strftime("%B %Y")


def build_facts(record: dict) -> tuple[dict, dict]:
    """Return (facts for the prompt, variables for the calculator)."""
    ex = record.get("_extras", {})
    bm = record.get("bill_month")
    units = record.get("units_consumed")
    history = [h for h in ex.get("usage_history", []) if h.get("month")]
    previous = [h for h in history if not bm or h["month"] < bm]
    current_in_hist = next((h for h in history if h["month"] == bm), None)

    by_charge, by_tax = defaultdict(float), defaultdict(float)
    for c in record.get("charges") or []:
        by_charge[c["type"]] += c["amount"]
    for t in record.get("taxes") or []:
        by_tax[t["type"]] += t["amount"]

    hist_units = [h["units"] for h in previous]
    clean_units = [u for u in hist_units if isinstance(u, (int, float))]
    all_units = clean_units + ([units] if isinstance(units, (int, float)) else [])
    late = [x["amount"] for x in ex.get("late_payment_amounts", []) if x.get("amount") is not None]
    cb, pw, pa = record.get("current_bill"), record.get("payable_within_due_date"), record.get("payable_after_due_date")

    v = {
        "units": units, "current_bill": cb, "total_charges": record.get("total_charges"),
        "total_taxes": record.get("total_taxes"), "arrears": record.get("arrears"),
        "payable_within": pw, "payable_after": pa, "sanctioned_load_kw": record.get("sanctioned_load_kw"),
        "previous_reading": record.get("previous_reading"), "current_reading": record.get("current_reading"),
        "hist_units": clean_units, "hist_bills": [h["bill_amount"] for h in previous if h.get("bill_amount") is not None],
        "hist_payments": [h["payment"] for h in previous if h.get("payment") is not None],
        "all_units_incl_current": all_units, "late_amounts": late,
        "days_issue_to_due": _days(record.get("issue_date"), record.get("due_date")),
        "days_reading_to_due": _days(record.get("reading_date"), record.get("due_date")),
        "days_reading_to_issue": _days(record.get("reading_date"), record.get("issue_date")),
    }
    rows = [(_month_name(h["month"]), h["units"]) for h in previous if isinstance(h.get("units"), (int, float))]

    def _months(pred):
        return lambda t: [f"{m} ({u})" for m, u in rows if pred(u, t)]

    v.update(months_above=_months(lambda u, t: u > t), months_at_least=_months(lambda u, t: u >= t),
             months_below=_months(lambda u, t: u < t))

    cal = [(int(h["month"][5:7]), h) for h in previous if isinstance(h.get("units"), (int, float))]
    cur = [(int(bm[5:7]), {"month": bm, "units": units})] if bm and isinstance(units, (int, float)) else []

    def _sel(include_current: bool):
        def units_for(ms):
            ms = [int(m) for m in (ms if isinstance(ms, list) else [ms])]
            return [h["units"] for c, h in cal + (cur if include_current else []) if c in ms]
        def months_for(ms):
            ms = [int(m) for m in (ms if isinstance(ms, list) else [ms])]
            return [f"{_month_name(h['month'])} ({h['units']})" for c, h in cal + (cur if include_current else [])
                    if c in ms]
        return units_for, months_for

    v["units_for_months"], v["months_for"] = _sel(False)
    v["units_for_months_incl_current"], v["months_for_incl_current"] = _sel(True)
    for k, val in by_charge.items():
        v[f"charge_{k}"] = round(val, 2)
    for k, val in by_tax.items():
        v[f"tax_{k}"] = round(val, 2)

    pre = {}  # pre-computed common metrics (all by code)
    if clean_units:
        pre.update(history_months=len(clean_units), avg_units_previous=round(statistics.mean(clean_units), 2),
                   max_units_previous=max(clean_units), min_units_previous=min(clean_units),
                   total_units_previous=sum(clean_units))
        hi = max(previous, key=lambda h: h["units"] if h["units"] is not None else float("-inf"))
        lo = min(previous, key=lambda h: h["units"] if h["units"] is not None else float("inf"))
        pre.update(highest_month_previous=_month_name(hi["month"]), lowest_month_previous=_month_name(lo["month"]))
        if isinstance(units, (int, float)):
            avg = statistics.mean(clean_units)
            pre.update(current_vs_avg_units=round(units - avg, 2),
                       current_vs_avg_pct=round((units - avg) / avg * 100, 2) if avg else None)
            same = next((h for h in previous if bm and h["month"] == f"{int(bm[:4]) - 1}{bm[4:]}"), None)
            if same and same.get("units") is not None:
                pre.update(same_month_last_year_units=same["units"], change_vs_last_year=units - same["units"])
            if previous[-1].get("units") is not None:
                pre.update(last_month_units=previous[-1]["units"], change_vs_last_month=units - previous[-1]["units"])
    if all_units:
        pre.update(avg_units_incl_current=round(statistics.mean(all_units), 2))
    if cb is not None and record.get("total_taxes") is not None and cb:
        pre["taxes_pct_of_current_bill"] = round(record["total_taxes"] / cb * 100, 2)
    if cb is not None and isinstance(units, (int, float)) and units > 0:
        pre["avg_cost_per_unit_current_bill"] = round(cb / units, 2)
        if record.get("total_charges") is not None:
            pre["avg_charges_per_unit_before_tax"] = round(record["total_charges"] / units, 2)
    if pa is not None and pw is not None:
        pre["late_payment_extra_max"] = round(pa - pw, 2)
    if len(late) > 1 and pw is not None:
        pre["late_payment_extra_by_tier"] = [round(x - pw, 2) for x in late]

    facts = {
        "provider": record.get("provider"), "tariff": record.get("tariff"),
        "bill_month": bm, "reading_date": record.get("reading_date"), "issue_date": record.get("issue_date"),
        "due_date": record.get("due_date"),
        "charges_lines": [{"label": c.get("label"), "type": c["type"], "amount": c["amount"]}
                          for c in record.get("charges") or []],
        "tax_lines": [{"label": t.get("label"), "type": t["type"], "amount": t["amount"]}
                      for t in record.get("taxes") or []],
        "late_payment_tiers": ex.get("late_payment_amounts", []),
        "late_payment_surcharges": ex.get("late_payment_surcharges", []),
        "payable_after_due_date_note": ex.get("payable_after_due_date_note"),
        "amounts_summary": {"current_bill": cb, "arrears_previous_dues": record.get("arrears"),
                            "payable_within_due_date": pw, "payable_after_due_date_highest": pa,
                            "total_charges": record.get("total_charges"), "total_taxes": record.get("total_taxes"),
                            "units_consumed": units, "sanctioned_load_kw": record.get("sanctioned_load_kw")},
        "connection_type_evidence": ex.get("connection_type_evidence", []),
        "payment_record": [{"month": _month_name(h["month"]), "bill_amount": h.get("bill_amount"),
                            "payment": h.get("payment")} for h in previous
                           if h.get("bill_amount") is not None or h.get("payment") is not None],
        "net_metering_note": ("Negative units/amounts in the history indicate net export (net metering credit)."
                              if any((h.get("units") or 0) < 0 for h in previous) else None),
        "meter_rows": ex.get("meter_rows", []),
        "history_previous_months": [{**h, "month_name": _month_name(h["month"])} for h in previous],
        "current_month_in_history_chart": current_in_hist,
        "printed_percentages": ex.get("printed_percentages", {}),
        "other_printed_values": ex.get("other_printed_values", []),
        "notes_translated": ex.get("notes_english", []),
        "precomputed": pre,
        "calculator_variables": {k: val for k, val in v.items() if not callable(val)},
    }
    return facts, {**v, **{k: val for k, val in pre.items() if isinstance(val, (int, float))}}
