"""Command-line interface.

    python reorder.py                 report (default)
    python reorder.py add             log requests at the counter, one after another
    python reorder.py check           list invalid rows; exit code 1 if any
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .report import build_report, render_text, to_dict
from .store import DataFileError, append_request, load_aliases, read_requests
from .validate import CATEGORIES, CUSTOMER_TYPES, ValidationError, parse_row, parse_yes_no

EXIT_OK, EXIT_INVALID_ROWS, EXIT_ERROR = 0, 1, 2


def _threshold(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 1 <= n <= 1000:
        raise argparse.ArgumentTypeError("must be between 1 and 1000")
    return n


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", type=Path, default=Path("data.csv"), help="data file (default data.csv)")

    ap = argparse.ArgumentParser(prog="reorder.py", description="Missed-sales tracker for a shringar shop.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("report", parents=[common], help="show missed items, reorder list, busy hours")
    r.add_argument("--min-misses", type=_threshold, default=2,
                   help="times an item must be 'not in stock' to go on the reorder list (default 2)")
    r.add_argument("--aliases", type=Path, default=Path("aliases.csv"),
                   help="alias,item file to merge spellings (default aliases.csv, optional)")
    r.add_argument("--json", action="store_true", help="machine-readable output")

    a = sub.add_parser("add", parents=[common], help="log customer requests (asks questions if flags are missing)")
    a.add_argument("--item")
    a.add_argument("--category", choices=CATEGORIES)
    a.add_argument("--in-stock", choices=["y", "n"])
    a.add_argument("--bought", choices=["y", "n"])
    a.add_argument("--price")
    a.add_argument("--customer-type", choices=CUSTOMER_TYPES)
    a.add_argument("--note", default="")
    a.add_argument("--date", help="YYYY-MM-DD (default today)")
    a.add_argument("--time", help="HH:MM (default now)")

    sub.add_parser("check", parents=[common], help="validate the data file")
    return ap


# ---------------------------------------------------------------- commands

def cmd_report(args) -> int:
    requests, bad = read_requests(args.file)
    aliases = load_aliases(args.aliases)
    rep = build_report(requests, args.min_misses, aliases)
    if args.json:
        print(json.dumps(to_dict(rep, bad), indent=2, ensure_ascii=False))
    else:
        print(render_text(rep, bad))
    return EXIT_OK


def cmd_check(args) -> int:
    requests, bad = read_requests(args.file)
    for line_no, problems in bad:
        print(f"line {line_no}: {'; '.join(problems)}")
    print(f"{len(requests)} valid, {len(bad)} invalid")
    return EXIT_INVALID_ROWS if bad else EXIT_OK


def _ask(prompt: str, input_fn: Callable[[str], str], default: Optional[str] = None,
         choices: Optional[Sequence[str]] = None) -> str:
    hint = f" [{default}]" if default else ""
    if choices:
        menu = "  ".join(f"{i}={c}" for i, c in enumerate(choices, 1))
        prompt = f"{prompt} ({menu})"
    while True:
        ans = input_fn(f"{prompt}{hint}: ").strip()
        if not ans and default is not None:
            return default
        if choices:
            if ans.isdigit() and 1 <= int(ans) <= len(choices):
                return choices[int(ans) - 1]
            if ans.lower() in choices:
                return ans.lower()
            print(f"  pick 1-{len(choices)}")
            continue
        return ans


def _ask_yn(prompt: str, input_fn: Callable[[str], str], default: Optional[str] = None) -> str:
    while True:
        ans = _ask(f"{prompt} y/n", input_fn, default)
        if parse_yes_no(ans) is not None:
            return ans
        print("  type y or n")


def cmd_add(args, input_fn: Callable[[str], str] = input) -> int:
    # Quick one-line mode: item + in-stock, plus bought unless the item wasn't in stock
    # (nothing to buy then, so bought is saved as n below).
    flags_given = (args.item is not None and args.in_stock is not None
                   and (args.bought is not None or parse_yes_no(args.in_stock) is False))
    while True:
        now = datetime.now()
        raw = {
            "date": args.date or now.strftime("%Y-%m-%d"),
            "time": args.time or (now.strftime("%H:%M") if flags_given
                                  else _ask("Time", input_fn, now.strftime("%H:%M"))),
            "item": args.item if args.item is not None else _ask("Item asked for", input_fn),
            "category": args.category or (
                "other" if flags_given else _ask("Category", input_fn, "other", CATEGORIES)),
            "in_stock": args.in_stock or _ask_yn("In stock?", input_fn),
            "price_rs": args.price if args.price is not None else (
                "" if flags_given else _ask("Price said (blank if none)", input_fn, "")),
            "customer_type": args.customer_type or (
                "" if flags_given else _ask("Customer", input_fn, "", ["student", "local", "other", "skip"])),
            "note": args.note or ("" if flags_given else _ask("Note (optional)", input_fn, "")),
        }
        if raw["customer_type"] == "skip":
            raw["customer_type"] = ""
        if args.bought:
            raw["bought"] = args.bought
        elif parse_yes_no(raw["in_stock"]) is False:
            raw["bought"] = "n"  # can't buy what isn't there - don't ask
        else:
            raw["bought"] = _ask_yn("Bought?", input_fn)

        try:
            req = parse_row(raw)
        except ValidationError as e:
            print(f"Not saved: {e}", file=sys.stderr)
            if flags_given:
                return EXIT_ERROR
            continue  # ask again
        append_request(args.file, req)
        print(f"Saved: {req.when:%H:%M} {req.item} (in stock: {'y' if req.in_stock else 'n'}, "
              f"bought: {'y' if req.bought else 'n'})")

        if flags_given or not parse_yes_no(_ask_yn("Another?", input_fn, "y")):
            return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "report")
    args = build_parser().parse_args(argv)
    try:
        return {"report": cmd_report, "add": cmd_add, "check": cmd_check}[args.cmd](args)
    except DataFileError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Rows already saved are kept.", file=sys.stderr)
        return EXIT_OK
