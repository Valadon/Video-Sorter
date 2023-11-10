
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

# Initialize logging
logging.basicConfig(format='[%(levelname)s] %(asctime)s %(message)s', datefmt='[%m/%d/%Y %I:%M:%S %p]', filename='log.txt', level=logging.INFO)

# Global list to store course details
courses = []

class Course:
    def __init__(self, number, section, name, professor, room_number, days, start_time):
        self.number = number
        self.section_number = section
        self.name = name
        self.professor = professor
        self.room_number = room_number
        self.days = days
        self.start_time = start_time


    
class Recording:
    def __init__(self, filename: str, date: date, time: time, room_number: str, rec_device:str, course_number: str=None, section_number: str=None):
        self.filename = filename,
        self.date = date
        self.time = time
        self.room_number = room_number
        self.rec_device = rec_device
        
    def wasScheduled (self):
        return not ((self.room_number is None) and (self.date is None) and (self.time))
    
# Read course details from the Excel sheet into the global 'courses' list
def read_courses(csv_path) -> list(Course):
    global courses
    df = pd.read_excel(csv_path)
    df = df.dropna(how='all')
    for index, row in df.iterrows():
        instructor = str(row['Instructor LAST']).replace(' & ', ' ') if pd.notna(row['Instructor LAST']) else ''
        days_pattern = re.findall(r'M|TTh|T|W|F|Sa', str(row['Meeting Pattern']))
        days = []  # Define the days list here
        for day_pattern in days_pattern:
            if day_pattern == 'M':
                days.append('Monday')
            elif day_pattern == 'TTh':
                days.append('Tuesday')
                days.append('Thursday')
            elif day_pattern == 'T':
                days.append('Tuesday')
            elif day_pattern == 'W':
                days.append('Wednesday')
            elif day_pattern == 'F':
                days.append('Friday')
            elif day_pattern == 'Sa':
                days.append('Saturday')

        start_time = None

        # Extract time pattern from the correct column (modify as needed)
        time_pattern = str(row['Meeting Pattern'])

        # Correctly parse start time
        start_time_str = re.search(r'\b\d{1,2}:\d{2}(?:am|pm)\b|\b\d{1,2}(?:am|pm)\b', time_pattern, re.IGNORECASE)
        if start_time_str:
            start_time_str = start_time_str.group()
            start_time_str = start_time_str.replace('am', 'AM').replace('pm', 'PM')
            start_time = datetime.strptime(start_time_str, '%I:%M%p' if ':' in start_time_str else '%I%p').time().strftime('%H:%M')
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

# Existing helper functions
# ... (existing helper functions like parse_filename, create_folder, move_video, etc.)

def find_course_by_number_and_section(course_number, section_number) -> Course:
    logging.info(f"Searching for course: {course_number}, section: {section_number}")  # Debugging line

    # Iterate through the courses list and look for a match
    for course in courses:
        if course['course_number'] == course_number and course['section_number'] == section_number:
            return course

    # Return None if no match is found
    return None



def parse_filename(filename) -> Recording:
    global courses  # Accessing the global courses variable

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
        rec = Recording(filename, dt.date(), dt.time(), room_number, 'extron')
        return rec  # Additional None values for course_number and section_number
    if match_extron_2100:
        date, time = match_extron_2100.groups()
        room_number = '2100'
        dt = datetime.strptime(f'{date}{time}', '%Y%m%d%H%M%S')
        rec = Recording(filename, dt.date(), dt.time(), room_number, 'extron_2100')
        return rec  # Additional None values for course_number and section_number
    if match_capturecast:
        course_code, course_number, section_number, month, day, year = match_capturecast.groups()
        date = f"{year}{month.zfill(2)}{day.zfill(2)}"
        dt = datetime.strptime(f'{date}{time}', '%Y%m%d%H%M%S')
        rec = Recording(filename, dt.date(), dt.time(), None, 'capturecast')
        return rec
        # TODO: DETERMINE IF THIS IS ACTUALLY NECESSARY
        # course_number_full = course_code + ' ' + course_number  # Combine course_code and course_number

        # matching_course = find_course_by_number_and_section(course_number_full, section_number)
        # if matching_course:
        #     room_number = matching_course['room_number']
        #     return room_number, date, None, course_number_full, section_number  # Additional values for course_number and section_number

    return None  # Default return



