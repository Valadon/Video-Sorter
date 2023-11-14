import os
import configparser
import datetime
config = configparser.ConfigParser()
config.read('config.ini')

from video_sorter import read_courses, match_courses_to_recordings, Recording

def clear_directory(directory_path):
    try:
        files = os.listdir(directory_path)
        for file in files:
            file_path = os.path.join(directory_path, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
            else:
                clear_directory(file_path)
    except OSError:
        exit(1)

def generate_files (recs: list[Recording]) -> list[str]:
    test_folder = config.get('Paths', 'test_folder')

    clear_directory(test_folder)

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
        rec.filename = filename
        f = open(filepath, "wb")
        f.close()

    return (watch_path, unmatched_path, matched_path)

def read_test_courses ():
    return read_courses(os.path.join(os.path.curdir, 'test_courses.xlsx'))

def get_test_recs ():
    return {
        'extron': Recording(None, datetime.date(2023, 11, 14), datetime.time(14, 28), 4603, 'extron'),
        'extron_2100': Recording(None, datetime.date(2023, 11, 13), datetime.time(13, 32), 2100, 'extron_2100'),
        'capturecast': Recording(None, datetime.date(2023, 11, 14), None, None, 'capturecast', '4560', '1', 'LAW'),
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
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course1'

    def test_valid_2100_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['extron_2100']
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course3'

    def test_valid_capturecast_sorting (self):
        courses = read_test_courses()
        testrec = get_test_recs()['capturecast']
        watch, unmatched, matched = generate_files([testrec])
        pairs = match_courses_to_recordings(courses, watch)
        assert str(pairs[0][0]) == str(testrec)
        assert pairs[0][1].name == 'Course2'
