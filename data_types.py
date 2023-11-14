from datetime import date, time
import os

class Instructor:
    def __init__(self, first, last, unid):
        self.first = first
        self.last = last
        self.unid = unid

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
        return f"title: {self.number}-{self.section_number} {self.name} room_number: {self.room_number}, days: {self.days}, time: {self.start_time}"

    
class Recording:
    def __init__(self, filepath: str, date: date, time: time, room_number: str, rec_device:str, course_number: str=None, section_number: str=None, course_code: str=None):
        self.filepath = filepath
        self.filename = os.path.basename(filepath) if filepath else None
        self.date = date
        self.time = time
        self.room_number = room_number
        self.rec_device = rec_device
        self.course_number = course_number
        self.section_number = section_number
        self.course_code = course_code

    def get_datetime(self):
        return datetime(self.date.year, self.date.month, self.date.day, self.time.hour, self.time.minute, 0, 0)
        
    def was_scheduled (self):
        return not ((self.room_number is None) and (self.date is None) and (self.time))
    
    def course_number_full(self):
        if self.course_code is None or self.course_number is None:
            return ''
        return self.course_code + ' ' + self.course_number
    
    def __str__ (self):
        return f"room_number: {self.room_number}, date: {self.date}, time: {self.time}, course_number_full: {self.course_number_full()}, section_number: {self.section_number}"
 