"""
Heatmap Comparison: Event Distribution Across Three Timetables (Weeks 9-10)
============================================================================
Compares vet school event density (day × timeslot) for:
  - Original_Data.xlsx         (original university timetable)
  - MILP_only.xlsx             (MILP solver output)
  - proposedTimetable/improved_weeks_9_10.xlsx  (MILP + Simulated Annealing)

All three panels share the same colour scale for direct visual comparison.
Output: plots/heatmap_comparison_weeks_9_10.png
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from utils import DAY_ORDER, parse_timeslot, parse_weeks

WORKDAYS = [d for d in DAY_ORDER if d in {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'}]
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
# Pivot builder
# ---------------------------------------------------------------------------

def compute_hour_order(dfs: list) -> list:
    hours = set()
    for df in dfs:
        for ts in df['Timeslot'].dropna():
            _, hour = parse_timeslot(ts)
            if hour:
                hours.add(hour)
    return sorted(hours)


def build_pivot(df: pd.DataFrame, hour_order: list) -> pd.DataFrame:
    parsed = df['Timeslot'].apply(parse_timeslot)
    df = df.copy()
    df['Day'] = parsed.apply(lambda x: x[0])
    df['Hour'] = parsed.apply(lambda x: x[1])
    df = df.dropna(subset=['Day', 'Hour'])
    df = df[df['Day'].isin(WORKDAYS)]

    pivot = (
        df.groupby(['Hour', 'Day'])
        .size()
        .unstack(fill_value=0)
        .reindex(index=hour_order, columns=WORKDAYS, fill_value=0)
    )
    return pivot


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_heatmaps(pivots: list, titles: list, output_path: Path) -> None:
    global_max = max(int(p.values.max()) for p in pivots)

    fig, axes = plt.subplots(1, 3, figsize=(18, 8))
    fig.suptitle(
        "Vet School Event Distribution — Weeks 9–10",
        fontsize=15, fontweight='bold', y=1.02
    )

    for i, (ax, pivot, title) in enumerate(zip(axes, pivots, titles)):
        is_last = (i == len(pivots) - 1)
        sns.heatmap(
            pivot,
            ax=ax,
            vmin=0,
            vmax=global_max,
            cmap='YlOrRd',
            annot=True,
            fmt='d',
            linewidths=0.4,
            linecolor='lightgrey',
            cbar=is_last,
            cbar_kws={'label': 'Number of Events', 'shrink': 0.8} if is_last else None,
        )
        ax.set_title(title, fontsize=12, fontweight='bold', pad=8)
        ax.set_xlabel('Day', fontsize=10)
        ax.tick_params(axis='x', rotation=30)
        ax.tick_params(axis='y', rotation=0)

        if i == 0:
            ax.set_ylabel('Timeslot', fontsize=10)
        else:
            ax.set_ylabel('')
            ax.set_yticklabels([])

    plt.tight_layout()
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

    hour_order = compute_hour_order([df_orig, df_sa])

    pivots = [
        build_pivot(df_orig, hour_order),
        build_pivot(df_sa,   hour_order),
    ]

    labels = ["Original", "SA"]
    for label, df, pivot in zip(labels, [df_orig, df_sa], pivots):
        print(f"{label:8s}: {len(df):4d} events  |  pivot total = {int(pivot.values.sum())}")

    titles = ["Original Timetable", "MILP + Simulated Annealing"]
    plot_heatmaps(pivots, titles, PLOTS_DIR / "heatmap_comparison_weeks_9_10.png")


if __name__ == "__main__":
    main()
