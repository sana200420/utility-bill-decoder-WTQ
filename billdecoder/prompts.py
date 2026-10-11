"""Extraction schema and prompts. The schema forces Gemini to return typed JSON (no free text parsing)."""
from __future__ import annotations

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}

CHARGE_TYPES = ["energy", "fixed", "fpa", "quarterly_adjustment", "surcharge", "meter_rent", "subsidy", "other"]
TAX_TYPES = ["gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax"]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "provider": {"type": "string", "enum": ["KE", "LESCO", "IESCO"]},
        "tariff": STR,
        "sanctioned_load_kw": NUM,
        "bill_month": {**STR, "description": "YYYY-MM"},
        "reading_date": {**STR, "description": "YYYY-MM-DD"},
        "issue_date": {**STR, "description": "YYYY-MM-DD"},
        "due_date": {**STR, "description": "YYYY-MM-DD"},
        "previous_reading": NUM,
        "current_reading": NUM,
        "units_consumed": NUM,
        "charges": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": "line label exactly as printed"},
            "type": {"type": "string", "enum": CHARGE_TYPES},
            "amount_text": {"type": "string", "description": "amount exactly as printed, e.g. '1,054.00' or '-1,442.74'"},
        }, "required": ["label", "type", "amount_text"]}},
        "total_charges_text": STR,
        "taxes": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"},
            "type": {"type": "string", "enum": TAX_TYPES},
            "amount_text": {"type": "string"},
        }, "required": ["label", "type", "amount_text"]}},
        "total_taxes_text": STR,
        "current_bill_text": STR,
        "arrears_text": STR,
        "payable_within_due_date_text": STR,
        "late_payment_amounts": {"type": "array", "description": "every printed amount payable after/late (tiers)",
                                 "items": {"type": "object", "properties": {
                                     "label": {"type": "string"}, "amount_text": {"type": "string"}},
                                     "required": ["label", "amount_text"]}},
        "payable_after_due_date_note": {**STR, "description": "any text printed in the payable-after-due-date box, e.g. 'NOT TO BE PAID'"},
        "late_payment_surcharge_amounts": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "amount_text": {"type": "string"}}, "required": ["label", "amount_text"]}},
        "meter_rows": {"type": "array", "items": {"type": "object", "properties": {
            "label": STR, "previous": NUM, "present": NUM, "units": NUM}}},
        "usage_history": {"type": "array", "description": "monthly history table/chart, oldest first",
                          "items": {"type": "object", "properties": {
                              "month": {"type": "string", "description": "YYYY-MM"},
                              "units": NUM, "bill_amount": NUM, "payment": NUM}, "required": ["month"]}},
        "printed_percentages": {"type": "object", "properties": {
            "energy_charges_pct": NUM, "taxes_pct": NUM}},
        "other_printed_values": {"type": "array", "description": "other useful non-personal values (MDI, connected load, "
                                 "rates, units per slab, installments, net metering benefit, total FPA, subsidy note amounts...)",
                                 "items": {"type": "object", "properties": {
                                     "label": {"type": "string"}, "value": {"type": "string"}},
                                     "required": ["label", "value"]}},
        "connection_type_evidence": {"type": "array", "items": {"type": "string"},
                                     "description": "printed evidence of net metering / solar (e.g. 'NET METERING CONNECTION', "
                                     "Customer Type NM, Import/Export registers, Net Metering Benefit, export units, negative "
                                     "units in history). Empty list if none."},
        "notes_english": {"type": "array", "items": {"type": "string"},
                          "description": "printed messages/notices about charges, FPA, subsidy, translated to English"},
    },
    "required": ["provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date", "issue_date", "due_date",
                 "previous_reading", "current_reading", "units_consumed", "charges", "total_charges_text", "taxes",
                 "total_taxes_text", "current_bill_text", "arrears_text", "payable_within_due_date_text",
                 "late_payment_amounts", "meter_rows", "usage_history", "notes_english"],
}

SYSTEM_EXTRACT = """You are a meticulous data-entry auditor for Pakistani electricity bills (K-Electric, LESCO, IESCO).
You read the bill image and transcribe values EXACTLY as printed. Never calculate, round, correct or guess.
If a value is not printed, blank, hidden by a grey box, or unreadable, return null. Never output personal
identifiers (names, addresses, CNIC, account/reference/consumer/meter/contract numbers)."""