def find_course_by_room_and_time(rec: Recording):
    # Iterate through the courses and look for a match
    for course in courses:
        # Check for room match
        if course['room_number'] != rec.room_number:
            continue

        # Convert course start time into a datetime object on the correct date
        course_time = datetime.strptime(course['start_time'], '%H:%M') # Assuming time in CSV is in HH:MM format
        course_datetime = file_datetime.replace(hour=course_time.hour, minute=course_time.minute, second=0)

        # Check if the file's datetime is within a tolerance window around the course start time
        tolerance = timedelta(minutes=30) # You can adjust this value
        if abs(file_datetime - course_datetime) <= tolerance:
            # Check if the day of the week matches
            if file_datetime.strftime('%A') in course['days']: # Assuming days in CSV are full names like 'Monday'
                return course
    
    # Return None if no match is found
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

def create_folder(course, base_path, date, time):
    # Getting the date from the determine_course function
    # Check if time is None before concatenation
    if time is not None:
        file_datetime = datetime.strptime(date + time, '%Y%m%d%H%M%S')
    else:
        file_datetime = datetime.strptime(date, '%Y%m%d')  # Just use the date part
    semester = determine_semester(file_datetime)
    
    # Creating the semester folder if it doesn't exist
    semester_path = os.path.join(base_path, semester)
    os.makedirs(semester_path, exist_ok=True)
    
    # Creating the course folder inside the semester folder
    folder_name = f"{course['course_number']}_{course['course_name']}_{course['professor_name']}".replace('&', '')
    folder_path = os.path.join(semester_path, folder_name)
    folder_path = os.path.abspath(folder_path)  # Use absolute path
    try:
        os.makedirs(folder_path, exist_ok=True)
        logging.info(f"Folder created at {folder_path}")
    except Exception as e:
        logging.error(f"An error occurred while creating directory: {e}")
    return folder_path

def move_video(src_path, dest_folder, course, date):  # Added date as a parameter
    # Convert the date to a more readable format
    readable_date = datetime.strptime(date, '%Y%m%d').strftime('%m-%d-%y')

    new_filename = f"{course['course_name']}_{course['professor_name']}_{readable_date}.mp4"
    dest_path = os.path.join(dest_folder, new_filename)
    counter = 1

    # If the file already exists, append a number to the name
    while os.path.exists(dest_path):
        name, ext = os.path.splitext(new_filename)
        dest_path = os.path.join(dest_folder, f"{name}_{counter}{ext}")
        counter += 1

    try:
        shutil.move(src_path, dest_path)
        logging.info(f"Video moved from {src_path} to {dest_path}")
    except Exception as e:
        logging.error(f"An error occurred while moving file: {e}")

def move_unmatched_video(src_path, dest_folder):
    dest_path = os.path.join(dest_folder, os.path.basename(src_path))
    try:
        shutil.move(src_path, dest_path)
        logging.info(f"Video moved from {src_path} to {dest_path}")
    except Exception as e:
        logging.info(f"An error occurred while moving file: {e}")

def process_existing_files():
    for filename in os.listdir(WATCH_FOLDER):

        if not filename.endswith('.mp4'):
            continue

        file_path = os.path.join(WATCH_FOLDER, filename)
        room_number, date, time, course_number_full, section_number = parse_filename(os.path.basename(file_path))

        logging.info(f"room_number: {room_number}, date: {date}, time: {time}, course_number_full: {course_number_full}, section_number: {section_number}")

        if course.wasScheduled():
            course = find_course_by_room_and_time(room_number, date, time)
            if course:
                dest_folder = create_folder(course, DESTINATION_FOLDER, date, time)
                move_video(file_path, dest_folder, course, date)
            else:
                unmatched_folder = os.path.join(DESTINATION_FOLDER, 'Unmatched_Videos')
                os.makedirs(unmatched_folder, exist_ok=True)
                move_unmatched_video(file_path, unmatched_folder)
                logging.info(f"No course matched for {file_path}. Moved to {unmatched_folder}")
        else:
            unmatched_folder = os.path.join(DESTINATION_FOLDER, 'Unmatched_Videos')
            os.makedirs(unmatched_folder, exist_ok=True)
            move_unmatched_video(file_path, unmatched_folder)
            

if __name__ == "__main__":
    read_courses(EXCEL_FILE_PATH)
    
    # Process existing files immediately upon script start
    process_existing_files()
    
    while True:
        current_time = datetime.now().time()
        if current_time.hour == 3:
            logging.info("It's around 3 AM, time to sort the videos.")
            process_existing_files()
            time.sleep(3600)  # Sleep for 1 hour
        else:
            time.sleep(60)  # Sleep for 1 minute
