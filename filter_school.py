"""
Filter University Timetabling Data for all schools
=====================================================================================
Reads all 5 Excel files via TimetablingData, filters rows belonging to the school,
and saves the results to a folder.

Note: This no longer filters out specific rooms/buildings for each school, since not all 
schools are self-contained like the Vet School. 
"""

from data_loader import TimetablingData
from pathlib import Path

data = TimetablingData()
data.load_all()
all_schools = data.events["Module Department"].dropna().unique()

def filter_data(school: str):
    OUT_DIR = Path("Data") / school

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. DPT Data
    print("Filtering DPT Data...")
    filterDPT = data.dpt[data.dpt["Programme School Name"] == school]
    filterDPT.to_excel(OUT_DIR / "2024-5 DPT Data.xlsx", index=False)
    print(f"  {len(filterDPT):,} / {len(data.dpt):,} rows")

    # 2. Event Module Room
    print("Filtering Event Module Room...")
    filterEvents = data.events[data.events["Module Department"] == school]
    filterEvents.to_excel(OUT_DIR / "2024-5 Event Module Room.xlsx", index=False)
    print(f"  {len(filterEvents):,} / {len(data.events):,} rows")

    # 3. Student Programme Module Event
    print("Filtering Student Programme Module Event...")
    enroll = data.enrollments[data.enrollments["Department"] == school]
    enroll.to_excel(
        OUT_DIR / "2024-5 Student Programme Module Event.xlsx", index=False
    )
    print(f"  {len(enroll):,} / {len(data.enrollments):,} rows")

    # 4. Programme-Course (no department column — join via DPT programme codes)
    print("Filtering Programme-Course...")
    prog_codes = set(filterDPT["Programme Code"].unique())
    prog_code_col = data.prog_course["CourseId"].str.extract(r"^(.+?)_YR")[0]
    pc = data.prog_course[prog_code_col.isin(prog_codes)]
    pc.to_excel(OUT_DIR / "Programme-Course.xlsx", index=False)
    print(f"  {len(pc):,} / {len(data.prog_course):,} rows")

    # 5. Rooms and Room Types
    # print("Filtering Rooms...")
    # rooms_vet = data.rooms[data.rooms["Building.1"].astype(str) == VET_BUILDING]
    # rooms_vet.to_excel(OUT_DIR / "Rooms and Room Types.xlsx", index=False)
    # print(f"  {len(rooms_vet):,} / {len(data.rooms):,} rows")


    print(f"{all_schools.tolist().index(school)+1}/{len(all_schools)} Done — filtered files saved to {OUT_DIR}/\n")

def main():
    for school in all_schools:
        print(f"Filtering {school}")
        filter_data(school)
        
if __name__ == "__main__":
    main()
