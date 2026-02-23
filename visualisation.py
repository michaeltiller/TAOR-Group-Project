"""
EDA Visualizations for Vet School Timetable
============================================
Accepts the 2-week timetable DataFrame produced by build_timetable.py and
generates 5 targeted analyses relevant to scheduling optimisation.

Expected columns:
    Event ID, Event Name, Event Type, Module Code, Module Name,
    Timeslot, Duration (minutes), Weeks, Event Size, Semester,
    Room, Building, Campus, Room Capacity, Room Type (detail), Core
"""

import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from utils import (
    PLOTS_DIR,
    DAY_ORDER,
    HOUR_ORDER,
    parse_timeslot,
    get_fill_ratio_color,
    calculate_fill_ratio,
)

# Configuration
PLOTS_DIR.mkdir(exist_ok=True)

# Style settings
plt.style.use("seaborn-v0_8-whitegrid")
sns.set_palette("husl")


def preprocess_timetable(df):
    """Add Day, Hour, and Fill_Ratio columns to the timetable DataFrame."""
    df = df.copy()
    df["Day"], df["Hour"] = zip(*df["Timeslot"].apply(parse_timeslot))
    df["Fill_Ratio"] = df.apply(
        lambda row: calculate_fill_ratio(row["Event Size"], row["Room Capacity"]),
        axis=1,
    )
    return df


def plot_room_utilization(df):
    """Plot 1: Total scheduled hours per room (08:00–17:00 window)."""
    # Filter to daytime hours only (08:00–17:00 start)
    daytime_hours = [h for h in HOUR_ORDER if h <= "17:00"]
    day_df = df[df["Hour"].isin(daytime_hours)].copy()

    if day_df.empty:
        print("  Skipped: 01_room_utilization.png (no daytime events)")
        return

    # Compute total scheduled hours per room (sum of durations in minutes → hours)
    room_hours = (
        day_df.groupby("Room")["Duration (minutes)"]
        .sum()
        .div(60)
        .sort_values(ascending=False)
    )

    # Max possible hours: 10 hrs/day × 10 weekdays in 2 weeks = 100 hrs
    # Or compute dynamically from distinct (day, room) combos present
    distinct_days = day_df[["Room", "Day"]].drop_duplicates()
    max_hrs_per_room = distinct_days.groupby("Room").size() * 10  # 10 hrs per day
    global_max = 100  # fallback cap for annotation

    fig, ax = plt.subplots(figsize=(10, max(6, len(room_hours) * 0.35)))

    colors = plt.cm.viridis(np.linspace(0.2, 0.8, len(room_hours)))
    bars = ax.barh(
        room_hours.index[::-1],
        room_hours.values[::-1],
        color=colors[::-1],
        edgecolor="black",
        linewidth=0.5,
    )

    # Annotate with utilisation %
    for i, (room, hrs) in enumerate(room_hours.items()):
        cap = max_hrs_per_room.get(room, global_max)
        pct = 100 * hrs / cap if cap > 0 else 0
        ax.text(
            hrs + room_hours.max() * 0.01,
            len(room_hours) - 1 - i,
            f"{hrs:.1f}h ({pct:.0f}%)",
            va="center",
            fontsize=8,
        )

    ax.set_xlabel("Total Scheduled Hours", fontsize=12)
    ax.set_ylabel("Room", fontsize=12)
    ax.set_title(
        "Room Utilization — Total Scheduled Hours (08:00–17:00)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlim(0, room_hours.max() * 1.2)

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "01_room_utilization.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: 01_room_utilization.png")


