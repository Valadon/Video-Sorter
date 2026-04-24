# Course Sheet Input

The sorter expects a registrar-style Excel workbook with one row per course section. A sanitized example lives at:

- [examples/course_schedule_example.xlsx](examples/course_schedule_example.xlsx)

The example mirrors the shape of the Spring 2026 export without committing live schedule data or real instructor identifiers.

## Required Columns

`read_courses()` currently reads these columns:

- `Course`
- `Section #`
- `Course Title`
- `Meeting Pattern`
- `Meetings`
- `Instructor LAST`
- `Room (cleaned)`
- `Instructor`
- `Room`

Extra columns are allowed. The current Spring 2026 export also includes `Schedule Print`, which the importer ignores.

## Important Formatting Rules

- `Course` should include the subject and number, such as `LAW 1010`.
- `Section #` may be numeric in Excel. The importer converts it to a string for matching.
- `Meeting Pattern` drives room/time matching. Supported day tokens include `M`, `T`, `W`, `Th`, `F`, `Sa`, `Su`, plus combined patterns such as `MW`, `TTh`, `MTTh`, `MWF`, `WF`, and `FSa`.
- Meeting start times should look like `7:30am`, `6pm`, or another `am`/`pm` time at the start of the time range.
- Rows with `Does Not Meet` import successfully, but they do not participate in room/time matching because they have no start time.
- `Room (cleaned)` should contain the bare room value used in recording filenames, such as `2100` or `4603`.
- `Instructor` should use `LAST, FIRST (00123456)`. Multiple instructors should be separated with semicolons, for example `SMITH, CASEY (00100003); JONES, RILEY (00100004)`.
- `Instructor LAST` is used for destination folder and filename labels.

## Matching Notes

Timed recordings match by:

1. `Room (cleaned)`
2. weekday from `Meeting Pattern`
3. start time within `[Settings].start_time_tolerance`

CaptureCast recordings do not include a meeting time, so they match by `Course` plus `Section #`.

## Current Export Check

A Spring 2026 workbook shaped like this was checked against the current code. It had:

- 134 non-empty rows
- all required columns present
- `Schedule Print` as an ignored extra column
- 11 multi-instructor rows
- 47 `Does Not Meet` rows that import but skip room/time matching

The sanitized example workbook is covered by the pytest suite so future parsing changes are checked against this input style.
