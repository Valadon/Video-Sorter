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

1. Resolve `config.ini` beside the source app or packaged executable, or use the file passed with `--config`.
2. Acquire `.video_sorter.lock` beside that config. A second process using the same config directory exits before credentials, email, or processing are initialized.
3. Load `.env` from the selected config directory and build `RECORDING_START_TOLERANCE` from `[Settings].start_time_tolerance`.
4. Start the file log with the app version, build identity, and absolute config path.
5. Read the course spreadsheet into `Course` objects.
6. Enter an infinite loop.
7. Process immediately on first launch, then process again whenever `datetime.now().time().hour == 3`.
8. For every `.mp4` in the watch folder:
   - parse the filename into a `LectureRecording`
   - try to match that recording to a `Course`
   - move or upload+move depending on mode
9. Reap old files from the destination folder based on `weeks_before_deletion`.
10. Flush one warning/error email digest, if the run produced any qualifying alerts, then sleep until the next polling interval.

There is no filesystem watcher. This is a polling/scheduled batch job. The operating system releases the single-instance lock if the process exits or crashes; the lock file can remain on disk without blocking the next launch.

For a controlled batch that exits after processing and retention cleanup, run `video_sorter.py --run-once`.

## Input Contracts

### 1. `config.ini`

The app expects a real `config.ini`. With no option, source runs look beside `video_sorter.py` and packaged runs look beside `video_sorter.exe`. `--config PATH` selects another file; relative paths are resolved from the caller's current directory. The example file shows the required keys:

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

Email alerts are batched by processing pass. Records at or above `[LoggingEmails].level` accumulate while the startup schedule and batch are handled, then the app sends one plain-text digest containing the warning/error count and every qualifying log entry. A clean pass sends nothing. An unexpected processing exception is logged and included before the digest flushes. Schedule blockers flush a single startup-validation digest before exit. This does not buffer or reduce the normal file log.

### 2. `.env`

Only needed for `Upload` mode. `kaltura_uploader.py` expects:

- `PARTNER_ID`
- `TOKEN`
- `TOKEN_ID`

On normal startup, these are loaded via `python-dotenv` from `.env` beside the selected `config.ini`. Existing process environment values take precedence. Validation, `--version`, and `--upload-status` do not load the credentials.

### 3. Schedule spreadsheet

`read_courses()` uses `pandas.read_excel()`. It accepts extra columns and any column order. Known headers are matched without regard to capitalization or extra whitespace.

Required columns:

- `Course`
- `Section #`
- `Course Title`
- `Instructor LAST`
- `Instructor`

The sheet also needs at least one of `Meetings` or `Meeting Pattern`, plus at least one of `Room (cleaned)` or `Room`.

The code directly depends on:

- `Meetings`, when present, for weekday, start-time, and date-limit parsing
- `Meeting Pattern` as the meeting fallback
- `Instructor LAST` for output folder/file naming
- `Room (cleaned)`, with `Room` as a fallback, for room-based matching
- `Instructor` for parsing people and uNIDs
- `Course` plus `Section #` for CaptureCast matching

See [COURSE_SHEET_INPUT.md](COURSE_SHEET_INPUT.md) and [examples/course_schedule_example.xlsx](examples/course_schedule_example.xlsx) for a sanitized workbook that mirrors the current export shape.

The importer handles the current registrar variations:

- building-prefixed rooms such as `LAW 2100` and `GC 3700` normalize to bare room numbers
- nonphysical values such as `CANVAS`, `ONLINE`, and `No Meeting Pattern` skip room/time matching
- instructor role suffixes such as `[Primary Instructor]` are removed before parsing
- semicolon-delimited meeting segments keep separate days, times, rooms, and optional date limits
- one room applies to all meeting segments; equal room and segment counts map by order
- unequal multi-room and segment counts generate an error and skip timed matching for the row
- rows such as `Does Not Meet` import but skip room/time matching

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
- list of meeting segments
- list of instructor hosts

Each `CourseMeeting` stores its room number, days, start time, optional inclusive start and end dates, and original source text. The legacy `Course.room_number`, `Course.days`, and `Course.start_time` attributes remain for older callers, but matching uses the meeting list.

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
4. an optional single-date or date-range limit from `Meetings`

The time tolerance comparison ignores the calendar date for its arithmetic. The meeting date check uses the actual recording date.

The matcher ranks candidates by absolute minute distance from the recording time and only considers the nearest scheduled start inside the tolerance window. Equally near candidates with the same nonempty upload-host set use stable course and section order and log a warning. Candidates with different or missing hosts are ambiguous. The app leaves those files in the watch folder instead of moving or uploading them.

### Untimed recordings

Untimed recordings are matched by:

1. `course_code + " " + course_number`
2. section number
3. explicit meeting date limits, when the course has them

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