RULES = """
Extraction rules:
- Dates: return ISO. '22nd Sep 2026' -> 2026-09-22, '17 JUL 26' -> 2026-07-17, bill month 'MAR 2026'/'Apr-26' -> 2026-03 / 2026-04.
- Amount *_text fields: copy the printed amount string including any minus sign or 'CR' suffix (CR = credit).
- charges: one entry per printed line of the CHARGES section; skip blank lines and lines printed as 0.
  Keep separate entries when the same type repeats (e.g. two energy slabs, two quarterly adjustments).
  Types: energy (Variable/Units/Cost of Electricity/Energy Charges, Off-peak/Peak), fixed, fpa (FPA/FCA),
  quarterly_adjustment (QTA/Uniform Quarterly Adjustment), surcharge (Additional Surcharge PHL, F.C Surcharge),
  meter_rent (meter/service rent), subsidy (subsidy/relief, negative), other.
  Subtotal rows (Electricity Charges, Total, Net) are NOT lines: put them in total_charges_text.
- taxes: one entry per printed line of the TAX / government section (classify by section, not name).
  Types: gst (Sales Tax/GST, incl. GST on FPA), electricity_duty (ED), income_tax, municipal_tax (e.g. MUCT/KMC),
  other_tax (extra tax, further tax, TV fee). If the bill prints only one combined 'Taxes' amount, taxes=[] and
  put the amount in total_taxes_text.
- current_bill_text: the printed amount for this month's bill (Current Bill / Current Bill Amount /
  Electricity Charges for current month).
- arrears_text: previous dues / arrears carried forward, with printed sign; null if blank.
- payable_within_due_date_text: amount payable within due date (Amount Due / Payable Within Due Date).
- late_payment_amounts: EVERY printed 'payable after due date' amount (all tiers). If the bill says
  'NOT TO BE PAID' or prints nothing, return [].
- late_payment_surcharge_amounts: the L.P surcharge amounts (the extra fee, not the total payable).
- meter_rows: every meter/register row printed in the readings table.
- previous_reading / current_reading / units_consumed: the main printed readings and units billed this month.
- usage_history: every month in the usage / bill history (bar chart labels or table), oldest first, with units,
  billed amount and payment where printed. Include the current month only if it is drawn in the history.
"""

