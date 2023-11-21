import os
import configparser
config = configparser.ConfigParser()
config.read('config.ini')

from data_types import *
from video_sorter import read_courses, match_courses_to_recordings, move_video, get_new_filepath

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

def clear_test_folder ():
    test_folder = config.get('Paths', 'test_folder')
    clear_directory(test_folder)

def generate_files (recs: list[Recording], numBytes=4):
    test_folder = config.get('Paths', 'test_folder')

    watch_path = os.path.join(test_folder, 'WATCH')
    os.makedirs(watch_path, exist_ok=True)
    unmatched_path = os.path.join(test_folder, 'UNMATCHED')
    os.makedirs(unmatched_path, exist_ok=True)
    matched_path = os.path.join(test_folder, 'MATCHED')
    os.makedirs(matched_path, exist_ok=True)

    for rec in recs:
        filename = ''

        if rec.rec_device == 'extron':
            stringdate = rec.date.strftime("%Y%m%d")
            stringtime = rec.time.strftime("%H%M%S")
            filename = f'{rec.room_number}_Rec1_{stringdate}-1_{stringdate}-{stringtime}_S1R1.mp4'
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
        f = open(filepath, "wb")
        f.write(bytes([0xFF for _ in range(numBytes)]))
        f.close()

    return (watch_path, unmatched_path, matched_path)

def read_test_courses ():
    return read_courses(os.path.join(os.path.curdir, 'test_courses.xlsx'))

def get_test_recs ():
    return {
        'extron': Recording(None, date(2023, 11, 14), time(14, 28), 4603, 'extron'),
        'extron_2100': Recording(None, date(2023, 11, 13), time(13, 32), 2100, 'extron_2100'),
        'capturecast': Recording(None, date(2023, 11, 14), None, None, 'capturecast', '4560', '1', 'LAW'),
        'extron_invalid': Recording(None, date(2023, 11, 14), time(10, 28), 4603, 'extron'),
    }

class TestSorter:
    def test_course_import (self):
        courses = read_test_courses()
        lastnames = ['BEEKHUIZEN', 'FERGUSON', 'FINCH MORALES']
        for i, course in enumerate(courses):
            assert course.name == f'Course{i+1}'
            assert course.instructor_last == lastnames[i]
            for ins in course.instructors:
                assert ins.last in lastnames[i]

    def test_valid_extron_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course1'
    
    def test_unscheduled_extron_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_invalid']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1] is None

    def test_valid_2100_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_2100']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course3'

    def test_valid_capturecast_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['capturecast']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course2'

    def test_get_user_alphabetical (self):
        courses = read_test_courses()
        for x in courses:
            if len(x.instructors) == 2:
                assert x.get_first_instructor_alphabetically().last == 'FINCH'

    def test_instructor_import (self):
        courses = read_test_courses()
        assert courses[0].instructors[0].last == 'BEEKHUIZEN'
        assert courses[2].instructors[0].last == 'FINCH'
        assert courses[2].instructors[1].last == 'MORALES'
        for c in courses:
            for i in c.instructors:
                assert len(i.unid) == 8
                assert i.unid[0] == 'u'

    def test_matched_video_moving_extron (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], matched)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_2100 (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_2100']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], matched)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_capturecast (self):
        courses = read_test_courses()
        testrec = get_test_recs()['capturecast']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec])
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], matched)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath

    def test_matched_video_moving_extron_large (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron']
        clear_test_folder()
        watch, unmatched, matched = generate_files([testrec], numBytes=10)
        old_path = testrec.filepath
        assert os.path.exists(old_path)
        pairs = match_courses_to_recordings(courses, watch)
        new_path = get_new_filepath(pairs[0][0], pairs[0][1], matched)
        move_video(pairs[0][0], new_path)
        assert not os.path.exists(old_path)
        assert os.path.exists(new_path)
        assert new_path == pairs[0][0].filepath