The custom client deliberately uploads through `https://www.kaltura.com/api_v3/service/uploadtoken/action/upload`, matching the repository's proven legacy behavior. It does not use the alternate `uploadUrl` host returned with a token: that hostname did not resolve on the production PC. The client forces `format=1` on both API and file-upload calls. When the upload response is empty, non-JSON, or lost after transmission, the client checks the known upload token and continues only after the token reaches full-upload status. It URL-encodes query values and applies bounded connect/read timeouts. Uploads allow up to three minutes for connection and TLS negotiation because a verified 720.7 MB production upload took about 63 seconds before returning. Failures identify the upload stage, HTTP status, content type, sanitized endpoint, and response size without logging session tokens or query parameters.

Upload mode keeps `upload_journal.sqlite3` beside the selected `config.ini`. Each source-file SHA-256 and owner has a durable receipt for the upload token, confirmed bytes, media entry, and content attachment. A restart resumes only a confirmed stage and skips owners whose attachment is already complete. If Kaltura may have created an entry or attached content without returning a usable receipt, the journal marks that owner for manual reconciliation and later runs refuse to create a possible duplicate.

The app creates a fresh Kaltura client for each owner upload, so a long backlog does not reuse a session past its lifetime. It hashes each source file once per processing pass and reuses that hash for every owner. The file moves only after every owner reaches a confirmed attachment. Successful and reused receipts log the filename, course, owner, and Kaltura entry ID.

Inspect the journal without loading credentials or processing files:

```bash
video_sorter.py --config /path/to/config.ini --upload-status
```

The report includes the source filename, abbreviated hash, owner, state, whether a token is recorded, entry ID, update time, and reconciliation detail. It never prints the upload token itself.

Two live, read-only Kaltura checks use credentials from the selected config directory:

```bash
video_sorter.py --config /path/to/config.ini --find-media "Exact Media Name" --owner u1234567
video_sorter.py --config /path/to/config.ini --verify-uploads
```

The first command performs an exact name-and-owner search. The second reads every entry ID in the upload journal and fetches its current Kaltura record. Both print only the entry ID, name, owner, status, and duration. They do not upload, attach, move, or delete recordings, and they do not take the processing lock, so an operator can run them while the sorter is active.

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

Run the full suite with `.venv/bin/python -m pytest -q`.

The suite includes the current header, room, instructor-role, multi-meeting, date-limit, and duplicate-slot cases.

## Schedule preflight

Run this read-only check before changing the configured workbook:

```bash
.venv/bin/python video_sorter.py --validate-schedule /path/to/course_schedule.xlsx
```

It prints course, physical timed-meeting, missing-host, invalid room-mapping, and duplicate-slot counts. Unreadable workbooks, bad schemas, and empty schedules return exit code 2. Invalid room-to-meeting mappings and active physical courses without valid upload hosts return exit code 1. Duplicate slots remain warnings because the matcher handles them safely.

Normal processing enforces the same row blockers before touching a recording. Invalid room mappings stop Move and Upload modes. A physical timed course without a valid host stops Upload mode.

## Packaging

Use Python 3.11 for the current dependency set. `requirements.txt` is UTF-8 and pins `setuptools==79.0.1`; PyInstaller 6.2.0 and altgraph 0.17.4 still import `pkg_resources`, which newer setuptools releases removed.

Build with:

```bash
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m PyInstaller --clean --noconfirm video_sorter.spec
```

The spec creates a one-directory bundle at `dist/video_sorter/`. Copy the whole directory, including `_internal`. PyInstaller builds for the current operating system and CPU architecture, so create the production Windows executable on Windows rather than copying a macOS build. The repository's **Windows build** GitHub Actions workflow is the supported fallback when the deployment PC does not have a build toolchain; it runs the test suite and a packaged executable smoke test before publishing a zipped Windows bundle and SHA-256 checksum.

The CI-built executable is unsigned and intended for controlled internal deployment. Verify both the workflow commit and ZIP checksum before extraction. SmartScreen or endpoint security may flag it until an Authenticode signing process is configured.

The build does not contain `config.ini`, `.env`, `upload_journal.sqlite3`, or a schedule workbook. The bundled `Start-VideoSorter.ps1` launcher sets the working directory to the install folder and passes `--config` explicitly. It uses `config.ini` beside the executable by default and accepts `-ConfigPath` for another location.

Run `video_sorter.exe --version` to print the app version, source commit, build time, and workflow run embedded by CI. This command does not load config, credentials, or the process lock.

## Current Sharp Edges

These are not necessarily production bugs, but they are the main maintenance hotspots.

- The build stack currently depends on the `setuptools==79.0.1` compatibility pin for `pkg_resources`.
- Instructor names still need the `LAST, FIRST (00123456)` core format after optional bracketed role suffixes.
- The default process is a forever loop with time-based polling. The CLI has validation and one-pass modes but no service wrapper.
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
