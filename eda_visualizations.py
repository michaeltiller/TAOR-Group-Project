"""
EDA Visualizations for University Timetabling Dataset
======================================================
Generates exploratory data analysis plots for OR-based scheduling optimization.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from collections import Counter
from data_loader import TimetablingData

# Configuration
PLOTS_DIR = Path("plots")
PLOTS_DIR.mkdir(exist_ok=True)

# Style settings
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")

# Day and time ordering
DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
HOUR_ORDER = ['08:00', '09:00', '10:00', '11:00', '12:00', '13:00', '14:00',
              '15:00', '16:00', '17:00', '18:00', '19:00', '20:00', '21:00']


def parse_timeslot(timeslot):
    """Parse timeslot string into day and hour."""
    if pd.isna(timeslot):
        return None, None
    parts = str(timeslot).split(' ')
    if len(parts) >= 2:
        day = parts[0]
        hour = parts[1]
        return day, hour
    return None, None


def parse_weeks(weeks_str):
    """Parse weeks string into list of week numbers."""
    if pd.isna(weeks_str):
        return []
    try:
        weeks = []
        for part in str(weeks_str).split(','):
            part = part.strip()
            if '-' in part:
                start, end = part.split('-')
                weeks.extend(range(int(start), int(end) + 1))
            else:
                weeks.append(int(part))
        return weeks
    except (ValueError, AttributeError):
        return []


def preprocess_data(data):
    """Add derived columns to events dataframe."""
    events = data.events.copy()

    # Parse timeslot into day and hour
    events['Day'], events['Hour'] = zip(*events['Timeslot'].apply(parse_timeslot))

    # Parse weeks
    events['Week_List'] = events['Weeks'].apply(parse_weeks)
    events['Num_Weeks'] = events['Week_List'].apply(len)

    # Merge room capacity from rooms data (use 'Id' column which matches event 'Room')
    room_capacity = data.rooms.set_index('Id')['Capacity'].to_dict()
    events['Room_Capacity'] = events['Room'].map(room_capacity)

    # Calculate capacity utilization ratio
    events['Capacity_Utilization'] = events['Event Size'] / events['Room_Capacity']

    return events


def plot_01_room_capacity_histogram(data):
    """Plot 1: Room capacity distribution histogram."""
    fig, ax = plt.subplots(figsize=(10, 6))

    capacities = data.rooms['Capacity'].dropna()

    ax.hist(capacities, bins=30, edgecolor='black', alpha=0.7, color='steelblue')
    ax.axvline(capacities.median(), color='red', linestyle='--', linewidth=2,
               label=f'Median: {capacities.median():.0f}')
    ax.axvline(capacities.mean(), color='orange', linestyle='--', linewidth=2,
               label=f'Mean: {capacities.mean():.0f}')

    ax.set_xlabel('Room Capacity', fontsize=12)
    ax.set_ylabel('Number of Rooms', fontsize=12)
    ax.set_title('Distribution of Room Capacities', fontsize=14, fontweight='bold')
    ax.legend()

    # Add summary statistics as text
    stats_text = (f"Total Rooms: {len(capacities)}\n"
                  f"Min: {capacities.min():.0f}\n"
                  f"Max: {capacities.max():.0f}")
    ax.text(0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '01_room_capacity_histogram.png', dpi=150)
    plt.close()
    print("  Saved: 01_room_capacity_histogram.png")


def plot_02_event_vs_room_capacity_scatter(events):
    """Plot 2: Event size vs Room capacity scatter plot."""
    fig, ax = plt.subplots(figsize=(10, 8))

    # Filter valid data
    valid = events.dropna(subset=['Event Size', 'Room_Capacity'])

    scatter = ax.scatter(valid['Event Size'], valid['Room_Capacity'],
                        alpha=0.3, s=20, c='steelblue')

    # Add diagonal line (perfect match)
    max_val = max(valid['Event Size'].max(), valid['Room_Capacity'].max())
    ax.plot([0, max_val], [0, max_val], 'r--', linewidth=2, label='Perfect Match')

    # Add threshold lines
    ax.plot([0, max_val], [0, max_val * 1.5], 'g--', alpha=0.5, label='1.5x Room Size')
    ax.plot([0, max_val], [0, max_val * 2], 'orange', linestyle='--', alpha=0.5, label='2x Room Size')

    ax.set_xlabel('Event Size (Students)', fontsize=12)
    ax.set_ylabel('Room Capacity', fontsize=12)
    ax.set_title('Event Size vs Room Capacity', fontsize=14, fontweight='bold')
    ax.legend(loc='lower right')

    # Add counts
    undersized = (valid['Room_Capacity'] < valid['Event Size']).sum()
    pct = 100 * undersized / len(valid) if len(valid) > 0 else 0
    ax.text(0.05, 0.95, f"Undersized Rooms: {undersized} events\n({pct:.1f}%)",
            transform=ax.transAxes, fontsize=10, verticalalignment='top',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '02_event_vs_room_capacity_scatter.png', dpi=150)
    plt.close()
    print("  Saved: 02_event_vs_room_capacity_scatter.png")


def plot_03_capacity_utilization_ratio(events):
    """Plot 3: Capacity utilization ratio distribution."""
    fig, ax = plt.subplots(figsize=(10, 6))

    valid = events.dropna(subset=['Capacity_Utilization'])
    utilization = valid['Capacity_Utilization'].clip(upper=2)  # Clip for visualization

    # Histogram
    ax.hist(utilization, bins=40, edgecolor='black', alpha=0.7, color='steelblue')
    ax.axvline(1.0, color='red', linestyle='--', linewidth=2, label='100% Full')
    ax.axvline(0.5, color='orange', linestyle='--', linewidth=2, label='50% Full')
    ax.axvline(utilization.median(), color='green', linestyle='--', linewidth=2,
               label=f'Median: {utilization.median():.2f}')
    ax.set_xlabel('Capacity Utilization Ratio (Event Size / Room Capacity)', fontsize=12)
    ax.set_ylabel('Number of Events', fontsize=12)
    ax.set_title('Capacity Utilization Distribution', fontsize=14, fontweight='bold')
    ax.legend()

    # Add statistics
    overcrowded = (utilization > 1).sum()
    underfilled = (utilization < 0.5).sum()
    stats_text = (f"Overcrowded (>100%): {overcrowded} ({100*overcrowded/len(utilization):.1f}%)\n"
                  f"Underfilled (<50%): {underfilled} ({100*underfilled/len(utilization):.1f}%)")
    ax.text(0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '03_capacity_utilization_ratio.png', dpi=150)
    plt.close()
    print("  Saved: 03_capacity_utilization_ratio.png")


def plot_04_room_type_distribution(data):
    """Plot 4: Room type distribution."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Room type counts
    room_types = data.rooms['Room Type'].value_counts()

    axes[0].barh(room_types.index, room_types.values, color='steelblue', edgecolor='black')
    axes[0].set_xlabel('Number of Rooms', fontsize=12)
    axes[0].set_ylabel('Room Type', fontsize=12)
    axes[0].set_title('Rooms by Type', fontsize=12, fontweight='bold')

    # Capacity by room type
    capacity_by_type = data.rooms.groupby('Room Type')['Capacity'].agg(['mean', 'median', 'sum'])
    capacity_by_type = capacity_by_type.sort_values('sum', ascending=False)

    x = np.arange(len(capacity_by_type))
    width = 0.35

    axes[1].bar(x - width/2, capacity_by_type['mean'], width, label='Mean Capacity',
                color='steelblue', edgecolor='black')
    axes[1].bar(x + width/2, capacity_by_type['median'], width, label='Median Capacity',
                color='coral', edgecolor='black')
    axes[1].set_xlabel('Room Type', fontsize=12)
    axes[1].set_ylabel('Capacity', fontsize=12)
    axes[1].set_title('Capacity Statistics by Room Type', fontsize=12, fontweight='bold')
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(capacity_by_type.index, rotation=45, ha='right')
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '04_room_type_distribution.png', dpi=150)
    plt.close()
    print("  Saved: 04_room_type_distribution.png")


