# Video Sorter

This repository contains a Python utility that sorts classroom recording files into semester/course folders and can optionally upload them to Kaltura before moving them.

If you are taking the project over, start here:

- [AGENTS.md](AGENTS.md)
- [docs/DEEP_DIVE.md](docs/DEEP_DIVE.md)

## What The App Expects

The sorter depends on three local inputs:

1. `config.ini` beside the source app or packaged executable, unless `--config` selects another file
2. a schedule spreadsheet matching the expected column names
3. recording filenames that match one of the supported parser formats

For `Upload` mode it also needs a `.env` file beside the selected `config.ini`.

A sanitized schedule workbook example is available at [docs/examples/course_schedule_example.xlsx](docs/examples/course_schedule_example.xlsx), with field notes in [docs/COURSE_SHEET_INPUT.md](docs/COURSE_SHEET_INPUT.md).

## Setup

### 1. Create a virtual environment

Use Python 3.11 for the current pinned dependency set and executable builds.

```bash
python3.11 -m venv .venv
```

### 2. Install dependencies

```bash
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` is UTF-8. It pins `setuptools==79.0.1` because the current PyInstaller and altgraph versions still import `pkg_resources`, which newer setuptools releases removed.

### 3. Create `config.ini`

Use `config-EXAMPLE.ini` as a reference. Put the finished file beside the app, or pass its path with `--config`. The app supports inline config comments, but the example keeps explanatory comments on their own lines so it stays easy to copy and audit.

### 4. Add `.env` if using upload mode

`kaltura_uploader.py` expects:

- `PARTNER_ID`
- `TOKEN`
- `TOKEN_ID`

Put `.env` in the same directory as the selected `config.ini`. Existing environment variables take precedence over values in that file. These values should stay local and never be committed.

### 5. Run the app

```bash
.venv/bin/python video_sorter.py
```

The app uses `config.ini` beside `video_sorter.py` by default. To choose another file, run:

```bash
.venv/bin/python video_sorter.py --config /path/to/config.ini
```

The app processes immediately on first launch, then continues running and checks again when the local time reaches 3 AM. It holds `.video_sorter.lock` beside the selected config while running. A second copy using the same config directory exits before loading credentials or configuring email. The operating system releases the lock if the process stops or crashes; the small lock file may remain and is safe to leave in place.

For a controlled one-pass test, run:

```bash
.venv/bin/python video_sorter.py --run-once
```

This uses the selected config, processes one batch, runs retention cleanup, and exits. `--config` can be combined with `--run-once`.

Warnings and errors at or above `[LoggingEmails].level` are collected during schedule loading and processing, then sent as one digest at the end of the processing pass. A clean pass sends no email. If a pass raises an unexpected exception, the exception is added and the digest is flushed before the process exits. The regular file log remains immediate and complete.

These read-only checks are available:

```bash
.venv/bin/python video_sorter.py --version
.venv/bin/python video_sorter.py --config /path/to/config.ini --upload-status
.venv/bin/python video_sorter.py --config /path/to/config.ini --find-media "Exact Media Name" --owner u1234567
.venv/bin/python video_sorter.py --config /path/to/config.ini --verify-uploads
.venv/bin/python video_sorter.py --config /path/to/config.ini --verify-upload-tokens
```

`--version` prints the app version and embedded build identity without reading config or credentials. `--upload-status` lists the durable per-owner receipts beside the selected config without loading credentials. `--find-media` performs an exact Kaltura name-and-owner lookup, while `--verify-uploads` checks every journaled entry ID against Kaltura. `--verify-upload-tokens` shows each receipt's 12-character fingerprint and the live Kaltura token status, file size, uploaded byte count, update time, and upload hostname. It strips the upload URL's path, query, and user information. These reports never print upload-token IDs or process recordings.

If token verification shows an interrupted byte upload, an operator can resume exactly one eligible receipt:

