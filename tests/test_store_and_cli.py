"""Tests for file handling, report maths and the CLI. Uses temp files only."""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tracker import store
from tracker.cli import main
from tracker.report import build_report
from tracker.store import DataFileError, append_request, read_requests
from tracker.validate import COLUMNS, parse_row

HEADER = ",".join(COLUMNS) + "\n"
D = (date.today() - timedelta(days=1)).isoformat()  # yesterday: times are never "future"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.csv = self.dir / "data.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, name="data.csv"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p


class StoreTests(TempDirTest):
    def test_reads_good_and_reports_bad_lines(self):
        self.write(HEADER + f"{D},16:05,a,bindi,n,n,20,,\n{D},99:00,b,bindi,x,n,,,\n")
        good, bad = read_requests(self.csv)
        self.assertEqual(len(good), 1)
        self.assertEqual(bad[0][0], 3)

    def test_extra_cells_flagged(self):
        self.write(HEADER + f"{D},16:05,a,bindi,n,n,20,,,EXTRA\n")
        _, bad = read_requests(self.csv)
        self.assertIn("more cells", bad[0][1][0])

    def test_excel_bom_ok(self):
        self.write("\ufeff" + HEADER + f"{D},16:05,a,bindi,n,n,,,\n")
        self.assertEqual(len(read_requests(self.csv)[0]), 1)

    def test_rejects_bad_files(self):
        self.write("date,time,item\n")
        with self.assertRaisesRegex(DataFileError, "missing columns"):
            read_requests(self.csv)
        self.write("date,date,time,item,in_stock,bought\n")
        with self.assertRaisesRegex(DataFileError, "duplicate"):
            read_requests(self.csv)
        txt = self.write(HEADER, name="data.txt")
        with self.assertRaisesRegex(DataFileError, ".csv"):
            read_requests(txt)
        with self.assertRaisesRegex(DataFileError, "not found"):
            read_requests(self.dir / "nope.csv")

    def test_non_utf8(self):
        (self.dir / "data.csv").write_bytes(HEADER.encode() + b"\xff\xfe\xfa\n")
        with self.assertRaisesRegex(DataFileError, "UTF-8"):
            read_requests(self.csv)

    @unittest.skipIf(os.name == "nt", "symlinks need admin on Windows")
    def test_symlink_refused(self):
        target = self.write(HEADER, name="real.csv")
        link = self.dir / "link.csv"
        link.symlink_to(target)
        with self.assertRaisesRegex(DataFileError, "symlink"):
            read_requests(link)

    def test_size_limit(self):
        old = store.MAX_FILE_BYTES
        store.MAX_FILE_BYTES = 100
        try:
            self.write(HEADER + "x" * 200)
            with self.assertRaisesRegex(DataFileError, "larger"):
                read_requests(self.csv)
        finally:
            store.MAX_FILE_BYTES = old

    def test_huge_field_rejected(self):
        self.write(HEADER + f'{D},16:05,"{"x" * 5000}",bindi,n,n,,,\n')
        with self.assertRaisesRegex(DataFileError, "malformed"):
            read_requests(self.csv)

    def test_append_creates_private_file_and_roundtrips(self):
        req = parse_row({"date": D, "time": "16:07", "item": "=evil()", "in_stock": "n", "bought": "n"})
        append_request(self.csv, req)
        append_request(self.csv, req)
        text = self.csv.read_text()
        self.assertTrue(text.startswith(HEADER))
        self.assertIn("'=evil()", text)
        good, bad = read_requests(self.csv)
        self.assertEqual((len(good), bad), (2, []))
        self.assertEqual(good[0].item, "=evil()")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.csv.stat().st_mode), 0o600)

    def test_append_after_file_without_trailing_newline(self):
        self.write(HEADER + f"{D},16:05,a,bindi,n,n,,,")
        append_request(self.csv, parse_row({"date": D, "time": "16:09", "item": "b",
                                            "in_stock": "y", "bought": "y"}))
        self.assertEqual(len(read_requests(self.csv)[0]), 2)

    def test_append_refuses_wrong_header(self):
        self.write("a,b,c\n")
        req = parse_row({"date": D, "time": "16:07", "item": "x", "in_stock": "n", "bought": "n"})
        with self.assertRaisesRegex(DataFileError, "header"):
            append_request(self.csv, req)


class ReportTests(unittest.TestCase):
    def test_counts_aliases_and_threshold(self):
        rows = [
            {"date": D, "time": "16:05", "item": "kajal", "in_stock": "n", "bought": "n", "price_rs": "90"},
            {"date": D, "time": "16:20", "item": "lakme kajal", "in_stock": "n", "bought": "n", "price_rs": "110"},
            {"date": D, "time": "17:01", "item": "clip", "in_stock": "y", "bought": "y", "price_rs": "30"},
            {"date": D, "time": "17:30", "item": "clip", "in_stock": "y", "bought": "n"},
        ]
        reqs = [parse_row(r) for r in rows]
        rep = build_report(reqs, min_misses=2, aliases={"lakme kajal": "kajal"})
        self.assertEqual(rep.missed[0].item, "kajal")
        self.assertEqual(rep.missed[0].times, 2)
        self.assertEqual(rep.total_lost, 200)
        self.assertEqual([m.item for m in rep.reorder], ["kajal"])
        self.assertEqual(rep.by_hour, {16: 2, 17: 2})
        self.assertEqual(build_report(reqs, min_misses=3).reorder, [])


