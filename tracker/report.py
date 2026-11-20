"""Turn validated requests into the owner's report."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .store import BadRow
from .validate import Request


@dataclass
class MissedItem:
    item: str
    times: int
    avg_price: Optional[Decimal]

    @property
    def est_lost(self) -> Optional[Decimal]:
        return None if self.avg_price is None else self.avg_price * self.times


@dataclass
class Report:
    total: int
    sold: int
    not_in_stock: int
    in_stock_not_bought: int
    min_misses: int
    days: int
    missed: list[MissedItem] = field(default_factory=list)
    by_hour: dict[int, int] = field(default_factory=dict)

    @property
    def reorder(self) -> list[MissedItem]:
        return [m for m in self.missed if m.times >= self.min_misses]

    @property
    def total_lost(self) -> Decimal:
        return sum((m.est_lost for m in self.missed if m.est_lost is not None), Decimal(0))


def _rs(x: Decimal) -> str:
    return f"Rs {x.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"


def build_report(requests: list[Request], min_misses: int,
                 aliases: Optional[dict[str, str]] = None) -> Report:
    aliases = aliases or {}

    def name(r: Request) -> str:
        return aliases.get(r.item, r.item)

    prices: dict[str, list[Decimal]] = defaultdict(list)
    for r in requests:
        if r.price is not None:
            prices[name(r)].append(r.price)

    misses = Counter(name(r) for r in requests if not r.in_stock)
    missed = []
    for item, n in sorted(misses.items(), key=lambda kv: (-kv[1], kv[0])):
        p = prices.get(item)
        avg = (sum(p) / len(p)) if p else None
        missed.append(MissedItem(item, n, avg))

    return Report(
        total=len(requests),
        sold=sum(r.bought for r in requests),
        not_in_stock=sum(not r.in_stock for r in requests),
        in_stock_not_bought=sum(r.in_stock and not r.bought for r in requests),
        min_misses=min_misses,
        days=len({r.when.date() for r in requests}),
        missed=missed,
        by_hour=dict(sorted(Counter(r.hour for r in requests).items())),
    )


def render_text(rep: Report, bad: list[BadRow]) -> str:
    out = [f"Rows: {rep.total + len(bad)}  usable: {rep.total}  skipped: {len(bad)}"]
    for line_no, problems in bad:
        out.append(f"  skipped line {line_no}: {'; '.join(problems)}")
    if rep.total == 0:
        out.append("No usable rows yet. Log some with:  python reorder.py add")
        return "\n".join(out)

    out.append(f"Requests: {rep.total}  sold: {rep.sold}  not in stock: {rep.not_in_stock}  "
               f"in stock but not bought: {rep.in_stock_not_bought}")

    out.append("\n== Asked for but NOT in stock ==")
    if not rep.missed:
        out.append("  None - everything asked for was available.")
    for m in rep.missed:
        money = (f"~{_rs(m.est_lost)} lost (avg {_rs(m.avg_price)})"
                 if m.avg_price is not None else "(no price noted)")
        out.append(f"  {m.item:<28} {m.times:>3}x   {money}")
    if rep.total_lost:
        out.append(f"  Estimated lost sales: ~{_rs(rep.total_lost)}")

    out.append(f"\n== Reorder list (missed {rep.min_misses}+ times) ==")
    if rep.reorder:
        out.extend(f"  [ ] {m.item}  (asked {m.times}x)" for m in rep.reorder)
    else:
        out.append(f"  Nothing missed {rep.min_misses}+ times.")

    out.append("\n== Requests by hour ==")
    peak = max(rep.by_hour.values())
    for h, n in rep.by_hour.items():
        out.append(f"  {h:02d}:00  {'#' * max(1, round(20 * n / peak))} {n}")

    if rep.days < 3:
        out.append(f"\nNote: only {rep.days} day(s) of data - the reorder list is a hint, not a rule.")
    return "\n".join(out)


def to_dict(rep: Report, bad: list[BadRow]) -> dict:
    def num(x: Optional[Decimal]):
        return None if x is None else float(x.quantize(Decimal("0.01")))

    return {
        "total": rep.total, "sold": rep.sold, "not_in_stock": rep.not_in_stock,
        "in_stock_not_bought": rep.in_stock_not_bought, "days": rep.days,
        "min_misses": rep.min_misses,
        "missed": [{"item": m.item, "times": m.times, "avg_price": num(m.avg_price),
                    "est_lost": num(m.est_lost)} for m in rep.missed],
        "reorder": [m.item for m in rep.reorder],
        "by_hour": {f"{h:02d}": n for h, n in rep.by_hour.items()},
        "skipped": [{"line": ln, "problems": p} for ln, p in bad],
    }
