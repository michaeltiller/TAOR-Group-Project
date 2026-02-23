"""
Filter University Timetabling Data for the Royal (Dick) School of Veterinary Studies
=====================================================================================
Reads all 5 Excel files via TimetablingData, filters rows belonging to the vet school,
and saves the results to Data/vet/.
"""

from data_loader import TimetablingData
from pathlib import Path

VET_SCHOOL = "Royal (Dick) School of Veterinary Studies"
VET_BUILDING = "Vet School"
OUT_DIR = Path("Data") / "vet"


def filter_vet_data():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    data = TimetablingData()
    data.load_all()

    # 1. DPT Data
    print("Filtering DPT Data...")
    dpt_vet = data.dpt[data.dpt["Programme School Name"] == VET_SCHOOL]
    dpt_vet.to_excel(OUT_DIR / "2024-5 DPT Data.xlsx", index=False)
    print(f"  {len(dpt_vet):,} / {len(data.dpt):,} rows")

    # 2. Event Module Room
    print("Filtering Event Module Room...")
    events_vet = data.events[data.events["Module Department"] == VET_SCHOOL]
    events_vet.to_excel(OUT_DIR / "2024-5 Event Module Room.xlsx", index=False)
    print(f"  {len(events_vet):,} / {len(data.events):,} rows")

    # 3. Student Programme Module Event
    print("Filtering Student Programme Module Event...")
    enrol_vet = data.enrollments[data.enrollments["Department"] == VET_SCHOOL]
    enrol_vet.to_excel(
        OUT_DIR / "2024-5 Student Programme Module Event.xlsx", index=False
    )
    print(f"  {len(enrol_vet):,} / {len(data.enrollments):,} rows")

    # 4. Programme-Course (no department column — join via DPT programme codes)
    print("Filtering Programme-Course...")
    vet_prog_codes = set(dpt_vet["Programme Code"].unique())
    prog_code_col = data.prog_course["CourseId"].str.extract(r"^(.+?)_YR")[0]
    pc_vet = data.prog_course[prog_code_col.isin(vet_prog_codes)]
    pc_vet.to_excel(OUT_DIR / "Programme-Course.xlsx", index=False)
    print(f"  {len(pc_vet):,} / {len(data.prog_course):,} rows")

    # 5. Rooms and Room Types
    print("Filtering Rooms...")
    rooms_vet = data.rooms[data.rooms["Building.1"].astype(str) == VET_BUILDING]
    rooms_vet.to_excel(OUT_DIR / "Rooms and Room Types.xlsx", index=False)
    print(f"  {len(rooms_vet):,} / {len(data.rooms):,} rows")

    print(f"\nDone — filtered files saved to {OUT_DIR}/")


if __name__ == "__main__":
    filter_vet_data()
