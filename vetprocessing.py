#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb  4 14:57:10 2026

@author: michael
"""
import pandas as pd
import os
from pathlib import Path
from data_loader import *


data = TimetablingData().load_all()

events = data.events

vet = events.loc[events['Module Department'] == "Royal (Dick) School of Veterinary Studies"]

vet = vet[['Module Department', 'Module Code', 'Module Name', 'Event ID',
       'Event Name', 'Event Type', 'Duration (minutes)', 'Event Size',
       'Timeslot', 'WholeClass', 'Online Delivery', 'Number of Weeks', 'Weeks',
       'Room', 'Room type 2', 'Room Type 1', 'Building', 'Campus', 'Semester',
       'Room Lock']]

# drop duplicates 


# merge to dprs to get course year etc 