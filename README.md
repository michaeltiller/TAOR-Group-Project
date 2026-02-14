## Setup

python -m venv .venv
source venv/bin/activate (maybe different on mac and windows)
pip install -r requirements.txt

All data files are expected in Data/

## Usage

### Filter data to the vet school
python filter_vet.py

### Build 2 week timetable
python build_timetable.py

Flags:
--start_week: week to start with (default week 9 which is week 1 of classes)
--data_path: where to find data files (default to Data/vet which is where everything is saved from filter_vet)
--save: save the output or not (default is saved when running in terminal and not saved when imported for future)

