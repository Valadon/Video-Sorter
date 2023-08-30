import csv
import os
import shutil
import re
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime, timedelta
import time
import pandas as pd
import logging
import configparser

config = configparser.ConfigParser()
config.read('config.ini')


def read_courses(csv_path):
    courses = []
    df = pd.read_excel(csv_path)

    # Drop rows that contain all NaN values
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
            continue  # Skip this row if no start time found

        # Extract and store the section number
        section_number = int(row['Section #']) if pd.notna(row['Section #']) else None

        courses.append({
            'course_number': row['Course'],
            'section_number': section_number,
            'course_name': row['Course Title'],
            'professor_name': instructor,
            'room_number': str(row['Room (cleaned)']),
            'days': days,
            'start_time': start_time
        })
        
    return courses



def find_course_by_number_and_section(course_number, section_number):
    print(f"Searching for course: {course_number}, section: {section_number}")  # Debugging line

    # Iterate through the courses list and look for a match
    for course in courses:
        print(f"Comparing with course: {course['course_number']}, section: {course['section_number']}")  # Debugging line

        if course['course_number'] == course_number and course['section_number'] == int(section_number):
            print("Match found!")  # Debugging line
            return course

    print("No match found.")  # Debugging line
    # Return None if no match is found
    return None



def parse_filename(filename):
    global courses  # Accessing the global courses variable

    # Existing filename patterns
    existing_pattern1 = r'(\d+)_Rec\d+_(\d{8})-\d_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    existing_pattern2 = r'SMP-2100_(\d{8})-(\d{6})_[sS]1[rR]1.mp4'
    
    # Matching existing patterns
    match_existing1 = re.match(existing_pattern1, filename)
    match_existing2 = re.match(existing_pattern2, filename)
    
    if match_existing1:
        room_number, _, date, time = match_existing1.groups()
        return room_number, date, time, None, None  # Additional None values for course_number and section_number
    if match_existing2:
        date, time = match_existing2.groups()
        room_number = '2100'
        return room_number, date, time, None, None  # Additional None values for course_number and section_number

    # New filename pattern
    new_pattern = r'(\w+)-(\d+)-(\d+)---(\d{1,2})-(\d{1,2})-(\d{4}).mp4'
    match_new = re.match(new_pattern, filename)

    if match_new:
        course_code, course_number, section_number, month, day, year = match_new.groups()
        section_number = int(section_number)  # Convert to integer
        date = f"{year}{month.zfill(2)}{day.zfill(2)}"
        course_number_full = course_code + ' ' + course_number  # Combine course_code and course_number

        matching_course = find_course_by_number_and_section(course_number_full, section_number)
        if matching_course:
            room_number = matching_course['room_number']
            return room_number, date, None, course_number_full, section_number  # Additional values for course_number and section_number

    return None, None, None, None, None  # Default return



def determine_course(room_number, date, time):
    # Check if time is None and handle accordingly
    if time is None:
        file_datetime = datetime.strptime(date, '%Y%m%d')
    else:
        file_datetime = datetime.strptime(date + time, '%Y%m%d%H%M%S')

    # Iterate through the courses and look for a match
    for course in courses:
        # Check for room match
        if course['room_number'] != room_number:
            continue

        # Convert course start time into a datetime object on the correct date
        course_time = datetime.strptime(course['start_time'], '%H:%M') # Assuming time in CSV is in HH:MM format
        course_datetime = file_datetime.replace(hour=course_time.hour, minute=course_time.minute, second=0)

        # Check if the file's datetime is within a tolerance window around the course start time
        tolerance = timedelta(minutes=15) # You can adjust this value
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
        print(f"Folder created at {folder_path}")
    except Exception as e:
        print(f"An error occurred while creating directory: {e}")
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
        print(f"Video moved from {src_path} to {dest_path}")
    except Exception as e:
        print(f"An error occurred while moving file: {e}")


