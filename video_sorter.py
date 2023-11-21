
import os
import re
from time import sleep
import logging
import shutil
import pandas as pd
from datetime import datetime, timedelta, date, time
import configparser
from kaltura_uploader import *
from data_types import *

# Reading paths from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

DESTINATION_FOLDER = os.path.normpath(config.get('Paths', 'destination_folder'))
RECORDING_START_TOLERANCE = timedelta(minutes=30)
   
# Read course details from the Excel sheet into the global 'courses' list
def read_courses(excel_path) -> list[Course]:
    """
    Reads a list of courses from an excel file and parses them to Course objects. 
    For an example of how this excel file should look, see test_courses.xlsx
    """
    courses: list[Course] = []
    df = pd.read_excel(excel_path)
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

        instructors = []
        instructorStrings = row['Instructor'].split('; ')
        for s in instructorStrings:
            instructorMatch = re.search(r'([^(),\d]+),\s*([^()\d]+)\s+\((\d{8})\);*\s*', s)
            groups = instructorMatch.groups()
            instructors.append(Instructor(groups[1], groups[0], groups[2]))


        # Extract and store the section number
        section_number = row['Section #'] if pd.notna(row['Section #']) else None

        course = Course(
            row['Course'],
            str(section_number),
            row['Course Title'],
            instructor,
            str(row['Room (cleaned)']),
            days,
            start_time,
            instructors
        )

        courses.append(course)
        
    return courses

def find_course_by_number_and_section(courses: list[Course], rec: Recording) -> Course:
    """
    Finds the course in the list whose number and section matches the 
    one in the given recording object
    """
    logging.debug(f"Searching for course: {rec.course_number}, section: {rec.course_number}")

    # Iterate through the courses list and look for a match
    for course in courses:
        if course.number == rec.course_number_full() and course.section_number == rec.section_number:
            return course

    # Return None if no match is found
    return None

def parse_recording_file(filepath: str) -> Recording:
    """
    Parses a recording filename and puts that information into a recording object
    """
    filename = os.path.basename(filepath)

    # Existing filename patterns
    extron_pattern = r'(\d+)_Rec\d+_.*?_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    extron_2100_pattern = r'SMP-2100_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    capturecast_pattern = r'(\w+)-(\d+)-(\d+)---(\d{1,2})-(\d{1,2})-(\d{4}).mp4'
    
    # Matching existing patterns
    match_extron = re.match(extron_pattern, filename)
    match_extron_2100 = re.match(extron_2100_pattern, filename)
    match_capturecast = re.match(capturecast_pattern, filename)
    
    if match_extron:
        room_number, rec_date, rec_time = match_extron.groups()
        dt = datetime.strptime(f'{rec_date}{rec_time}', '%Y%m%d%H%M%S')
        rec = Recording(filepath, dt.date(), dt.time(), room_number, 'extron')
        return rec
    if match_extron_2100:
        rec_date, rec_time = match_extron_2100.groups()
        room_number = '2100'
        dt = datetime.strptime(f'{rec_date}{rec_time}', '%Y%m%d%H%M%S')
        rec = Recording(filepath, dt.date(), dt.time(), room_number, 'extron_2100')
        return rec
    if match_capturecast:
        course_code, course_number, section_number, month, day, year = match_capturecast.groups()
        rec_date = date(int(year), int(month), int(day))
        rec = Recording(filepath, rec_date, None, None, 'capturecast', course_number, section_number, course_code)
        return rec
    
    return Recording(filepath, None, None, None, None)



def find_course_by_room_and_datetime(courses: list[Course], rec: Recording) -> Course:
    """
    Finds the course in the given list which has a matching room, 
    date and time to the given recording
    """
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
        course_datetime_no_date = datetime(2023, 1, 1, course.start_time.hour, course.start_time.minute)
        rec_datetime_no_date = datetime(2023, 1, 1, rec.time.hour, rec.time.minute)
        window_start = course_datetime_no_date - tolerance
        window_end = course_datetime_no_date + tolerance
        if window_start <= rec_datetime_no_date and rec_datetime_no_date <= window_end:
            return course
    
    return None

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

def get_or_create_class_folder(course: Course, rec: Recording, dest_folder: str):
    """
    Returns the file path of the folder where a recording should go 
    based on the recording date and course
    """
    # Getting the date from the determine_course function
    # Check if time is None before concatenation
    semester = determine_semester(rec.date)
    
    # Creating the semester folder if it doesn't exist
    semester_path = os.path.join(dest_folder, semester)
    os.makedirs(semester_path, exist_ok=True)
    
    # Creating the course folder inside the semester folder
    folder_name = f"{course.number}_{course.name}_{course.instructor_last}".replace('&', '')
    folder_path = os.path.join(semester_path, folder_name)
    folder_path = os.path.abspath(folder_path)  # Use absolute path
    try:
        os.makedirs(folder_path, exist_ok=True)
        logging.info(f"Folder created at {folder_path}")
    except Exception as e:
        logging.error(f"An error occurred while creating directory: {e}")

    return folder_path