def plot_05_events_by_day_hour_heatmap(events):
    """Plot 5: Heatmap of events by day and hour."""
    fig, ax = plt.subplots(figsize=(12, 8))

    # Create pivot table
    valid = events.dropna(subset=['Day', 'Hour'])
    heatmap_data = valid.groupby(['Day', 'Hour']).size().unstack(fill_value=0)

    # Reorder
    days_present = [d for d in DAY_ORDER if d in heatmap_data.index]
    hours_present = [h for h in HOUR_ORDER if h in heatmap_data.columns]
    heatmap_data = heatmap_data.reindex(index=days_present, columns=hours_present, fill_value=0)

    sns.heatmap(heatmap_data, annot=True, fmt='d', cmap='YlOrRd', ax=ax,
                linewidths=0.5, cbar_kws={'label': 'Number of Events'})

    ax.set_xlabel('Hour', fontsize=12)
    ax.set_ylabel('Day', fontsize=12)
    ax.set_title('Events by Day and Hour', fontsize=14, fontweight='bold')

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '05_events_by_day_hour_heatmap.png', dpi=150)
    plt.close()
    print("  Saved: 05_events_by_day_hour_heatmap.png")


def plot_06_events_per_timeslot_bar(events):
    """Plot 6: Events per timeslot bar chart."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Events by day
    day_counts = events['Day'].value_counts()
    day_counts = day_counts.reindex([d for d in DAY_ORDER if d in day_counts.index])

    axes[0].bar(day_counts.index, day_counts.values, color='steelblue', edgecolor='black')
    axes[0].set_xlabel('Day', fontsize=12)
    axes[0].set_ylabel('Number of Events', fontsize=12)
    axes[0].set_title('Events by Day of Week', fontsize=12, fontweight='bold')
    axes[0].tick_params(axis='x', rotation=45)

    # Events by hour
    hour_counts = events['Hour'].value_counts()
    hour_counts = hour_counts.reindex([h for h in HOUR_ORDER if h in hour_counts.index])

    colors = ['red' if c > hour_counts.median() * 1.5 else 'steelblue' for c in hour_counts.values]
    axes[1].bar(hour_counts.index, hour_counts.values, color=colors, edgecolor='black')
    axes[1].axhline(hour_counts.median(), color='orange', linestyle='--',
                    label=f'Median: {hour_counts.median():.0f}')
    axes[1].set_xlabel('Hour', fontsize=12)
    axes[1].set_ylabel('Number of Events', fontsize=12)
    axes[1].set_title('Events by Hour (Red = Peak Hours)', fontsize=12, fontweight='bold')
    axes[1].tick_params(axis='x', rotation=45)
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '06_events_per_timeslot_bar.png', dpi=150)
    plt.close()
    print("  Saved: 06_events_per_timeslot_bar.png")


def plot_07_room_demand_vs_supply(events, data):
    """Plot 7: Room demand vs supply by timeslot."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Demand by hour
    valid = events.dropna(subset=['Hour'])
    demand_by_hour = valid.groupby('Hour').size()
    demand_by_hour = demand_by_hour.reindex([h for h in HOUR_ORDER if h in demand_by_hour.index])

    total_rooms = len(data.rooms)

    x = np.arange(len(demand_by_hour))
    width = 0.35

    axes[0].bar(x - width/2, demand_by_hour.values, width, label='Demand (Events)',
                color='coral', edgecolor='black')
    axes[0].axhline(total_rooms, color='steelblue', linestyle='--', linewidth=2,
                    label=f'Supply (Rooms): {total_rooms}')
    axes[0].set_xlabel('Hour', fontsize=12)
    axes[0].set_ylabel('Count', fontsize=12)
    axes[0].set_title('Demand vs Room Supply by Hour', fontsize=12, fontweight='bold')
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(demand_by_hour.index, rotation=45, ha='right')
    axes[0].legend()

    # Calculate bottleneck ratio
    bottleneck_ratio = demand_by_hour / total_rooms
    colors = ['red' if r > 1 else 'green' for r in bottleneck_ratio.values]

    axes[1].bar(bottleneck_ratio.index, bottleneck_ratio.values, color=colors, edgecolor='black')
    axes[1].axhline(1.0, color='black', linestyle='--', linewidth=2, label='Supply = Demand')
    axes[1].set_xlabel('Hour', fontsize=12)
    axes[1].set_ylabel('Demand/Supply Ratio', fontsize=12)
    axes[1].set_title('Bottleneck Analysis (Red = Over Capacity)', fontsize=12, fontweight='bold')
    axes[1].tick_params(axis='x', rotation=45)
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '07_room_demand_vs_supply.png', dpi=150)
    plt.close()
    print("  Saved: 07_room_demand_vs_supply.png")


