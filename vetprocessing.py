#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb  4 14:57:10 2026

@author: michael

Filter Timetabling Data for Vet School
==================================================
Read all 5 excel files via TimetablingData, filter rows
and save result to Data/vet
"""

from pathlib import Path
from data_loader import TimetablingData


VET_SCHOOL = "Royal (Dick) School of Veterinary Studies"
VET_BUILDING = "Vet School"
OUT_DIR = Path("Data") / "vet"


def filter_vet_data():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    data = TimetablingData()
    data.load_all()

    # DPT Data
    print("Filtering DPT Data...")
    dpt_vet = data.dpt[data.dpt["Programme School Name"] == VET_SCHOOL]
    dpt_vet.to_excel(OUT_DIR / "2024-5 DPT DATA.xlsx", index=False)

    # Events
    print("Filtering events...")
    events_vet = data.events[data.events["Module Department"] == VET_SCHOOL]
    events_vet.to_excel(OUT_DIR / "2024-5 Event Module Room.xlsx", index=False)

    # Student Programme
    print("Filtering Student Programme...")
    enrol_vet = data.enrollments[data.enrollments["Department"] == VET_SCHOOL]
    enrol_vet.to_excel(
        OUT_DIR / "2024-5 Student Programme Module Event.xlsx", index=False
    )

    # Programme Course (no dep, join via DPT)
    print("Filtering Programme-Course")
    vet_prog_codes = set(dpt_vet["Programme Code"].unique())
    prog_code_col = data.prog_course["CourseId"].str.extract(r"^(.+?)_YR")[0]
    pc_vet = data.prog_course[prog_code_col.isin(vet_prog_codes)]
    pc_vet.to_excel(OUT_DIR / "Programme-Course.xlsx", index=False)

    # Rooms
    print("Filtering Rooms")
    rooms_vet = data.rooms[data.rooms["Building.1"].astype(str) == VET_BUILDING]
    rooms_vet.to_excel(OUT_DIR / "Rooms and Room Types.xlsx", index=False)

    print(f"Done - filtered files saved to {OUT_DIR}")


if __name__ == "__main__":
    filter_vet_data()

