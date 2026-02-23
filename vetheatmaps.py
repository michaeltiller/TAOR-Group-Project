import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import sys

BASE = Path(input("Enter path to your timetabling_data folder: ").strip())
VET_SCHOOL = "Royal (Dick) School of Veterinary Studies"
VET_BUILDING = "Vet School"
OUT = BASE / "vet_plots"
OUT.mkdir(exist_ok=True)

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
HOUR_ORDER = ["08:00", "09:00", "10:00", "11:00", "12:00",
              "13:00", "14:00", "15:00", "16:00", "17:00", "18:00"]

plt.style.use("seaborn-v0_8-whitegrid")

events = pd.read_excel(BASE / "2024-5 Event Module Room.xlsx")
events = events[events["Module Department"] == VET_SCHOOL].copy()

rooms = pd.read_excel(BASE / "Rooms and Room Types.xlsx")
rooms_vet = rooms[rooms["Building.1"].astype(str) == VET_BUILDING]

def split_timeslot(ts):
    if pd.isna(ts):
        return None, None
    parts = str(ts).split(" ")
    return (parts[0], parts[1]) if len(parts) >= 2 else (None, None)

events["Day"], events["Hour"] = zip(*events["Timeslot"].apply(split_timeslot))
cap_map = rooms_vet.set_index("Id")["Capacity"].to_dict()
events["Room_Capacity"] = events["Room"].map(cap_map)
events["Utilization"] = events["Event Size"] / events["Room_Capacity"]

valid = events.dropna(subset=["Day", "Hour"])
pivot = valid.groupby(["Day", "Hour"]).size().unstack(fill_value=0)
pivot = pivot.reindex(
    index=[d for d in DAY_ORDER if d in pivot.index],
    columns=[h for h in HOUR_ORDER if h in pivot.columns],
    fill_value=0
)
fig, ax = plt.subplots(figsize=(16, 6))
sns.heatmap(pivot, annot=True, fmt="d", cmap="YlOrRd",
            linewidths=0.5, cbar_kws={"label": "Number of Events"}, ax=ax)
ax.set_title("Vet School — When Are Events Scheduled?", fontsize=15, fontweight="bold", pad=15)
ax.set_xlabel("Time of Day", fontsize=12)
ax.set_ylabel("Day of Week", fontsize=12)
plt.tight_layout()
plt.savefig(OUT / "1_weekly_schedule.png", dpi=150)
plt.close()
print("Saved: 1_weekly_schedule.png")

valid2 = events.dropna(subset=["Room", "Day", "Utilization"])
pivot2 = valid2.pivot_table(index="Room", columns="Day",
                             values="Utilization", aggfunc="mean", fill_value=0)
pivot2 = pivot2.reindex(columns=[d for d in DAY_ORDER if d in pivot2.columns], fill_value=0)
pivot2 = pivot2.loc[pivot2.mean(axis=1).sort_values(ascending=False).index]
fig, ax = plt.subplots(figsize=(10, max(6, len(pivot2) * 0.45)))
sns.heatmap(pivot2, annot=True, fmt=".0%", cmap="RdYlGn_r",
            vmin=0, vmax=1.5, linewidths=0.5,
            cbar_kws={"label": "Avg Capacity Used"}, ax=ax)
ax.set_title("Vet School — How Full Are Rooms Each Day?", fontsize=13, fontweight="bold", pad=15)
ax.set_xlabel("Day of Week", fontsize=12)
ax.set_ylabel("Room", fontsize=12)
plt.tight_layout()
plt.savefig(OUT / "2_room_utilization.png", dpi=150)
plt.close()
print("Saved: 2_room_utilization.png")

room_counts = events["Room"].value_counts().head(15)
fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.barh(room_counts.index[::-1], room_counts.values[::-1], color="steelblue", edgecolor="white")
ax.set_xlabel("Number of Events", fontsize=12)
ax.set_title("Vet School — Top 15 Busiest Rooms", fontsize=14, fontweight="bold", pad=15)
for bar, val in zip(bars, room_counts.values[::-1]):
    ax.text(val + 1, bar.get_y() + bar.get_height()/2, str(val), va="center", fontsize=9)
plt.tight_layout()
plt.savefig(OUT / "3_busiest_rooms.png", dpi=150)
plt.close()
print("Saved: 3_busiest_rooms.png")

mod_counts = events.groupby("Module Code")["Event Size"].sum().sort_values(ascending=False).head(15)
fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.barh(mod_counts.index[::-1], mod_counts.values[::-1], color="coral", edgecolor="white")
ax.set_xlabel("Total Students Across All Events", fontsize=12)
ax.set_title("Vet School — Top 15 Modules by Total Students", fontsize=14, fontweight="bold", pad=15)
for bar, val in zip(bars, mod_counts.values[::-1]):
    ax.text(val + 1, bar.get_y() + bar.get_height()/2, str(val), va="center", fontsize=9)
plt.tight_layout()
plt.savefig(OUT / "4_busiest_modules.png", dpi=150)
plt.close()
print("Saved: 4_busiest_modules.png")

print("\nTop 10 rooms:")
print(events["Room"].value_counts().head(10).to_string())
print("\nTop 10 modules:")
print(events["Module Code"].value_counts().head(10).to_string())
print(f"\nAll plots saved to: {OUT}")
