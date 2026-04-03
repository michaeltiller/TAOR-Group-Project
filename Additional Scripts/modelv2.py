#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 25 15:06:33 2026

@author: michael
"""
import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from timetabler import Timetabler
from utils import DAY_ORDER, HOUR_ORDER, parse_timeslot
from filter_vet import filter_vet_data
from data_loader import TimetablingData
from build_timetable import build_timetable



build_timetable()

data = TimetablingData("Data/vet")
data.load_all()

# in tempdpy we have course code this link to first half of module code in timetable_weeks 9_10, so we cna merge progyear to data 


tempdt = data.dpt[["Course Code", "ProgYear"]]



data = pd.read_excel("Data/vet/timetable_weeks_9_10.xlsx").dropna()

data = data[data['Event Type'] != 'Exam']
data['Year'] = data['Event Name'].str.get(1)
data['Course Code'] = data['Module Code'].str.split('_').str[0]

data = pd.merge(data, tempdt, on = 'Course Code', how ='left')



E = data['Event Name'].unique() #Event list string 

R = data['Room'].unique()

T = np.tile(np.array(HOUR_ORDER)[:, None], 5)


# filter on year 

K_1 = data.loc[data['Core'] == True  ] 



prob = xp.problem("Vet Problem")


x = {
    (e,r, t): prob.addVariable(name=f"x__E{e}_R{r}_T{t}", vartype = xp.binary, lb = 0)
    for e in E for r in R for t in T 
}