class CliTests(TempDirTest):
    def test_empty_file_report(self):
        self.write(HEADER)
        code, out, _ = run("--file", str(self.csv))
        self.assertEqual(code, 0)
        self.assertIn("No usable rows", out)

    def test_add_flags_then_json_report(self):
        for t in ("16:05", "16:40"):
            code, _, _ = run("add", "--file", str(self.csv), "--item", "black bindi",
                             "--in-stock", "n", "--bought", "n", "--price", "20", "--time", t, "--date", D)
            self.assertEqual(code, 0)
        code, out, _ = run("report", "--file", str(self.csv), "--json")
        data = json.loads(out)
        self.assertEqual(data["reorder"], ["black bindi"])
        self.assertEqual(data["missed"][0]["est_lost"], 40.0)

    def test_add_flags_not_in_stock_needs_no_bought(self):
        from tracker.cli import build_parser, cmd_add
        args = build_parser().parse_args(["add", "--file", str(self.csv), "--item", "black bindi",
                                          "--in-stock", "n", "--time", "16:05", "--date", D])

        def no_prompt(prompt):
            raise AssertionError(f"unexpected prompt: {prompt}")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_add(args, input_fn=no_prompt), 0)
        good, _ = read_requests(self.csv)
        self.assertEqual(len(good), 1)
        self.assertFalse(good[0].bought)

    def test_add_invalid_not_saved(self):
        code, _, err = run("add", "--file", str(self.csv), "--item", "x",
                           "--in-stock", "n", "--bought", "y")
        self.assertEqual(code, 2)
        self.assertIn("Not saved", err)
        self.assertFalse(self.csv.exists())

    def test_interactive_add_retries_then_saves(self):
        from tracker.cli import build_parser, cmd_add
        answers = iter([
            "16:10", "", "1", "maybe", "y", "", "4", "", "y",  # 'maybe' re-asked; empty item -> rejected
            "16:11", "glass bangle 2.6", "2", "n", "45", "1", "size not there", "n",
        ])
        args = build_parser().parse_args(["add", "--file", str(self.csv), "--date", D])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cmd_add(args, input_fn=lambda _: next(answers)), 0)
        good, _ = read_requests(self.csv)
        self.assertEqual(len(good), 1)
        self.assertEqual(good[0].item, "glass bangle 2.6")
        self.assertFalse(good[0].bought)  # auto 'n' because not in stock

    def test_check_exit_codes(self):
        self.write(HEADER + f"{D},16:05,a,bindi,n,n,,,\n")
        self.assertEqual(run("check", "--file", str(self.csv))[0], 0)
        self.write(HEADER + f"{D},bad,a,bindi,n,n,,,\n")
        self.assertEqual(run("check", "--file", str(self.csv))[0], 1)

    def test_missing_file_is_clean_error(self):
        code, _, err = run("--file", str(self.dir / "none.csv"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)
        self.assertNotIn("Traceback", err)

    def test_threshold_validated(self):
        self.write(HEADER)
        for bad in ("0", "-1", "abc", "100000"):
            with self.subTest(bad), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                main(["report", "--file", str(self.csv), "--min-misses", bad])



class RobustnessTests(TempDirTest):
    def test_missing_folder_is_clean_error_not_traceback(self):
        code, _, err = run("add", "--file", str(self.dir / "nope" / "data.csv"),
                           "--item", "a", "--in-stock", "n", "--date", D, "--time", "10:00")
        self.assertEqual(code, 2)
        self.assertIn("Error:", err)

    def test_empty_file_report_and_add(self):
        self.write("")
        code, out, _ = run("--file", str(self.csv))
        self.assertEqual(code, 0)
        self.assertIn("No usable rows", out)
        code, _, _ = run("add", "--file", str(self.csv), "--item", "a", "--in-stock", "n",
                         "--date", D, "--time", "10:00")
        self.assertEqual(code, 0)
        self.assertTrue(self.csv.read_text(encoding="utf-8").startswith(HEADER))
        self.assertEqual(len(read_requests(self.csv)[0]), 1)

    def test_blank_lines_before_header(self):
        self.write("\n\n" + HEADER + f"{D},16:05,a,bindi,n,n,,,\n{D},99:00,b,bindi,n,n,,,\n")
        good, bad = read_requests(self.csv)
        self.assertEqual(len(good), 1)
        self.assertEqual(bad[0][0], 5)  # real line number in the file
        append_request(self.csv, good[0])
        self.assertEqual(len(read_requests(self.csv)[0]), 2)
        aliases = self.write("\nalias,item\nkaali bindi,black bindi\n", "aliases.csv")
        self.assertEqual(store.load_aliases(aliases), {"kaali bindi": "black bindi"})

    def test_hindi_item_round_trip(self):
        code, _, _ = run("add", "--file", str(self.csv), "--item", "काजल",
                         "--in-stock", "n", "--date", D, "--time", "10:00")
        self.assertEqual(code, 0)
        code, out, _ = run("--file", str(self.csv), "--aliases", str(self.dir / "none.csv"))
        self.assertEqual(code, 0)
        self.assertIn("काजल", out)

    def test_exponent_price_saved_plainly(self):
        run("add", "--file", str(self.csv), "--item", "a", "--in-stock", "n", "--price", "1e3",
            "--date", D, "--time", "10:00")
        self.assertIn(",1000,", self.csv.read_text(encoding="utf-8"))

    def test_interactive_second_row_does_not_reuse_time_flag(self):
        from tracker.cli import build_parser, cmd_add
        answers = iter(["a", "1", "n", "", "4", "", "y",              # row 1 (time from --time)
                        "11:30", "b", "1", "n", "", "4", "", "n"])   # row 2 asks time again
        args = build_parser().parse_args(["add", "--file", str(self.csv), "--date", D, "--time", "10:00"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_add(args, input_fn=lambda _: next(answers)), 0)
        good, _ = read_requests(self.csv)
        self.assertEqual([r.when.strftime("%H:%M") for r in good], ["10:00", "11:30"])


if __name__ == "__main__":
    unittest.main()
