# Video Sorter Deep Dive

## What This App Does

The app watches a folder full of `.mp4` lecture captures, tries to determine which class each video belongs to, then either:

- moves the file into a semester/course folder structure, or
- uploads it to Kaltura and then moves it into that same folder structure.

It also reaps old files from the destination tree based on a retention window.

This is not a generic media pipeline. It is a narrow operational script built around:

- specific schedule spreadsheet exports,
- specific recording filename formats,
- a Kaltura app-token workflow,
- and a local config file that points at real folders.

## Runtime Flow

The main runtime lives in `video_sorter.py`.

1. Read `config.ini` from the repo root at import/runtime.
2. Build `RECORDING_START_TOLERANCE` from `[Settings].start_time_tolerance`.
3. Read the course spreadsheet into `Course` objects.
4. Enter an infinite loop.
5. Process immediately on first launch, then process again whenever `datetime.now().time().hour == 3`.
6. For every `.mp4` in the watch folder:
   - parse the filename into a `LectureRecording`
   - try to match that recording to a `Course`
   - move or upload+move depending on mode
7. Reap old files from the destination folder based on `weeks_before_deletion`.
8. Sleep until the next polling interval.

Important operational detail: there is no filesystem watcher. This is a polling/scheduled batch job.

## Input Contracts

### 1. `config.ini`

The app expects a real `config.ini` at the repo root. The example file shows the required keys:

- `[Paths]`
  - `watch_folder`
  - `destination_folder`
  - `excel_file`
  - `test_folder`
- `[Settings]`
  - `mode`
  - `log_level`
  - `start_time_tolerance`
  - `weeks_before_deletion`
  - `log_file`
- `[LoggingEmails]`
  - `level`
  - `subject`
  - `outbound_server`
  - `from_address`
  - `to_count`
  - `to_email_0...n`

The runtime config parser now supports inline comments, but the example file keeps comments on their own lines so it stays easy to copy and audit.

### 2. `.env`

Only needed for `Upload` mode. `kaltura_uploader.py` expects:

- `PARTNER_ID`
- `TOKEN`
- `TOKEN_ID`

These are loaded via `python-dotenv`.

### 3. Schedule spreadsheet

`read_courses()` uses `pandas.read_excel()` and expects these columns from the workbook:

- `Course`
- `Section #`
- `Course Title`
- `Meeting Pattern`
- `Meetings`
- `Instructor LAST`
- `Room (cleaned)`
- `Instructor`
- `Room`

The code directly depends on:

- `Meeting Pattern` for weekday and start-time parsing
- `Instructor LAST` for output folder/file naming
- `Room (cleaned)` for room-based matching
- `Instructor` for parsing people and uNIDs
- `Course` plus `Section #` for CaptureCast matching

See [COURSE_SHEET_INPUT.md](COURSE_SHEET_INPUT.md) and [examples/course_schedule_example.xlsx](examples/course_schedule_example.xlsx) for a sanitized workbook that mirrors the current export shape.

The current parsing is brittle by design:

- day parsing is regex-based
- start time is extracted from free-form text
- instructor parsing assumes `LAST, FIRST (00123456)` formatting
- rows such as `Does Not Meet` import, but they are skipped by room/time matching because they do not have start times

If the registrar/export format changes, this function is one of the first places to inspect.

## Supported Filename Formats

Filename parsing lives in `format_parser.py`.

### Extron

Pattern:

```text
(\d+)_.*?_(\d{8})-(\d{6})_[sS]1[rR]1.mp4
```

Extracts:

- room number
- recording date
- recording time

Matching path:

- room number
- weekday
- start time within tolerance

### Legacy Extron

There is also an older unused parser for filenames containing `Rec\d+`.

### Extron 2100

Pattern:

```text
SMP-2100_(\d{8})-(\d{6})_[sS]1[rR]1.mp4
```

This hard-codes room `2100`.

### CaptureCast

Pattern:

```text
(\w+)-(\d+)-(\d+)---(\d{1,2})-(\d{1,2})-(\d{4}).mp4
```

