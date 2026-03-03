# Deliverable for Mid Project Report

- All data loading and preprocessing functions and pipelines have been completed
- Model for Vet School is implemented in main.py; however is infeasible.
- Currently working on simplifying the code and formulation to solve the infeasibility issue. 


# Vet School Timetabling Pipeline

Data analysis pipeline for the Royal (Dick) School of Veterinary Studies (University of Edinburgh). Part of the TiAOR (Timetabling and Operations Research) course.

## Setup

```bash
pip install -r requirements.txt
```

## Data Files

Raw inputs are expected in `Data/` (not committed to version control):

| File | Description | Size |
|------|-------------|------|
| `2024-5 DPT Data.xlsx` | Degree programme table
| `2024-5 Event Module Room.xlsx` | Events, timeslots, room assignments
| `2024-5 Student Programme Module Event.xlsx` | Student enrolments 
| `Programme-Course.xlsx` | Compulsory/optional course flags
| `Rooms and Room Types.xlsx` | Room inventory and capacities

Vet-school-filtered copies are written to `Data/vet/` after running `filter_vet.py`.

---

## Scripts

### `filter_vet.py`

Reads all 5 raw Excel files and filters each down to rows belonging to the Royal (Dick) School of Veterinary Studies. Filtered files are saved to `Data/vet/` with the same filenames.

**Run:**
```bash
python filter_vet.py
```

**Output:** 5 filtered Excel files in `Data/vet/`

---

### `build_timetable.py`

Loads the vet-filtered data, selects events falling within a configurable 2-week window, joins room capacity information, adds a `Core` flag for compulsory modules, and exports the result as a structured Excel file. Also triggers EDA visualisations automatically.

**Run:**
```bash
python build_timetable.py                  # default: weeks 9-10
python build_timetable.py --start-week 15  # weeks 15-16
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--start-week` | int | `9` | First week of the 2-week window |

**Output:**
- `Data/vet/timetable_weeks_<start>_<end>.xlsx`
- 5 PNG plots in `plots/` (via `eda_visualizations.py`)

---

### `eda_visualizations.py`

Generates 5 analysis plots from a timetable DataFrame or Excel file. Called automatically by `build_timetable.py`, but can also be run standalone against any saved timetable file.

**Run (standalone):**
```bash
python eda_visualizations.py --timetable-path Data/vet/timetable_weeks_9_10.xlsx
```

**Arguments:**

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `--timetable-path` | str | Yes | Path to timetable Excel file from `build_timetable.py` |

**Output:** 5 PNG files saved to `plots/`:

| File | Description |
|------|-------------|
| `01_room_utilization.png` | Total scheduled hours per room (08:00–17:00) |
| `02_fill_ratio.png` | Event size vs room capacity histogram and pie chart |
| `03_event_distribution.png` | Heatmap of simultaneous events by day and hour |
| `04_busiest_rooms.png` | Top 15 rooms by event count, colour-coded by fill ratio |
| `05_busiest_modules.png` | Top 15 modules by event count, colour-coded by Core flag |

---

### `data_loader.py`

Central data hub. The `TimetablingData` class loads and exposes all 5 datasets. Run directly to print summary statistics and a sample of unique timeslots.

**Run:**
```bash
python data_loader.py
```

**`TimetablingData` class — key methods:**

| Method | Description |
|--------|-------------|
| `load_all()` | Loads all 5 Excel files into memory |
| `summary()` | Prints row counts, event types, room stats, and enrolment stats |
| `get_timeslots()` | Returns sorted list of unique timeslot strings |
| `get_events_by_module(module_code)` | Filters events to a given module code substring |
| `get_room_by_capacity(min, max)` | Filters rooms by capacity range |
| `get_student_schedule(student_id)` | Returns all events for a given anonymous student ID |

---

### `utils.py`

Shared constants and helper functions used across the pipeline. Not intended to be run directly.

**Contents:**

| Name | Type | Description |
|------|------|-------------|
| `VET_SCHOOL` | constant | Full school name string used for filtering |
| `VET_BUILDING` | constant | Building name string used for room filtering |
| `DATA_DIR` | `Path` | `Data/` |
| `VET_DATA_DIR` | `Path` | `Data/vet/` |
| `PLOTS_DIR` | `Path` | `plots/` |
| `DAY_ORDER` | list | Monday–Sunday ordering for plots |
| `HOUR_ORDER` | list | `08:00`–`21:00` hourly ordering for plots |
| `parse_weeks(value)` | function | Parses a Weeks cell (int, range, or comma-separated) into a `set` of week numbers |
| `parse_timeslot(timeslot)` | function | Splits a `"Day HH:MM"` string into a `(day, hour)` tuple |
| `get_fill_ratio_color(ratio)` | function | Returns a hex colour for a fill ratio (red/green/blue/grey) |
| `calculate_fill_ratio(size, capacity)` | function | Returns `size / capacity`, or `NaN` for missing data |

---

## Pipeline Overview

```
Raw Data (Data/*.xlsx)
        |
   filter_vet.py
        |
Vet-Filtered Data (Data/vet/*.xlsx)
        |
 build_timetable.py  ------>  eda_visualizations.py
        |                              |
Timetable Excel               5 PNG analysis plots
(Data/vet/timetable_weeks_*.xlsx)    (plots/)
```

## Project Structure

```
├── data_loader.py           # TimetablingData class and data loading utilities
├── filter_vet.py            # Filters university-wide data to vet school subset
├── build_timetable.py       # Generates 2-week timetable Excel output
├── eda_visualizations.py    # EDA plots (called by build_timetable or standalone)
├── utils.py                 # Shared constants and helper functions
├── Data/                    # Raw input data (not committed)
│   └── vet/                 # Vet-filtered outputs and timetable files
└── plots/                   # Generated PNG visualizations
```
