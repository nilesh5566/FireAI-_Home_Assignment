#!/usr/bin/env python3
"""
restock.py - reads a small shop's hand-collected sales log (data.csv) and
suggests how much of each item to stock for tomorrow.

Expected columns (header row required, order does not matter):
    date,time,item,category,in_stock,bought,price_rs,customer_type,note

Uses only the Python standard library. No network access, no eval, no shell.

Run:  python restock.py data.csv
Help: python restock.py --help
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings you may want to change live during the call.
# All of these can also be overridden from the command line.
# ---------------------------------------------------------------------------
SAFETY_MARGIN = 0.20      # stock 20% more than the expected sales
WINDOW_DAYS = 7           # use the last N observed days for the forecast
MIN_DAYS = 3              # fewer observed days than this -> "low confidence"
LOW_STOCK_UNITS = 3       # in_stock at or below this counts as "running low"

# ---------------------------------------------------------------------------
# Security limits. These protect against huge / malicious input files.
# ---------------------------------------------------------------------------
MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB is far more than a hand-collected log
MAX_ROWS = 50_000
MAX_FIELD_CHARS = 200
MAX_BOUGHT = 10_000
MAX_STOCK = 100_000
MAX_PRICE = 1_000_000
EARLIEST_DATE = date(2000, 1, 1)

REQUIRED_COLUMNS = [
    "date", "time", "item", "category", "in_stock",
    "bought", "price_rs", "customer_type", "note",
]
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p")
YES = {"yes", "y", "true", "available", "in", "haan", "ha"}
NO = {"no", "n", "false", "out", "nil", "none", "finished", "khatam", "nahi"}

# Characters that make spreadsheet apps treat a cell as a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class DataFileError(Exception):
    """The file as a whole cannot be used (missing, too big, bad header...)."""


# ---------------------------------------------------------------------------
# Small sanitising helpers
# ---------------------------------------------------------------------------
def clean_text(value: str | None) -> str:
    """Normalise unicode, drop control chars (incl. terminal escape codes),
    collapse whitespace."""
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL_RE.sub("", value)
    return " ".join(value.split())


def spreadsheet_safe(value):
    """Stop CSV/formula injection when the report is opened in Excel/Sheets."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def item_key(name: str) -> str:
    """'Milk ', 'MILK' and 'milk' are the same item."""
    return name.casefold()


# ---------------------------------------------------------------------------
# Field parsers. Each returns a value or raises ValueError with a clear reason.
# ---------------------------------------------------------------------------
def parse_date(raw: str, today: date) -> date:
    if not raw:
        raise ValueError("date is empty")
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"date '{raw}' not understood (use YYYY-MM-DD or DD-MM-YYYY)")
    if d < EARLIEST_DATE:
        raise ValueError(f"date {d} is too old")
    if d > today + timedelta(days=1):
        raise ValueError(f"date {d} is in the future")
    return d


def parse_time(raw: str) -> time | None:
    if not raw:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(raw.upper(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"time '{raw}' not understood (use HH:MM, e.g. 18:05)")


def _parse_number(raw: str, what: str) -> float:
    cleaned = raw.replace(",", "").replace("₹", "")
    cleaned = re.sub(r"(?i)^\s*(rs\.?|inr)\s*", "", cleaned).strip()
    # Only plain decimal numbers: rejects 'nan', 'inf', '1e9', hex, etc.
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        raise ValueError(f"{what} '{raw}' is not a number")
    num = float(cleaned)
    if not math.isfinite(num):
        raise ValueError(f"{what} '{raw}' is not a finite number")
    return num


def parse_bought(raw: str) -> int:
    """Units bought. Also accepts yes/no ('y' = bought 1, 'n' = bought nothing)."""
    if not raw:
        raise ValueError("bought is empty")
    low = raw.casefold()
    if low in YES:
        return 1
    if low in NO:
        return 0
    if not re.fullmatch(r"\s*-?[\d.,]+\s*", raw):
        raise ValueError(f"bought '{raw}' must be a number or y/n")
    num = _parse_number(raw, "bought")
    if num != int(num):
        raise ValueError(f"bought '{raw}' must be a whole number")
    if num < 0:
        raise ValueError(f"bought '{raw}' cannot be negative")
    if num > MAX_BOUGHT:
        raise ValueError(f"bought '{raw}' is unrealistically large (> {MAX_BOUGHT})")
    return int(num)


def parse_in_stock(raw: str) -> tuple[int | None, bool | None]:
    """Returns (count, is_out). in_stock may be a count or yes/no. Empty is allowed."""
    if not raw:
        return None, None
    low = raw.casefold()
    if low in YES:
        return None, False
    if low in NO:
        return 0, True
    num = _parse_number(raw, "in_stock")
    if num != int(num) or num < 0 or num > MAX_STOCK:
        raise ValueError(f"in_stock '{raw}' must be a whole number 0-{MAX_STOCK} or yes/no")
    return int(num), int(num) == 0


def parse_price(raw: str) -> float | None:
    if not raw:
        return None
    num = _parse_number(raw, "price_rs")
    if num < 0 or num > MAX_PRICE:
        raise ValueError(f"price_rs '{raw}' must be between 0 and {MAX_PRICE}")
    return round(num, 2)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
@dataclass
class Row:
    line: int
    day: date
    at: time | None
    item: str
    category: str
    stock_count: int | None
    out_of_stock: bool | None
    bought: int
    price: float | None
    customer_type: str
    note: str


@dataclass
class LoadResult:
    rows: list[Row] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)


