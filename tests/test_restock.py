"""Tests for restock.py. Run from the project folder:  python -m unittest -v

The CSV rows below are made-up test fixtures, written to temp files.
They are NOT field data and must never be used as data.csv.
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import restock  # noqa: E402

HEADER = "date,time,item,category,in_stock,bought,price_rs,customer_type,note\n"
TODAY = date(2026, 11, 20)

FIXTURE = HEADER + (
    "2026-11-17,08:10,Milk 500ml,dairy,12,2,27,regular,\n"
    "2026-11-17,09:40,Milk 500ml,dairy,9,3,27,student,\n"
    "2026-11-17,18:05,Bread,bakery,4,1,45,regular,last packet of brown\n"
    "2026-11-18,08:20,Milk 500ml,dairy,6,4,27,regular,\n"
    "2026-11-18,19:30,milk 500ML,dairy,0,2,27,walk-in,ran out\n"
    "2026-11-18,19:45,Bread,bakery,2,2,45,student,\n"
    "2026-11-19,07:55,Milk 500ml,dairy,10,5,27,regular,\n"
    "2026-11-19,10:15,Eggs,dairy,,6,7,regular,loose eggs\n"
    "2026-11-19,20:00,Milk 500ml,dairy,2,3,27,student,\n"
)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.dir)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def write(self, text, name="data.csv", mode="w"):
        p = self.dir / name
        if mode == "wb":
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
        return str(p)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = restock.main(list(args))
        return code, out.getvalue(), err.getvalue()


class TestParsers(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(restock.parse_date("2026-11-19", TODAY), date(2026, 11, 19))
        self.assertEqual(restock.parse_date("19-11-2026", TODAY), date(2026, 11, 19))
        self.assertEqual(restock.parse_date("19/11/2026", TODAY), date(2026, 11, 19))
        for bad in ["", "2026-13-01", "yesterday", "2030-01-01", "1990-01-01"]:
            with self.assertRaises(ValueError, msg=bad):
                restock.parse_date(bad, TODAY)

    def test_times(self):
        self.assertEqual(restock.parse_time("18:05").hour, 18)
        self.assertEqual(restock.parse_time("6:05 pm").hour, 18)
        self.assertIsNone(restock.parse_time(""))
        with self.assertRaises(ValueError):
            restock.parse_time("25:00")

    def test_bought(self):
        self.assertEqual(restock.parse_bought("3"), 3)
        for bad in ["", "-1", "2.5", "abc", "nan", "inf", "1e9", "99999999"]:
            with self.assertRaises(ValueError, msg=bad):
                restock.parse_bought(bad)

    def test_in_stock(self):
        self.assertEqual(restock.parse_in_stock("5"), (5, False))
        self.assertEqual(restock.parse_in_stock("0"), (0, True))
        self.assertEqual(restock.parse_in_stock("no"), (0, True))
        self.assertEqual(restock.parse_in_stock("Yes"), (None, False))
        self.assertEqual(restock.parse_in_stock(""), (None, None))
        with self.assertRaises(ValueError):
            restock.parse_in_stock("-3")

    def test_price(self):
        self.assertEqual(restock.parse_price("₹27"), 27.0)
        self.assertEqual(restock.parse_price("Rs. 1,200.50"), 1200.5)
        self.assertIsNone(restock.parse_price(""))
        for bad in ["-5", "free", "NaN", "Infinity"]:
            with self.assertRaises(ValueError, msg=bad):
                restock.parse_price(bad)

    def test_clean_text_strips_terminal_escapes(self):
        self.assertEqual(restock.clean_text("Milk\x1b[31m  red\x07"), "Milk[31m red")

    def test_spreadsheet_safe(self):
        self.assertEqual(restock.spreadsheet_safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(restock.spreadsheet_safe("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(restock.spreadsheet_safe("Milk"), "Milk")
        self.assertEqual(restock.spreadsheet_safe(-3), -3)


class TestLoading(TempDirTest):
    def test_good_file(self):
        res = restock.load_csv(self.write(FIXTURE), today=TODAY)
        self.assertEqual(len(res.rows), 9)
        self.assertEqual(res.rejected, [])

    def test_bad_rows_are_rejected_with_line_numbers(self):
        text = FIXTURE + (
            "2026-11-19,10:00,Sugar,grocery,5,abc,40,regular,\n"     # line 11
            "2026-11-19,10:00,,grocery,5,1,40,regular,\n"            # line 12
            "2026-11-19,10:00,Sugar,grocery,5,1\n"                   # line 13
            "2099-01-01,10:00,Sugar,grocery,5,1,40,regular,\n"       # line 14
            "2026-11-19,10:00,Sugar,grocery,5,-2,40,regular,\n"      # line 15
        )
        res = restock.load_csv(self.write(text), today=TODAY)
        self.assertEqual(len(res.rows), 9)
        self.assertEqual([l for l, _ in res.rejected], [11, 12, 13, 14, 15])

    def test_header_checks(self):
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(self.write("date,item,bought\n2026-11-19,Milk,1\n"))
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(self.write(HEADER.strip() + ",secret\n"))

    def test_header_order_and_case_do_not_matter(self):
        text = ("NOTE,Bought,Item,date,time,category,in_stock,price_rs,customer_type\n"
                ",2,Milk,2026-11-19,08:00,dairy,5,27,regular\n")
        res = restock.load_csv(self.write(text), today=TODAY)
        self.assertEqual(res.rows[0].bought, 2)

    def test_path_checks(self):
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(str(self.dir / "missing.csv"))
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(self.write(FIXTURE, name="data.txt"))
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(self.write("", name="empty.csv"))
        (self.dir / "folder.csv").mkdir()
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(str(self.dir / "folder.csv"))

    def test_size_limit(self):
        old = restock.MAX_FILE_BYTES
        restock.MAX_FILE_BYTES = 100
        try:
            with self.assertRaises(restock.DataFileError):
                restock.load_csv(self.write(FIXTURE))
        finally:
            restock.MAX_FILE_BYTES = old

    def test_binary_file_rejected(self):
        with self.assertRaises(restock.DataFileError):
            restock.load_csv(self.write(b"\x00\x01\x02binary", mode="wb"))

    def test_excel_cp1252_and_bom(self):
        text = FIXTURE.replace("loose eggs", "caf\xe9 order")
        res = restock.load_csv(self.write(text.encode("cp1252"), mode="wb"), today=TODAY)
        self.assertEqual(len(res.rows), 9)
        res = restock.load_csv(self.write(b"\xef\xbb\xbf" + FIXTURE.encode(), mode="wb"),
                               today=TODAY)
        self.assertEqual(len(res.rows), 9)

    def test_overlong_field_rejected(self):
        text = HEADER + f"2026-11-19,08:00,Milk,dairy,5,1,27,regular,{'x' * 500}\n"
        res = restock.load_csv(self.write(text), today=TODAY)
        self.assertEqual(len(res.rows), 0)
        self.assertEqual(len(res.rejected), 1)

    def test_duplicates_flagged(self):
        line = "2026-11-19,08:00,Milk,dairy,5,1,27,regular,\n"
        res = restock.load_csv(self.write(HEADER + line + line), today=TODAY)
        self.assertEqual(len(res.rows), 2)
        self.assertEqual(res.duplicates, [3])


class TestAnalysis(TempDirTest):
    def reports(self, **kw):
        rows = restock.load_csv(self.write(FIXTURE), today=TODAY).rows
        return {r.item.casefold(): r for r in restock.analyse(rows, **kw)}

    def test_item_names_merge_case_insensitively(self):
        reps = self.reports()
        self.assertIn("milk 500ml", reps)
        self.assertEqual(len(reps), 3)

    def test_milk_numbers(self):
        milk = self.reports()["milk 500ml"]
        self.assertEqual(milk.total_sold, 19)       # 5 + 6 + 8
        self.assertEqual(milk.max_day, 8)
        self.assertEqual(milk.last_stock, 2)
        self.assertEqual(milk.stockout_days, 1)
        # mean 6.333, pstdev 1.247 -> forecast 6.957; *1.2 -> 8.35 -> 9; minus 2 in stock
        self.assertEqual(milk.suggest_order, 7)
        self.assertEqual(milk.revenue_rs, 19 * 27)
        self.assertIn("ran out", milk.warning)

    def test_safety_threshold_changes_order(self):
        self.assertEqual(self.reports(safety=0.0)["milk 500ml"].suggest_order, 5)

    def test_missing_days_count_as_zero(self):
        bread = self.reports()["bread"]
        self.assertEqual(bread.avg_per_day, 1.0)    # 3 units over 3 observed days

    def test_no_stock_count(self):
        eggs = self.reports()["eggs"]
        self.assertIsNone(eggs.suggest_order)
        self.assertEqual(eggs.confidence, "LOW")

    def test_price_is_total(self):
        milk = self.reports(price_is_total=True)["milk 500ml"]
        self.assertEqual(milk.revenue_rs, 6 * 27)

    def test_empty(self):
        self.assertEqual(restock.analyse([]), [])


class TestCLI(TempDirTest):
    def test_report_runs(self):
        code, out, _ = self.run_cli(self.write(FIXTURE))
        self.assertEqual(code, 0)
        self.assertIn("SUGGESTION FOR TOMORROW", out)
        self.assertIn("Milk 500ml", out)

    def test_filters(self):
        _, out, _ = self.run_cli(self.write(FIXTURE), "--item", "bread")
        self.assertIn("Bread", out)
        self.assertNotIn("Eggs", out)
        _, out, _ = self.run_cli(self.write(FIXTURE), "--category", "BAKERY")
        self.assertNotIn("Milk", out)

    def test_strict_mode(self):
        path = self.write(FIXTURE + "bad,row,here,,,,,,\n")
        self.assertEqual(self.run_cli(path)[0], 0)
        self.assertEqual(self.run_cli(path, "--strict")[0], 3)

    def test_all_rows_bad(self):
        code, _, err = self.run_cli(self.write(HEADER + "x,x,x,x,x,x,x,x,x\n"))
        self.assertEqual(code, 1)
        self.assertIn("No valid rows", err)

    def test_bad_thresholds_rejected(self):
        for args in (["--safety", "-1"], ["--safety", "nan"], ["--window", "0"]):
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    restock.main([self.write(FIXTURE)] + args)

    def test_export_csv_is_injection_safe(self):
        text = HEADER + "2026-11-19,08:00,=cmd|' /C calc'!A0,dairy,5,1,27,regular,\n"
        code, _, _ = self.run_cli(self.write(text), "--export", "out.csv")
        self.assertEqual(code, 0)
        with open("out.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertTrue(rows[1][0].startswith("'="))

    def test_export_json(self):
        code, _, _ = self.run_cli(self.write(FIXTURE), "--json", "out.json")
        self.assertEqual(code, 0)
        data = json.loads(Path("out.json").read_text(encoding="utf-8"))
        self.assertEqual(data["rows_used"], 9)

    @unittest.skipIf(os.name == "nt", "POSIX permissions only")
    def test_output_is_private(self):
        self.run_cli(self.write(FIXTURE), "--export", "out.csv")
        self.assertEqual(os.stat("out.csv").st_mode & 0o777, 0o600)

    def test_output_path_traversal_blocked(self):
        code, _, err = self.run_cli(self.write(FIXTURE), "--export", "../evil.csv")
        self.assertEqual(code, 1)
        self.assertIn("inside the current folder", err)
        self.assertFalse((self.dir.parent / "evil.csv").exists())

    def test_cannot_overwrite_input(self):
        path = self.write(FIXTURE)
        code, _, err = self.run_cli(path, "--clean", "data.csv")
        self.assertEqual(code, 1)
        self.assertIn("overwrite", err)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), FIXTURE)

    def test_bad_output_fails_before_report(self):
        code, out, _ = self.run_cli(self.write(FIXTURE), "--export", "../evil.csv")
        self.assertEqual((code, out), (1, ""))

    @unittest.skipIf(os.name == "nt", "symlinks need admin on Windows")
    def test_symlink_output_blocked(self):
        target = self.dir.parent / "outside.txt"
        os.symlink(target, self.dir / "link.csv")
        code, _, _ = self.run_cli(self.write(FIXTURE), "--export", "link.csv")
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())

    def test_same_output_name_twice(self):
        code, _, err = self.run_cli(self.write(FIXTURE), "--export", "a.csv", "--clean", "a.csv")
        self.assertEqual(code, 1)
        self.assertIn("different file names", err)

    def test_drop_notes_needs_clean(self):
        self.assertEqual(self.run_cli(self.write(FIXTURE), "--drop-notes")[0], 1)

    def test_wrong_output_extension(self):
        code, _, _ = self.run_cli(self.write(FIXTURE), "--export", "out.py")
        self.assertEqual(code, 1)

    def test_clean_copy(self):
        path = self.write(FIXTURE + "bad,row,,,,,,,\n")
        code, out, _ = self.run_cli(path, "--clean", "clean.csv", "--drop-notes")
        self.assertEqual(code, 0)
        with open("clean.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows), 10)              # header + 9 good rows
        self.assertTrue(all(r[-1] == "" for r in rows[1:]))
        # cleaned file must load again without errors
        again = restock.load_csv("clean.csv", today=TODAY)
        self.assertEqual((len(again.rows), again.rejected), (9, []))


if __name__ == "__main__":
    unittest.main()
