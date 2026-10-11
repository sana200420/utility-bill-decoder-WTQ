"""Arithmetic cross-checks. They never change printed values; they score competing readings."""
from __future__ import annotations

TOL = 1.5


def _close(a, b, tol=TOL) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _check(name, ok, detail) -> dict:
    return {"check": name, "status": "na" if ok is None else ("pass" if ok else "fail"), "detail": detail}


def run_checks(r: dict) -> list[dict]:
    res = []
    prev, cur, units = r.get("previous_reading"), r.get("current_reading"), r.get("units_consumed")
    single_meter = len(r.get("_extras", {}).get("meter_rows") or []) <= 1
    if None not in (prev, cur, units) and single_meter:
        res.append(_check("readings", _close(cur - prev, units), f"{cur} - {prev} vs units {units}"))
    else:
        res.append(_check("readings", None, "multi-register or missing"))

    charges, tc = r.get("charges") or [], r.get("total_charges")
    ke = r.get("provider") == "KE"
    if charges and tc is not None:
        # LESCO/IESCO print 'Total FPA' outside the Net Electricity Charges subtotal
        s_all = sum(c["amount"] for c in charges)
        s_nofpa = sum(c["amount"] for c in charges if c["type"] != "fpa")
        ok = _close(s_all, tc) or (not ke and _close(s_nofpa, tc))
        res.append(_check("charges_sum", ok, f"sum {s_all:.2f} (excl. FPA {s_nofpa:.2f}) vs total {tc}"))
    else:
        res.append(_check("charges_sum", None, "no lines or subtotal"))

    taxes, tt = r.get("taxes") or [], r.get("total_taxes")
    if taxes and tt is not None:
        s = sum(t["amount"] for t in taxes)
        res.append(_check("taxes_sum", _close(s, tt), f"sum {s:.2f} vs total {tt}"))
    else:
        res.append(_check("taxes_sum", None, "combined taxes"))

    cb = r.get("current_bill")
    if None not in (tc, tt, cb):
        exp = tc + tt
        pw0 = r.get("payable_within_due_date")
        # LESCO itemised layout: charges + taxes = payable (current bill excludes the separate Total FPA)
        ok = _close(exp, cb, 2.5) or (not ke and pw0 is not None and _close(exp + (r.get("arrears") or 0), pw0, 2.5))
        res.append(_check("bill_total", ok, f"charges {tc} + taxes {tt} = {exp:.2f} vs bill {cb}"))
    else:
        res.append(_check("bill_total", None, "missing totals"))

    ar, pw = r.get("arrears"), r.get("payable_within_due_date")
    if None not in (cb, pw):
        fpa_outside = 0 if ke else sum(c["amount"] for c in charges if c["type"] == "fpa")
        exp = cb + (ar or 0) + fpa_outside
        alt = (tc + tt + (ar or 0)) if None not in (tc, tt) and not ke else None
        res.append(_check("payable", _close(exp, pw, 2.5) or _close(cb + (ar or 0), pw, 2.5) or
                          _close(alt, pw, 2.5) or (pw == 0 and exp <= 0),
                          f"current {cb} + arrears {ar} = {exp:.2f} vs payable {pw}"))
    else:
        res.append(_check("payable", None, "missing"))

    pa = r.get("payable_after_due_date")
    if None not in (pa, pw):
        res.append(_check("late_ge_within", pa >= pw, f"after {pa} >= within {pw}"))
    dates = [r.get("reading_date"), r.get("issue_date"), r.get("due_date")]
    if None not in dates:
        res.append(_check("date_order", dates[0] <= dates[1] <= dates[2], " <= ".join(dates)))
    bm = r.get("bill_month")
    if bm and r.get("issue_date"):
        res.append(_check("month_match", r["issue_date"][:7] >= bm, f"issue {r['issue_date']} vs month {bm}"))
    return res


def score(checks: list[dict]) -> float:
    return sum(1 for c in checks if c["status"] == "pass") - 1.5 * sum(1 for c in checks if c["status"] == "fail")


CONFUSABLE = {frozenset(p) for p in [("3", "8"), ("1", "7"), ("5", "6"), ("0", "8"), ("6", "8"), ("9", "8"), ("5", "3")]}
SUM_FIELDS = ["total_taxes", "total_charges", "current_bill", "arrears", "payable_within_due_date"]


def _one_confusable_digit_apart(a, b) -> bool:
    sa, sb = f"{a}", f"{b}"
    if len(sa) != len(sb):
        return False
    diffs = [(x, y) for x, y in zip(sa, sb) if x != y]
    return len(diffs) == 1 and frozenset(diffs[0]) in CONFUSABLE


def confusable_digit_repair(r: dict) -> tuple[dict, list]:
    """If a sum check fails and flipping ONE visually-confusable digit (e.g. 3<->8 hidden by a fold or pen mark)
    in one printed total makes every sum check pass, apply that reading. Logged in the audit."""
    def fails(x):
        return sum(c["status"] == "fail" for c in run_checks(x))
    base = fails(r)
    if not base:
        return r, []
    for f in SUM_FIELDS:
        v = r.get(f)
        if not isinstance(v, (int, float)):
            continue
        digits = f"{v}"
        for i, ch in enumerate(digits):
            for pair in CONFUSABLE:
                if ch in pair:
                    alt_ch = next(iter(pair - {ch}))
                    try:
                        alt = float(digits[:i] + alt_ch + digits[i + 1:])
                    except ValueError:
                        continue
                    alt = int(alt) if alt == int(alt) else alt
                    trial = {**r, f: alt}
                    if fails(trial) == 0:
                        return trial, [{"field": f, "from": v, "to": alt, "reason": "confusable digit repair"}]
    return r, []
