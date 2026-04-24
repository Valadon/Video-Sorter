# Video Sorter

This repository contains a Python utility that sorts classroom recording files into semester/course folders and can optionally upload them to Kaltura before moving them.

If you are taking the project over, start here:

- [AGENTS.md](AGENTS.md)
- [docs/DEEP_DIVE.md](docs/DEEP_DIVE.md)

## What The App Expects

The sorter depends on three local inputs:

1. `config.ini` in the repo root
2. a schedule spreadsheet matching the expected column names
3. recording filenames that match one of the supported parser formats

For `Upload` mode it also needs a repo-root `.env` file with Kaltura credentials.

A sanitized schedule workbook example is available at [docs/examples/course_schedule_example.xlsx](docs/examples/course_schedule_example.xlsx), with field notes in [docs/COURSE_SHEET_INPUT.md](docs/COURSE_SHEET_INPUT.md).

## Setup

### 1. Create a virtual environment

```bash
python3 -m venv .venv
```

### 2. Install dependencies

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Note: the current `requirements.txt` file is UTF-16LE encoded.

### 3. Create `config.ini`

Use `config-EXAMPLE.ini` as a reference. The app now supports inline config comments, but the example keeps explanatory comments on their own lines to stay easy to copy and audit.

### 4. Add `.env` if using upload mode

`kaltura_uploader.py` expects:

- `PARTNER_ID`
- `TOKEN`
- `TOKEN_ID`

These values should stay local and never be committed.

### 5. Run the app

```bash
.venv/bin/python video_sorter.py
```

The app processes immediately on first launch, then continues running and checks again when the local time reaches 3 AM.

## Running Tests

Run:

```bash
.venv/bin/python -m pytest -q
```

The tests depend on:

- `test_courses.xlsx`
- the folder structure configured by `[Paths].test_folder`

Status verified locally on April 24, 2026:

- 29 tests passed

## Build To Executable

The repo includes `video_sorter.spec`.

Build with:

```bash
.venv/bin/pyinstaller video_sorter.spec
```

## Schedule Spreadsheet Expectations

The current importer expects a registrar-style Excel workbook with columns including:

- `Course`
- `Section #`
- `Course Title`
- `Meeting Pattern`
- `Meetings`
- `Instructor LAST`
- `Room (cleaned)`
- `Instructor`
- `Room`

Operational notes:

- `Room (cleaned)` should contain the bare room number
- `Meeting Pattern` needs to contain recognizable day/time text
- `Instructor` is parsed using a strict `LAST, FIRST (00123456)` pattern
- `Does Not Meet` rows import successfully, but they do not participate in room/time matching

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
- Make spreadsheet and instructor parsing more defensive