def is_file_downloaded(filepath, interval=5, retries=3):
    """
    Check if a file has been completely downloaded.
    
    :param filepath: The path to the file.
    :param interval: The time interval to wait before checking the file size again.
    :param retries: The number of retries to check if the file has been downloaded.
    :return: True if the file has been downloaded, False otherwise.
    """
    last_size = -1
    retries_left = retries
    
    while retries_left > 0:
        if not os.path.exists(filepath):
            time.sleep(1)  # Wait for 1 second if the file does not exist
            continue

        current_size = os.path.getsize(filepath)
        
        if current_size == last_size and current_size > 0:
            return True
        
        last_size = current_size
        time.sleep(interval)
        retries_left -= 1

    return False


class VideoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith('.mp4'):
            return

        # Wait until the file has finished downloading
        if not is_file_downloaded(event.src_path):
            logging.warning(f"File {event.src_path} is not completely downloaded. Skipping.")
            return

        # Accessing the global courses variable directly
        room_number, date, time, course_number_full, section_number = parse_filename(os.path.basename(event.src_path))
        if room_number is None and date is None and time is None:
            logging.warning(f"Filename pattern didn't match for {event.src_path}. Moving to Unmatched_Videos.")
            unmatched_folder = os.path.join(DESTINATION_FOLDER, 'Unmatched_Videos')
            os.makedirs(unmatched_folder, exist_ok=True)
            move_unmatched_video(event.src_path, unmatched_folder)
        else:
            # If time is None, use find_course_by_number_and_section to find the course
            course = find_course_by_number_and_section(course_number_full, section_number) if time is None else determine_course(room_number, date, time)
            if course:
                dest_folder = create_folder(course, DESTINATION_FOLDER, date, time) # Modified to include date and time
                move_video(event.src_path, dest_folder, course, date)
            else:
                unmatched_folder = os.path.join(DESTINATION_FOLDER, 'Unmatched_Videos')
                os.makedirs(unmatched_folder, exist_ok=True)
                move_unmatched_video(event.src_path, unmatched_folder)
                logging.info(f"No course matched for {event.src_path}. Moved to {unmatched_folder}")

def process_existing_files():
    for filename in os.listdir(WATCH_FOLDER):
        if filename.endswith('.mp4'):
            file_path = os.path.join(WATCH_FOLDER, filename)
            # Wait until the file has finished downloading
            if not is_file_downloaded(file_path):
                logging.warning(f"File {file_path} is not completely downloaded. Skipping.")
                continue

            room_number, date, time, course_number_full, section_number = parse_filename(os.path.basename(file_path))
            if room_number is None and date is None and time is None:
                unmatched_folder = os.path.join(WATCH_FOLDER, 'Unmatched_Videos')
                os.makedirs(unmatched_folder, exist_ok=True)
                move_unmatched_video(file_path, unmatched_folder)
            else:
                course = find_course_by_number_and_section(course_number_full, section_number) if time is None else determine_course(room_number, date, time)
                if course:
                    dest_folder = create_folder(course, DESTINATION_FOLDER, date, time)
                    move_video(file_path, dest_folder, course, date)
                else:
                    unmatched_folder = os.path.join(WATCH_FOLDER, 'Unmatched_Videos')
                    os.makedirs(unmatched_folder, exist_ok=True)
                    move_unmatched_video(file_path, unmatched_folder)
                    logging.info(f"No course matched for {file_path}. Moved to {unmatched_folder}")


def move_unmatched_video(src_path, dest_folder):
    dest_path = os.path.join(dest_folder, os.path.basename(src_path))
    try:
        shutil.move(src_path, dest_path)
        print(f"Video moved from {src_path} to {dest_path}")
    except Exception as e:
        print(f"An error occurred while moving file: {e}")


def watch_folder(folder_path):
    process_existing_files()
    observer = Observer()
    observer.schedule(VideoHandler(), folder_path, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

# Set up logging
logging.basicConfig(level=logging.INFO)

WATCH_FOLDER = os.path.normpath(config.get('Paths', 'watch_folder'))
DESTINATION_FOLDER = os.path.normpath(config.get('Paths', 'destination_folder'))
EXCEL_FILE_PATH = os.path.normpath(config.get('Paths', 'excel_file'))
WATCH_FOLDER = os.path.abspath(WATCH_FOLDER) # Use absolute path

courses = read_courses(EXCEL_FILE_PATH)

watch_folder(WATCH_FOLDER)
process_existing_files()