def plot_08_events_per_week(events):
    """Plot 8: Events per week (semester progression)."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Flatten week lists and count
    all_weeks = []
    for weeks in events['Week_List']:
        all_weeks.extend(weeks)

    week_counts = pd.Series(Counter(all_weeks)).sort_index()

    # Filter to reasonable range (weeks 1-52)
    week_counts = week_counts[(week_counts.index >= 1) & (week_counts.index <= 52)]

    axes[0].plot(week_counts.index, week_counts.values, 'b-', linewidth=2, marker='o', markersize=4)
    axes[0].fill_between(week_counts.index, week_counts.values, alpha=0.3)
    axes[0].set_xlabel('Week Number', fontsize=12)
    axes[0].set_ylabel('Number of Event Occurrences', fontsize=12)
    axes[0].set_title('Events Across Academic Year', fontsize=12, fontweight='bold')

    # Add semester divider (typically around week 13)
    axes[0].axvline(13, color='red', linestyle='--', alpha=0.7, label='~Semester Break')
    axes[0].legend()

    # Events by semester
    sem_counts = events['Semester'].value_counts()
    axes[1].bar(sem_counts.index.astype(str), sem_counts.values, color='steelblue', edgecolor='black')
    axes[1].set_xlabel('Semester', fontsize=12)
    axes[1].set_ylabel('Number of Events', fontsize=12)
    axes[1].set_title('Events by Semester', fontsize=12, fontweight='bold')

    # Add percentages
    for i, (idx, val) in enumerate(sem_counts.items()):
        pct = 100 * val / sem_counts.sum()
        axes[1].text(i, val + sem_counts.max()*0.02, f'{pct:.1f}%', ha='center', fontsize=10)

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '08_events_per_week.png', dpi=150)
    plt.close()
    print("  Saved: 08_events_per_week.png")


def plot_09_events_by_department(events):
    """Plot 9: Events by department workload."""
    fig, ax = plt.subplots(figsize=(12, 8))

    dept_counts = events['Module Department'].value_counts().head(20)  # Top 20

    colors = plt.cm.viridis(np.linspace(0, 0.8, len(dept_counts)))
    ax.barh(dept_counts.index[::-1], dept_counts.values[::-1], color=colors[::-1], edgecolor='black')

    ax.set_xlabel('Number of Events', fontsize=12)
    ax.set_ylabel('Department', fontsize=12)
    ax.set_title('Top 20 Departments by Event Count', fontsize=14, fontweight='bold')

    # Add count labels
    for i, v in enumerate(dept_counts.values[::-1]):
        ax.text(v + dept_counts.max()*0.01, i, f'{v:,}', va='center', fontsize=9)

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '09_events_by_department.png', dpi=150)
    plt.close()
    print("  Saved: 09_events_by_department.png")


def plot_10_event_duration_distribution(events):
    """Plot 10: Event duration distribution."""
    fig, ax = plt.subplots(figsize=(10, 6))

    durations = events['Duration (minutes)'].dropna()

    # Histogram
    ax.hist(durations, bins=30, edgecolor='black', alpha=0.7, color='steelblue')
    ax.axvline(durations.median(), color='red', linestyle='--', linewidth=2,
               label=f'Median: {durations.median():.0f} min')
    ax.axvline(60, color='green', linestyle='--', alpha=0.7, label='1 Hour')
    ax.axvline(120, color='orange', linestyle='--', alpha=0.7, label='2 Hours')
    ax.set_xlabel('Duration (minutes)', fontsize=12)
    ax.set_ylabel('Number of Events', fontsize=12)
    ax.set_title('Event Duration Distribution', fontsize=14, fontweight='bold')
    ax.legend()

    # Add statistics
    stats_text = (f"Total Events: {len(durations):,}\n"
                  f"Mean: {durations.mean():.0f} min\n"
                  f"Most Common: {durations.mode().iloc[0]:.0f} min")
    ax.text(0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '10_event_duration_distribution.png', dpi=150)
    plt.close()
    print("  Saved: 10_event_duration_distribution.png")


def plot_11_students_per_event(events, data):
    """Plot 11: Students per event distribution."""
    fig, ax = plt.subplots(figsize=(10, 6))

    # Event size distribution
    event_sizes = events['Event Size'].dropna()

    ax.hist(event_sizes, bins=50, edgecolor='black', alpha=0.7, color='steelblue')
    ax.axvline(event_sizes.median(), color='red', linestyle='--', linewidth=2,
               label=f'Median: {event_sizes.median():.0f}')
    ax.axvline(event_sizes.mean(), color='orange', linestyle='--', linewidth=2,
               label=f'Mean: {event_sizes.mean():.0f}')
    ax.set_xlabel('Event Size (Students)', fontsize=12)
    ax.set_ylabel('Number of Events', fontsize=12)
    ax.set_title('Event Size Distribution', fontsize=14, fontweight='bold')
    ax.legend()

    # Add statistics
    stats_text = (f"Total Events: {len(event_sizes):,}\n"
                  f"Min: {event_sizes.min():.0f}\n"
                  f"Max: {event_sizes.max():.0f}")
    ax.text(0.95, 0.95, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', horizontalalignment='right',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '11_students_per_event.png', dpi=150)
    plt.close()
    print("  Saved: 11_students_per_event.png")


def plot_12_events_per_student(data):
    """Plot 12: Events per student distribution."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Events per student
    events_per_student = data.enrollments.groupby('AnonID')['Event ID'].nunique()

    axes[0].hist(events_per_student, bins=50, edgecolor='black', alpha=0.7, color='steelblue')
    axes[0].axvline(events_per_student.median(), color='red', linestyle='--', linewidth=2,
                    label=f'Median: {events_per_student.median():.0f}')
    axes[0].set_xlabel('Number of Events per Student', fontsize=12)
    axes[0].set_ylabel('Number of Students', fontsize=12)
    axes[0].set_title('Events per Student Distribution', fontsize=12, fontweight='bold')
    axes[0].legend()

    # Courses per student
    courses_per_student = data.enrollments.groupby('AnonID')['Course ID'].nunique()

    axes[1].hist(courses_per_student, bins=30, edgecolor='black', alpha=0.7, color='coral')
    axes[1].axvline(courses_per_student.median(), color='blue', linestyle='--', linewidth=2,
                    label=f'Median: {courses_per_student.median():.0f}')
    axes[1].set_xlabel('Number of Courses per Student', fontsize=12)
    axes[1].set_ylabel('Number of Students', fontsize=12)
    axes[1].set_title('Courses per Student Distribution', fontsize=12, fontweight='bold')
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '12_events_per_student.png', dpi=150)
    plt.close()
    print("  Saved: 12_events_per_student.png")


