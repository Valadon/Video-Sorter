# Repository Guidelines

This repository is a small Python operations app that sorts classroom recording files into semester/course folders and can optionally upload them to Kaltura before moving them. The codebase is compact, but a lot of behavior is driven by filename conventions, spreadsheet formatting, and local configuration, so understanding the inputs matters more here than chasing framework patterns.

## Read This First

Before changing code, read:

1. [readme.md](readme.md)
2. [docs/DEEP_DIVE.md](docs/DEEP_DIVE.md)
3. `config-EXAMPLE.ini`
4. `unit_test.py`

If those sources disagree, trust the code and tests over the older README language.

## Project Shape

- `video_sorter.py`: main entry point, matching pipeline, moving/uploading, and the long-running scheduler loop.
- `data_types.py`: small domain model for courses, instructors, and recordings.
- `format_parser.py`: filename parsers for supported recording systems.
- `kaltura_uploader.py`: upload/auth flow, backed by `mock_kaltura_client.py`.
- `file_reaper.py`: retention cleanup for old files and empty directories.
- `unit_test.py`: behavioral test suite using `test_courses.xlsx` and a configured test folder.
- `config-EXAMPLE.ini`: shape of the required local config file.
- `video_sorter.spec`: PyInstaller build spec.

## Commands

- Create env: `python3 -m venv .venv`
- Install deps: `.venv/bin/python -m pip install -r requirements.txt`
- Run tests: `.venv/bin/python -m pytest -q`
- Run app: `.venv/bin/python video_sorter.py`
- Build executable: `.venv/bin/pyinstaller video_sorter.spec`

If `pip install -r requirements.txt` behaves strangely, check the file encoding first. The current `requirements.txt` is UTF-16LE.

## Operational Guardrails

- Do not commit `config.ini`, `.env`, logs, or real schedule exports.
- Assume `config.ini` and `.env` are machine-specific. The app reads both at import/runtime from the repo root.
- Treat the spreadsheet schema as part of the app contract. Small header changes can break parsing.
- Preserve filename compatibility unless a task explicitly allows changing ingest rules.
- When editing docs or config examples, keep Windows operators in mind. The original workflow was clearly Windows-first even if development now happens elsewhere too.

## Behavior To Keep In Mind

- Matching happens in this order:
  - Parse filename into a `LectureRecording`
  - If the parsed recording has a time, match by room + weekday + start-time tolerance
  - If it does not have a time, match by course number + section
  - If nothing matches, move to `Unmatched_Videos`
- `Move` mode sorts files locally.
- `Upload` mode uploads to Kaltura and then moves files locally.
- Retention cleanup runs after every processing pass and deletes files older than `weeks_before_deletion`.
- The script processes immediately on first launch, then waits for the next time the local clock hits hour `3`.

## Known Maintenance Traps

- `config-EXAMPLE.ini` currently includes inline comments on value lines. Python's default `ConfigParser` treats those comments as part of the value, so copying them verbatim can break startup.
- One pytest currently fails on macOS/Linux because it asserts a Windows-style path string inside `unit_test.py`.
- The README that came with the repo was written for an older workflow and has been refreshed, but the code still has several Windows-era assumptions worth preserving carefully.
- The parser and spreadsheet logic are not highly defensive. Unexpected instructor formatting or renamed columns are likely to fail loudly.

## Documentation Rule

If you change any of these, update the docs in the same pass:

- Spreadsheet column expectations
- Supported filename formats
- Config keys or environment variables
- Test commands or known test status
- Runtime scheduling or folder naming behavior
