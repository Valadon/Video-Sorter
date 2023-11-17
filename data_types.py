from datetime import date, time, datetime
import os

class Instructor:
    def __init__(self, first, last, unid_zero_prefix):
        self.first = first
        self.last = last
        self.unid = f'u{unid_zero_prefix[1:]}'

    def full (self):
        return f'{self.first} {self.last}'

    def __str__(self) -> str:
        return f'Instructor(name={self.full()}, unid={self.unid})'

class Course:
    def __init__(self, number: str, section: str, name: str, instructor_last: str, room_number: str, days: set[str], start_time: time, instructors: list[Instructor]):
        self.number = number
        self.section_number = section
        self.name = name
        self.instructor_last = instructor_last
        self.room_number = room_number
        self.days = days
        self.start_time = start_time
        self.instructors = instructors

    def get_first_instructor_alphabetically (self) -> Instructor:
        return sorted(self.instructors, key=lambda x : x.last + x.first)[0]

    def __str__(self) -> str:
        return f"Course(title={self.number}-{self.section_number} {self.name}, room_number={self.room_number}, days={self.days}, time={self.start_time})"

    
class Recording:
    def __init__(self, filepath: str, date: date, time: time, room_number: str, rec_device:str, course_number: str=None, section_number: str=None, course_code: str=None):
        self.filepath = filepath
        self.date = date
        self.time = time
        self.room_number = room_number
        self.rec_device = rec_device
        self.course_number = course_number
        self.section_number = section_number
        self.course_code = course_code

    @property
    def filename (self):
        return os.path.basename(self.filepath) if self.filepath else None

    def get_datetime(self):
        if self.time is None:
            return None
        return datetime(self.date.year, self.date.month, self.date.day, self.time.hour, self.time.minute, 0, 0)
        
    def was_scheduled (self):
        return not ((self.room_number is None) and (self.date is None) and (self.time))
    
    def course_number_full(self):
        if self.course_code is None or self.course_number is None:
            return ''
        return self.course_code + ' ' + self.course_number
    
    def __str__ (self):
        return f"Recording(room_number={self.room_number}, date={self.date}, time={self.time}, course_number_full={self.course_number_full()}, section_number={self.section_number}, path={self.filepath})"
 