def plot_13_scheduling_issues_summary(events, data):
    """Plot 13: Scheduling issues and conflicts summary."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Issue counts
    issues = {
        'No Room Assigned': events['Room'].isna().sum(),
        'Online Delivery': events['Online Delivery'].notna().sum(),
        'No Timeslot': events['Timeslot'].isna().sum(),
        'No Capacity Info': events['Room_Capacity'].isna().sum(),
        'Potential Overcrowding': (events['Capacity_Utilization'] > 1).sum() if 'Capacity_Utilization' in events else 0,
    }

    # Check for room double-bookings
    room_time = events.dropna(subset=['Room', 'Timeslot']).groupby(['Room', 'Timeslot', 'Semester']).size()
    potential_conflicts = (room_time > 1).sum()
    issues['Potential Room Conflicts'] = potential_conflicts

    colors = ['red' if v > 0 else 'green' for v in issues.values()]
    axes[0].barh(list(issues.keys()), list(issues.values()), color=colors, edgecolor='black')
    axes[0].set_xlabel('Count', fontsize=12)
    axes[0].set_title('Scheduling Issues Summary', fontsize=12, fontweight='bold')

    # Add count labels
    for i, v in enumerate(issues.values()):
        axes[0].text(v + max(issues.values())*0.01, i, f'{v:,}', va='center', fontsize=9)

    # Events with/without rooms by semester
    room_status = events.groupby('Semester').agg({
        'Room': lambda x: x.notna().sum(),
        'Event ID': 'count'
    }).rename(columns={'Room': 'With Room', 'Event ID': 'Total'})
    room_status['No Room'] = room_status['Total'] - room_status['With Room']

    x = np.arange(len(room_status))
    width = 0.35

    axes[1].bar(x - width/2, room_status['With Room'], width, label='With Room',
                color='green', edgecolor='black')
    axes[1].bar(x + width/2, room_status['No Room'], width, label='No Room',
                color='red', edgecolor='black')
    axes[1].set_xlabel('Semester', fontsize=12)
    axes[1].set_ylabel('Number of Events', fontsize=12)
    axes[1].set_title('Room Assignment by Semester', fontsize=12, fontweight='bold')
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(room_status.index.astype(str))
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '13_scheduling_issues_summary.png', dpi=150)
    plt.close()
    print("  Saved: 13_scheduling_issues_summary.png")


def plot_14_campus_building_distribution(events, data):
    """Plot 14: Campus and building distribution."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Campus distribution for rooms
    campus_rooms = data.rooms['Campus'].value_counts()

    axes[0].bar(campus_rooms.index, campus_rooms.values, color='steelblue', edgecolor='black')
    axes[0].set_xlabel('Campus', fontsize=12)
    axes[0].set_ylabel('Number of Rooms', fontsize=12)
    axes[0].set_title('Rooms by Campus', fontsize=12, fontweight='bold')
    axes[0].tick_params(axis='x', rotation=45)

    # Top buildings by event count
    building_counts = events['Building'].value_counts().head(15)

    axes[1].barh(building_counts.index[::-1], building_counts.values[::-1],
                 color='coral', edgecolor='black')
    axes[1].set_xlabel('Number of Events', fontsize=12)
    axes[1].set_ylabel('Building', fontsize=12)
    axes[1].set_title('Top 15 Buildings by Event Count', fontsize=12, fontweight='bold')

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / '14_campus_building_distribution.png', dpi=150)
    plt.close()
    print("  Saved: 14_campus_building_distribution.png")