def get_new_filepath(rec: Recording, course: Course, dest_folder: str):
    """
    Returns a new filepath for the recording based on the course it is assigned to.

    For example, LAW 5000_Beekhuizen_11-17-2023
    """
    dest_folder = get_or_create_class_folder(course, rec, dest_folder)
    
    # Convert the date to a more readable format
    readable_date = rec.date.strftime("%m-%d-%y")

    new_filename = f"{course.name}_{course.instructor_last}_{readable_date}"
    counter = 1
    ext = '.mp4'
    full_path = os.path.join(dest_folder, f'{new_filename}{ext}')

    # If the file already exists, append a number to the name
    while os.path.exists(full_path):
        full_path = os.path.join(dest_folder, f'{new_filename}_{counter}{ext}')
        counter += 1

    return full_path

def move_video(rec: Recording, dest_path):
    """
    Moves a recording to the given destination
    """
    try:
        shutil.move(rec.filepath, dest_path)
        logging.info(f"Video moved from {rec.filepath} to {dest_path}")
        rec.filepath = dest_path
    except Exception as e:
        logging.error(f"An error occurred while moving {rec}: {e}")

def move_unmatched_video(rec: Recording, dest_folder):
    """
    Moves a recording to the unmatched videos location specified in the config
    """
    unmatched_folder = os.path.join(dest_folder, 'Unmatched_Videos')
    os.makedirs(unmatched_folder, exist_ok=True)
    dest_path = os.path.join(unmatched_folder, os.path.basename(rec.filepath))
    try:
        shutil.move(rec.filepath, dest_path)
        logging.info(f"No course matched for {rec}. Moved to {unmatched_folder}")
        rec.filepath = dest_path
    except Exception as e:
        logging.info(f"An error occurred while moving file: {e}")

def match_courses_to_recordings (courses: list[Course], watch_path) -> list[tuple[Recording, Course or None]]:
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

def move_files (pairs: list[tuple[Recording, Course or None]], dest_folder: str):
    """
    Given a list of recordings and their matching course, generate the new 
    file name and move the video to the correct folder.
    """
    for pair in pairs:
        if pair[1] is None:
            move_unmatched_video(pair[0], dest_folder)
        else:
            new_path = get_new_filepath(pair[0], pair[1], dest_folder)
            move_video(pair[0], new_path)

def upload_files (pairs: list[tuple[Recording, Course or None]], dest_folder: str):
    """
    Given a list of tuples containing Recordings and their corresponding Courses, 
    uploads files to Kaltura, and then sorts them into folders based on their course
    """
    try:
        client = get_kaltura_client()
    except Exception as e:
        logging.error(f"Could not establish a kaltura session: {e}")
        return
    for pair in pairs:
        if pair[1] is not None:
            for i in enumerate(pair[1].instructors):
                try:
                    new_path = get_new_filepath(pair[0], pair[1], dest_folder)
                    new_name = os.path.basename(new_path).replace('.mp4', '')
                    upload_video(pair[0], pair[1], client, new_name, i)
                    logging.info(f'Sucessfully uploaded: {pair[0]}. Now moving')
                except Exception as e:
                    logging.error(f'Error while uploading and moving {pair[0]}. {e}')

                move_video(pair[0], new_path)
                logging.info(f'Sucessfully moved: {pair[0]}')
        else:
            move_unmatched_video(pair[0], dest_folder)


def process_existing_files(courses: list[Course], watch_path, dest_path, mode):
    """
    Given a list of courses and file path on which to watch for 
    videos, processes videos accord to what mode has been set 
    in the config file.
    """
    pairs = match_courses_to_recordings(courses, watch_path)
    if mode == 'Upload':
        upload_files(pairs, dest_path)
    elif mode == 'Move':
        move_files(pairs, dest_path)

if __name__ == "__main__":
    WATCH_FOLDER = os.path.normpath(config.get('Paths', 'watch_folder'))
    WATCH_FOLDER = os.path.abspath(WATCH_FOLDER)
    DEST_FOLDER = os.path.normpath(config.get('Paths', 'dest_folder'))
    EXCEL_FILE_PATH = os.path.normpath(config.get('Paths', 'excel_file'))
    MODE = os.path.normpath(config.get('Settings', 'mode'))
    LOG_FILE = config.get('Settings', 'log_file')
    
    # Initialize logging
    logging.basicConfig(format='[%(levelname)s] %(asctime)s %(message)s', datefmt='[%m/%d/%Y %I:%M:%S %p]', filename=LOG_FILE, level=logging.INFO)
    logging.info('\n\nStarting the video sorter\n')

    courses = read_courses(EXCEL_FILE_PATH)
 
    # Process existing files immediately upon script start
    process_existing_files(courses, WATCH_FOLDER, DEST_FOLDER, MODE)
    
    while True:
        current_time = datetime.now().time()
        if current_time.hour == 3:
            logging.info("It's around 3 AM, time to sort the videos.")
            process_existing_files(courses)
            sleep(3600)  # Sleep for 1 hour
        else:
            sleep(60)  # Sleep for 1 minute
