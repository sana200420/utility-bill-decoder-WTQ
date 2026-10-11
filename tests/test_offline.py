"""Offline tests (no API calls):  python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billdecoder.calc import CalcError, evaluate  # noqa: E402
from billdecoder.normalize import L1_FIELDS, normalize, parse_amount, parse_date, parse_month, to_level1  # noqa: E402
from billdecoder.validate import run_checks  # noqa: E402

KESC_0008_EXPECTED = {
    "provider": "KE", "tariff": "A1-R", "sanctioned_load_kw": 3, "bill_month": "2026-04",
    "reading_date": "2026-04-03", "issue_date": "2026-04-07", "due_date": "2026-04-21",
    "previous_reading": 8819, "current_reading": 8970, "units_consumed": 151,
    "charges": [{"type": "fixed", "amount": 900}, {"type": "energy", "amount": 1054},
                {"type": "energy", "amount": 663.51}, {"type": "quarterly_adjustment", "amount": 52.91},
                {"type": "fpa", "amount": 125.27}, {"type": "surcharge", "amount": 64.93}],
    "total_charges": 2860.62,
    "taxes": [{"type": "electricity_duty", "amount": 29.41}, {"type": "gst", "amount": 520.21},
              {"type": "municipal_tax", "amount": 20}],
    "total_taxes": 569.62, "current_bill": 3430.24, "arrears": -0.53,
    "payable_within_due_date": 3430, "payable_after_due_date": 3716,
}

RAW_KESC_0008 = {
    "provider": "KE", "tariff": "A1-R", "sanctioned_load_kw": 3, "bill_month": "Apr-26",
    "reading_date": "03-Apr-26", "issue_date": "07-Apr-2026", "due_date": "21st Apr 2026",
    "previous_reading": 8819, "current_reading": 8970, "units_consumed": 151,
    "charges": [{"label": "Fixed Charges", "type": "fixed", "amount_text": "900.00"},
                {"label": "Variable Upto 100", "type": "energy", "amount_text": "1,054.00"},
                {"label": "Variable Upto 200", "type": "energy", "amount_text": "663.51"},
                {"label": "Uniform Quarterly Adjustment", "type": "quarterly_adjustment", "amount_text": "52.91"},
                {"label": "FCA Feb-26", "type": "fpa", "amount_text": "125.27"},
                {"label": "Additional Surcharge (PHL)", "type": "surcharge", "amount_text": "64.93"},
                {"label": "Meter rent", "type": "meter_rent", "amount_text": "0.00"}],
    "total_charges_text": "2,860.62",
    "taxes": [{"label": "Electricity Duty", "type": "electricity_duty", "amount_text": "29.41"},
              {"label": "Sales Tax", "type": "gst", "amount_text": "520.21"},
              {"label": "MUCT (KMC)", "type": "municipal_tax", "amount_text": "20.00"}],
    "total_taxes_text": "569.62", "current_bill_text": "3,430.24", "arrears_text": "-0.53",
    "payable_within_due_date_text": "Rs. 3,430",
    "late_payment_amounts": [{"label": "22-24 Apr", "amount_text": "3,574"},
                             {"label": "After 24-Apr", "amount_text": "3,716"}],
    "meter_rows": [], "usage_history": [], "notes_english": [],
}


class TestNormalize(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("Rs. 19,964"), 19964)
        self.assertEqual(parse_amount("38209CR"), -38209)
        self.assertEqual(parse_amount("39411 / 2"), 39411)
        self.assertIsNone(parse_amount("NOT TO BE PAID"))

    def test_dates(self):
        self.assertEqual(parse_date("22nd Sep 2026"), "2026-09-22")
        self.assertEqual(parse_date("17 JUL 26"), "2026-07-17")
        self.assertEqual(parse_month("MAR 2026"), "2026-03")

    def test_worked_example_matches_guide(self):
        rec = normalize(RAW_KESC_0008, "KESC_0008")
        out = to_level1(rec)
        self.assertEqual(list(out), L1_FIELDS)
        self.assertEqual(out, KESC_0008_EXPECTED)
        self.assertTrue(all(c["status"] != "fail" for c in run_checks(rec)))

    def test_subsidy_negative(self):
        raw = {**RAW_KESC_0008, "charges": [{"label": "Subsidies", "type": "subsidy", "amount_text": "3,332"}]}
        self.assertEqual(to_level1(normalize(raw, "IESCO_0001"))["charges"], [{"type": "subsidy", "amount": -3332}])


class TestCalc(unittest.TestCase):
    def test_functions(self):
        v = {"hist_units": [141, 138, 197, 239, 203]}
        self.assertEqual(evaluate("count_above(hist_units, 200)", v), 2)
        self.assertAlmostEqual(evaluate("pct(569.62, 3430.24)", {}), 16.6058, places=3)

    def test_rejects_code(self):
        with self.assertRaises(CalcError):
            evaluate("__import__('os').system('x')", {})


if __name__ == "__main__":
    unittest.main()
