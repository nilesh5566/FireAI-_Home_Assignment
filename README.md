# Missed-sales tracker: Priya Shringar Sadan, IIIT Road, Jhalwa

![ci](../../actions/workflows/ci.yml/badge.svg)

**Who it's for:** ___ (owner), Priya Shringar Sadan, 4 IIIT Rd, Jhalwa, Prayagraj 211015.
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
From `python reorder.py` on the committed `data.csv` (140 requests, 2026-10-21 to 2026-11-20):
```
Rows: 140  usable: 140  skipped: 0
Requests: 140  sold: 91  not in stock: 13  in stock but not bought: 36

== Asked for but NOT in stock ==
  glass bangle                   2x   ~Rs 80 lost (avg Rs 40)
  hair clip                      2x   ~Rs 40 lost (avg Rs 20)
  hair pin pack                  2x   ~Rs 20 lost (avg Rs 10)
  safety pin pack                2x   ~Rs 19 lost (avg Rs 10)
  bangle set                     1x   ~Rs 150 lost (avg Rs 150)
  hair band                      1x   ~Rs 28 lost (avg Rs 28)
  maroon bindi                   1x   ~Rs 15 lost (avg Rs 15)
  nose pin                       1x   ~Rs 23 lost (avg Rs 23)
  scrunchie                      1x   ~Rs 35 lost (avg Rs 35)
  Estimated lost sales: ~Rs 410

== Reorder list (missed 2+ times) ==
  [ ] glass bangle  (asked 2x)
  [ ] hair clip  (asked 2x)
  [ ] hair pin pack  (asked 2x)
  [ ] safety pin pack  (asked 2x)

== Requests by hour ==
  10:00  ### 3
  11:00  ######## 9
  12:00  ########### 13
  13:00  ######### 10
  14:00  ###### 7
  15:00  ########### 13
  16:00  ############## 16
  17:00  ################# 20
  18:00  #################### 23
  19:00  ############### 17
  20:00  ######## 9
```
To try a stricter list live: `python reorder.py --min-misses 3`.

## Project layout
```
reorder.py           entry point
tracker/validate.py  every input is checked here (one place to change rules)
tracker/store.py     safe CSV read/append
tracker/report.py    the maths
tracker/cli.py       commands and prompts
tests/               39 unit tests
SECURITY.md          threat model and the defences in the code
data.csv             the logged requests (the real data)
aliases.csv          spellings to merge, e.g. chudi -> glass bangle
photos/              photos from the shop
ai-chats/            exported AI chats used while building
examples/            AI-generated demo data, clearly not fieldwork
```

## Tests and security checks
```bash
python -m unittest discover -s tests -t . -v
pip install ruff bandit && ruff check . && bandit -c pyproject.toml -r tracker
```
Covered: formula injection, invisible Unicode, oversized files and fields, symlinks, non-UTF-8 files, bad headers, crash-safe appends, `0600` file permissions. Details are in [SECURITY.md](SECURITY.md).

## What it gets wrong / don't trust it for
- One month of evenings is not a season. The wedding season and Karva Chauth change demand completely.
- 13 misses in 140 requests is a small sample: an item asked for twice makes the reorder list, which could be chance.
- "Lost money" is the average quoted price × the number of misses. Some of those customers would have bought a substitute.
- It counts *requests*, not *people*. One customer asking twice counts twice.
- Free-text item names need `aliases.csv` to merge spellings.

## Screen recording
[3-minute demo](https://drive.google.com/file/d/1oERbwV_61Q42jVSgQj0slO4aWckz3EDE/view?usp=sharing)
