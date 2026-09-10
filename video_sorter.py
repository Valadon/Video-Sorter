
import os
import re
import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit
from time import sleep
import logging
import shutil
import pandas as pd
from datetime import datetime, timedelta, date, time
from kaltura_uploader import *
from data_types import *
from format_parser import *
from collections.abc import Callable
from alert_digest import AlertDigestHandler, run_with_alert_digest
from file_reaper import reap_files
from upload_journal import (
    STATE_ATTACHED,
    STATE_MANUAL_RECONCILE,
    UploadJournal,
    sha256_file,
)
from runtime_guard import (
    AlreadyRunningError,
    RuntimeConfigurationError,
    SingleInstanceLock,
    load_config_environment,
    load_runtime_config,
    lock_path_for_config,
    resolve_config_path,
    version_string,
)

MEETING_START_TIME_PATTERN = re.compile(r'\b\d{1,2}:\d{2}(?:am|pm)\b|\b\d{1,2}(?:am|pm)\b', re.IGNORECASE)
MEETING_DAY_PATTERN = re.compile(r'TTh|Th|Su|Sa|M|T|W|F')
MEETING_DATE_PATTERN = re.compile(
    r'\((\d{1,2}/\d{1,2}/\d{4})(?:\s+to\s+(\d{1,2}/\d{1,2}/\d{4}))?\)',
    re.IGNORECASE,
)
MEETING_DAY_MAP = {
    'M': {'Monday'},
    'T': {'Tuesday'},
    'W': {'Wednesday'},
    'Th': {'Thursday'},
    'TTh': {'Tuesday', 'Thursday'},
    'F': {'Friday'},
    'Sa': {'Saturday'},
    'Su': {'Sunday'},
}
SCHEDULE_COLUMNS = {
    'Course',
    'Section #',
    'Course Title',
    'Meeting Pattern',
    'Meetings',
    'Instructor LAST',
    'Room (cleaned)',
    'Instructor',
    'Room',
}
REQUIRED_SCHEDULE_COLUMNS = {
    'Course',
    'Section #',
    'Course Title',
    'Instructor LAST',
    'Instructor',
}
NONPHYSICAL_ROOMS = {
    '',
    'canvas',
    'does not meet',
    'no meeting pattern',
    'no room',
    'online',
    'tba',
    'tbd',
}

RECORDING_START_TOLERANCE = timedelta(minutes=30)


class ScheduleFormatError(ValueError):
    """Raised when a schedule workbook does not have a usable schema."""