def plot_fill_ratio(df):
    """Plot 2: Two-panel fill ratio analysis (histogram + category breakdown)."""
    valid = df.dropna(subset=["Fill_Ratio"])

    if valid.empty:
        print("  Skipped: 02_fill_ratio.png (no valid fill ratio data)")
        return

    fill = valid["Fill_Ratio"]

    # Categorise
    overfilled = (fill > 1.0).sum()
    well_used = ((fill >= 0.5) & (fill <= 1.0)).sum()
    underfilled = (fill < 0.5).sum()
    total = len(fill)

    fig, (ax_hist, ax_pie) = plt.subplots(1, 2, figsize=(14, 6))

    # --- Left: histogram ---
    ax_hist.hist(
        fill.clip(upper=2.0), bins=40, edgecolor="black", alpha=0.7, color="steelblue"
    )
    ax_hist.axvline(1.0, color="red", linestyle="--", linewidth=2, label="100% Full")
    ax_hist.axvline(0.5, color="orange", linestyle="--", linewidth=2, label="50% Full")
    ax_hist.axvline(
        fill.mean(),
        color="green",
        linestyle="--",
        linewidth=1.5,
        label=f"Mean: {fill.mean():.2f}",
    )
    ax_hist.set_xlabel("Fill Ratio (Event Size / Room Capacity)", fontsize=12)
    ax_hist.set_ylabel("Number of Events", fontsize=12)
    ax_hist.set_title("Fill Ratio Distribution", fontsize=13, fontweight="bold")
    ax_hist.legend()

    stats_text = (
        f"Overfilled (>100%): {overfilled} ({100 * overfilled / total:.1f}%)\n"
        f"Well-used (50–100%): {well_used} ({100 * well_used / total:.1f}%)\n"
        f"Underfilled (<50%): {underfilled} ({100 * underfilled / total:.1f}%)"
    )
    ax_hist.text(
        0.97,
        0.97,
        stats_text,
        transform=ax_hist.transAxes,
        fontsize=9,
        va="top",
        ha="right",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.6),
    )

    # --- Right: pie ---
    labels = [
        f"Overfilled\n(>100%)\n{overfilled}",
        f"Well-used\n(50–100%)\n{well_used}",
        f"Underfilled\n(<50%)\n{underfilled}",
    ]
    sizes = [overfilled, well_used, underfilled]
    colors_pie = ["#e74c3c", "#2ecc71", "#3498db"]
    wedges, texts, autotexts = ax_pie.pie(
        sizes,
        labels=labels,
        colors=colors_pie,
        autopct="%1.1f%%",
        startangle=90,
        wedgeprops=dict(edgecolor="white", linewidth=1.5),
    )
    for at in autotexts:
        at.set_fontsize(10)
    ax_pie.set_title("Fill Ratio Categories", fontsize=13, fontweight="bold")

    plt.suptitle("Room Fill Ratio Analysis", fontsize=15, fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "02_fill_ratio.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: 02_fill_ratio.png")


def plot_event_distribution(df):
    """Plot 3: Heatmap of simultaneous events by day and hour (08:00–18:00)."""
    daytime_hours = [h for h in HOUR_ORDER if h <= "18:00"]
    valid = df.dropna(subset=["Day", "Hour"])
    valid = valid[valid["Hour"].isin(daytime_hours)]

    if valid.empty:
        print("  Skipped: 03_event_distribution.png (no daytime events)")
        return

    heatmap_data = valid.groupby(["Day", "Hour"]).size().unstack(fill_value=0)

    # Reorder days and hours
    days_present = [d for d in DAY_ORDER if d in heatmap_data.index]
    hours_present = [h for h in HOUR_ORDER if h in heatmap_data.columns]
    heatmap_data = heatmap_data.reindex(
        index=days_present, columns=hours_present, fill_value=0
    )

    fig, ax = plt.subplots(figsize=(13, 5))

    sns.heatmap(
        heatmap_data,
        annot=True,
        fmt="d",
        cmap="YlOrRd",
        ax=ax,
        linewidths=0.5,
        cbar_kws={"label": "Number of Events"},
    )

    ax.set_xlabel("Hour", fontsize=12)
    ax.set_ylabel("Day", fontsize=12)
    ax.set_title(
        "Event Distribution by Day & Hour (08:00–18:00)", fontsize=13, fontweight="bold"
    )

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "03_event_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: 03_event_distribution.png")