```bash
.venv/bin/python video_sorter.py --config /path/to/config.ini \
  --resume-upload-bytes SHA_PREFIX --owner u1234567
```

The SHA prefix must contain at least eight hexadecimal characters and must identify exactly one receipt for that owner. The command takes the normal process lock, verifies that the original watch-folder file has the journaled name, size, and SHA-256, then resumes the recorded token in 10,240,000-byte chunks. It prints confirmed-byte progress after each accepted chunk and stops in `bytes_uploaded`. It cannot create or attach a media entry. Receipts that may already have created or attached media are never eligible.

## Running Tests

Run:

```bash
.venv/bin/python -m pytest -q
```

The tests depend on:

- `test_courses.xlsx`
- the folder structure configured by `[Paths].test_folder`

## Build To Executable

The repo includes `video_sorter.spec`.

Build with:

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm video_sorter.spec
```

PyInstaller produces `dist/video_sorter/`. Keep that whole directory together; the executable depends on its `_internal` contents.

Builds are platform-specific. The macOS arm64 build runs only on Apple silicon Macs. For Windows, either build on a Windows machine or run the repository's **Windows build** GitHub Actions workflow. The workflow tests the release, builds it with Python 3.11, and publishes `video-sorter-windows-x64`, containing the complete zipped bundle and a SHA-256 checksum.

The Actions artifact is unsigned and intended for controlled internal deployment. Confirm the workflow's commit matches the intended release and verify the ZIP against the recorded SHA-256 before extracting it. Windows SmartScreen or endpoint security may flag the executable until Authenticode signing is added.

`config.ini`, `.env`, the upload journal, and the schedule workbook are intentionally not bundled. The release includes `Start-VideoSorter.ps1`, which starts the executable from its install directory and passes the config path explicitly. By default it uses `config.ini` beside the executable; pass `-ConfigPath` to select another file.

## Schedule spreadsheet expectations

The importer accepts extra columns and any column order. It ignores capitalization and extra whitespace in known headers. These columns are required:

- `Course`
- `Section #`
- `Course Title`
- `Instructor LAST`
- `Instructor`

The workbook also needs at least one meeting column, `Meetings` or `Meeting Pattern`, and at least one room column, `Room (cleaned)` or `Room`.

Operational notes:

- Room values may be bare numbers or building-prefixed values such as `LAW 2100` and `GC 3700`.
- `ONLINE`, `CANVAS`, `No Meeting Pattern`, and other nonphysical values do not participate in room/time matching.
- Semicolon-delimited meeting segments may have different days, times, rooms, and date limits.
- If one room value is listed, the importer applies it to every meeting segment. If the number of room values equals the number of meeting segments, it maps them by order, including mixed physical and nonphysical meetings. Other multi-room layouts or partly malformed room lists generate an error and disable timed matching for the whole row.
- `Instructor` uses `LAST, FIRST (00123456)`. Bracketed role labels such as `[Primary Instructor]` are allowed. Separate multiple instructors with semicolons.
- When more than one start time falls inside the tolerance window, the matcher only considers the nearest one. Equal-distance rows with the same upload hosts use stable course and section order. Equal-distance rows with different hosts leave recordings in the watch folder for review.

Check a workbook without moving or uploading anything:

```bash
.venv/bin/python video_sorter.py --validate-schedule /path/to/course_schedule.xlsx
```

Operational startup also stops before moving or uploading files when the schedule has an invalid room mapping. Upload mode additionally stops when a physical timed course has no valid upload host.

## Supported Recording Sources

The active parser pipeline supports:

- Extron filenames with room/date/time information
- Extron 2100 filenames
- CaptureCast filenames with course/section/date information

Details and examples live in [docs/DEEP_DIVE.md](docs/DEEP_DIVE.md).

## Roadmap / Future Cleanup Ideas

- Support unscheduled/manual recordings that start with one or more uNIDs
- Make config examples safer and more copy-pasteable
- Reduce Windows-only assumptions in tests and docs