def check_input_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.suffix.lower() != ".csv":
        raise DataFileError(f"'{path.name}' is not a .csv file")
    try:
        real = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise DataFileError(f"file not found: {path_str}") from None
    if not real.is_file():
        raise DataFileError(f"not a regular file: {path_str}")
    size = real.stat().st_size
    if size == 0:
        raise DataFileError("file is empty")
    if size > MAX_FILE_BYTES:
        raise DataFileError(f"file is {size} bytes; limit is {MAX_FILE_BYTES}")
    return real


def load_csv(path_str: str, today: date | None = None) -> LoadResult:
    today = today or date.today()
    path = check_input_path(path_str)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise DataFileError(f"cannot read file: {exc.strerror}") from None
    if b"\x00" in raw_bytes:
        raise DataFileError("file contains NUL bytes; is it really a CSV?")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Windows often saves as cp1252.
        try:
            text = raw_bytes.decode("cp1252")
        except UnicodeDecodeError:
            raise DataFileError("file is not UTF-8 text; re-save it as 'CSV UTF-8'") from None

    csv.field_size_limit(MAX_FIELD_CHARS * 20)
    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        raise DataFileError("file has no header row") from None
    except csv.Error as exc:
        raise DataFileError(f"header row is broken: {exc}") from None

    header = [clean_text(h).casefold() for h in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    extra = [h for h in header if h not in REQUIRED_COLUMNS]
    if missing:
        raise DataFileError(f"missing column(s): {', '.join(missing)}")
    if extra:
        raise DataFileError(f"unexpected column(s): {', '.join(extra)}")
    if len(set(header)) != len(header):
        raise DataFileError("duplicate column names in header")
    idx = {name: header.index(name) for name in REQUIRED_COLUMNS}

    result = LoadResult()
    seen = {}
    line_no = 1
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            line_no += 1
            result.rejected.append((line_no, f"broken CSV line: {exc}"))
            continue
        line_no += 1
        if line_no - 1 > MAX_ROWS:
            raise DataFileError(f"more than {MAX_ROWS} rows; refusing to process")
        if not any(c.strip() for c in cells):
            continue  # blank line
        if len(cells) != len(header):
            result.rejected.append(
                (line_no, f"has {len(cells)} values, expected {len(header)} (check commas)"))
            continue
        if any(len(c) > MAX_FIELD_CHARS for c in cells):
            result.rejected.append((line_no, f"a value is longer than {MAX_FIELD_CHARS} chars"))
            continue
        v = {name: clean_text(cells[i]) for name, i in idx.items()}
        try:
            if not v["item"]:
                raise ValueError("item is empty")
            count, out = parse_in_stock(v["in_stock"])
            row = Row(
                line=line_no,
                day=parse_date(v["date"], today),
                at=parse_time(v["time"]),
                item=v["item"],
                category=v["category"] or "uncategorised",
                stock_count=count,
                out_of_stock=out,
                bought=parse_bought(v["bought"]),
                price=parse_price(v["price_rs"]),
                customer_type=v["customer_type"].casefold() or "unknown",
                note=v["note"],
            )
        except ValueError as exc:
            result.rejected.append((line_no, str(exc)))
            continue
        fingerprint = tuple(v[c].casefold() for c in REQUIRED_COLUMNS)
        if fingerprint in seen:
            result.duplicates.append(line_no)  # kept, but flagged for the user
        seen[fingerprint] = line_no
        result.rows.append(row)
    return result


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
@dataclass
class ItemReport:
    item: str
    category: str
    days_observed: int
    customers: int
    total_sold: int
    lost_no_stock: int
    walked_away: int
    lost_rs: float
    avg_per_day: float
    max_day: int
    revenue_rs: float
    last_stock: int | None
    stockout_days: int
    forecast: float
    suggest_order: int | None
    confidence: str
    warning: str


def analyse(rows: list[Row], safety: float = SAFETY_MARGIN, window: int = WINDOW_DAYS,
            min_days: int = MIN_DAYS, low_stock: int = LOW_STOCK_UNITS,
            price_is_total: bool = False) -> list[ItemReport]:
    if not rows:
        return []
    # Every date in the log counts as a day you watched the shop. An item with
    # no rows on such a day sold 0 that day.
    all_days = sorted({r.day for r in rows})
    recent_days = all_days[-window:]

    by_item: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_item[item_key(r.item)].append(r)

    reports = []
    for rs in by_item.values():
        rs.sort(key=lambda r: (r.day, r.at or time.min, r.line))
        name = rs[-1].item
        category = rs[-1].category
        daily = defaultdict(int)        # units actually sold
        demand = defaultdict(int)       # units sold + customers turned away (no stock)
        lost = walked = 0
        lost_rs = 0.0
        for r in rs:
            daily[r.day] += r.bought
            demand[r.day] += r.bought
            if r.bought == 0 and r.out_of_stock:
                # Wanted it, couldn't get it: a lost sale. Count it as demand.
                demand[r.day] += 1
                lost += 1
                lost_rs += r.price or 0.0
            elif r.bought == 0:
                walked += 1             # it was there, they still did not buy
        series = [demand.get(d, 0) for d in recent_days]
        total = sum(daily.values())
        avg = total / len(all_days)
        recent_avg = statistics.mean(series)
        spread = statistics.pstdev(series) if len(series) > 1 else 0.0
        forecast = recent_avg + spread * 0.5
        target = math.ceil(forecast * (1 + safety)) if forecast > 0 else 0

        stock_rows = [r for r in rs if r.stock_count is not None]
        last_stock = stock_rows[-1].stock_count if stock_rows else None
        suggest = max(0, target - last_stock) if last_stock is not None else None
        stockout_days = len({r.day for r in rs if r.out_of_stock})

        revenue = 0.0
        for r in rs:
            if r.price is not None:
                revenue += r.price if price_is_total else r.price * r.bought

        days_with_rows = len(daily)
        confidence = "ok" if days_with_rows >= min_days else "LOW"
        warnings = []
        if lost:
            warnings.append(f"{lost} customer(s) turned away, no stock")
        elif stockout_days:
            warnings.append(f"ran out on {stockout_days} day(s): real demand may be higher")
        if last_stock is not None and last_stock <= low_stock:
            warnings.append(f"only {last_stock} left now")
        if last_stock is None:
            warnings.append(f"no stock count; stock about {target} total")

        reports.append(ItemReport(
            item=name, category=category, days_observed=days_with_rows,
            customers=len(rs), total_sold=total, lost_no_stock=lost, walked_away=walked,
            lost_rs=round(lost_rs, 2), avg_per_day=round(avg, 2), max_day=max(daily.values()),
            revenue_rs=round(revenue, 2), last_stock=last_stock,
            stockout_days=stockout_days, forecast=round(forecast, 2),
            suggest_order=suggest, confidence=confidence, warning="; ".join(warnings),
        ))
    # Most urgent first: ran out / low stock, then biggest sellers.
    def urgency(r: ItemReport):
        low = r.stockout_days > 0 or (r.last_stock is not None and r.last_stock <= low_stock)
        return (-r.lost_no_stock, -int(low), -r.total_sold, r.item.casefold())
    reports.sort(key=urgency)
    return reports


def busy_hours(rows: list[Row]) -> list[tuple[int, int]]:
    counts = defaultdict(int)
    for r in rows:
        if r.at is not None:
            counts[r.at.hour] += 1     # one row = one customer
    return sorted(counts.items())


def customer_mix(rows: list[Row]) -> list[tuple[str, int, int]]:
    """(customer_type, customers who bought, all customers) per type."""
    total = defaultdict(int)
    buyers = defaultdict(int)
    for r in rows:
        total[r.customer_type] += 1
        if r.bought > 0:
            buyers[r.customer_type] += 1
    return sorted(((k, buyers[k], n) for k, n in total.items()), key=lambda t: -t[2])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    lines = []
    for n, row in enumerate(cells):
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def print_report(load: LoadResult, reports: list[ItemReport], rows: list[Row],
                 show_rejected: int, out=None) -> None:
    out = out or sys.stdout
    days = sorted({r.day for r in rows})
    p = lambda *a: print(*a, file=out)
    p(f"Rows used: {len(rows)}   Rejected: {len(load.rejected)}   "
      f"Days: {len(days)} ({days[0]} to {days[-1]})" if days else "No usable rows.")
    if load.rejected:
        p("\nRejected rows (line number in the file: reason):")
        for line, reason in load.rejected[:show_rejected]:
            p(f"  line {line}: {reason}")
        if len(load.rejected) > show_rejected:
            p(f"  ... and {len(load.rejected) - show_rejected} more (use --show-rejected N)")
    if load.duplicates:
        p(f"\nPossible duplicate rows (kept, please check): lines "
          f"{', '.join(map(str, load.duplicates[:20]))}")
    if not reports:
        return
    p("\nSUGGESTION FOR TOMORROW")
    p(_table(
        ["item", "customers", "sold", "lost", "avg/day", "best day", "stock now", "expect",
         "ORDER", "conf", "warning"],
        [[r.item, r.customers, r.total_sold, r.lost_no_stock, r.avg_per_day, r.max_day,
          "?" if r.last_stock is None else r.last_stock, r.forecast,
          "-" if r.suggest_order is None else r.suggest_order, r.confidence, r.warning]
         for r in reports]))
    total_rev = sum(r.revenue_rs for r in reports)
    p(f"\nRevenue in the log: Rs {total_rev:,.2f}")
    lost_total = sum(r.lost_no_stock for r in reports)
    if lost_total:
        lost_rev = sum(r.lost_rs for r in reports)
        p(f"Customers turned away (item out of stock): {lost_total}"
          + (f", about Rs {lost_rev:,.2f} of sales lost" if lost_rev else ""))
    walked_total = sum(r.walked_away for r in reports)
    if walked_total:
        p(f"Customers who did not buy although it was in stock: {walked_total}")
    hours = busy_hours(rows)
    if hours:
        peak = max(hours, key=lambda h: h[1])
        p("\nCustomers by hour:")
        top = peak[1] or 1
        for hour, n in hours:
            p(f"  {hour:02d}:00  {'#' * max(1, round(20 * n / top)) if n else ''} {n}")
    mix = customer_mix(rows)
    if mix:
        p("\nCustomers by type (bought / total): "
          + ", ".join(f"{k} {b}/{n}" for k, b, n in mix))


def safe_output_path(path_str: str, input_path: Path, allowed_suffixes: tuple[str, ...]) -> Path:
    """Only write inside the current folder, never over the input, never through a symlink."""
    out = Path(path_str)
    if out.suffix.lower() not in allowed_suffixes:
        raise DataFileError(f"output must end with {' or '.join(allowed_suffixes)}")
    base = Path.cwd().resolve()
    target = (base / out).resolve() if not out.is_absolute() else out.resolve()
    if base != target and base not in target.parents:
        raise DataFileError("output file must be inside the current folder")
    if target == input_path:
        raise DataFileError("refusing to overwrite the input data file")
    if out.is_symlink() or target.is_dir():
        raise DataFileError("output path is a symlink or a folder")
    return target


def _open_private(path: Path):
    """Create the file readable only by the current user (where the OS supports it)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="")


def export_csv(reports: list[ItemReport], path: Path) -> None:
    cols = list(ItemReport.__dataclass_fields__)
    with _open_private(path) as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in reports:
            w.writerow([spreadsheet_safe(getattr(r, c)) for c in cols])


def export_json(load: LoadResult, reports: list[ItemReport], path: Path) -> None:
    data = {
        "rows_used": len(load.rows),
        "rejected": [{"line": l, "reason": why} for l, why in load.rejected],
        "items": [r.__dict__ for r in reports],
    }
    with _open_private(path) as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def write_clean_copy(rows: list[Row], path: Path, drop_notes: bool) -> None:
    """Valid rows only, in a single date format. Useful before publishing data."""
    with _open_private(path) as fh:
        w = csv.writer(fh)
        w.writerow(REQUIRED_COLUMNS)
        for r in sorted(rows, key=lambda r: (r.day, r.at or time.min, r.line)):
            if r.stock_count is not None:
                stock = r.stock_count
            elif r.out_of_stock is False:
                stock = "yes"
            else:
                stock = ""
            w.writerow([spreadsheet_safe(x) for x in [
                r.day.isoformat(), r.at.strftime("%H:%M") if r.at else "", r.item,
                r.category, stock, r.bought, "" if r.price is None else r.price,
                r.customer_type, "" if drop_notes else r.note]])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fraction(text: str) -> float:
    v = float(text)
    if not math.isfinite(v) or not 0 <= v <= 5:
        raise argparse.ArgumentTypeError("must be between 0 and 5 (e.g. 0.2 = 20%)")
    return v


def _positive_int(text: str) -> int:
    v = int(text)
    if not 1 <= v <= 365:
        raise argparse.ArgumentTypeError("must be between 1 and 365")
    return v


def _non_negative_int(text: str) -> int:
    v = int(text)
    if not 0 <= v <= MAX_STOCK:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_STOCK}")
    return v


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Suggest tomorrow's stock for each item from a hand-collected sales log.")
    ap.add_argument("csv_file", help="path to data.csv")
    ap.add_argument("--safety", type=_fraction, default=SAFETY_MARGIN,
                    help=f"extra stock as a fraction (default {SAFETY_MARGIN})")
    ap.add_argument("--window", type=_positive_int, default=WINDOW_DAYS,
                    help=f"use the last N observed days (default {WINDOW_DAYS})")
    ap.add_argument("--min-days", type=_positive_int, default=MIN_DAYS,
                    help=f"days of data needed for 'ok' confidence (default {MIN_DAYS})")
    ap.add_argument("--low-stock", type=_non_negative_int, default=LOW_STOCK_UNITS,
                    help=f"warn when stock is at or below this (default {LOW_STOCK_UNITS})")
    ap.add_argument("--item", help="show only items whose name contains this text")
    ap.add_argument("--category", help="show only this category")
    ap.add_argument("--price-is-total", action="store_true",
                    help="price_rs is the total paid for the row, not the price per unit")
    ap.add_argument("--show-rejected", type=_non_negative_int, default=10,
                    help="how many rejected rows to list (default 10)")
    ap.add_argument("--strict", action="store_true",
                    help="exit with code 3 if any row was rejected")
    ap.add_argument("--export", metavar="FILE.csv", help="also save the suggestions as CSV")
    ap.add_argument("--json", metavar="FILE.json", help="also save everything as JSON")
    ap.add_argument("--clean", metavar="FILE.csv",
                    help="save only the valid rows to a new CSV (input is never changed)")
    ap.add_argument("--drop-notes", action="store_true",
                    help="with --clean: blank the note column (privacy)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        input_path = check_input_path(args.csv_file)
        # Check every output path before doing any work, so we fail fast.
        out_csv = safe_output_path(args.export, input_path, (".csv",)) if args.export else None
        out_json = safe_output_path(args.json, input_path, (".json",)) if args.json else None
        out_clean = safe_output_path(args.clean, input_path, (".csv",)) if args.clean else None
        if len({p for p in (out_csv, out_json, out_clean) if p}) < \
                len([p for p in (out_csv, out_json, out_clean) if p]):
            raise DataFileError("--export, --json and --clean must use different file names")
        if args.drop_notes and not args.clean:
            raise DataFileError("--drop-notes only works together with --clean")

        load = load_csv(args.csv_file)
        rows = load.rows
        if args.category:
            want = clean_text(args.category).casefold()
            rows = [r for r in rows if r.category.casefold() == want]
        if args.item:
            want = clean_text(args.item).casefold()
            rows = [r for r in rows if want in r.item.casefold()]

        reports = analyse(rows, safety=args.safety, window=args.window,
                          min_days=args.min_days, low_stock=args.low_stock,
                          price_is_total=args.price_is_total)
        print_report(load, reports, rows, args.show_rejected)
        if not load.rows:
            if load.rejected:
                print("\nNo valid rows found. Fix the rejected lines above.", file=sys.stderr)
            else:
                print("\nThe file has a header but no data rows yet. "
                      "Add one line per sale under the header.", file=sys.stderr)
            return 1
        if (args.item or args.category) and not rows:
            print("\nNo rows match that filter.", file=sys.stderr)

        if out_csv:
            export_csv(reports, out_csv)
            print(f"\nSaved suggestions to {out_csv.name}")
        if out_json:
            export_json(load, reports, out_json)
            print(f"Saved JSON to {out_json.name}")
        if out_clean:
            write_clean_copy(load.rows, out_clean, args.drop_notes)
            print(f"Saved {len(load.rows)} valid rows to {out_clean.name}")
    except DataFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error: could not write file ({exc.strerror})", file=sys.stderr)
        return 1
    if args.strict and load.rejected:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())