"""
Heatmap Comparison: Event Distribution Across Two Timetables (Weeks 9-10)
==========================================================================
Compares event density (day × hour) for:
  - Original_Data.xlsx                          (original university timetable)
  - proposedTimetable/improved_weeks_9_10.xlsx  (MILP + Simulated Annealing)

Both panels share the same colour scale for direct visual comparison.
Output: plots/heatmap_comparison_weeks_9_10.png
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from utils import DAY_ORDER, HOUR_ORDER, parse_timeslot, parse_weeks

WORKDAYS = [d for d in DAY_ORDER if d in {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}]
DAYTIME_HOURS = [h for h in HOUR_ORDER if h <= '17:00']
TARGET_WEEKS = {9, 10}
PLOTS_DIR = Path("plots")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _in_target_weeks(df: pd.DataFrame) -> pd.Series:
    return df['Weeks'].apply(lambda v: bool(parse_weeks(v) & TARGET_WEEKS))


def load_original(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name='Sheet1')
    return df[_in_target_weeks(df)].copy()


def load_sa(path: Path) -> pd.DataFrame:
    # File is already scoped to weeks 9-10 and has no Weeks column
    return pd.read_excel(path, sheet_name='Sheet1').copy()


# ---------------------------------------------------------------------------
# Pivot builder  (rows=Days, columns=Hours — matches 03_event_distribution style)
# ---------------------------------------------------------------------------

def build_pivot(df: pd.DataFrame) -> pd.DataFrame:
    parsed = df['Timeslot'].apply(parse_timeslot)
    df = df.copy()
    df['Day'] = parsed.apply(lambda x: x[0])
    df['Hour'] = parsed.apply(lambda x: x[1])
    df = df.dropna(subset=['Day', 'Hour'])
    df = df[df['Day'].isin(WORKDAYS) & df['Hour'].isin(DAYTIME_HOURS)]

    pivot = (
        df.groupby(['Day', 'Hour'])
        .size()
        .unstack(fill_value=0)
        .reindex(index=WORKDAYS, columns=DAYTIME_HOURS, fill_value=0)
    )
    # Drop columns (hours) that are empty across both timetables — keep layout clean
    return pivot


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_heatmaps(pivots: list, titles: list, output_path: Path) -> None:
    # Drop hours with no events in either timetable
    active_hours = [h for h in DAYTIME_HOURS if any(p[h].sum() > 0 for p in pivots if h in p.columns)]
    pivots = [p.reindex(columns=active_hours, fill_value=0) for p in pivots]

    global_max = max(int(p.values.max()) for p in pivots)

    fig, axes = plt.subplots(len(pivots), 1, figsize=(13, 5 * len(pivots)))

    for i, (ax, pivot, title) in enumerate(zip(axes, pivots, titles)):
        sns.heatmap(
            pivot,
            ax=ax,
            vmin=0,
            vmax=global_max,
            cmap='YlOrRd',
            annot=True,
            fmt='d',
            linewidths=0.5,
            cbar=False,
        )
        ax.set_title(title, fontsize=13, fontweight='bold')
        ax.set_xlabel('Hour' if i == len(pivots) - 1 else '', fontsize=12)
        ax.set_ylabel('Day', fontsize=12)
        ax.tick_params(axis='x', rotation=0)
        ax.tick_params(axis='y', rotation=0)

    # Single shared colorbar on the right, sized to span all subplots
    import matplotlib as mpl
    norm = mpl.colors.Normalize(vmin=0, vmax=global_max)
    sm = mpl.cm.ScalarMappable(cmap='YlOrRd', norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=axes.tolist(), label='Number of Events', shrink=0.6)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    df_orig = load_original(Path("Original_Data.xlsx"))
    df_sa   = load_sa(Path("proposedTimetable/improved_weeks_9_10.xlsx"))

    pivots = [
        build_pivot(df_orig),
        build_pivot(df_sa),
    ]

    labels = ["Original", "SA"]
    for label, df, pivot in zip(labels, [df_orig, df_sa], pivots):
        print(f"{label:8s}: {len(df):4d} events  |  pivot total = {int(pivot.values.sum())}")

    titles = ["Original Timetable", "MILP + Simulated Annealing"]
    plot_heatmaps(pivots, titles, PLOTS_DIR / "heatmap_comparison_weeks_9_10.png")


if __name__ == "__main__":
    main()
