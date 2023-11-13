
import os
import re
import time
import logging
import shutil
import pandas as pd
from datetime import datetime, timedelta, date, time
import configparser

# Reading paths from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

WATCH_FOLDER = os.path.normpath(config.get('Paths', 'watch_folder'))
DESTINATION_FOLDER = os.path.normpath(config.get('Paths', 'destination_folder'))
EXCEL_FILE_PATH = os.path.normpath(config.get('Paths', 'excel_file'))
WATCH_FOLDER = os.path.abspath(WATCH_FOLDER)

RECORDING_START_TOLERANCE = timedelta(minutes=30)

# Initialize logging
logging.basicConfig(format='[%(levelname)s] %(asctime)s %(message)s', datefmt='[%m/%d/%Y %I:%M:%S %p]', filename='log.txt', level=logging.INFO)

class Course:
    def __init__(self, number: str, section: str, name: str, professor: str, room_number: str, days: set[str], start_time: time):
        self.number = number
        self.section_number = section
        self.name = name
        self.professor = professor
        self.room_number = room_number
        self.days = days
        self.start_time = start_time


    
class Recording:
    def __init__(self, file_path: str, date: date, time: time, room_number: str, rec_device:str, course_number: str=None, section_number: str=None, course_code: str=None):
        self.file_path = file_path,
        self.filename = os.path.basename(file_path)
        self.date = date
        self.time = time
        self.room_number = room_number
        self.rec_device = rec_device
        self.course_number = course_number
        self.section_number = section_number
        self.course_code = course_code

    def get_datetime(self):
        return datetime(self.date.year, self.date.month, self.date.day, self.time.hour, self.time.minute, 0, 0)
        
    def wasScheduled (self):
        return not ((self.room_number is None) and (self.date is None) and (self.time))
    
# Read course details from the Excel sheet into the global 'courses' list
def read_courses(csv_path) -> list(Course):
    courses: list[Course] = []
    df = pd.read_excel(csv_path)
    df = df.dropna(how='all')
    for index, row in df.iterrows():
        instructor = str(row['Instructor LAST']).replace(' & ', ' ') if pd.notna(row['Instructor LAST']) else ''
        days_pattern = re.findall(r'M|TTh|T|W|F|Sa', str(row['Meeting Pattern']))
        days = set()  # Define the days list here
        for day_pattern in days_pattern:
            if day_pattern == 'M':
                days.add('Monday')
            elif day_pattern == 'TTh':
                days.add('Tuesday')
                days.add('Thursday')
            elif day_pattern == 'T':
                days.add('Tuesday')
            elif day_pattern == 'W':
                days.add('Wednesday')
            elif day_pattern == 'F':
                days.add('Friday')
            elif day_pattern == 'Sa':
                days.add('Saturday')

        start_time = None

        # Extract time pattern from the correct column (modify as needed)
        time_pattern = str(row['Meeting Pattern'])

        # Correctly parse start time
        start_time_str = re.search(r'\b\d{1,2}:\d{2}(?:am|pm)\b|\b\d{1,2}(?:am|pm)\b', time_pattern, re.IGNORECASE)
        if start_time_str:
            start_time_str = start_time_str.group()
            start_time_str = start_time_str.replace('am', 'AM').replace('pm', 'PM')
            start_time = datetime.strptime(start_time_str, '%I:%M%p' if ':' in start_time_str else '%I%p').time()
        else:
            logging.warning(f"Invalid start time found for course {row['Course']}. Skipping.")


        # Extract and store the section number
        section_number = int(row['Section #']) if pd.notna(row['Section #']) else None

        course = Course(
            row['Course'],
            section_number,
            row['Course Title'],
            instructor,
            str(row['Room (cleaned)']),
            days,
            start_time
        )

        courses.append(course)
        
    return courses

def find_course_by_number_and_section(courses: list[Course], rec: Recording) -> Course:
    logging.debug(f"Searching for course: {rec.course_number}, section: {rec.course_number}")

    # Iterate through the courses list and look for a match
    for course in courses:
        if course.number == rec.course_number and course.section_number == rec.section_number:
            return course

    # Return None if no match is found
    return None

def parse_recording_file(file_path: str) -> Recording:
    filename = os.path.basename(file_path)

    # Existing filename patterns
    extron_pattern = r'(\d+)_Rec\d+_.*?_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    extron_2100_pattern = r'SMP-2100_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    capturecast_pattern = r'(\w+)-(\d+)-(\d+)---(\d{1,2})-(\d{1,2})-(\d{4}).mp4'
    
    # Matching existing patterns
    match_extron = re.match(extron_pattern, filename)
    match_extron_2100 = re.match(extron_2100_pattern, filename)
    match_capturecast = re.match(capturecast_pattern, filename)
    
    if match_extron:
        room_number, date, time = match_extron.groups()
        dt = datetime.strptime(f'{date}{time}', '%Y%m%d%H%M%S')
        rec = Recording(file_path, dt.date(), dt.time(), room_number, 'extron')
        return rec
    if match_extron_2100:
        date, time = match_extron_2100.groups()
        room_number = '2100'
        dt = datetime.strptime(f'{date}{time}', '%Y%m%d%H%M%S')
        rec = Recording(file_path, dt.date(), dt.time(), room_number, 'extron_2100')
        return rec
    if match_capturecast:
        course_code, course_number, section_number, month, day, year = match_capturecast.groups()
        date = f"{year}{month.zfill(2)}{day.zfill(2)}"
        dt = datetime.strptime(f'{date}{time}', '%Y%m%d%H%M%S')
        rec = Recording(file_path, dt.date(), dt.time(), None, 'capturecast', course_number, section_number, course_code)
        return rec
        # TODO: DETERMINE IF THIS IS ACTUALLY NECESSARY
        # course_number_full = course_code + ' ' + course_number  # Combine course_code and course_number

        # matching_course = find_course_by_number_and_section(course_number_full, section_number)
        # if matching_course:
        #     room_number = matching_course['room_number']
        #     return room_number, date, None, course_number_full, section_number  # Additional values for course_number and section_number

    return None  # Default return



