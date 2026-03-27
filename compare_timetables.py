"""
Compare Two Timetable Files
============================
Accepts two arbitrary timetable Excel files, auto-enriches each if needed
(joins Building, Campus, Room Type, Core, Duration, Semester, Weeks from vet
data sources), then generates the full EDA plot suite for each.

Usage:
    python compare_timetables.py <path1> <path2>
    python compare_timetables.py <path1> <path2> --fallback-weeks 9
    python compare_timetables.py <path1> <path2> --output-dir myplots/

Output:
    <output-dir>/<stem1>/  — 5 PNGs for the first timetable
    <output-dir>/<stem2>/  — 5 PNGs for the second timetable

If both files share the same filename stem, suffixes _1 and _2 are appended
to avoid overwriting.
"""

import argparse
import pandas as pd
from pathlib import Path

from utils import VET_DATA_DIR
from eda_visualizations import main as run_eda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_timetable(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Timetable file not found: {path}")
    return pd.read_excel(path)


def needs_enrichment(df: pd.DataFrame) -> bool:
    """Return True if the DataFrame is missing enrichment columns.

    The original timetable (from build_timetable.py) always has both
    'Building' and 'Core'. MILP/improved outputs lack both.
    Checking the conjunction avoids a false-positive if either join
    happened to fail independently.
    """
    return "Building" not in df.columns or "Core" not in df.columns


def enrich_if_needed(df: pd.DataFrame, fallback_weeks=None) -> pd.DataFrame:
    """Add the columns that proposed/improved timetables lack by joining vet data sources.

    Skips enrichment entirely if the DataFrame already has 'Building' and 'Core'.
    """
    if not needs_enrichment(df):
        return df

    df = df.copy()

    # Drop Source column if present (not needed for EDA)
    df = df.drop(columns=["Source"], errors="ignore")

    # --- Join Duration (minutes), Semester, Weeks from vet events ---
    events_path = VET_DATA_DIR / "2024-5 Event Module Room.xlsx"
    events = pd.read_excel(events_path)

    event_cols = ["Event ID"]
    for col in ["Duration (minutes)", "Semester", "Weeks"]:
        if col in events.columns:
            event_cols.append(col)

    # Drop columns that will be re-joined to avoid _x/_y collision
    cols_to_rejoin = [c for c in event_cols if c != "Event ID"]
    df = df.drop(columns=[c for c in cols_to_rejoin if c in df.columns])

    event_lookup = events[event_cols].drop_duplicates(subset=["Event ID"])
    df = df.merge(event_lookup, on="Event ID", how="left")

    # Fallback Weeks label if join produced nothing
    if "Weeks" not in df.columns or df["Weeks"].isna().all():
        if fallback_weeks is not None:
            df["Weeks"] = f"{fallback_weeks}-{fallback_weeks + 1}"
        # else: leave as NaN — EDA plots do not consume the Weeks column

    # --- Join Building, Campus, Room Type (detail) from vet rooms ---
    rooms_path = VET_DATA_DIR / "Rooms and Room Types.xlsx"
    rooms = pd.read_excel(rooms_path)

    room_cols = ["Id"]
    col_renames = {"Id": "Room"}
    for src, dst in [
        ("Building", "Building"),
        ("Campus", "Campus"),
        ("Room Type", "Room Type (detail)"),
    ]:
        if src in rooms.columns:
            room_cols.append(src)
            col_renames[src] = dst

    room_lookup = (
        rooms[room_cols].rename(columns=col_renames).drop_duplicates(subset=["Room"])
    )
    df = df.merge(room_lookup, on="Room", how="left")

    # --- Compute Core flag from Programme-Course data ---
    prog_course_path = VET_DATA_DIR / "Programme-Course.xlsx"
    prog_course = pd.read_excel(prog_course_path)

    if "ModuleId" in prog_course.columns and "Compulsory" in prog_course.columns:
        compulsory_modules = set(
            prog_course.loc[prog_course["Compulsory"] == True, "ModuleId"]
        )
        df["Core"] = df["Module Code"].isin(compulsory_modules)
    else:
        df["Core"] = False

    return df


def _resolve_output_dirs(stem1: str, stem2: str, base: str):
    """Return two output Path objects, appending _1/_2 on stem collision."""
    base_path = Path(base)
    if stem1 != stem2:
        return base_path / stem1, base_path / stem2
    return base_path / (stem1 + "_1"), base_path / (stem2 + "_2")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate EDA comparison plots for two timetable Excel files."
    )
    parser.add_argument("file1", type=Path, help="First timetable Excel file")
    parser.add_argument("file2", type=Path, help="Second timetable Excel file")
    parser.add_argument(
        "--fallback-weeks",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Fallback value for the 'Weeks' column label (e.g. 9 → '9-10') used "
            "only when enrichment is applied and the events data has no Weeks column."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="plots",
        help="Base directory for output plots (default: plots/)",
    )
    args = parser.parse_args()

    out1, out2 = _resolve_output_dirs(args.file1.stem, args.file2.stem, args.output_dir)

    for path, out_dir in [(args.file1, out1), (args.file2, out2)]:
        print(f"\nLoading {path} ...")
        df = load_timetable(path)
        print(f"  {len(df)} rows, columns: {list(df.columns)}")

        if needs_enrichment(df):
            print("  Enriching with vet data joins...")
            df = enrich_if_needed(df, fallback_weeks=args.fallback_weeks)
            print(f"  Enriched columns: {list(df.columns)}")
        else:
            print("  Already enriched — skipping joins.")

        print(f"\nGenerating plots -> {out_dir}/")
        run_eda(df, output_dir=str(out_dir))

    print("\nDone.")
    print(f"  {out1}/ — plots for {args.file1.name}")
    print(f"  {out2}/ — plots for {args.file2.name}")


if __name__ == "__main__":
    main()
