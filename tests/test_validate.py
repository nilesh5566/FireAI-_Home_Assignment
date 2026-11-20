"""Unit tests for validation. Rows here are synthetic test inputs, not shop data."""

import unittest
from datetime import date, datetime
from decimal import Decimal

from tracker.validate import ValidationError, clean_text, escape_formula, parse_row, to_csv_row

TODAY = date(2026, 11, 20)


def row(**kw):
    base = {"date": "2026-11-20", "time": "16:07", "item": "test item", "category": "bindi",
            "in_stock": "n", "bought": "n", "price_rs": "20", "customer_type": "student", "note": ""}
    base.update(kw)
    return base


class ParseRowTests(unittest.TestCase):
    def test_valid_row(self):
        r = parse_row(row(), today=TODAY)
        self.assertEqual(r.item, "test item")
        self.assertEqual(r.hour, 16)
        self.assertEqual(r.price, Decimal("20"))
        self.assertFalse(r.in_stock)

    def test_blank_price_is_allowed(self):
        self.assertIsNone(parse_row(row(price_rs=""), today=TODAY).price)

    def test_hindi_yes_no(self):
        r = parse_row(row(in_stock="haan", bought="nahi"), today=TODAY)
        self.assertTrue(r.in_stock)
        self.assertFalse(r.bought)

    def test_collects_all_problems(self):
        with self.assertRaises(ValidationError) as cm:
            parse_row(row(time="25:00", in_stock="maybe", price_rs="abc", item=""), today=TODAY)
        self.assertEqual(len(cm.exception.problems), 4)

    def test_rejects_bad_values(self):
        cases = {
            "future date": row(date="2026-12-01"),
            "old date": row(date="2019-01-01"),
            "bad date": row(date="20/11/2026"),
            "negative price": row(price_rs="-5"),
            "huge price": row(price_rs="99999"),
            "nan price": row(price_rs="NaN"),
            "inf price": row(price_rs="Infinity"),
            "bought but not in stock": row(in_stock="n", bought="y"),
            "unknown category": row(category="weapons"),
            "long item": row(item="x" * 61),
            "long note": row(note="x" * 201),
        }
        for name, r in cases.items():
            with self.subTest(name), self.assertRaises(ValidationError):
                parse_row(r, today=TODAY)

    def test_rejects_time_later_than_now(self):
        now = datetime(2026, 11, 20, 16, 24)
        parse_row(row(time="16:27"), today=TODAY, now=now)  # within clock skew: ok
        with self.assertRaisesRegex(ValidationError, "later than now"):
            parse_row(row(time="17:28"), today=TODAY, now=now)
        parse_row(row(date="2026-11-19", time="17:28"), today=TODAY, now=now)  # yesterday: ok

    def test_category_synonyms(self):
        self.assertEqual(parse_row(row(category="Makeup"), today=TODAY).category, "cosmetic")
        self.assertEqual(parse_row(row(category="jewellery"), today=TODAY).category, "jewellery")

    def test_missing_keys_do_not_crash(self):
        with self.assertRaises(ValidationError):
            parse_row({}, today=TODAY)


class TextSafetyTests(unittest.TestCase):
    def test_strips_control_and_invisible_chars(self):
        # zero-width space, RTL override, ANSI escape, NUL
        self.assertEqual(clean_text("bla\u200bck\u202e bin\x1b[31mdi\x00", 60), "black bin[31mdi")

    def test_collapses_whitespace(self):
        self.assertEqual(clean_text("  black \t  bindi\n ", 60), "black bindi")

    def test_formula_injection_escaped_on_write(self):
        for payload in ("=HYPERLINK(\"http://x\")", "+1+1", "-2", "@SUM(A1)", "\tx"):
            with self.subTest(payload):
                self.assertTrue(escape_formula(payload).startswith("'"))

    def test_formula_roundtrip(self):
        r = parse_row(row(item="=cmd|' /C calc'!A0"), today=TODAY)
        cell = to_csv_row(r)[2]
        self.assertTrue(cell.startswith("'="))
        self.assertEqual(parse_row(row(item=cell), today=TODAY).item, r.item)

    def test_normal_text_untouched(self):
        self.assertEqual(escape_formula("black bindi"), "black bindi")


if __name__ == "__main__":
    unittest.main()
