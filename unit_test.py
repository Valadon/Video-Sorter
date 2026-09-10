import os
import configparser
import io
import tempfile
import pandas as pd
import pytest
import requests
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config.read('config.ini')

from data_types import *
from mock_kaltura_client import KalturaApiError, KalturaClient, KalturaConfiguration
import video_sorter
from video_sorter import read_courses, match_courses_to_recordings, move_video, get_new_filepath, process_existing_files, find_course_by_room_and_datetime, upload_files, parse_meeting_days, parse_start_time
from file_reaper import reap_files

def clear_directory(directory_path):
    try:
        files = os.listdir(directory_path)
        for file in files:
            file_path = os.path.join(directory_path, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
            else:
                clear_directory(file_path)
                os.rmdir(file_path)
    except OSError as e:
        exit(1)

def count_nondirectory_files(directory_path):
    count = 0
    try:
        files = os.listdir(directory_path)
        for file in files:
            file_path = os.path.join(directory_path, file)
            if os.path.isfile(file_path):
                count += 1
            else:
                count += count_nondirectory_files(file_path)
    except OSError as e:
        return 0

    return count

def clear_test_folder ():
    test_folder = config.get('Paths', 'test_folder')
    clear_directory(test_folder)

def generate_files (recs: list[LectureRecording], numBytes=4):
    test_folder = config.get('Paths', 'test_folder')

    watch_path = os.path.join(test_folder, 'WATCH')
    os.makedirs(watch_path, exist_ok=True)
    destination_path = os.path.join(test_folder, 'DESTINATION')
    os.makedirs(destination_path, exist_ok=True)

    for rec in recs:
        filename = ''

        if rec.rec_device == 'extron':
            stringdate = rec.date.strftime("%Y%m%d")
            stringtime = rec.time.strftime("%H%M%S")
            filename = f'{rec.room_number}_{stringdate}-1_{stringdate}-{stringtime}_S1R1.mp4'
        elif rec.rec_device == 'extron_2100':
            stringdate = rec.date.strftime("%Y%m%d")
            stringtime = rec.time.strftime("%H%M%S")
            filename = f'SMP-2100_{stringdate}-{stringtime}_S1R1.mp4'
        elif rec.rec_device == 'capturecast':
            filename = f'{rec.course_code}-{rec.course_number}-{rec.section_number}---{rec.date.month}-{rec.date.day}-{rec.date.year}.mp4'
        else:
            return

        filepath = os.path.join(watch_path, filename)
        rec.filepath = filepath
        with open(filepath, "wb") as f:
            f.write(bytes([0xFF for _ in range(numBytes)]))
        ts = rec.get_datetime().timestamp() if rec.time else datetime(day=rec.date.day, month=rec.date.month, year=rec.date.year).timestamp()
        os.utime(filepath, (ts, ts))

    return (watch_path, destination_path)

def read_test_courses ():
    return read_courses(os.path.join(os.path.curdir, 'test_courses.xlsx'))

def read_example_courses ():
    return read_courses(os.path.join(os.path.curdir, 'docs', 'examples', 'course_schedule_example.xlsx'))

def get_test_recs ():
    return {
        'extron': LectureRecording(None, date(2023, 11, 14), time(14, 28), 4603, 'extron'),
        'extron_2100': LectureRecording(None, date(2023, 11, 13), time(13, 32), 2100, 'extron_2100'),
        'capturecast': LectureRecording(None, date(2023, 11, 14), None, None, 'capturecast', '4560', '1', 'LAW'),
        'capturecast_old': LectureRecording(None, date(2022, 10, 5), None, None, 'capturecast', '4560', '1', 'LAW'),
        'extron_invalid': LectureRecording(None, date(2023, 11, 14), time(10, 28), 4603, 'extron'),
    }

class TestSorter:
    def test_course_import (self):
        courses = read_test_courses()
        expected_course_names = ["Course1: The Sequel", "Course2", "Course3"]
        lastnames = ['BEEKHUIZEN', 'FERGUSON', 'FINCH MORALES']
        for i, course in enumerate(courses):
            assert course.name == expected_course_names[i]
            assert course.instructor_last == lastnames[i]
            for ins in course.hosts:
                assert ins.last in lastnames[i]

    def test_valid_extron_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course1: The Sequel'
    
    def test_unscheduled_extron_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_invalid']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1] is None

    def test_valid_2100_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_2100']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course3'

    def test_valid_capturecast_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['capturecast']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course2'

    def test_get_user_alphabetical (self):
        courses = read_test_courses()
        for x in courses:
            if len(x.hosts) == 2:
                assert x.get_first_host_alphabetically().last == 'FINCH'

    def test_instructor_import (self):
        courses = read_test_courses()
        assert courses[0].hosts[0].last == 'BEEKHUIZEN'
        assert courses[2].hosts[0].last == 'FINCH'
        assert courses[2].hosts[1].last == 'MORALES'
        for c in courses:
            for i in c.hosts:
                assert len(i.unid) == 8
                assert i.unid[0] == 'u'

    def test_matched_video_moving_extron (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], destination)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_extron_absolute_path (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        old_path = os.path.join(watch, '4603_20231114-1_20231114-142800_S1R1.mp4')
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], destination)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        new_path = os.path.join(destination, 'Fall23', 'LAW 1230_Course1 The Sequel_BEEKHUIZEN', 'Course1 The Sequel_BEEKHUIZEN_11-14-23.mp4')
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_2100 (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_2100']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], destination)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_capturecast (self):
        courses = read_test_courses()
        testrec = get_test_recs()['capturecast']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], destination)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_extron_large (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, destination = generate_files([testrec], numBytes=10)
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], destination)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_process_existing (self):
        courses = read_test_courses()
        recdict = get_test_recs()
        testrects = [recdict['extron'], recdict['extron_2100'], recdict['capturecast'], recdict['extron_invalid'], recdict['capturecast_old']]
        clear_test_folder()
        watch, destination = generate_files(testrects)
        assert count_nondirectory_files(watch) == 5
        old_paths = []
        for rec in testrects:
            assert os.path.exists(rec.filepath)
            old_paths.append(rec.filepath)
        process_existing_files(courses, watch, destination, 'Move', from_date=datetime(2023, 4, 6))
        for path in old_paths:
            assert not os.path.exists(path)
        assert count_nondirectory_files(destination) == 4
        assert count_nondirectory_files(os.path.join(destination, 'Unmatched_Videos')) == 1
        assert count_nondirectory_files(os.path.join(destination, 'Fall23')) == 3
        assert not os.path.exists(os.path.join(destination, 'Fall22'))

    def test_no_videos (self):
        courses = read_test_courses()
        clear_test_folder()
        watch, destination = generate_files([])
        assert count_nondirectory_files(watch) == 0
        assert count_nondirectory_files(destination) == 0
        process_existing_files(courses, watch, destination, 'Move')
        assert count_nondirectory_files(watch) == 0
        assert count_nondirectory_files(destination) == 0

    def test_unparseable_instructor_does_not_crash_course_import(self, monkeypatch):
        df = pd.DataFrame([
            {
                'Course': 'LAW 1230',
                'Section #': 1,
                'Course Title': 'Course1',
                'Meeting Pattern': 'TTh 2:30pm-4:30pm',
                'Meetings': 'TTh 2:30pm-4:30pm',
                'Instructor LAST': 'STAFF',
                'Room (cleaned)': 4603,
                'Instructor': 'staff',
                'Room': 'LAW 4603',
            }
        ])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        courses = read_courses('ignored.xlsx')

        assert len(courses) == 1
        assert courses[0].hosts == []

    def test_unparseable_meeting_time_does_not_crash_room_match(self, monkeypatch):
        df = pd.DataFrame([
            {
                'Course': 'LAW 1230',
                'Section #': 1,
                'Course Title': 'Course1',
                'Meeting Pattern': 'TTh TBD',
                'Meetings': 'TTh TBD',
                'Instructor LAST': 'TESTER',
                'Room (cleaned)': 4603,
                'Instructor': 'TESTER, CASEY (00100001)',
                'Room': 'LAW 4603',
            }
        ])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)
        courses = read_courses('ignored.xlsx')
        rec = LectureRecording('test.mp4', date(2023, 11, 14), time(14, 30), '4603', 'extron')

        assert find_course_by_room_and_datetime(courses, rec) is None

    def test_thursday_only_meeting_pattern_maps_to_thursday(self, monkeypatch):
        df = pd.DataFrame([
            {
                'Course': 'LAW 1230',
                'Section #': 1,
                'Course Title': 'Course1',
                'Meeting Pattern': 'Th 2:30pm-4:30pm',
                'Meetings': 'Th 2:30pm-4:30pm',
                'Instructor LAST': 'TESTER',
                'Room (cleaned)': 4603,
                'Instructor': 'TESTER, CASEY (00100001)',
                'Room': 'LAW 4603',
            }
        ])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        courses = read_courses('ignored.xlsx')

        assert courses[0].days == {'Thursday'}

    def test_does_not_meet_meeting_pattern_has_no_days_or_start_time(self):
        assert parse_meeting_days('Does Not Meet') == set()
        assert parse_start_time('Does Not Meet') is None

    def test_course_sheet_example_imports_current_export_shape(self):
        courses = read_example_courses()

        assert len(courses) == 5
        assert courses[0].number == 'LAW 1010'
        assert courses[0].section_number == '1'
        assert courses[0].room_number == '2100'
        assert courses[0].days == {'Tuesday', 'Thursday'}
        assert courses[0].start_time == time(7, 30)
        assert courses[1].days == {'Monday', 'Tuesday', 'Thursday'}
        assert courses[2].days == {'Wednesday', 'Friday'}
        assert [host.last for host in courses[2].hosts] == ['SMITH', 'JONES']
        assert courses[3].days == {'Friday', 'Saturday'}
        assert courses[4].days == set()
        assert courses[4].start_time is None

    def test_course_sheet_example_matches_realistic_extron_filename(self):
        courses = read_example_courses()

        with tempfile.TemporaryDirectory() as watch:
            filepath = os.path.join(watch, '2100_20260106-1_20260106-073000_S1R1.mp4')
            with open(filepath, 'wb') as f:
                f.write(b'test')

            pairs = match_courses_to_recordings(courses, watch)

        assert len(pairs) == 1
        assert pairs[0][1].number == 'LAW 1010'
        assert pairs[0][1].section_number == '1'

    def test_import_normalizes_reordered_headers_rooms_and_instructor_roles(self, monkeypatch):
        df = pd.DataFrame([{
            'Extra registrar field': 'ignored',
            '  instructor  ': (
                'TESTER, CASEY (00100001) [Primary Instructor]; '
                'REVIEWER, RILEY (00100002) [Secondary Instructor]; '
                'TESTER, CASEY (00100001) [Primary Instructor]'
            ),
            ' ROOM ': '0070037000 - GC 3700',
            'MEETINGS': 'TTh 10am-12pm',
            'course title': 'Header Test',
            'section # ': 3,
            'instructor last': 'Tester & Reviewer',
            ' course ': 'LAW 5000',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        courses = read_courses('ignored.xlsx')

        assert len(courses) == 1
        assert courses[0].room_number == '3700'
        assert courses[0].instructor_last == 'Tester Reviewer'
        assert [host.last for host in courses[0].hosts] == ['TESTER', 'REVIEWER']

    def test_import_reports_missing_headers_clearly(self, monkeypatch):
        df = pd.DataFrame([{
            'Course': 'LAW 5000',
            'Course Title': 'Missing Section',
            'Meetings': 'T 10am-12pm',
            'Instructor LAST': 'TESTER',
            'Instructor': 'TESTER, CASEY (00100001)',
            'Room': 'LAW 3700',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        with pytest.raises(video_sorter.ScheduleFormatError, match=r'missing required column\(s\): Section #'):
            read_courses('ignored.xlsx')

    def test_multiple_meetings_map_to_multiple_rooms_and_honor_date_ranges(self, monkeypatch):
        df = pd.DataFrame([{
            'Course': 'LAW 7999',
            'Section #': 3,
            'Course Title': 'Synthetic Multi Meeting',
            'Meeting Pattern': 'WTh 9am-12pm; F 10am-11am',
            'Meetings': (
                'WTh 9am-12pm (01/02/2030 to 01/03/2030); '
                'F 10am-11am (01/04/2030 to 05/31/2030)'
            ),
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'LAW 4100\nLAW 4200',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': '0070041000 - LAW 4100; 0070042000 - LAW 4200',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)
        course = read_courses('ignored.xlsx')[0]

        assert [meeting.room_number for meeting in course.meetings] == ['4100', '4200']
        assert [meeting.start_time for meeting in course.meetings] == [time(9, 0), time(10, 0)]
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('first.mp4', date(2030, 1, 2), time(9, 0), '4100', 'extron')
        ) is course
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('second.mp4', date(2030, 1, 11), time(10, 0), '4200', 'extron')
        ) is course
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('off-date.mp4', date(2030, 1, 9), time(9, 0), '4100', 'extron')
        ) is None
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('wrong-room.mp4', date(2030, 1, 11), time(10, 0), '4100', 'extron')
        ) is None
        assert video_sorter.find_course_by_number_and_section(
            [course],
            LectureRecording('capturecast.mp4', date(2030, 1, 11), None, None, 'capturecast', '7999', '3', 'LAW'),
        ) is course
        assert video_sorter.find_course_by_number_and_section(
            [course],
            LectureRecording('capturecast-off-date.mp4', date(2030, 1, 9), None, None, 'capturecast', '7999', '3', 'LAW'),
        ) is None

    def test_single_date_meetings_only_match_listed_dates(self, monkeypatch):
        df = pd.DataFrame([{
            'Course': 'LAW 7000',
            'Section #': 1,
            'Course Title': 'Limited Meetings',
            'Meeting Pattern': 'F 9am-11am',
            'Meetings': (
                'F 9am-11am (01/04/2030); F 9am-11am (02/01/2030); '
                'F 9am-11am (03/01/2030)'
            ),
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'LAW 4100',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': '0070041000 - LAW 4100',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)
        course = read_courses('ignored.xlsx')[0]

        assert find_course_by_room_and_datetime(
            [course], LectureRecording('listed.mp4', date(2030, 3, 1), time(9, 0), '4100', 'extron')
        ) is course
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('also-listed.mp4', date(2030, 2, 1), time(9, 0), '4100', 'extron')
        ) is course
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('unlisted.mp4', date(2030, 2, 8), time(9, 0), '4100', 'extron')
        ) is None

    def test_room_segment_count_mismatch_skips_timed_mapping(self, monkeypatch, caplog):
        df = pd.DataFrame([{
            'Course': 'LAW 7999',
            'Section #': 3,
            'Course Title': 'Ambiguous Rooms',
            'Meeting Pattern': 'W 9am-12pm; F 10am-11am',
            'Meetings': 'W 9am-12pm; F 10am-11am',
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'LAW 4100\nLAW 4200\nGC 4300',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': 'LAW 4100; LAW 4200; GC 4300',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        with caplog.at_level('ERROR'):
            course = read_courses('ignored.xlsx')[0]

        assert all(meeting.room_number is None for meeting in course.meetings)
        assert 'Cannot safely map 3 rooms to 2 meeting segments' in caplog.text

    def test_partly_malformed_room_list_skips_entire_row(self, monkeypatch, caplog):
        df = pd.DataFrame([{
            'Course': 'LAW 7999',
            'Section #': 3,
            'Course Title': 'Malformed Room List',
            'Meeting Pattern': 'W 9am-12pm; F 10am-11am',
            'Meetings': 'W 9am-12pm; F 10am-11am',
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'LAW 4100\nMALFORMED',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': 'LAW 4100; MALFORMED',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        with caplog.at_level('ERROR'):
            course = read_courses('ignored.xlsx')[0]

        assert all(meeting.room_number is None for meeting in course.meetings)
        assert len(course.validation_errors) == 1
        assert 'Timed matching will skip this row' in caplog.text

    def test_mixed_physical_and_nonphysical_rooms_map_positionally(self, monkeypatch):
        df = pd.DataFrame([{
            'Course': 'LAW 7999',
            'Section #': 4,
            'Course Title': 'Mixed Delivery',
            'Meeting Pattern': 'W 9am-10am; F 10am-11am',
            'Meetings': 'W 9am-10am; F 10am-11am',
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'CANVAS\nLAW 4200',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': 'CANVAS; LAW 4200',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        course = read_courses('ignored.xlsx')[0]

        assert [meeting.room_number for meeting in course.meetings] == [None, '4200']
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('friday.mp4', date(2030, 1, 11), time(10, 0), '4200', 'extron')
        ) is course

    def test_schedule_preflight_flags_blocking_rows(self, monkeypatch, capsys):
        df = pd.DataFrame([
            {
                'Course': 'LAW 7010',
                'Section #': 1,
                'Course Title': 'Missing Host',
                'Meeting Pattern': 'T 10am-12pm',
                'Meetings': 'T 10am-12pm',
                'Instructor LAST': 'STAFF',
                'Room (cleaned)': 'LAW 3200',
                'Instructor': 'staff',
                'Room': 'LAW 3200',
            },
            {
                'Course': 'LAW 7020',
                'Section #': 1,
                'Course Title': 'Ambiguous Rooms',
                'Meeting Pattern': 'W 9am-12pm; F 10am-11am',
                'Meetings': 'W 9am-12pm; F 10am-11am',
                'Instructor LAST': 'TESTER',
                'Room (cleaned)': 'LAW 3200\nLAW 2110\nGC 3700',
                'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
                'Room': 'LAW 3200; LAW 2110; GC 3700',
            },
        ])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        result = video_sorter.validate_schedule_file('ignored.xlsx')
        output = capsys.readouterr().out

        assert result == 1
        assert 'Active physical courses blocked by a missing upload host: 1' in output
        assert 'Invalid room-to-meeting mappings: 1' in output
        assert 'Schedule validation found 2 blocking row problem(s).' in output

    def test_operational_blockers_depend_on_mode(self):
        invalid = Course(
            'LAW 7010', '1', 'Invalid Mapping', 'Tester', None, {'Tuesday'}, time(10, 0),
            [EventHost('CASEY', 'TESTER', '00100001')],
        )
        invalid.validation_errors.append('invalid room mapping')
        hostless = Course('LAW 7020', '1', 'Missing Host', 'Staff', '3200', {'Tuesday'}, time(10, 0), [])

        move_blockers = video_sorter.schedule_blocking_messages(
            [invalid, hostless], require_upload_hosts=False
        )
        upload_blockers = video_sorter.schedule_blocking_messages(
            [invalid, hostless], require_upload_hosts=True
        )

        assert move_blockers == ['LAW 7010-1: invalid room mapping']
        assert len(upload_blockers) == 2
        assert 'LAW 7020-1 has a physical timed meeting but no valid upload host.' in upload_blockers

    def test_capturecast_honors_date_limit_without_parseable_time(self):
        course = Course(
            'LAW 7000', '1', 'Date Limited', 'Tester', None, set(), None,
            [EventHost('CASEY', 'TESTER', '00100001')],
            [CourseMeeting(set(), None, None, date(2026, 8, 25), date(2026, 8, 25), 'T TBD (08/25/2026)')],
        )

        on_date = LectureRecording(
            'on-date.mp4', date(2026, 8, 25), None, None, 'capturecast', '7000', '1', 'LAW'
        )
        off_date = LectureRecording(
            'off-date.mp4', date(2026, 8, 26), None, None, 'capturecast', '7000', '1', 'LAW'
        )

        assert video_sorter.find_course_by_number_and_section([course], on_date) is course
        assert video_sorter.find_course_by_number_and_section([course], off_date) is None

    def test_nonphysical_rooms_do_not_participate_in_timed_matching(self, monkeypatch):
        df = pd.DataFrame([{
            'Course': 'LAW 7800',
            'Section #': 5,
            'Course Title': 'Online Seminar',
            'Meeting Pattern': 'W 1:30pm-3:30pm',
            'Meetings': 'W 1:30pm-3:30pm',
            'Instructor LAST': 'Tester',
            'Room (cleaned)': 'CANVAS',
            'Instructor': 'TESTER, CASEY (00100001) [Primary Instructor]',
            'Room': 'CANVAS',
        }])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)
        course = read_courses('ignored.xlsx')[0]

        assert course.meetings[0].room_number is None
        assert find_course_by_room_and_datetime(
            [course], LectureRecording('online.mp4', date(2026, 8, 26), time(13, 30), '3200', 'extron')
        ) is None

    def test_duplicate_slot_with_same_hosts_uses_stable_course_order(self, caplog):
        first_host = EventHost('CASEY', 'TESTER', '00100001')
        second_host = EventHost('CASEY', 'TESTER', '00100001')
        first = Course('LAW 7800', '1', 'Seminar', 'Tester', '3200', {'Tuesday'}, time(17, 0), [first_host])
        second = Course('LAW 7801', '1', 'Special Topics', 'Tester', '3200', {'Tuesday'}, time(17, 0), [second_host])
        rec = LectureRecording('duplicate.mp4', date(2026, 8, 25), time(17, 0), '3200', 'extron')

        with caplog.at_level('WARNING'):
            match = find_course_by_room_and_datetime([second, first], rec)

        assert match is first
        assert 'chosen by stable course and section order' in caplog.text
        issues = video_sorter.validate_courses([second, first])
        assert len(issues) == 1
        assert 'choose LAW 7800-1 by stable course and section order' in issues[0]

    def test_room_matching_only_considers_nearest_start_time(self):
        earlier = Course(
            'LAW 7000', '1', 'Earlier', 'Tester', '3200', {'Tuesday'}, time(17, 0),
            [EventHost('CASEY', 'TESTER', '00100001')],
        )
        nearer = Course(
            'LAW 7001', '1', 'Nearer', 'Reviewer', '3200', {'Tuesday'}, time(17, 20),
            [EventHost('RILEY', 'REVIEWER', '00100002')],
        )
        rec = LectureRecording('nearest.mp4', date(2026, 8, 25), time(17, 18), '3200', 'extron')

        assert find_course_by_room_and_datetime([earlier, nearer], rec) is nearer

    def test_equal_distance_with_different_hosts_refuses_to_guess(self):
        earlier = Course(
            'LAW 7000', '1', 'Earlier', 'Tester', '3200', {'Tuesday'}, time(8, 0),
            [EventHost('CASEY', 'TESTER', '00100001')],
        )
        later = Course(
            'LAW 7001', '1', 'Later', 'Reviewer', '3200', {'Tuesday'}, time(9, 0),
            [EventHost('RILEY', 'REVIEWER', '00100002')],
        )
        rec = LectureRecording('midpoint.mp4', date(2026, 8, 25), time(8, 30), '3200', 'extron')

        assert find_course_by_room_and_datetime([earlier, later], rec) is None
        assert rec.matching_error is not None

    def test_duplicate_preflight_requires_a_shared_weekday_date(self):
        first = Course(
            'LAW 7000', '1', 'First', 'Tester', '3200', {'Tuesday'}, time(9, 0),
            [EventHost('CASEY', 'TESTER', '00100001')],
            [CourseMeeting({'Tuesday'}, time(9, 0), '3200', date(2026, 8, 18), date(2026, 8, 24))],
        )
        second = Course(
            'LAW 7001', '1', 'Second', 'Reviewer', '3200', {'Tuesday'}, time(9, 0),
            [EventHost('RILEY', 'REVIEWER', '00100002')],
            [CourseMeeting({'Tuesday'}, time(9, 0), '3200', date(2026, 8, 24), date(2026, 9, 1))],
        )

        assert video_sorter.validate_courses([first, second]) == []

    def test_duplicate_slot_with_different_hosts_stays_in_watch_folder(self, caplog):
        first = Course(
            'LAW 7000', '1', 'First', 'Tester', '3200', {'Tuesday'}, time(17, 0),
            [EventHost('CASEY', 'TESTER', '00100001')],
        )
        second = Course(
            'LAW 7001', '1', 'Second', 'Reviewer', '3200', {'Tuesday'}, time(17, 0),
            [EventHost('RILEY', 'REVIEWER', '00100002')],
        )

        with tempfile.TemporaryDirectory() as work:
            source = os.path.join(work, 'duplicate.mp4')
            destination = os.path.join(work, 'destination')
            with open(source, 'wb') as file:
                file.write(b'test')
            rec = LectureRecording(source, date(2026, 8, 25), time(17, 0), '3200', 'extron')

            with caplog.at_level('ERROR'):
                match = find_course_by_room_and_datetime([first, second], rec)
                video_sorter.move_files([(rec, match)], destination)

            assert match is None
            assert rec.matching_error is not None
            assert os.path.exists(source)
            assert not os.path.exists(destination)
            assert 'Leaving' in caplog.text

    def test_unknown_recording_is_not_marked_scheduled(self):
        rec = LectureRecording('unknown.mp4', None, None, None, None)
        assert rec.was_scheduled() is False

    def test_failed_upload_leaves_file_in_watch_folder(self, monkeypatch):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, destination = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        source_path = pairs[0][0].filepath

        monkeypatch.setattr(video_sorter, 'get_kaltura_client', lambda: object())

        def fail_upload(*args, **kwargs):
            raise RuntimeError('upload failed')

        monkeypatch.setattr(video_sorter, 'upload_video', fail_upload)

        upload_files(pairs, destination)

        assert os.path.exists(source_path)
        assert count_nondirectory_files(destination) == 0
        clear_test_folder()

    def test_config_parser_supports_inline_comments(self):
        cfg = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        cfg.read_file(io.StringIO("""
[Settings]
mode=Upload # Upload or Move
log_level=INFO # See python's logging levels
start_time_tolerance=30 # Tolerance in minutes
"""))

        assert cfg.get('Settings', 'mode') == 'Upload'
        assert cfg.get('Settings', 'log_level') == 'INFO'
        assert cfg.getint('Settings', 'start_time_tolerance') == 30

def create_files_with_mod_date (dest_folder: str, pairs: list[tuple[str, datetime]]): 
    created = []
    for pair in pairs:
        path = os.path.join(dest_folder, pair[0])
        with open(path, 'w') as file:
            file.write('A')
        ts = pair[1].timestamp()
        os.utime(path, (ts, ts))
        created.append(path)

    return created

class TestReaper:
    def setup_directory(self):
        test_dir = os.path.join(config.get('Paths', 'test_folder'))
        work_path = os.path.join(test_dir, 'REAPERTESTS')
        if not os.path.exists(work_path):
            os.mkdir(work_path)
        clear_directory(work_path)
        return work_path
    
    def test_reap_old_files(self):
        dest = self.setup_directory()
        create_files_with_mod_date(dest, [
            ('barely', datetime(2023, 5, 22, 11, 59, 00)),
            ('byawidemargin', datetime(2022, 6, 22, 13, 25, 00))
        ])
        assert count_nondirectory_files(dest) == 2
        reap_files(dest, datetime(2023, 5, 22, 12, 00))
        assert count_nondirectory_files(dest) == 0

    def test_no_reap_new_files(self):
        dest = self.setup_directory()
        create_files_with_mod_date(dest, [
            ('barely', datetime(2023, 5, 22, 12, 1, 00)),
            ('byawidemargin', datetime(2023, 11, 23, 4, 45))
        ])
        assert count_nondirectory_files(dest) == 2
        reap_files(dest, datetime(2023, 5, 22, 12, 00))
        assert count_nondirectory_files(dest) == 2

    def test_reap_mixed(self):
        dest = self.setup_directory()
        files = create_files_with_mod_date(dest, [
            ('tooold', datetime(2023, 4, 21, 6, 34, 32)),
            ('newenough', datetime(2023, 6, 1, 15, 43))
        ])
        assert count_nondirectory_files(dest) == 2
        reap_files(dest, datetime(2023, 5, 22, 12, 00))
        assert count_nondirectory_files(dest) == 1
        assert not os.path.exists(files[0])
        assert os.path.exists(files[1])

    def test_reap_dirs(self):
        dest = self.setup_directory()
        folder_to_delete = os.path.join(dest, 'DELETE')
        folder_to_keep = os.path.join(dest, 'KEEP')
        os.mkdir(folder_to_delete)
        os.mkdir(folder_to_keep)
        create_files_with_mod_date(folder_to_delete, [
            ('shouldbedeleted', datetime(2020, 5, 6, 6, 45, 10)),
        ])
        create_files_with_mod_date(folder_to_keep, [
            ('shouldbedeleted', datetime(2020, 1, 4, 15, 59, 17)),
            ('shouldbekept', datetime(2023, 8, 4, 15, 59, 17))
        ])
        create_files_with_mod_date(dest, [
            ('tooold', datetime(2023, 4, 21, 6, 34, 32)),
            ('newenough', datetime(2023, 6, 1, 15, 43))
        ])
        assert count_nondirectory_files(dest) == 5
        reap_files(dest, datetime(2023, 5, 22, 12, 00))
        assert count_nondirectory_files(dest) == 2
        assert not os.path.exists(folder_to_delete)
        assert os.path.exists(folder_to_keep)
        assert count_nondirectory_files(folder_to_keep) == 1

class TestKalturaClient:
    def make_client(self):
        client = KalturaClient(KalturaConfiguration())
        client.sessionData = KalturaClient.SessionData({
            'ks': 'test-ks',
            'partnerId': 1234567,
        })
        return client

    def test_api_errors_raise_helpful_exception(self, monkeypatch):
        client = self.make_client()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'objectType': 'KalturaAPIException',
                    'code': 'SERVICE_FORBIDDEN',
                    'message': 'Forbidden action',
                }

        monkeypatch.setattr('mock_kaltura_client.requests.post', lambda *args, **kwargs: FakeResponse())

        with pytest.raises(KalturaApiError, match='SERVICE_FORBIDDEN: Forbidden action'):
            client.media.add(type('Entry', (), {
                'toDict': lambda self: {'name': 'Demo', 'mediaType': 1},
            })())

    def test_upload_uses_primary_action_endpoint_when_alternate_host_is_returned(self, monkeypatch):
        client = self.make_client()
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        def fake_post(url, json=None, files=None, timeout=None):
            calls.append({'url': url, 'json': json, 'files': files, 'timeout': timeout})
            if 'uploadtoken/action/add' in url:
                return FakeResponse({
                    'id': 'upload-token-1',
                    'uploadUrl': 'https://upload.example.com',
                })

            return FakeResponse({
                'id': 'upload-token-1',
                'status': 2,
            })

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        token = client.uploadToken.add(None)
        result = client.uploadToken.upload(token.id, io.BytesIO(b'test'), False, True, 0)

        assert result.id == 'upload-token-1'
        assert calls[1]['url'].startswith(
            'https://www.kaltura.com/api_v3/service/uploadtoken/action/upload'
        )
        assert 'upload.example.com' not in calls[1]['url']
        assert 'format=1' in calls[1]['url']
        assert 'uploadTokenId=upload-token-1' in calls[1]['url']
        assert calls[1]['timeout'] == (180, 900)

    def test_non_json_upload_error_identifies_stage_without_leaking_query(self, monkeypatch):
        client = self.make_client()
        client.sessionData.ks = 'secret-session-value'
        client.upload_urls['upload-token-1'] = (
            'https://upload.example.com/api_v3/service/uploadtoken/action/upload'
        )

        class FakeResponse:
            status_code = 200
            headers = {'Content-Type': 'text/html; charset=UTF-8'}
            content = b'<html>temporary proxy response</html>'

            def __init__(self, url):
                self.url = url

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError('not JSON')

        def fake_post(url, json=None, files=None, timeout=None):
            return FakeResponse(url)

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        with pytest.raises(KalturaApiError) as exc_info:
            client.uploadToken.upload('upload-token-1', io.BytesIO(b'test'), False, True, 0)

        message = str(exc_info.value)
        assert 'verify accepted upload failed' in message
        assert 'HTTP 200' in message
        assert 'content-type text/html; charset=UTF-8' in message
        assert 'endpoint https://www.kaltura.com/api_v3/service/uploadtoken/action/get' in message
        assert 'secret-session-value' not in message

    def test_empty_accepted_upload_is_verified_by_token_status(self, monkeypatch):
        client = self.make_client()
        client.upload_urls['upload-token-1'] = (
            'https://upload.example.com/api_v3/service/uploadtoken/action/upload'
        )
        calls = []

        class FakeResponse:
            def __init__(self, url, payload=None, status_code=200, content=b'{}'):
                self.url = url
                self.payload = payload
                self.status_code = status_code
                self.content = content
                self.headers = {'Content-Type': 'application/json'}

            def raise_for_status(self):
                return None

            def json(self):
                if self.payload is None:
                    raise ValueError('empty response')
                return self.payload

        def fake_post(url, json=None, files=None, timeout=None):
            calls.append({'url': url, 'json': json, 'files': files, 'timeout': timeout})
            if files:
                return FakeResponse(
                    url,
                    status_code=202,
                    content=b'',
                )
            return FakeResponse(url, {
                'id': 'upload-token-1',
                'status': 2,
            })

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        result = client.uploadToken.upload(
            'upload-token-1', io.BytesIO(b'test'), False, True, 0
        )

        assert result.id == 'upload-token-1'
        assert result.status == 2
        assert len(calls) == 2
        assert 'uploadtoken/action/get' in calls[1]['url']
        assert calls[1]['timeout'] == KalturaClient.JSON_TIMEOUT

    def test_empty_accepted_upload_must_reach_full_status(self, monkeypatch):
        client = self.make_client()
        client.UPLOAD_STATUS_POLL_ATTEMPTS = 2
        client.UPLOAD_STATUS_POLL_INTERVAL = 0
        client.upload_urls['upload-token-1'] = (
            'https://upload.example.com/api_v3/service/uploadtoken/action/upload'
        )

        class FakeResponse:
            headers = {'Content-Type': 'application/json'}

            def __init__(self, url, payload=None, status_code=200, content=b'{}'):
                self.url = url
                self.payload = payload
                self.status_code = status_code
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                if self.payload is None:
                    raise ValueError('empty response')
                return self.payload

        def fake_post(url, json=None, files=None, timeout=None):
            if files:
                return FakeResponse(url, status_code=202, content=b'')
            return FakeResponse(url, {
                'id': 'upload-token-1',
                'status': 1,
            })

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        with pytest.raises(KalturaApiError, match='last token status 1'):
            client.uploadToken.upload(
                'upload-token-1', io.BytesIO(b'test'), False, True, 0
            )

    def test_network_error_identifies_stage_without_leaking_query(self, monkeypatch):
        client = self.make_client()
        client.sessionData.ks = 'secret-session-value'
        client.upload_urls['upload-token-1'] = (
            'https://upload.example.com/api_v3/service/uploadtoken/action/upload'
        )

        def fake_post(url, json=None, files=None, timeout=None):
            raise requests.Timeout('request URL includes secret-session-value')

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        with pytest.raises(KalturaApiError) as exc_info:
            client.uploadToken.upload('upload-token-1', io.BytesIO(b'test'), False, True, 0)

        message = str(exc_info.value)
        assert 'verify accepted upload did not return a response' in message
        assert 'Timeout' in message
        assert 'endpoint https://www.kaltura.com/api_v3/service/uploadtoken/action/get' in message
        assert 'secret-session-value' not in message