def create_summary_dashboard(events, data):
    """Create summary dashboard combining key insights."""
    fig = plt.figure(figsize=(20, 16))

    # Create grid
    gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

    # 1. Room capacity histogram
    ax1 = fig.add_subplot(gs[0, 0])
    capacities = data.rooms['Capacity'].dropna()
    ax1.hist(capacities, bins=25, edgecolor='black', alpha=0.7, color='steelblue')
    ax1.axvline(capacities.median(), color='red', linestyle='--', label=f'Median: {capacities.median():.0f}')
    ax1.set_xlabel('Capacity')
    ax1.set_ylabel('Rooms')
    ax1.set_title('Room Capacity Distribution', fontweight='bold')
    ax1.legend(fontsize=8)

    # 2. Events by day/hour heatmap
    ax2 = fig.add_subplot(gs[0, 1])
    valid = events.dropna(subset=['Day', 'Hour'])
    heatmap_data = valid.groupby(['Day', 'Hour']).size().unstack(fill_value=0)
    days_present = [d for d in DAY_ORDER if d in heatmap_data.index]
    hours_present = [h for h in HOUR_ORDER if h in heatmap_data.columns]
    heatmap_data = heatmap_data.reindex(index=days_present, columns=hours_present, fill_value=0)
    sns.heatmap(heatmap_data, cmap='YlOrRd', ax=ax2, cbar_kws={'shrink': 0.5})
    ax2.set_title('Weekly Schedule Heatmap', fontweight='bold')

    # 3. Event Size vs Room Capacity
    ax3 = fig.add_subplot(gs[0, 2])
    valid_scatter = events.dropna(subset=['Event Size', 'Room_Capacity'])
    if len(valid_scatter) > 0:
        ax3.scatter(valid_scatter['Event Size'], valid_scatter['Room_Capacity'],
                   alpha=0.3, s=10, c='steelblue')
        max_val = max(valid_scatter['Event Size'].max(), valid_scatter['Room_Capacity'].max())
        ax3.plot([0, max_val], [0, max_val], 'r--', linewidth=1, label='Perfect Match')
        ax3.set_xlabel('Event Size')
        ax3.set_ylabel('Room Capacity')
        ax3.legend(fontsize=8)
    ax3.set_title('Event Size vs Room Capacity', fontweight='bold')

    # 4. Capacity utilization
    ax4 = fig.add_subplot(gs[1, 0])
    valid = events.dropna(subset=['Capacity_Utilization'])
    utilization = valid['Capacity_Utilization'].clip(upper=2)
    ax4.hist(utilization, bins=30, edgecolor='black', alpha=0.7, color='coral')
    ax4.axvline(1.0, color='red', linestyle='--', label='100% Full')
    ax4.set_xlabel('Utilization Ratio')
    ax4.set_ylabel('Events')
    ax4.set_title('Capacity Utilization', fontweight='bold')
    ax4.legend(fontsize=8)

    # 5. Events per hour (bar)
    ax5 = fig.add_subplot(gs[1, 1])
    hour_counts = events['Hour'].value_counts()
    hour_counts = hour_counts.reindex([h for h in HOUR_ORDER if h in hour_counts.index])
    colors = ['red' if c > hour_counts.median() * 1.3 else 'steelblue' for c in hour_counts.values]
    ax5.bar(hour_counts.index, hour_counts.values, color=colors, edgecolor='black')
    ax5.set_xlabel('Hour')
    ax5.set_ylabel('Events')
    ax5.set_title('Events by Hour (Red=Peak)', fontweight='bold')
    ax5.tick_params(axis='x', rotation=45)

    # 6. Scheduling issues
    ax6 = fig.add_subplot(gs[1, 2])
    issues = {
        'No Room': events['Room'].isna().sum(),
        'Online': events['Online Delivery'].notna().sum(),
        'Overcrowded': (events['Capacity_Utilization'] > 1).sum() if 'Capacity_Utilization' in events else 0,
    }
    colors = ['red' if v > 0 else 'green' for v in issues.values()]
    ax6.barh(list(issues.keys()), list(issues.values()), color=colors, edgecolor='black')
    ax6.set_xlabel('Count')
    ax6.set_title('Scheduling Issues', fontweight='bold')

    # 7. Event size distribution
    ax7 = fig.add_subplot(gs[2, 0])
    event_sizes = events['Event Size'].dropna()
    ax7.hist(event_sizes, bins=40, edgecolor='black', alpha=0.7, color='steelblue')
    ax7.axvline(event_sizes.median(), color='red', linestyle='--', label=f'Median: {event_sizes.median():.0f}')
    ax7.set_xlabel('Students')
    ax7.set_ylabel('Events')
    ax7.set_title('Event Size Distribution', fontweight='bold')
    ax7.legend(fontsize=8)

    # 8. Top departments
    ax8 = fig.add_subplot(gs[2, 1])
    dept_counts = events['Module Department'].value_counts().head(10)
    ax8.barh(dept_counts.index[::-1], dept_counts.values[::-1], color='teal', edgecolor='black')
    ax8.set_xlabel('Events')
    ax8.set_title('Top 10 Departments', fontweight='bold')

    # 9. Key statistics text
    ax9 = fig.add_subplot(gs[2, 2])
    ax9.axis('off')

    stats_text = f"""
    KEY STATISTICS
    ══════════════════════

    Events: {len(events):,}
    Unique Modules: {events['Module Code'].nunique():,}
    Rooms: {len(data.rooms):,}
    Students: {data.enrollments['AnonID'].nunique():,}

    Avg Event Size: {events['Event Size'].mean():.1f}
    Avg Room Capacity: {data.rooms['Capacity'].mean():.1f}

    Events without rooms: {events['Room'].isna().sum():,}
    Online events: {events['Online Delivery'].notna().sum():,}

    Peak Hour: {events['Hour'].mode().iloc[0] if len(events['Hour'].mode()) > 0 else 'N/A'}
    Peak Day: {events['Day'].mode().iloc[0] if len(events['Day'].mode()) > 0 else 'N/A'}
    """

    ax9.text(0.1, 0.9, stats_text, transform=ax9.transAxes, fontsize=11,
            verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    fig.suptitle('University Timetabling EDA Summary Dashboard', fontsize=16, fontweight='bold', y=0.98)

    plt.savefig(PLOTS_DIR / 'summary_dashboard.png', dpi=150, bbox_inches='tight')
    plt.close()
    print("  Saved: summary_dashboard.png")


def main():
    """Generate all EDA visualizations."""
    print("=" * 60)
    print("EDA VISUALIZATIONS FOR UNIVERSITY TIMETABLING")
    print("=" * 60)

    # Load data
    print("\nLoading data...")
    data = TimetablingData()
    data.load_all()

    # Preprocess
    print("\nPreprocessing events data...")
    events = preprocess_data(data)

    # Generate all plots
    print("\nGenerating visualizations...\n")

    print("Room & Capacity Analysis:")
    plot_01_room_capacity_histogram(data)
    plot_02_event_vs_room_capacity_scatter(events)
    plot_03_capacity_utilization_ratio(events)
    plot_04_room_type_distribution(data)

    print("\nTimeslot & Demand Analysis:")
    plot_05_events_by_day_hour_heatmap(events)
    plot_06_events_per_timeslot_bar(events)
    plot_07_room_demand_vs_supply(events, data)
    plot_08_events_per_week(events)

    print("\nEvent Distribution:")
    plot_09_events_by_department(events)
    plot_10_event_duration_distribution(events)

    print("\nStudent & Enrollment Analysis:")
    plot_11_students_per_event(events, data)
    plot_12_events_per_student(data)

    print("\nConflicts & Issues:")
    plot_13_scheduling_issues_summary(events, data)
    plot_14_campus_building_distribution(events, data)

    print("\nSummary Dashboard:")
    create_summary_dashboard(events, data)

    print("\n" + "=" * 60)
    print("COMPLETE! All visualizations saved to plots/ directory.")
    print("=" * 60)

    # List generated files
    print(f"\nGenerated {len(list(PLOTS_DIR.glob('*.png')))} PNG files:")
    for f in sorted(PLOTS_DIR.glob('*.png')):
        print(f"  - {f.name}")


if __name__ == "__main__":
    main()