Extracts:

- course code
- course number
- section number
- date

Matching path:

- course number + section

Because CaptureCast filenames do not include a meeting time, they skip the room/time tolerance path.

### Manual recordings

There is a stub for `manual_format_parser()` representing filenames prefixed with one or more uNIDs, but it is currently unused and not part of the active parser list.

## Domain Model

### `EventHost`

- stores first name, last name, and a normalized `u########` style identifier

### `Course`

- number
- section number
- course name
- instructor last-name display string
- room number
- set of meeting days
- start time
- list of instructor hosts

It also chooses a default host alphabetically for upload ownership if an explicit instructor index is not supplied.

### `LectureRecording`

- filepath
- recording device label
- date
- time
- room number
- optional course number/section/code

This class is the boundary object produced by the filename parsers.

## Matching Logic

The matching engine is small but opinionated.

### Timed recordings

Timed recordings are matched by:

1. room equality
2. weekday membership
3. start time within `RECORDING_START_TOLERANCE`

The comparison ignores the actual calendar date for time-window math and instead compares both times against a dummy date.

### Untimed recordings

Untimed recordings are matched by:

1. `course_code + " " + course_number`
2. section number

This is mainly for CaptureCast.

### Unmatched recordings

Anything that does not match ends up in:

```text
<destination>/Unmatched_Videos/
```

## Output Layout

Destination paths are built from:

```text
<destination>/<semester>/<course number>_<course title>_<instructor last>/<course title>_<instructor last>_<mm-dd-yy>.mp4
```

Examples:

- `Fall23/LAW 1230_Course1 The Sequel_BEEKHUIZEN/...`
- `Spring26/...`

Unsafe characters are stripped by `get_folder_safe_name()`. That means punctuation is removed, not replaced.

If the destination filename already exists, the app appends `_1`, `_2`, and so on.

## Upload Mode

Upload behavior lives in `kaltura_uploader.py` and `mock_kaltura_client.py`.

Flow:

1. start widget session
2. hash token with SHA-256
3. start app-token session
4. request upload token
5. upload file bytes
6. create media entry
7. attach uploaded bytes to the media entry

The repository includes a minimal custom client because the author notes that the official Kaltura Python library was not reliable for this workflow.

Operational nuance: upload ownership is assigned per host. In `upload_files()`, the script loops through each course host and performs an upload before moving the file.

## Retention / Reaper

`file_reaper.py` recursively deletes files older than the cutoff and removes directories once they become empty.

Important detail: reaping happens against the destination tree after each processing pass, not as a separate command.

## Tests

The repo has a meaningful pytest suite in `unit_test.py`. It covers:

- spreadsheet import
- instructor parsing
- parser-based matching
- move operations
- full `process_existing_files()` behavior
- reaper behavior

Local verification on April 24, 2026:

- command run: `.venv/bin/python -m pytest -q`
- result: `29 passed`

## Current Sharp Edges

These are not necessarily production bugs, but they are the main maintenance hotspots.

- `requirements.txt` is encoded as UTF-16LE, which can surprise tools and diffs.
- `config-EXAMPLE.ini` is not safely copy-pasteable because of inline value comments.
- The app reads config at import time, which makes code reuse and isolated testing more awkward.
- `read_courses()` is not defensive against unexpected instructor formatting.
- The main process is a forever loop with time-based polling, not a service wrapper or CLI with subcommands.
- The repo still reflects a Windows-first operational history even though development can happen on macOS/Linux.

## Recommended Mental Model For Future Work

If you need to change this app later, think in this order:

1. Is the input contract changing?
   - spreadsheet export
   - filename format
   - config keys
   - Kaltura auth shape
2. Does the change affect matching correctness?
3. Does it affect downstream folder naming or retention?
4. Can it be covered by extending `unit_test.py` without rewriting the architecture?

This is a pragmatic operations script. The best improvements will usually come from making input parsing safer, config/setup clearer, and tests less platform-specific before attempting a large refactor.
