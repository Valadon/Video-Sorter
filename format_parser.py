from data_types import *
import re

def extron_format_parser(filepath: str) -> LectureRecording or None:
    filename = os.path.basename(filepath)
    extron_pattern = r'(\d+)_Rec\d+_.*?_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    match_extron = re.match(extron_pattern, filename)
    if match_extron:
        room_number, rec_date, rec_time = match_extron.groups()
        dt = datetime.strptime(f'{rec_date}{rec_time}', '%Y%m%d%H%M%S')
        rec = LectureRecording(filepath, dt.date(), dt.time(), room_number, 'extron')
        return rec
    else:
        return None
    
def capturecast_format_parser(filepath: str) -> LectureRecording or None:
    filename = os.path.basename(filepath)
    capturecast_pattern = r'(\w+)-(\d+)-(\d+)---(\d{1,2})-(\d{1,2})-(\d{4}).mp4'
    match_capturecast = re.match(capturecast_pattern, filename)
    if match_capturecast:
        course_code, course_number, section_number, month, day, year = match_capturecast.groups()
        rec_date = date(int(year), int(month), int(day))
        rec = LectureRecording(filepath, rec_date, None, None, 'capturecast', course_number, section_number, course_code)
        return rec
    else:
        return None
    
def extron_2100_format_parser(filepath: str) -> LectureRecording or None:
    filename = os.path.basename(filepath)
    extron_2100_pattern = r'SMP-2100_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    match_extron_2100 = re.match(extron_2100_pattern, filename)

    if match_extron_2100:
        rec_date, rec_time = match_extron_2100.groups()
        room_number = '2100'
        dt = datetime.strptime(f'{rec_date}{rec_time}', '%Y%m%d%H%M%S')
        rec = LectureRecording(filepath, dt.date(), dt.time(), room_number, 'extron_2100')
        return rec
    else:
        return None
    
# CURRENTLY UNUSED
def manual_format_parser(filepath: str) -> ManualRecording or None:
    filename = os.path.basename(filepath)
    pattern = r'((?:u\d{7} )+)(.+)'
    match_pattern = re.match(pattern, filename)

    if match_pattern:
        unids, metadata = match_pattern.groups()
        dt = datetime.fromtimestamp(os.stat().st_mtime)
        rec = ManualRecording(filepath, 'manual', dt.date(), dt.time(), metadata, unids.split(' '))
        return rec
    else:
        return None