def plot_busiest_rooms(df):
    """Plot 4: Top 15 rooms by event count, with mean fill ratio colour coding."""
    valid = df.dropna(subset=["Room"])

    if valid.empty:
        print("  Skipped: 04_busiest_rooms.png (no room data)")
        return

    event_counts = valid.groupby("Room").size().sort_values(ascending=False).head(15)
    mean_fill = valid.groupby("Room")["Fill_Ratio"].mean().reindex(event_counts.index)

    fig, ax = plt.subplots(figsize=(10, 6))

    # Colour bars by mean fill ratio (red = over capacity, green = well-used, blue = under)
    bar_colors = [get_fill_ratio_color(fr) for fr in mean_fill.values]

    bars = ax.barh(
        event_counts.index[::-1],
        event_counts.values[::-1],
        color=bar_colors[::-1],
        edgecolor="black",
        linewidth=0.5,
    )

    # Annotate with mean fill ratio
    for i, (room, count) in enumerate(event_counts.items()):
        fr = mean_fill.get(room)
        label = f"{count}  (fill: {fr:.0%})" if not pd.isna(fr) else str(count)
        ax.text(
            count + event_counts.max() * 0.01,
            len(event_counts) - 1 - i,
            label,
            va="center",
            fontsize=9,
        )

    ax.set_xlabel("Number of Events", fontsize=12)
    ax.set_ylabel("Room", fontsize=12)
    ax.set_title("Top 15 Busiest Rooms", fontsize=13, fontweight="bold")
    ax.set_xlim(0, event_counts.max() * 1.25)

    # Legend for colour coding
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(facecolor="#e74c3c", label="Overfilled (>100%)"),
        Patch(facecolor="#2ecc71", label="Well-used (50–100%)"),
        Patch(facecolor="#3498db", label="Underfilled (<50%)"),
        Patch(facecolor="#999999", label="No capacity data"),
    ]
    ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "04_busiest_rooms.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: 04_busiest_rooms.png")


def plot_busiest_modules(df):
    """Plot 5: Top 15 modules by event count, coloured by Core flag."""
    if "Module Code" not in df.columns:
        print("  Skipped: 05_busiest_modules.png (no Module Code column)")
        return

    # Build a display label combining code + name (truncated)
    df = df.copy()
    if "Module Name" in df.columns:
        df["_label"] = df["Module Code"] + " — " + df["Module Name"].fillna("").str[:35]
    else:
        df["_label"] = df["Module Code"]

    agg = (
        df.groupby("_label")
        .agg(
            count=("Event ID", "count"),
            core=("Core", "first")
            if "Core" in df.columns
            else ("_label", lambda x: False),
        )
        .sort_values("count", ascending=False)
        .head(15)
    )

    fig, ax = plt.subplots(figsize=(12, 6))

    has_core = "Core" in df.columns
    if has_core:
        bar_colors = ["#e74c3c" if c else "#3498db" for c in agg["core"].values]
    else:
        bar_colors = "#3498db"

    ax.barh(
        agg.index[::-1],
        agg["count"].values[::-1],
        color=bar_colors[::-1] if has_core else bar_colors,
        edgecolor="black",
        linewidth=0.5,
    )

    # Count labels
    for i, (label, row) in enumerate(agg.iterrows()):
        ax.text(
            row["count"] + agg["count"].max() * 0.01,
            len(agg) - 1 - i,
            str(row["count"]),
            va="center",
            fontsize=9,
        )

    ax.set_xlabel("Number of Events", fontsize=12)
    ax.set_ylabel("Module", fontsize=12)
    ax.set_title("Top 15 Busiest Modules", fontsize=13, fontweight="bold")
    ax.set_xlim(0, agg["count"].max() * 1.15)

    if has_core:
        from matplotlib.patches import Patch

        legend_elements = [
            Patch(facecolor="#e74c3c", label="Core (compulsory)"),
            Patch(facecolor="#3498db", label="Elective"),
        ]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / "05_busiest_modules.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: 05_busiest_modules.png")


def main(df):
    """Generate all 5 timetable EDA plots. Accepts the timetable DataFrame directly."""
    print("=" * 60)
    print("EDA VISUALIZATIONS — VET SCHOOL TIMETABLE")
    print("=" * 60)

    print("\nPreprocessing timetable data...")
    df = preprocess_timetable(df)

    print(f"  Rows: {len(df)}")
    print(f"  Rooms: {df['Room'].nunique()}")
    print(f"  Modules: {df['Module Code'].nunique()}")

    print("\nGenerating visualizations...\n")
    plot_room_utilization(df)
    plot_fill_ratio(df)
    plot_event_distribution(df)
    plot_busiest_rooms(df)
    plot_busiest_modules(df)

    print("\n" + "=" * 60)
    print("COMPLETE! Plots saved to plots/ directory.")
    print("=" * 60)
    generated = sorted(PLOTS_DIR.glob("0[1-5]_*.png"))
    for f in generated:
        print(f"  - {f.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run timetable EDA from a saved Excel file"
    )
    parser.add_argument(
        "--timetable-path",
        type=str,
        required=True,
        help="Path to the timetable Excel file produced by build_timetable.py",
    )
    args = parser.parse_args()
    timetable_df = pd.read_excel(args.timetable_path)
    main(timetable_df)