def _is_blank_cell(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _cell_text(value) -> str:
    if _is_blank_cell(value):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalized_header(value) -> str:
    return re.sub(r'\s+', ' ', str(value).replace('\ufeff', '')).strip().casefold()


def normalize_schedule_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Map known headers case-insensitively while rejecting ambiguous schemas."""
    canonical_by_normalized = {_normalized_header(column): column for column in SCHEDULE_COLUMNS}
    rename_map = {}
    matched_sources: dict[str, object] = {}

    for source_column in df.columns:
        canonical = canonical_by_normalized.get(_normalized_header(source_column))
        if canonical is None:
            continue
        if canonical in matched_sources:
            first_source = matched_sources[canonical]
            raise ScheduleFormatError(
                f"Schedule spreadsheet has two columns that both map to '{canonical}': "
                f"'{first_source}' and '{source_column}'. Remove or rename one of them."
            )
        matched_sources[canonical] = source_column
        rename_map[source_column] = canonical

    available = ', '.join(repr(str(column)) for column in df.columns)
    missing = sorted(REQUIRED_SCHEDULE_COLUMNS - set(matched_sources))
    if missing:
        missing_text = ', '.join(missing)
        raise ScheduleFormatError(
            f"Schedule spreadsheet is missing required column(s): {missing_text}. "
            f"Available columns: {available}"
        )
    if not {'Meeting Pattern', 'Meetings'} & set(matched_sources):
        raise ScheduleFormatError(
            "Schedule spreadsheet needs either 'Meeting Pattern' or 'Meetings'. "
            f"Available columns: {available}"
        )
    if not {'Room (cleaned)', 'Room'} & set(matched_sources):
        raise ScheduleFormatError(
            "Schedule spreadsheet needs either 'Room (cleaned)' or 'Room'. "
            f"Available columns: {available}"
        )

    return df.rename(columns=rename_map)

def parse_meeting_days(meeting_pattern: str) -> set[str]:
    days: set[str] = set()

    for segment in str(meeting_pattern).split(';'):
        start_time_match = MEETING_START_TIME_PATTERN.search(segment)
        if start_time_match is None:
            continue

        day_text = segment[:start_time_match.start()].strip()
        position = 0
        while position < len(day_text):
            if day_text[position].isspace():
                position += 1
                continue

            token_match = MEETING_DAY_PATTERN.match(day_text, position)
            if token_match is None:
                break

            days.update(MEETING_DAY_MAP[token_match.group()])
            position = token_match.end()

    return days

def parse_start_time(meeting_pattern: str) -> time | None:
    start_time_match = MEETING_START_TIME_PATTERN.search(str(meeting_pattern))
    if start_time_match is None:
        return None

    start_time_str = start_time_match.group().replace('am', 'AM').replace('pm', 'PM')
    return datetime.strptime(start_time_str, '%I:%M%p' if ':' in start_time_str else '%I%p').time()


def parse_meeting_date_range(meeting_text: str) -> tuple[date | None, date | None]:
    date_match = MEETING_DATE_PATTERN.search(str(meeting_text))
    if date_match is None:
        return None, None

    start_text, end_text = date_match.groups()
    start_date = datetime.strptime(start_text, '%m/%d/%Y').date()
    end_date = datetime.strptime(end_text, '%m/%d/%Y').date() if end_text else start_date
    return start_date, end_date


def parse_course_meetings(meeting_text: str) -> list[CourseMeeting]:
    meetings = []
    for raw_segment in str(meeting_text).split(';'):
        segment = raw_segment.strip()
        if segment == '':
            continue
        days = parse_meeting_days(segment)
        start_time = parse_start_time(segment)
        start_date, end_date = parse_meeting_date_range(segment)
        meetings.append(CourseMeeting(days, start_time, None, start_date, end_date, segment))
    return meetings


def _is_nonphysical_room(room_text: str) -> bool:
    normalized = re.sub(r'\s+', ' ', room_text).strip().casefold()
    return normalized in NONPHYSICAL_ROOMS


def normalize_room_number(raw_room) -> str | None:
    """Return the room number from a registrar room value when unambiguous."""
    text = _cell_text(raw_room)
    if _is_nonphysical_room(text):
        return None

    if re.fullmatch(r'\d{3,5}', text):
        return text
    numeric_excel_value = re.fullmatch(r'(\d{3,5})\.0+', text)
    if numeric_excel_value is not None:
        return numeric_excel_value.group(1)

    building_room = re.fullmatch(
        r"(?:\d+\s*-\s*)?[A-Za-z][A-Za-z .&'/-]*\s+(\d{3,5})",
        text,
    )
    if building_room is not None:
        return building_room.group(1)
    return None


def parse_room_numbers(raw_room) -> tuple[list[str | None], list[str]]:
    rooms: list[str | None] = []
    invalid_values = []
    text = _cell_text(raw_room)
    if text == '':
        return rooms, invalid_values

    for value in re.split(r'\s*(?:;|\r?\n)\s*', text):
        value = value.strip()
        if value == '':
            continue
        if _is_nonphysical_room(value):
            # Keep the placeholder so mixed physical/nonphysical room lists can
            # still be mapped positionally to their meeting segments.
            rooms.append(None)
            continue
        room_number = normalize_room_number(value)
        if room_number is None:
            invalid_values.append(value)
        else:
            rooms.append(room_number)
    return rooms, invalid_values

def parse_instructors(raw_instructors, course_number: str, section_number: str) -> list[EventHost]:
    instructors: list[EventHost] = []
    seen_unids = set()

    if pd.isna(raw_instructors):
        logging.warning(f"Course {course_number}-{section_number} is missing instructor data. Upload mode will leave matching files in place.")
        return instructors

    instructor_strings = str(raw_instructors).split(';')
    for instructor_string in instructor_strings:
        instructor_string = instructor_string.strip()
        if instructor_string == '':
            continue

        instructor_string = re.sub(r'(?:\s*\[[^\]]+\]\s*)+$', '', instructor_string).strip()

        instructor_match = re.fullmatch(r'([^(),\d]+),\s*([^()\d]+)\s+\((\d{8})\);*', instructor_string)
        if instructor_match is None:
            logging.warning(f"Could not parse instructor '{instructor_string}' for course {course_number}-{section_number}. Upload mode will leave matching files in place.")
            continue

        last_name, first_name, unid = instructor_match.groups()
        if unid in seen_unids:
            continue
        seen_unids.add(unid)
        instructors.append(EventHost(first_name.strip(), last_name.strip(), unid))

    return instructors
   
def _course_label(course: Course) -> str:
    return f'{course.number}-{course.section_number}'


def _natural_sort_key(value: str) -> tuple:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r'(\d+)', str(value))
        if part != ''
    )


def _course_sort_key(course: Course) -> tuple:
    return (
        _natural_sort_key(course.number),
        _natural_sort_key(course.section_number),
        course.name.casefold(),
        course.instructor_last.casefold(),
    )


def _host_set(course: Course) -> frozenset[str]:
    return frozenset(host.unid for host in course.hosts)


def _date_ranges_overlap(
    first: CourseMeeting,
    second: CourseMeeting,
    weekday: str | None = None,
) -> bool:
    first_start = first.start_date or date.min
    first_end = first.end_date or date.max
    second_start = second.start_date or date.min
    second_end = second.end_date or date.max
    overlap_start = max(first_start, second_start)
    overlap_end = min(first_end, second_end)
    if overlap_start > overlap_end:
        return False
    if weekday is None:
        return True

    weekday_number = WEEKDAYS.index(weekday)
    days_until_weekday = (weekday_number - overlap_start.weekday()) % 7
    return overlap_start + timedelta(days=days_until_weekday) <= overlap_end


def validate_courses(courses: list[Course]) -> list[str]:
    """Return actionable warnings for duplicate physical schedule slots."""
    slots: dict[tuple[str, str, time], list[tuple[Course, CourseMeeting]]] = {}
    for course in courses:
        for meeting in course.meetings:
            if meeting.room_number is None or meeting.start_time is None:
                continue
            for day in meeting.days:
                key = (meeting.room_number, day, meeting.start_time)
                slots.setdefault(key, []).append((course, meeting))

    conflicts: dict[tuple[int, int, str, time], tuple[Course, Course, set[str]]] = {}
    for (room_number, day, start_time), entries in slots.items():
        for index, (first_course, first_meeting) in enumerate(entries):
            for second_course, second_meeting in entries[index + 1:]:
                if first_course is second_course or not _date_ranges_overlap(first_meeting, second_meeting, day):
                    continue
                key = (id(first_course), id(second_course), room_number, start_time)
                if key not in conflicts:
                    conflicts[key] = (first_course, second_course, set())
                conflicts[key][2].add(day)

    issues = []
    for (_, _, room_number, start_time), (first, second, days) in conflicts.items():
        day_text = ', '.join(day for day in WEEKDAYS if day in days)
        candidates = f'{_course_label(first)} and {_course_label(second)}'
        if _host_set(first) and _host_set(first) == _host_set(second):
            chosen = min((first, second), key=_course_sort_key)
            policy = f'They have the same upload host(s), so the matcher will choose {_course_label(chosen)} by stable course and section order and log a warning.'
        else:
            policy = 'They do not have the same upload hosts, so recordings in this slot will stay in the watch folder for review.'
        issues.append(
            f'Duplicate schedule slot in room {room_number} on {day_text} at '
            f'{start_time.strftime("%I:%M %p")}: {candidates}. {policy}'
        )
    return issues


# Read course details from the Excel sheet into the global 'courses' list
def read_courses(excel_path, log_validation: bool = True) -> list[Course]:
    """
    Reads a list of courses from an excel file and parses them to Course objects. 
    For examples of how this excel file should look, see test_courses.xlsx and
    docs/examples/course_schedule_example.xlsx.
    """
    courses: list[Course] = []
    df = normalize_schedule_headers(pd.read_excel(excel_path)).dropna(how='all')
    for index, row in df.iterrows():
        spreadsheet_row = index + 2
        course_number = _cell_text(row['Course'])
        if course_number == '':
            raise ScheduleFormatError(f'Schedule spreadsheet row {spreadsheet_row} has a blank Course value.')
        instructor = str(row['Instructor LAST']).replace(' & ', ' ') if pd.notna(row['Instructor LAST']) else ''
        section_number = _cell_text(row['Section #'])
        if section_number == '':
            raise ScheduleFormatError(f'Schedule spreadsheet row {spreadsheet_row} has a blank Section # value.')

        row_validation_errors = []

        detailed_meetings = _cell_text(row['Meetings']) if 'Meetings' in df.columns else ''
        meeting_pattern = _cell_text(row['Meeting Pattern']) if 'Meeting Pattern' in df.columns else ''
        meeting_source = detailed_meetings or meeting_pattern
        meetings = parse_course_meetings(meeting_source)

        cleaned_room = row['Room (cleaned)'] if 'Room (cleaned)' in df.columns else None
        raw_room = row['Room'] if 'Room' in df.columns else None
        if _is_blank_cell(cleaned_room):
            rooms, invalid_rooms = parse_room_numbers(raw_room)
        else:
            rooms, invalid_rooms = parse_room_numbers(cleaned_room)
        if invalid_rooms and not _is_blank_cell(raw_room) and not _is_blank_cell(cleaned_room):
            fallback_rooms, fallback_invalid = parse_room_numbers(raw_room)
            if fallback_rooms and not fallback_invalid:
                rooms = fallback_rooms
                invalid_rooms = []
        if invalid_rooms:
            error_message = (
                f"Could not safely parse room value(s) {invalid_rooms!r} for {course_number}-{section_number} "
                f'on spreadsheet row {spreadsheet_row}. Timed matching will skip this row.'
            )
            logging.error(error_message)
            row_validation_errors.append(error_message)
            # Never infer a mapping from the valid subset of a malformed room
            # list. That can assign one known room to the wrong segment.
            rooms = []

        if len(rooms) == 1:
            for meeting in meetings:
                meeting.room_number = rooms[0]
        elif len(rooms) == len(meetings) and len(rooms) > 1:
            for meeting, room_number in zip(meetings, rooms):
                meeting.room_number = room_number
        elif len(rooms) > 1:
            error_message = (
                f'Cannot safely map {len(rooms)} rooms to {len(meetings)} meeting segments for '
                f'{course_number}-{section_number} on spreadsheet row {spreadsheet_row}. '
                'Timed matching will skip this row.'
            )
            logging.error(error_message)
            row_validation_errors.append(error_message)

        is_nonmeeting_row = meeting_source.strip().casefold().startswith('does not meet')
        if not is_nonmeeting_row and not any(meeting.start_time is not None for meeting in meetings):
            logging.warning(
                f"Could not parse a start time for course {course_number}-{section_number} from "
                f"meeting text '{meeting_source}'. Room/time matching will skip this course."
            )
        instructors = parse_instructors(row['Instructor'], course_number, section_number)

        first_meeting = meetings[0] if meetings else CourseMeeting(set(), None, None)

        course = Course(
            course_number,
            section_number,
            _cell_text(row['Course Title']),
            instructor,
            first_meeting.room_number,
            first_meeting.days,
            first_meeting.start_time,
            instructors,
            meetings,
        )
        course.validation_errors.extend(row_validation_errors)

        courses.append(course)

    if log_validation:
        for issue in validate_courses(courses):
            logging.warning(issue)

    return courses

def _resolve_course_candidates(
    candidates: list[Course],
    rec: LectureRecording,
    match_description: str,
) -> Course | None:
    if len(candidates) == 0:
        return None
    if len(candidates) == 1:
        return candidates[0]

    candidates = sorted(candidates, key=_course_sort_key)
    candidate_text = ', '.join(_course_label(course) for course in candidates)
    host_sets = {_host_set(course) for course in candidates}
    if len(host_sets) == 1 and next(iter(host_sets)):
        chosen = candidates[0]
        logging.warning(
            f'Multiple courses match {match_description}: {candidate_text}. They have the same '
            f'upload host(s), so {_course_label(chosen)} was chosen by stable course and section order.'
        )
        return chosen

    rec.matching_error = (
        f'Multiple courses with different or missing upload hosts match {match_description}: '
        f'{candidate_text}'
    )
    logging.error(f'{rec.matching_error}. Leaving {rec.filename} in the watch folder for review.')
    return None


def _course_allows_date_limited_recording(course: Course, recording_date: date | None) -> bool:
    if recording_date is None:
        return True
    date_limited_meetings = [meeting for meeting in course.meetings if meeting.has_date_limit]
    if not date_limited_meetings:
        return True

    recording_weekday = WEEKDAYS[recording_date.weekday()]
    for meeting in course.meetings:
        if meeting.start_date is not None and recording_date < meeting.start_date:
            continue
        if meeting.end_date is not None and recording_date > meeting.end_date:
            continue
        if meeting.days and recording_weekday not in meeting.days:
            continue
        if meeting.has_date_limit or meeting.days:
            return True
    return False


def find_course_by_number_and_section(courses: list[Course], rec: LectureRecording) -> Course | None:
    """
    Finds the course in the list whose number and section matches the 
    one in the given recording object
    """
    logging.debug(f"Searching for {rec} by course number and section")

    candidates = [
        course for course in courses
        if course.number == rec.course_number_full()
        and course.section_number == rec.section_number
        and _course_allows_date_limited_recording(course, rec.date)
    ]
    return _resolve_course_candidates(
        candidates,
        rec,
        f'course number {rec.course_number_full()} and section {rec.section_number}',
    )

def parse_recording_file(filepath: str) -> LectureRecording:
    """
    Parses a recording filename and puts that information into a recording object
    """
    parsers: list[Callable[[str], LectureRecording or None]] = (
        extron_format_parser,
        capturecast_format_parser,
        extron_2100_format_parser
    )

    for parser in parsers:
        rec = parser(filepath)
        if rec is not None:
            return rec
    
    return LectureRecording(filepath, None, None, None, None)

def _meeting_match_distance(meeting: CourseMeeting, rec: LectureRecording) -> int | None:
    if rec.time is None or rec.date is None:
        return None
    if meeting.room_number is None or meeting.start_time is None or not meeting.days:
        return None
    if meeting.room_number != normalize_room_number(rec.room_number):
        return None
    if not meeting.occurs_on(rec.date):
        return None

    course_minutes = meeting.start_time.hour * 60 + meeting.start_time.minute
    recording_minutes = rec.time.hour * 60 + rec.time.minute
    distance = abs(course_minutes - recording_minutes)
    tolerance_minutes = int(RECORDING_START_TOLERANCE.total_seconds() // 60)
    return distance if distance <= tolerance_minutes else None


def find_course_by_room_and_datetime(courses: list[Course], rec: LectureRecording) -> Course | None:
    """
    Finds the course in the given list which has a matching room, 
    date and time to the given recording
    """
    logging.debug(f'Trying to match {rec} by room and datetime')

    candidates_with_distance = []
    for course in courses:
        distances = [
            distance
            for meeting in course.meetings
            if (distance := _meeting_match_distance(meeting, rec)) is not None
        ]
        if distances:
            candidates_with_distance.append((min(distances), course))

    if not candidates_with_distance:
        return None

    nearest_distance = min(distance for distance, _ in candidates_with_distance)
    candidates = [
        course for distance, course in candidates_with_distance
        if distance == nearest_distance
    ]
    return _resolve_course_candidates(
        candidates,
        rec,
        f'room {rec.room_number} on {rec.date} at {rec.time} '
        f'({nearest_distance} minute(s) from the nearest scheduled start)',
    )

def determine_semester(date: date):
    """
    Gets the semester in which a date occurred
    """
    month = date.month
    year = date.year % 100  # Getting the last two digits of the year
    if month <= 4:
        return f'Spring{year}'
    elif month == 5 or month == 6 or month == 7:
        return f'Summer{year}'
    else:
        return f'Fall{year}'


def get_folder_safe_name(name) -> str:
    def is_valid_char (char: str):
        return char.isalnum() or char == " " or char == "_" or char == "-"
    
    return "".join([x if is_valid_char(x) else "" for x in name])

def get_or_create_class_folder(course: Course, rec: LectureRecording, dest_folder: str):
    """
    Returns the file path of the folder where a recording should go 
    based on the recording date and course
    """

    semester = determine_semester(rec.date)
    
    # Create the semester folder if it doesn't exist
    semester_path = os.path.join(dest_folder, semester)
    os.makedirs(semester_path, exist_ok=True)
    
    # Create the course folder inside the semester folder
    folder_name = get_folder_safe_name(f"{course.number}_{course.name}_{course.instructor_last}")
    folder_path = os.path.join(semester_path, folder_name)
    folder_path = os.path.abspath(folder_path)  # Use absolute path
    try:
        os.makedirs(folder_path, exist_ok=True)
        logging.info(f"Folder created at {folder_path}")
    except Exception as e:
        logging.error(f"An error occurred while creating directory: {e}")

    return folder_path

def get_new_filepath(rec: LectureRecording, course: Course, dest_folder: str):
    """
    Returns a new filepath for the recording based on the course you assign it to by passing it in here.

    For example, LAW 5000_Beekhuizen_11-17-2023
    """
    dest_folder = get_or_create_class_folder(course, rec, dest_folder)
    
    # Convert the date to a more readable format
    readable_date = rec.date.strftime("%m-%d-%y")

    new_filename = get_folder_safe_name(f"{course.name}_{course.instructor_last}_{readable_date}")
    counter = 1
    ext = '.mp4'
    full_path = os.path.join(dest_folder, f'{new_filename}{ext}')

    # If the file already exists, append a number to the name
    while os.path.exists(full_path):
        full_path = os.path.join(dest_folder, f'{new_filename}_{counter}{ext}')
        counter += 1

    return full_path

def move_video(rec: LectureRecording, dest_path):
    """
    Moves a recording to the given destination
    """
    source_path = rec.filepath
    try:
        shutil.move(source_path, dest_path)
        if os.path.exists(source_path) or not os.path.exists(dest_path):
            raise OSError(
                f'Move verification failed: source exists={os.path.exists(source_path)}, '
                f'destination exists={os.path.exists(dest_path)}'
            )
        logging.info(f"Video moved from {source_path} to {dest_path}")
        rec.filepath = dest_path
    except Exception as e:
        logging.error(f"An error occurred while moving {rec}: {e}")
        raise

def move_unmatched_video(rec: LectureRecording, dest_folder):
    """
    Moves a recording to the unmatched videos location specified in the config
    """
    unmatched_folder = os.path.join(dest_folder, 'Unmatched_Videos')
    os.makedirs(unmatched_folder, exist_ok=True)
    dest_path = os.path.join(unmatched_folder, os.path.basename(rec.filepath))
    try:
        shutil.move(rec.filepath, dest_path)
        logging.warning(f"No course matched for {rec}. Moved to {unmatched_folder}")
        rec.filepath = dest_path
    except Exception as e:
        logging.error(f"An error occurred while moving file: {e}")

def match_courses_to_recordings (courses: list[Course], watch_path) -> list[tuple[LectureRecording, Course or None]]:
    """
    Looks for videos in the watch path and tries to figure out which 
    course in the given list it was for. Returns a list of tuples
    which associates each recording to a course (or null)
    """
    pairs = []
    for filename in os.listdir(watch_path):

        if not filename.endswith('.mp4'):
            continue

        filepath = os.path.join(watch_path, filename)
        rec = parse_recording_file(filepath)

        if rec.was_scheduled():
            logging.info(rec)
            
            if rec.time is None:
                course = find_course_by_number_and_section(courses, rec)
            else:
                course = find_course_by_room_and_datetime(courses, rec)

            if course is not None:
                pairs.append((rec, course))
            else:
                pairs.append((rec, None))
        else:
            pairs.append((rec, None))

    return pairs

def move_files (pairs: list[tuple[LectureRecording, Course or None]], dest_folder: str):
    """
    Given a list of recordings and their matching course, generate the new 
    file name and move the video to the correct folder.
    """
    for pair in pairs:
        if pair[1] is None:
            if pair[0].matching_error is not None:
                logging.error(
                    f'Leaving {pair[0].filepath} in the watch folder because its schedule match is ambiguous: '
                    f'{pair[0].matching_error}'
                )
                continue
            move_unmatched_video(pair[0], dest_folder)
        else:
            new_path = get_new_filepath(pair[0], pair[1], dest_folder)
            move_video(pair[0], new_path)
            for ins in pair[1].hosts:
                logging.debug(f'Video moved for {ins}')

def upload_files (
    pairs: list[tuple[LectureRecording, Course or None]],
    dest_folder: str,
    journal: UploadJournal | None = None,
):
    """
    Given a list of tuples containing Recordings and their corresponding Courses, 
    uploads files to Kaltura, and then sorts them into folders based on their course
    """
    for pair in pairs:
        if pair[1] is not None:
            if len(pair[1].hosts) == 0:
                logging.error(f"Cannot upload {pair[0]} because the matching course has no valid instructors. Leaving the source file in place for review.")
                continue

            new_path = get_new_filepath(pair[0], pair[1], dest_folder)
            new_name = os.path.basename(new_path).replace('.mp4', '')
            upload_succeeded = True
            source_sha256 = None
            if journal is not None:
                try:
                    source_sha256 = sha256_file(pair[0].filepath)
                except Exception as e:
                    logging.error(
                        f'Could not hash recording {pair[0].filename} for course '
                        f'{_course_label(pair[1])}: {e}. Leaving the source file in place.'
                    )
                    continue

            for i, insr in enumerate(pair[1].hosts):
                try:
                    needs_remote_client = True
                    if journal is not None:
                        receipt = journal.get(source_sha256, insr.unid)
                        needs_remote_client = (
                            receipt is None
                            or receipt.state not in {STATE_ATTACHED, STATE_MANUAL_RECONCILE}
                        )
                    client = get_kaltura_client() if needs_remote_client else None
                    result = upload_video(
                        pair[0],
                        pair[1],
                        client,
                        new_name,
                        i,
                        journal=journal,
                        source_sha256=source_sha256,
                    )
                    if result.already_completed:
                        logging.info(
                            f'Confirmed prior upload for recording {pair[0].filename}, '
                            f'course {_course_label(pair[1])}, owner {insr.unid}, '
                            f'entry {result.entry_id}.'
                        )
                    else:
                        logging.info(
                            f'Successfully uploaded recording {pair[0].filename}, '
                            f'course {_course_label(pair[1])}, owner {insr.unid}, '
                            f'entry {result.entry_id}.'
                        )
                except UploadNeedsManualReconciliation as e:
                    logging.error(
                        f'Manual Kaltura reconciliation is required for recording '
                        f'{pair[0].filename}, course {_course_label(pair[1])}, owner '
                        f'{insr.unid}: {e}. Leaving the source file in place.'
                    )
                    upload_succeeded = False
                    break
                except Exception as e:
                    logging.error(
                        f'Error while uploading recording {pair[0].filename}, course '
                        f'{_course_label(pair[1])}, owner {insr.unid}: {e}. '
                        'Leaving the source file in place.'
                    )
                    upload_succeeded = False
                    break

            if not upload_succeeded:
                continue

            move_video(pair[0], new_path)
            logging.info(f'Successfully moved: {pair[0]}')
        elif pair[0].matching_error is not None:
            logging.error(
                f'Leaving {pair[0].filepath} in the watch folder because its schedule match is ambiguous: '
                f'{pair[0].matching_error}'
            )
        else:
            move_unmatched_video(pair[0], dest_folder)


def process_existing_files(
    courses: list[Course],
    watch_path,
    dest_path,
    mode,
    weeks_before_deletion=26,
    from_date: date | None=None,
    upload_journal: UploadJournal | None = None,
):
    """
    Given a list of courses and file path on which to watch for 
    videos, processes videos according to what mode has been set 
    in the config file.
    """
    pairs = match_courses_to_recordings(courses, watch_path)
    if len(pairs) > 0:
        if mode == 'Upload':
            upload_files(pairs, dest_path, upload_journal)
        elif mode == 'Move':
            move_files(pairs, dest_path)
    else:
        logging.info("No new videos to sort")

    start_date = from_date if from_date else datetime.now()
    cutoff = start_date - timedelta(weeks = weeks_before_deletion)
    logging.info(f'Reaping all files last modified before {cutoff.strftime("%m/%d/%Y, %H:%M:%S")}')
    try:
        reaped_files = reap_files(dest_path, cutoff)
        for f in reaped_files:
            logging.info(f'File deleted: {f}')
        logging.info(f'Deleted {len(reaped_files)} file(s)')
    except Exception as e:
        logging.error(f'Error occurred while reaping files: {e}')


def _has_physical_timed_meeting(course: Course) -> bool:
    return any(
        meeting.room_number is not None and meeting.start_time is not None and bool(meeting.days)
        for meeting in course.meetings
    )


def schedule_blocking_messages(
    courses: list[Course],
    require_upload_hosts: bool = True,
) -> list[str]:
    """Return row problems that must stop operational processing."""
    messages = [
        f'{_course_label(course)}: {error}'
        for course in courses
        for error in course.validation_errors
    ]
    if require_upload_hosts:
        messages.extend(
            f'{_course_label(course)} has a physical timed meeting but no valid upload host.'
            for course in courses
            if not course.hosts and _has_physical_timed_meeting(course)
        )
    return messages


def validate_schedule_file(excel_path: str) -> int:
    """Print a read-only schedule preflight summary."""
    try:
        courses = read_courses(excel_path, log_validation=False)
    except (OSError, ValueError, ScheduleFormatError) as error:
        print(f'Schedule validation failed: {error}')
        return 2

    timed_meetings = sum(
        meeting.room_number is not None and meeting.start_time is not None and bool(meeting.days)
        for course in courses
        for meeting in course.meetings
    )
    hostless_courses = [course for course in courses if not course.hosts]
    active_physical_hostless_courses = [
        course for course in hostless_courses
        if _has_physical_timed_meeting(course)
    ]
    import_errors = [
        (course, error)
        for course in courses
        for error in course.validation_errors
    ]
    issues = validate_courses(courses)

    print(f'Schedule: {os.path.abspath(excel_path)}')
    print(f'Courses: {len(courses)}')
    print(f'Physical timed meeting segments: {timed_meetings}')
    print(f'Courses without a valid upload host: {len(hostless_courses)}')
    print(f'Active physical courses blocked by a missing upload host: {len(active_physical_hostless_courses)}')
    print(f'Invalid room-to-meeting mappings: {len(import_errors)}')
    print(f'Duplicate physical slots: {len(issues)}')
    for course, error in import_errors:
        print(f'ERROR: {_course_label(course)}: {error}')
    for course in active_physical_hostless_courses:
        print(
            f'ERROR: {_course_label(course)} has a physical timed meeting but no valid upload host. '
            'Upload mode will leave its recordings in the watch folder.'
        )
    for issue in issues:
        print(f'WARNING: {issue}')

    if len(courses) == 0:
        print('Schedule validation failed: the workbook has no non-empty course rows.')
        return 2

    blocker_count = len(import_errors) + len(active_physical_hostless_courses)
    if blocker_count:
        print(f'Schedule validation found {blocker_count} blocking row problem(s).')
        return 1

    print('Schedule validation completed. Warnings use safe matching behavior and do not block startup.')
    return 0


def _parse_command_line(argv=None):
    parser = argparse.ArgumentParser(description='Sort classroom recordings using an Excel course schedule.')
    parser.add_argument(
        '--config',
        metavar='PATH',
        help='use a specific config.ini file instead of the one beside the app',
    )
    parser.add_argument(
        '--version',
        action='store_true',
        help='show the application version and build identity, then exit',
    )
    parser.add_argument(
        '--upload-status',
        action='store_true',
        help='show durable upload receipts without loading credentials or processing files',
    )
    parser.add_argument(
        '--find-media',
        metavar='NAME',
        help='find Kaltura media with an exact name and owner (read-only)',
    )
    parser.add_argument(
        '--owner',
        metavar='ID',
        help='Kaltura owner ID required by --find-media and --resume-upload-bytes',
    )
    parser.add_argument(
        '--verify-uploads',
        action='store_true',
        help='verify journaled Kaltura entry IDs against the live service (read-only)',
    )
    parser.add_argument(
        '--verify-upload-tokens',
        action='store_true',
        help='show live status and byte counts for journaled upload tokens (read-only)',
    )
    parser.add_argument(
        '--resume-upload-bytes',
        metavar='SHA_PREFIX',
        help='explicitly resume one eligible byte-stage receipt selected by hash and --owner',
    )
    parser.add_argument(
        '--validate-schedule',
        nargs='?',
        const='',
        metavar='PATH',
        help='check a schedule without moving or uploading files; omit PATH to use config.ini',
    )
    parser.add_argument(
        '--run-once',
        action='store_true',
        help='process one batch and exit instead of waiting for the daily run',
    )
    return parser.parse_args(argv)


def print_upload_status(config_path) -> int:
    journal_path = Path(config_path).resolve().parent / 'upload_journal.sqlite3'
    print(f'Upload journal: {journal_path}')
    if not journal_path.exists():
        print('Receipts: 0 (the upload journal does not exist yet)')
        return 0

    with UploadJournal(str(journal_path)) as journal:
        receipts = journal.list_receipts()

    print(f'Receipts: {len(receipts)}')
    for receipt in receipts:
        token_recorded = 'yes' if receipt.upload_token_id else 'no'
        entry_id = receipt.entry_id or '-'
        detail = f' detail={receipt.detail}' if receipt.detail else ''
        print(
            f'- {receipt.updated_at} file={receipt.source_name} '
            f'sha256={receipt.source_sha256[:12]}... owner={receipt.owner_id} '
            f'state={receipt.state} token_recorded={token_recorded} '
            f'entry={entry_id}{detail}'
        )
    return 0


def _one_line(value) -> str:
    if value is None or value == '':
        return '-'
    return str(value).replace('\r', r'\r').replace('\n', r'\n')


def _upload_hostname(upload_url) -> str:
    if not upload_url:
        return '-'
    try:
        return urlsplit(str(upload_url)).hostname or '-'
    except ValueError:
        return 'invalid'


def _print_media_entry(entry) -> None:
    print(
        f'id={_one_line(getattr(entry, "id", None))} '
        f'name={_one_line(getattr(entry, "name", None))} '
        f'owner={_one_line(getattr(entry, "userId", None))} '
        f'status={_one_line(getattr(entry, "status", None))} '
        f'duration={_one_line(getattr(entry, "duration", None))}'
    )


def find_media(name: str, owner_id: str) -> int:
    try:
        client = get_kaltura_client()
        entries = client.media.listByNameAndOwner(name, owner_id)
    except Exception as error:
        print(f'Kaltura media lookup failed: {error}', file=sys.stderr)
        return 1

    print(f'Matches: {len(entries)}')
    for entry in entries:
        _print_media_entry(entry)
    return 0


def verify_uploads(config_path) -> int:
    journal_path = Path(config_path).resolve().parent / 'upload_journal.sqlite3'
    if not journal_path.exists():
        print(f'Upload journal does not exist: {journal_path}', file=sys.stderr)
        return 1

    with UploadJournal(str(journal_path)) as journal:
        receipts = journal.list_receipts()
    entry_ids = list(dict.fromkeys(
        receipt.entry_id for receipt in receipts if receipt.entry_id
    ))
    print(f'Journaled entries: {len(entry_ids)}')
    if not entry_ids:
        return 0

    try:
        client = get_kaltura_client()
    except Exception as error:
        print(f'Kaltura verification login failed: {error}', file=sys.stderr)
        return 1

    failure_count = 0
    for entry_id in entry_ids:
        try:
            _print_media_entry(client.media.get(entry_id))
        except Exception as error:
            failure_count += 1
            print(
                f'entry={_one_line(entry_id)} verification_failed={type(error).__name__}: '
                f'{_one_line(error)}',
                file=sys.stderr,
            )
    return 1 if failure_count else 0


def verify_upload_tokens(config_path) -> int:
    journal_path = Path(config_path).resolve().parent / 'upload_journal.sqlite3'
    if not journal_path.exists():
        print(f'Upload journal does not exist: {journal_path}', file=sys.stderr)
        return 1

    with UploadJournal(str(journal_path)) as journal:
        receipts = [
            receipt for receipt in journal.list_receipts()
            if receipt.upload_token_id
        ]
    print(f'Journaled upload tokens: {len(receipts)}')
    if not receipts:
        return 0

    try:
        client = get_kaltura_client()
    except Exception as error:
        print(f'Kaltura token verification login failed: {error}', file=sys.stderr)
        return 1

    failure_count = 0
    for receipt in receipts:
        fingerprint = f'{receipt.source_sha256[:12]}...'
        try:
            token = client.uploadToken.get(receipt.upload_token_id)
            print(
                f'fingerprint={fingerprint} '
                f'entry={_one_line(receipt.entry_id)} '
                f'source={_one_line(receipt.source_name)} '
                f'owner={_one_line(receipt.owner_id)} '
                f'state={_one_line(receipt.state)} '
                f'status={_one_line(getattr(token, "status", None))} '
                f'fileSize={_one_line(getattr(token, "fileSize", None))} '
                f'uploadedFileSize={_one_line(getattr(token, "uploadedFileSize", None))} '
                f'uploadHost={_upload_hostname(getattr(token, "uploadUrl", None))} '
                f'updatedAt={_one_line(getattr(token, "updatedAt", None))}'
            )
        except Exception as error:
            failure_count += 1
            print(
                f'fingerprint={fingerprint} entry={_one_line(receipt.entry_id)} '
                f'source={_one_line(receipt.source_name)} '
                f'owner={_one_line(receipt.owner_id)} '
                f'verification_failed={type(error).__name__}: {_one_line(error)}',
                file=sys.stderr,
            )
    return 1 if failure_count else 0


def resume_upload_bytes(config, config_path, sha_prefix: str, owner_id: str) -> int:
    normalized_prefix = sha_prefix.strip().casefold()
    if not re.fullmatch(r'[0-9a-f]{8,64}', normalized_prefix):
        print(
            'SHA_PREFIX must contain 8 to 64 hexadecimal characters.',
            file=sys.stderr,
        )
        return 2

    journal_path = Path(config_path).resolve().parent / 'upload_journal.sqlite3'
    if not journal_path.exists():
        print(f'Upload journal does not exist: {journal_path}', file=sys.stderr)
        return 1

    with UploadJournal(str(journal_path)) as journal:
        matches = [
            receipt for receipt in journal.list_receipts()
            if receipt.owner_id == owner_id
            and receipt.source_sha256.casefold().startswith(normalized_prefix)
        ]
        if len(matches) != 1:
            print(
                f'Expected exactly one receipt for fingerprint {normalized_prefix} '
                f'and owner {owner_id}; found {len(matches)}.',
                file=sys.stderr,
            )
            return 2

        receipt = matches[0]
        if not receipt_allows_bytes_resume(receipt):
            print(
                f'Receipt {receipt.source_sha256[:12]}... for owner {owner_id} '
                f'is not eligible for bytes-only resume (state={receipt.state}).',
                file=sys.stderr,
            )
            return 1

        if Path(receipt.source_name).name != receipt.source_name:
            print('Journaled source name is not a safe filename.', file=sys.stderr)
            return 1

        watch_folder = Path(
            os.path.abspath(os.path.normpath(config.get('Paths', 'watch_folder')))
        ).resolve()
        source_path = (watch_folder / receipt.source_name).resolve()
        if source_path.parent != watch_folder or not source_path.is_file():
            print(
                f'Journaled source file is missing from the watch folder: '
                f'{receipt.source_name}',
                file=sys.stderr,
            )
            return 1
        source_size = source_path.stat().st_size
        if receipt.source_size is not None and receipt.source_size != source_size:
            print(
                f'Journaled source size does not match {receipt.source_name}: '
                f'expected {receipt.source_size}, found {source_size}.',
                file=sys.stderr,
            )
            return 1

        fingerprint = f'{receipt.source_sha256[:12]}...'

        def report_progress(confirmed_bytes: int, total_bytes: int) -> None:
            percent = 100 if total_bytes == 0 else int(
                confirmed_bytes * 100 / total_bytes
            )
            print(
                f'progress fingerprint={fingerprint} source={receipt.source_name} '
                f'owner={owner_id} confirmed_bytes={confirmed_bytes} '
                f'source_bytes={total_bytes} percent={percent}'
            )

        try:
            client = get_kaltura_client()
            result = resume_upload_bytes_only(
                str(source_path),
                owner_id,
                client,
                journal,
                receipt.source_sha256,
                progress=report_progress,
            )
        except Exception as error:
            print(
                f'Bytes-only resume failed for fingerprint={fingerprint} '
                f'source={receipt.source_name} owner={owner_id}: {_one_line(error)}',
                file=sys.stderr,
            )
            return 1

    print(
        f'Bytes confirmed: fingerprint={fingerprint} source={receipt.source_name} '
        f'owner={owner_id} state={result.state} '
        f'confirmed_bytes={result.confirmed_bytes} source_bytes={result.source_size}'
    )
    return 0


def _process_operational_pass(
    courses,
    watch_folder,
    dest_folder,
    mode,
    weeks_before_deletion,
    config_path,
):
    if mode.casefold() == 'upload':
        journal_path = Path(config_path).resolve().parent / 'upload_journal.sqlite3'
        with UploadJournal(str(journal_path)) as journal:
            return process_existing_files(
                courses,
                watch_folder,
                dest_folder,
                mode,
                weeks_before_deletion,
                upload_journal=journal,
            )
    return process_existing_files(
        courses,
        watch_folder,
        dest_folder,
        mode,
        weeks_before_deletion,
    )


def _run_operational(args, config, config_path):
    global RECORDING_START_TOLERANCE
    RECORDING_START_TOLERANCE = timedelta(
        minutes=config.getint('Settings', 'start_time_tolerance', fallback=30)
    )

    watch_folder = os.path.abspath(os.path.normpath(config.get('Paths', 'watch_folder')))
    dest_folder = os.path.normpath(config.get('Paths', 'destination_folder'))
    excel_file_path = os.path.normpath(config.get('Paths', 'excel_file'))
    mode = os.path.normpath(config.get('Settings', 'mode'))
    log_file = config.get('Settings', 'log_file')
    weeks_before_deletion = config.getint('Settings', 'weeks_before_deletion')

    print(f'See {log_file} for logs')

    # Initialize logging
    log_level = logging.getLevelNamesMapping()[config.get('Settings', 'log_level')]
    logging.basicConfig(format='[%(levelname)s] %(asctime)s %(message)s', datefmt='[%m/%d/%Y %I:%M:%S %p]', filename=log_file, level=log_level)
    logging.info(
        'Starting %s; config: %s',
        version_string(),
        Path(config_path).resolve(),
    )

    # Get the emails for logging
    emails = []
    COUNT = config.getint('LoggingEmails', 'to_count')
    for i in range(COUNT):
        emails.append(config.get('LoggingEmails', f'to_email_{i}'))

    HOST = config.get('LoggingEmails', 'outbound_server')
    FROM = config.get('LoggingEmails', 'from_address')
    smtp_level = logging.getLevelNamesMapping()[config.get('LoggingEmails', 'level')]
    smtp_handler = AlertDigestHandler(HOST, FROM, emails, config.get('LoggingEmails', 'subject'))
    smtp_handler.setLevel(smtp_level)
    root_logger = logging.getLogger()
    root_logger.addHandler(smtp_handler)

    try:
        try:
            courses = read_courses(excel_file_path)
        except Exception:
            logging.exception('Unhandled exception while loading the startup schedule.')
            smtp_handler.send_digest('startup schedule loading')
            raise

        schedule_blockers = schedule_blocking_messages(
            courses,
            require_upload_hosts=mode.casefold() == 'upload',
        )
        if schedule_blockers:
            for blocker in schedule_blockers:
                logging.critical(f'Schedule startup blocker: {blocker}')
            smtp_handler.send_digest('startup schedule validation')
            print(f'Schedule has {len(schedule_blockers)} blocking row problem(s). See {log_file} for details.')
            return 1

        if args.run_once:
            logging.info('Running one processing pass because --run-once was supplied.')
            run_with_alert_digest(
                lambda: _process_operational_pass(
                    courses,
                    watch_folder,
                    dest_folder,
                    mode,
                    weeks_before_deletion,
                    config_path,
                ),
                smtp_handler,
                'startup and one-pass processing',
            )
            return 0

        has_processed_videos = False

        while True:
            current_time = datetime.now().time()
            if current_time.hour == 3 or not has_processed_videos:
                logging.info("It's around 3 AM, time to sort the videos.")
                run_name = (
                    'startup and initial processing'
                    if not has_processed_videos
                    else 'scheduled processing'
                )
                run_with_alert_digest(
                    lambda: _process_operational_pass(
                        courses,
                        watch_folder,
                        dest_folder,
                        mode,
                        weeks_before_deletion,
                        config_path,
                    ),
                    smtp_handler,
                    run_name,
                )
                has_processed_videos = True
                sleep(3600)  # Sleep for 1 hour
            else:
                sleep(60)  # Sleep for 1 minute
    finally:
        root_logger.removeHandler(smtp_handler)
        smtp_handler.close()


def main(argv=None):
    args = _parse_command_line(argv)
    if args.version:
        print(version_string())
        return 0

    if args.validate_schedule:
        return validate_schedule_file(args.validate_schedule)

    try:
        config_path = resolve_config_path(args.config)
        runtime_config = load_runtime_config(config_path)
    except RuntimeConfigurationError as error:
        print(f'Video Sorter configuration failed: {error}', file=sys.stderr)
        return 2

    if args.upload_status:
        return print_upload_status(config_path)

    if args.find_media is not None:
        if not args.owner:
            print('--find-media requires --owner ID.', file=sys.stderr)
            return 2
        load_config_environment(config_path)
        return find_media(args.find_media, args.owner)

    if args.resume_upload_bytes is not None:
        if not args.owner:
            print('--resume-upload-bytes requires --owner ID.', file=sys.stderr)
            return 2
        try:
            with SingleInstanceLock(lock_path_for_config(config_path)):
                load_config_environment(config_path)
                return resume_upload_bytes(
                    runtime_config,
                    config_path,
                    args.resume_upload_bytes,
                    args.owner,
                )
        except AlreadyRunningError as error:
            print(str(error), file=sys.stderr)
            return 1

    if args.owner:
        print(
            '--owner is only valid with --find-media or --resume-upload-bytes.',
            file=sys.stderr,
        )
        return 2

    if args.verify_uploads:
        load_config_environment(config_path)
        return verify_uploads(config_path)

    if args.verify_upload_tokens:
        load_config_environment(config_path)
        return verify_upload_tokens(config_path)

    if args.validate_schedule is not None:
        if runtime_config.has_option('Paths', 'excel_file'):
            schedule_path = os.path.normpath(runtime_config.get('Paths', 'excel_file'))
        else:
            print(
                'Schedule validation failed: supply a workbook path after --validate-schedule '
                "or set Paths.excel_file in config.ini."
            )
            return 2
        return validate_schedule_file(schedule_path)

    try:
        with SingleInstanceLock(lock_path_for_config(config_path)):
            load_config_environment(config_path)
            return _run_operational(args, runtime_config, config_path)
    except AlreadyRunningError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
