"""Validation for one logged customer request.

Every value that enters the program (CSV rows, keyboard input, CLI flags)
goes through parse_row(). Nothing is trusted just because it is in our file.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from typing import Optional

COLUMNS = ("date", "time", "item", "category", "in_stock", "bought",
           "price_rs", "customer_type", "note")
REQUIRED = ("date", "time", "item", "in_stock", "bought")

CATEGORIES = ("bindi", "bangle", "jewellery", "cosmetic", "hair", "accessories", "stationery", "other")
# common words people type -> the category the report uses
CATEGORY_SYNONYMS = {"makeup": "cosmetic", "beauty": "cosmetic", "cosmetics": "cosmetic",
                     "jewelry": "jewellery", "accessory": "accessories", "chudi": "bangle"}
CLOCK_SKEW = timedelta(minutes=5)  # allow a slightly fast/slow phone clock
CUSTOMER_TYPES = ("student", "local", "other")

MAX_ITEM_LEN = 60
MAX_NOTE_LEN = 200
MAX_PRICE = Decimal("10000")
MIN_YEAR = 2020

# Cells starting with these are run as formulas by Excel / Sheets / LibreOffice.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_YES = {"y", "yes", "1", "true", "haan", "ha"}
_NO = {"n", "no", "0", "false", "nahi", "na"}
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_WS_RE = re.compile(r"\s+")


class ValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Request:
    when: datetime
    item: str
    category: str
    in_stock: bool
    bought: bool
    price: Optional[Decimal]
    customer_type: str
    note: str

    @property
    def hour(self) -> int:
        return self.when.hour


def clean_text(value: Optional[str], max_len: int) -> str:
    """Collapse whitespace, drop control/invisible chars, undo our formula escape.

    Removes Unicode categories Cc (control) and Cf (format: zero-width chars,
    right-to-left overrides) so that two visually identical item names
    can't be counted as different items and terminal output can't be spoofed.
    """
    s = _WS_RE.sub(" ", value or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf"))
    s = s.strip()
    if len(s) > 1 and s[0] == "'" and s[1] in FORMULA_PREFIXES:
        s = s[1:]
    if len(s) > max_len:
        raise ValidationError([f"text longer than {max_len} characters"])
    return s


def escape_formula(value: str) -> str:
    """Neutralise CSV/formula injection (OWASP) before writing a cell."""
    if value and value[0] in FORMULA_PREFIXES:
        return "'" + value
    return value


def parse_yes_no(value: Optional[str]) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in _YES:
        return True
    if v in _NO:
        return False
    return None


def parse_row(raw: Mapping[str, Optional[str]], today: Optional[date] = None,
              now: Optional[datetime] = None) -> Request:
    """Turn one raw row into a Request, or raise ValidationError listing every problem.

    `today` / `now` are injectable for tests. A request can't be logged before it happened,
    so anything later than `now` (plus small clock skew) is rejected.
    """
    if now is None:
        now = datetime.now() if today is None else datetime.combine(today, dtime.max)
    today = today or now.date()
    problems: list[str] = []

    def text(field: str, max_len: int) -> str:
        try:
            return clean_text(raw.get(field), max_len)
        except ValidationError:
            problems.append(f"{field} longer than {max_len} characters")
            return ""

    # date
    d_raw = text("date", 10)
    d: Optional[date] = None
    try:
        d = date.fromisoformat(d_raw)
        if d > today:
            problems.append(f"date {d_raw} is in the future")
        elif d.year < MIN_YEAR:
            problems.append(f"date {d_raw} is before {MIN_YEAR}")
    except ValueError:
        problems.append(f"date='{d_raw}' is not YYYY-MM-DD")

    # time
    t_raw = text("time", 5)
    m = _TIME_RE.match(t_raw)
    if not m:
        problems.append(f"time='{t_raw}' is not HH:MM")

    # item
    item = text("item", MAX_ITEM_LEN).lower()
    if not item:
        problems.append("item is empty")

    # category / customer type (optional, but must be from the list)
    category = text("category", 20).lower() or "other"
    category = CATEGORY_SYNONYMS.get(category, category)
    if category not in CATEGORIES:
        problems.append(f"category='{category}' not one of {', '.join(CATEGORIES)}")
    customer_type = text("customer_type", 20).lower()
    if customer_type and customer_type not in CUSTOMER_TYPES:
        problems.append(f"customer_type='{customer_type}' not one of {', '.join(CUSTOMER_TYPES)}")

    # yes / no
    in_stock = parse_yes_no(raw.get("in_stock"))
    bought = parse_yes_no(raw.get("bought"))
    if in_stock is None:
        problems.append(f"in_stock='{text('in_stock', 10)}' is not y/n")
    if bought is None:
        problems.append(f"bought='{text('bought', 10)}' is not y/n")
    if in_stock is False and bought is True:
        problems.append("bought=y but in_stock=n")

    # price (optional - blank means nobody said a price)
    p_raw = text("price_rs", 12)
    price: Optional[Decimal] = None
    if p_raw:
        try:
            price = Decimal(p_raw)
            if not price.is_finite():
                raise InvalidOperation
            if price < 0 or price > MAX_PRICE:
                problems.append(f"price {p_raw} outside 0-{MAX_PRICE}")
        except InvalidOperation:
            problems.append(f"price_rs='{p_raw}' is not a number")

    note = text("note", MAX_NOTE_LEN)

    if d is not None and m is not None and d == today:
        logged = datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)))
        if logged > now + CLOCK_SKEW:
            problems.append(f"time {t_raw} is later than now ({now:%H:%M})")

    if problems:
        raise ValidationError(problems)

    if d is None or m is None or in_stock is None or bought is None:  # unreachable; keeps type-checkers honest
        raise ValidationError(["internal: incomplete row"])
    return Request(
        when=datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2))),
        item=item, category=category, in_stock=in_stock, bought=bought,
        price=price, customer_type=customer_type, note=note,
    )


def to_csv_row(req: Request) -> list[str]:
    """Serialise a Request, escaping every free-text cell."""
    return [
        req.when.strftime("%Y-%m-%d"),
        req.when.strftime("%H:%M"),
        escape_formula(req.item),
        req.category,
        "y" if req.in_stock else "n",
        "y" if req.bought else "n",
        "" if req.price is None else format(req.price, "f"),  # 1e3 -> 1000, not 1E+3
        req.customer_type,
        escape_formula(req.note),
    ]
