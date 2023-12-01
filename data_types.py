from datetime import date, time, datetime
import os

class EventHost:
    def __init__(self, first, last, unid_zero_prefix):
        self.first = first
        self.last = last
        self.unid = f'u{unid_zero_prefix[1:]}'

    def full (self):
        return f'{self.first} {self.last}'

    def __str__(self) -> str:
        return f'Host(name={self.full()}, unid={self.unid})'

class Event:
    def __init__(self, start_time: time, hosts: list[EventHost]):
        self.start_time = start_time
        self.hosts = hosts

class Course(Event):
    def __init__(self, number: str, section: str, name: str, instructor_last: str, room_number: str, days: set[str], start_time: time, instructors: list[EventHost]):
        super().__init__(start_time, instructors)
        self.course_number_full = number
        self.section_number = section
        self.name = name
        self.instructor_last = instructor_last
        self.room_number = room_number
        self.days = days

    def get_first_host_alphabetically (self) -> EventHost:
        return sorted(self.hosts, key=lambda x : x.last + x.first)[0]

    def __str__(self) -> str:
        return f"Course(title={self.course_number_full}-{self.section_number} {self.name}, room_number={self.room_number}, days={self.days}, time={self.start_time})"

class Recording:
    def __init__(self, filepath: str, rec_device: str, date: date, time: time):
        self.filepath = filepath
        self.rec_device = rec_device
        self.date = date
        self.time = time

    @property
    def filename (self):
        return os.path.basename(self.filepath) if self.filepath else None

    def get_datetime(self):
        if self.time is None:
            return None
        return datetime(self.date.year, self.date.month, self.date.day, self.time.hour, self.time.minute, 0, 0)
    
class ManualRecording(Recording):
    def __init__(self, filepath: str, rec_device: str, date: date, time: time, metadata: str, unids: list[str]):
        super().__init__(filepath, rec_device, date, time)
        self.metadata = metadata
        self.unids = unids

class LectureRecording(Recording):
    def __init__(self, filepath: str, date: date, time: time, room_number: str, rec_device: str, course_number: str=None, section_number: str=None, course_code: str=None):
        super().__init__(filepath, rec_device, date, time)
        self.room_number = room_number
        self.course_number = course_number
        self.section_number = section_number
        self.course_code = course_code
    
    def course_number_full(self):
        if self.course_code is None or self.course_number is None:
            return ''
        return self.course_code + ' ' + self.course_number
    
    def __str__ (self):
        return f"Recording(room_number={self.room_number}, date={self.date}, time={self.time}, course_number_full={self.course_number_full()}, section_number={self.section_number}, path={self.filepath})"
 