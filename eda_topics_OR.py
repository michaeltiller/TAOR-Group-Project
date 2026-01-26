import pandas as pd
from pathlib import Path

base_path = Path("/Users/kyraanndignazio/Desktop/timetabling_data")

files = {
    "DPT_Data": "2024-5 DPT Data (1).xlsx",
    "Student_Programme_Module_Event": "2024-5 Student Programme Module Event (1).xlsx",
    "Programme_Course": "Programme-Course.xlsx",
    "Rooms_and_Room_Types": "Rooms and Room Types (1).xlsx"
}

for name, filename in files.items():
    df = pd.read_excel(base_path / filename)
    df.to_csv(base_path / f"{name}.csv", index=False)


