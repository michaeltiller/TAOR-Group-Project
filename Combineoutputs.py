#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Mar 25 14:49:33 2026

@author: michael
"""

import pandas as pd
import os

base_path = "/Users/michael/Desktop/TAOR/TAOR-Group-Project/multiSchoolOutput"

all_data = []

for root, dirs, files in os.walk(base_path):
    for file in files:
        if file.endswith(".xlsx"):
            file_path = os.path.join(root, file)
            df = pd.read_excel(file_path)
            df["source_file"] = file  # optional: track origin
            all_data.append(df)

combined = pd.concat(all_data, ignore_index=True)
combined.to_excel("combined_output.xlsx", index=False)
