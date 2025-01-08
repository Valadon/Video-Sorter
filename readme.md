# Installation
1. Create a venv (`python -m venv virtual`)
2. Activate your venv (`.\virtual\Scripts\activate`)
3. `pip install requirements.txt`
4. Acquire a copy of the .env file from our BeyondTrust vault and put it in the main directory. This file is included in the .gitignore and SHOULD NEVER BE COMMITTED TO VERSION CONTROL
5. Create a config.ini file. Check the config-EXAMPLE.ini file for an example of how this should look.
6. Run the sorter with `python video_sorter.py`

# Running Tests
To run the tests, simply run `pytest` in the command line after installing the project. These tests depend on a specific configuration of the test_courses.xlsx, so use caution if you choose to edit this file to add more tests.

# Build to Executable
1. Run `pyinstaller video_sorter.py`

# Considerations for the Schedule file
- Ensure that the Room (Cleaned) column contains only the room number, with no prefix
- You may delete any rows for online classes, or any classes which specify (Does not Meet) but this shouldn't be required
- Make sure the instructor column fits the expected format. Occasionally, the instructor will simply be listed as "staff"

# Roadmap
- ~~Write instructions for building to an executable.~~
- Develop a special case which allows the script to ingest filenames which are preceded by a list of uNIDs. For example: u1234567 u9876543 META_DATA.mp4. This would help us deliver videos to people even if their recording wasn't scheduled
- Alternatively, create a separate schedule that tracks one-time events
- ~~Purge files after a specific date~~
