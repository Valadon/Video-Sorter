import os
import configparser
import io
import pandas as pd
import pytest
config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
config.read('config.ini')

from data_types import *
from mock_kaltura_client import KalturaApiError, KalturaClient, KalturaConfiguration
import video_sorter
from video_sorter import read_courses, match_courses_to_recordings, move_video, get_new_filepath, process_existing_files, find_course_by_room_and_datetime, upload_files
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
                'Instructor LAST': 'BEEKHUIZEN',
                'Room (cleaned)': 4603,
                'Instructor': 'BEEKHUIZEN, MARK (00104226)',
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
                'Instructor LAST': 'BEEKHUIZEN',
                'Room (cleaned)': 4603,
                'Instructor': 'BEEKHUIZEN, MARK (00104226)',
                'Room': 'LAW 4603',
            }
        ])
        monkeypatch.setattr(video_sorter.pd, 'read_excel', lambda _: df)

        courses = read_courses('ignored.xlsx')

        assert courses[0].days == {'Thursday'}

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

    def test_upload_uses_returned_upload_url_when_available(self, monkeypatch):
        client = self.make_client()
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        def fake_post(url, json=None, files=None):
            calls.append({'url': url, 'json': json, 'files': files})
            if 'uploadtoken/action/add' in url:
                return FakeResponse({
                    'id': 'upload-token-1',
                    'uploadUrl': 'https://upload.example.com/api_v3/service/uploadtoken/action/upload?format=1',
                })

            return FakeResponse({
                'id': 'upload-token-1',
                'status': 2,
            })

        monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

        token = client.uploadToken.add(None)
        result = client.uploadToken.upload(token.id, io.BytesIO(b'test'), False, True, 0)

        assert result.id == 'upload-token-1'
        assert calls[1]['url'].startswith('https://upload.example.com/api_v3/service/uploadtoken/action/upload')
        assert 'uploadTokenId=upload-token-1' in calls[1]['url']