def find_course_by_room_and_datetime(courses: list[Course], rec: Recording) -> Course:
    # Iterate through the courses and look for a match
    for course in courses:
        # Check for room match
        if course.room_number != rec.room_number:
            continue
            
        if rec.time is None:
            continue

        weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        recording_weekday = rec.date.weekday()

        if weekdays[recording_weekday] not in course.days:
            continue

        # Check if the file's datetime is within a tolerance window around the course start time
        tolerance = RECORDING_START_TOLERANCE
        windowStart = course.start_time - tolerance
        windowEnd = course.start_time + tolerance
        if windowStart <= rec.time and rec.time <= windowEnd:
            return course
    
    return None

def determine_semester(date):
    month = date.month
    year = date.year % 100  # Getting the last two digits of the year
    if month <= 4:
        return f'Spring{year}'
    elif month == 5 or month == 6 or month == 7:
        return f'Summer{year}'
    else:
        return f'Fall{year}'

def get_or_create_class_folder(course: Course, rec: Recording):
    # Getting the date from the determine_course function
    # Check if time is None before concatenation
    semester = determine_semester(rec.get_datetime())
    
    # Creating the semester folder if it doesn't exist
    semester_path = os.path.join(DESTINATION_FOLDER, semester)
    os.makedirs(semester_path, exist_ok=True)
    
    # Creating the course folder inside the semester folder
    folder_name = f"{course.number}_{course.name}_{course.professor}".replace('&', '')
    folder_path = os.path.join(semester_path, folder_name)
    folder_path = os.path.abspath(folder_path)  # Use absolute path
    try:
        os.makedirs(folder_path, exist_ok=True)
        logging.info(f"Folder created at {folder_path}")
    except Exception as e:
        logging.error(f"An error occurred while creating directory: {e}")

    return folder_path

def move_video(course: Course, rec: Recording):  # Added date as a parameter
    dest_folder = get_or_create_class_folder(course, rec)

    # Convert the date to a more readable format
    readable_date = rec.get_datetime().strftime("%m-%d-%y")

    new_filename = f"{course.name}_{course.professor}_{readable_date}.mp4"
    dest_path = os.path.join(dest_folder, new_filename)
    counter = 1

    # If the file already exists, append a number to the name
    while os.path.exists(dest_path):
        name, ext = os.path.splitext(new_filename)
        dest_path = os.path.join(dest_folder, f"{name}_{counter}{ext}")
        counter += 1

    try:
        shutil.move(rec.file_path, dest_path)
        logging.info(f"Video moved from {rec.file_path} to {dest_path}")
    except Exception as e:
        logging.error(f"An error occurred while moving file: {e}")

def move_unmatched_video(rec: Recording):
    unmatched_folder = os.path.join(DESTINATION_FOLDER, 'Unmatched_Videos')
    os.makedirs(unmatched_folder, exist_ok=True)
    dest_path = os.path.join(unmatched_folder, os.path.basename(rec.file_path))
    try:
        shutil.move(rec.file_path, dest_path)
        logging.info(f"No course matched for {rec.file_path}. Moved to {unmatched_folder}")
    except Exception as e:
        logging.info(f"An error occurred while moving file: {e}")

def process_existing_files(courses: list[Course]):
    for filename in os.listdir(WATCH_FOLDER):

        if not filename.endswith('.mp4'):
            continue

        file_path = os.path.join(WATCH_FOLDER, filename)
        rec = parse_recording_file(file_path)

        logging.info(f"room_number: {rec.room_number}, date: {rec.date}, time: {rec.time}, course_number_full: {rec.course_number}, section_number: {rec.section_number}")

        if rec.wasScheduled():
            if rec.time is None:
                course = find_course_by_number_and_section(courses, rec)
            else:
                course = find_course_by_room_and_datetime(courses, rec)

            if course is not None:
                move_video(rec, course)
            else:
                move_unmatched_video(rec)
        else:
            move_unmatched_video(rec)
            

if __name__ == "__main__":
    courses = read_courses(EXCEL_FILE_PATH)
    
    # Process existing files immediately upon script start
    process_existing_files(courses)
    
    while True:
        current_time = datetime.now().time()
        if current_time.hour == 3:
            logging.info("It's around 3 AM, time to sort the videos.")
            process_existing_files(courses)
            time.sleep(3600)  # Sleep for 1 hour
        else:
            time.sleep(60)  # Sleep for 1 minute
