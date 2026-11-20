"""Safe reading and appending of the CSV data file.

Defences:
- only regular *.csv files, never symlinks (avoid being tricked into
  reading/writing some other file)
- size, row and field-length limits (a huge or hostile file can't hang the tool)
- strict header check, duplicate-column check
- appends are a single O_APPEND write, so a crash can't leave half a row
- new files are created owner-only (0600)
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Iterable
from pathlib import Path

from .validate import COLUMNS, REQUIRED, Request, ValidationError, clean_text, parse_row, to_csv_row

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_FIELD_CHARS = 1_000
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # not on Windows; is_symlink check still applies

BadRow = tuple[int, list[str]]


class DataFileError(Exception):
    """The file itself is unusable (as opposed to one bad row)."""


def _check_path(path: Path, must_exist: bool = True) -> Path:
    if path.suffix.lower() != ".csv":
        raise DataFileError(f"{path}: only .csv files are allowed")
    if path.is_symlink():
        raise DataFileError(f"{path}: symlinks are not allowed")
    if path.exists():
        if not path.is_file():
            raise DataFileError(f"{path}: not a regular file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise DataFileError(f"{path}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    elif must_exist:
        raise DataFileError(f"{path}: not found")
    return path


def _read_dicts(path: Path, required: Iterable[str]):
    """Yield (line_no, row) from a CSV after header and limit checks."""
    _check_path(path)
    csv.field_size_limit(MAX_FIELD_CHARS)
    if path.stat().st_size == 0:  # brand-new empty file: no rows yet, not an error
        return
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:  # -sig: Excel BOM
            skipped = _skip_blank_lines(f)
            reader = csv.DictReader(f)
            header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
            if len(set(header)) != len(header):
                raise DataFileError(f"{path}: duplicate column names in header")
            missing = [c for c in required if c not in header]
            if missing:
                raise DataFileError(f"{path}: missing columns: {', '.join(missing)}")
            reader.fieldnames = header
            for n, row in enumerate(reader, start=1):
                if n > MAX_ROWS:
                    raise DataFileError(f"{path}: more than {MAX_ROWS} rows")
                yield reader.line_num + skipped, row
    except UnicodeDecodeError:
        raise DataFileError(f"{path}: not UTF-8 text (save it as 'CSV UTF-8')") from None
    except csv.Error as e:
        raise DataFileError(f"{path}: malformed CSV ({e})") from None


def read_requests(path: Path) -> tuple[list[Request], list[BadRow]]:
    good: list[Request] = []
    bad: list[BadRow] = []
    for line_no, row in _read_dicts(path, REQUIRED):
        if None in row:  # DictReader puts extra cells under key None
            bad.append((line_no, ["more cells than header columns"]))
            continue
        try:
            good.append(parse_row(row))
        except ValidationError as e:
            bad.append((line_no, e.problems))
    return good, bad


def load_aliases(path: Path) -> dict[str, str]:
    """Optional alias,item file so 'kajal' and 'lakme kajal' count as one item."""
    if not path.exists():
        return {}
    aliases: dict[str, str] = {}
    for line_no, row in _read_dicts(path, ("alias", "item")):
        try:
            a = clean_text(row.get("alias"), 60).lower()
            i = clean_text(row.get("item"), 60).lower()
        except ValidationError as e:
            raise DataFileError(f"{path} line {line_no}: {e}") from None
        if a and i:
            aliases[a] = i
    return aliases


def _skip_blank_lines(f) -> int:
    """Move past blank lines before the header (editors sometimes leave one); return how many."""
    n = 0
    while True:
        pos = f.tell()
        line = f.readline()
        if line == "" or line.strip():
            f.seek(pos)
            return n
        n += 1


def _header_ok(path: Path) -> bool:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        _skip_blank_lines(f)
        first = next(csv.reader(f), [])
    return [c.strip().lower() for c in first] == list(COLUMNS)


def _ends_with_newline(path: Path) -> bool:
    size = path.stat().st_size
    if size == 0:
        return True
    with path.open("rb") as f:
        f.seek(size - 1)
        return f.read(1) == b"\n"


def append_request(path: Path, req: Request) -> None:
    _check_path(path, must_exist=False)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    if not path.exists():
        writer.writerow(COLUMNS)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW
        mode = 0o600
    elif path.stat().st_size == 0:  # empty file (e.g. created by hand): start it with the header
        writer.writerow(COLUMNS)
        flags = os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW
        mode = 0
    else:
        if not _header_ok(path):
            raise DataFileError(f"{path}: header doesn't match {','.join(COLUMNS)}; not appending")
        if not _ends_with_newline(path):  # file last saved by an editor without trailing newline
            buf.write("\n")
        flags = os.O_WRONLY | os.O_APPEND | _O_NOFOLLOW
        mode = 0

    writer.writerow(to_csv_row(req))
    data = buf.getvalue().encode("utf-8")

    fd = os.open(path, flags, mode)
    try:
        os.write(fd, data)  # single write: the row lands whole or not at all
        os.fsync(fd)
    finally:
        os.close(fd)
