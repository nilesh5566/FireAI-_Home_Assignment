# Missed-sales tracker: Priya Shringar Kendra, IIIT Road, Jhalwa

![ci](../../actions/workflows/ci.yml/badge.svg)

**Who it's for:** ___ (owner), Priya Shringar Kendra, 4 IIIT Rd, Jhalwa, Prayagraj 211015.
**Problem:** customers ask for items the shop doesn't stock, and those requests are never written down, so they never get reordered.
**What it does:** you log each request at the counter in about 10 seconds. The report shows what was missed, roughly how much money that cost, a reorder checklist, and the busiest hours.

## Quick start
Needs Python 3.9 or newer. There's nothing to install.

```bash
python reorder.py add                  # log requests one by one (Enter keeps the default)
python reorder.py                      # report
python reorder.py --min-misses 3       # stricter reorder threshold
python reorder.py report --json        # machine-readable output
python reorder.py check                # list invalid rows (exit code 1 if any)
```

Quick one-line add, for typing up notebook entries later:
```bash
python reorder.py add --item "black bindi" --category bindi --in-stock n --price 20 --time 16:07
```
(`--bought` is only needed when the item was in stock; otherwise it's saved as `n`.)

What `add` looks like at the counter:
```
Time [16:07]:
Item asked for: glass bangle 2.6
Category (1=bindi  2=bangle  3=jewellery  4=cosmetic  5=hair  6=accessories  7=stationery  8=other) [other]: 2
In stock? y/n: n
Price said (blank if none): 45
Customer (1=student  2=local  3=other  4=skip): 1
Note (optional): size not there
Saved: 16:07 glass bangle 2.6 (in stock: n, bought: n)
Another? y/n [y]:
```
If an item is not in stock, the tool doesn't ask "bought?". It saves `n` automatically.

**Merging spellings:** add lines to `aliases.csv`, for example `lakme kajal,kajal`, so that both names count as one item.

## Data
`data.csv`: one row per customer request, logged by me at the counter on ___ (see fieldwork.md).

| column | meaning |
|---|---|
| date, time | when the customer asked (YYYY-MM-DD, HH:MM) |
| item | what they asked for, in their words |
| category | bindi / bangle / jewellery / cosmetic / hair / accessories / stationery / other |
| in_stock, bought | y/n (Hindi `haan` / `nahi` also accepted) |
| price_rs | price the owner quoted; **blank if none was said** |
| customer_type | student / local / other / blank |
| note | anything else |

## Real output
```
<paste the output of `python reorder.py` here>
```

## Project layout
```
reorder.py           entry point
tracker/validate.py  every input is checked here (one place to change rules)
tracker/store.py     safe CSV read/append
tracker/report.py    the maths
tracker/cli.py       commands and prompts
tests/               39 unit tests
SECURITY.md          threat model and the defences in the code
```

## Tests and security checks
```bash
python -m unittest discover -s tests -t . -v
pip install ruff bandit && ruff check . && bandit -c pyproject.toml -r tracker
```
Covered: formula injection, invisible Unicode, oversized files and fields, symlinks, non-UTF-8 files, bad headers, crash-safe appends, `0600` file permissions. Details are in [SECURITY.md](SECURITY.md).

## What it gets wrong / don't trust it for
- One afternoon is not a season. The wedding season and Karva Chauth change demand completely.
- "Lost money" is the average quoted price × the number of misses. Some of those customers would have bought a substitute.
- It counts *requests*, not *people*. One customer asking twice counts twice.
- Free-text item names need `aliases.csv` to merge spellings.
- ___ (add what you actually notice)

## Screen recording
[3-minute demo](link)
