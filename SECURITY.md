# Security notes

This is a small offline tool, but it reads a file that people edit by hand and that gets opened in Excel, so it treats every cell as untrusted input.

## Threat model

| Threat | Example | What the code does | Where |
|---|---|---|---|
| **CSV / formula injection** (OWASP) | someone types `=HYPERLINK("http://evil")` as an item; the owner opens data.csv in Excel | cells starting with `= + - @ TAB CR` are written with a leading `'`, and that `'` is removed again when the file is read | `validate.escape_formula`, `clean_text` |
| Malformed or hostile input | `time=25:00`, `price=NaN`, `price=-5`, a future date, a 5,000-character item | every field is validated; a bad row is skipped and reported with its line number, never guessed at | `validate.parse_row` |
| Invisible-character spoofing | `bla​ck bindi` (zero-width space) counted as a separate item; an RTL override or ANSI escape garbles the terminal | Unicode Cc/Cf characters are removed and whitespace collapsed | `validate.clean_text` |
| Resource exhaustion | a 2 GB file, a million rows, one giant quoted field | 5 MB file cap, 50,000-row cap, 1,000-character field cap | `store.py` constants |
| Path tricks | `--file /etc/passwd`, or data.csv replaced by a symlink to another file | only `*.csv`, regular files only, symlinks refused (`O_NOFOLLOW` on append) | `store._check_path` |
| Corrupted writes | crash or power cut in the middle of a write; a file saved by an editor with no trailing newline | each row goes out in a single `O_APPEND` write followed by `fsync`; a missing newline is fixed before appending | `store.append_request` |
| Silent data loss on a bad header | appending to a file whose columns were reordered in Excel | refuses to append unless the header matches exactly | `store._header_ok` |
| Overly open file permissions | another user on a shared laptop reads the shop's data | new data files are created `0600` | `store.append_request` |
| Leaking internals | stack traces that show file paths | file errors print one line to stderr; exit codes are 0 ok, 1 invalid rows, 2 error | `cli.main` |
| Supply chain | a malicious or typo-squatted PyPI package | **no runtime dependencies**, standard library only | `pyproject.toml` |
| CI token abuse | a workflow with write permission | `permissions: contents: read`, pinned tool versions | `.github/workflows/ci.yml` |

## Privacy
- The tool logs **no names, phone numbers or photos of customers**. `customer_type` is only student / local / other.
- Photos in `fieldwork.md` show the shop, not customers' faces.

## Not covered (out of scope for an offline single-user tool)
- No encryption at rest. Anyone with access to the laptop can read the CSV.
- No authentication. Anyone who can run the script can add rows.
- Times are taken from the laptop clock with no timezone. If the clock is wrong, the times are wrong.

## Checks that run on every push
`python -m unittest` (30 tests, including injection, symlink, size-limit and encoding cases), `ruff` with security rules, and `bandit`.