PROVIDER_HINTS = {
    "KE": """Layout hints (K-Electric):
- Sanc Load, Tariff (e.g. A1-R) are in the Customer Information box. Bill Month and Issue Date are top-left.
- Reading Date is in the 'Bill Calculation' header. Due Date is the big date box.
- Charges = lines between the table header and the 'Electricity Charges' subtotal row (Fixed Charges, Variable...,
  Uniform Quarterly Adjustment, FCA/FPA, Additional Surcharge...). total_charges_text = 'Electricity Charges' row.
- Taxes = lines between 'Electricity Charges' and 'Taxes and Duties' (Electricity Duty, Sales Tax, MUCT/KMC,
  Income tax...). total_taxes_text = 'Taxes and Duties' row.
- current_bill_text = 'Electricity Charges for current month' (same as Current Month Rs.). arrears = 'Previous Dues'.
- payable_within_due_date_text = 'Amount Due' / 'Amount Payable within Due Date'.
- late_payment_amounts = all 'Amount Payable from ... with 5% LPS' / 'after ... with 10% LPS' / 'Till .. / After ..' values.
- Units: 'Current Month NNN units'. If several registers (off-peak/peak/import) are printed and none of them alone
  matches the billed units, set previous_reading and current_reading to null.
- usage_history: read the 13 Month Usage History bar labels (month + units). Payment history table gives
  billed amount and payment for MM/YY rows.""",
    "LESCO": """Layout hints (LESCO). LESCO has TWO layouts - identify which one this bill uses:
(1) ITEMISED layout (sections 'LESCO CHARGES', 'GOVT. CHARGES', 'TOTAL CHARGES'):
- charges = each non-zero amount line under LESCO CHARGES: COST OF ELECTRICITY -> energy, METER RENT /
  SERVICE RENT -> meter_rent, FUEL PRICE ADJUSTMENT -> fpa, F.C SURCHARGE -> surcharge, QUARTERLY TARIFF
  ADJUSTMENT -> quarterly_adjustment, FIXED CHARGES -> fixed. 'UNITS CONSUMED' is NOT a charge (it is units).
  total_charges_text = the TOTAL row of LESCO CHARGES.
- taxes = each non-zero line under GOVT. CHARGES: ELECTRICITY DUTY / ED ON FPA -> electricity_duty, GST / GST ON
  FPA -> gst, INCOME TAX / INCOME TAX ON FPA -> income_tax, EXTRA TAX / FURTHER TAX / RS TAX / TV FEE (and on FPA)
  -> other_tax. total_taxes_text = the TOTAL row of GOVT. CHARGES.
- TOTAL CHARGES column: ARREAR/AGE -> arrears_text (blank -> null), CURRENT BILL -> current_bill_text,
  PAYABLE WITHIN DUE DATE -> payable_within_due_date_text; 'TOTAL FPA' there is NOT a charge line.
- Header row has TARIFF, S. LOAD, BILL MONTH, Reading Date, ISSUE DATE, DUE DATE. History table at right.
- A small note like 'FPA FEB-26 @ 1.4235' (fuel adjustment month and rate) -> other_printed_values.
(2) SUMMARY layout ('BILL CHARGES BREAKDOWN'):
- 'Total Electricity Charges' is the energy charge line (type energy). 'Subsidies' is a subsidy line (negative)
  only if a non-zero amount is printed. 'Total FPA' (right column) is an fpa charge line when non-zero.
  'Net Electricity Charges' is the charges subtotal -> total_charges_text. 'Taxes' is a single combined amount:
  taxes=[] and total_taxes_text = it. 'Current Bill' -> current_bill_text. Right column 'Arrears' -> arrears_text
  (ignore a '/ 2' style suffix, keep the amount; 0 printed means 0). Grand Total = payable within due date.
- SAN LOAD, TARIFF (e.g. A-1b(03)T, A-1A(01)) in Consumer Detail; BILL MONTH, READING DATE, ISSUE DATE, DUE DATE at right.
- METER INFO rows: previous/present/units. Use the first row as the main readings unless another row is clearly the billed one.
- BILL HISTORY table has two halves (left: older months, right: newer); output oldest first.
Both layouts: 'PAYABLE AFTER DUE DATE' Upto/After (or Till/After) amounts -> late_payment_amounts;
'L.P SURCHARGE' amounts -> late_payment_surcharge_amounts. Keep CR suffixes.""",
    "IESCO": """Layout hints (IESCO):
- SAN LOAD, TARIFF (e.g. A-1A(01)) in Consumer Detail. BILL MONTH, READING DATE, ISSUE DATE, DUE DATE at right.
- 'BILL CHARGES BREAKDOWN': 'Total Electricity Charges' is the energy charge line (type energy); 'Subsidies' is a
  subsidy line (negative) if non-zero; 'Total FPA' (right column) is an fpa line when non-zero.
  'Net Electricity Charges' is the charges subtotal -> total_charges_text. 'Taxes' is a combined amount ->
  taxes=[] and total_taxes_text. 'Current Bill' -> current_bill_text. 'Arrears' -> arrears_text (0 printed means 0).
- Printed percentages next to 'Net Electricity Charges NN.NN %' and 'Taxes NN.NN %' -> printed_percentages.
- PAYABLE AFTER DUE DATE has 'Till dd-MMM' and 'After dd-MMM' amounts -> late_payment_amounts.
- Translate the Urdu notice about FPA/subsidy into notes_english (e.g. government subsidy amount). The FPA notice
  names the fuel-adjustment MONTH (e.g. 'جون-26' = June 2026, 'جولائی 26' = July 2026), the units and the rate:
  ALWAYS include that month in English in notes_english and add other_printed_values {label: 'FPA month', value}.
- BILL HISTORY table has two halves (left older, right newer), 6 months each = 12 months; output oldest first.
  The FIRST row of each half is often printed overlapping the header row (e.g. 'AUG 25 ... 721 80594 80594' over
  'MONTH STATUS UNITS BILL PAYMENT'); read it too so no month is missing.""",
}


def extraction_prompt(provider: str, view_note: str) -> str:
    return (f"Extract the electricity bill in the image(s). Provider: {provider}.\n{view_note}\n"
            f"{RULES}\n{PROVIDER_HINTS[provider]}\nReturn only the JSON object.")


FOCUS_PROMPT = """You previously extracted this bill, but independent readings disagree on some fields.
Look very carefully at the image(s) again and re-read ONLY these fields, exactly as printed:
{fields}
Candidate readings so far (may be wrong): {candidates}
Return the full JSON object; fields not listed may be copied from your best reading."""


RECHECK_PROMPT = """An arithmetic cross-check on your reading of this bill FAILED:
{failures}
Printed totals on a bill normally add up, so at least one of the involved values was probably misread (a digit
hidden by a pen mark, fold, stamp or glare, e.g. 3 vs 8, 1 vs 7, 5 vs 6). Zoom in on the involved printed values
and re-read each one digit by digit, exactly as printed. Do NOT calculate a value to make the check pass: if a digit
truly cannot be read, return the value you can see. Return the full JSON object."""
