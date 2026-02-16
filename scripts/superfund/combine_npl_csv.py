"""
combine_npl_csv.py

Purpose:
  Read two NPL CSV files (Current and Proposed), normalize their columns,
  concatenate them with Current prioritized, remove any duplicate EPA_IDs
  (none are expected) keeping only the Current record, and
  write out a clean combined CSV.

Behavior summary:
  - Reads `current_npl.csv` and `proposed_npl.csv`, skipping the
    first 10 rows of each file to reach the header row.
  - Ensures both DataFrames share the same set of columns before concatenation.
  - Concatenates Current first, then Proposed, so that drop_duplicates(keep='first')
    will prefer Current records if EPA_ID collisions occur (not expected).
  - Logs warnings if there are EPA_ID overlaps and reports how many were found.
  - Writes a single output CSV with standard headers (no preamble).

Usage (example):
  python combine_npl_csv.py --current-filename current_npl.csv --proposed-filename proposed_npl.csv --output-filename combined_npl.csv

Technical notes:
  - Uses pandas for data manipulation.
  - Uses argparse and a dataclass `Config` for runtime parameters.
  - The key column used to detect collisions is `EPA ID` (case-sensitive).

"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import logging
import pandas as pd
from typing import Tuple, List, Set


# --- Configuration dataclass ---------------------------------------------
@dataclass
class Config:
    input_path: str = "./inputs/test_data/"
    output_path: str = "./inputs/test_data/"
    current_filename: str = "current_npl.csv"
    proposed_filename: str = "proposed_npl.csv"
    output_filename: str = "combined_npl.csv"
    skip_rows: int = 10
    id_col: str = "EPA_ID"


# --- Helper functions ----------------------------------------------------
def get_config(argv=None) -> Config:
    parser = argparse.ArgumentParser(description="Combine Current and Proposed NPL CSVs; prefer Current on EPA_ID collisions")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help='Folder containing input CSVs (default: ./inputs/test_data/)')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help='Folder to write the output CSV (default: ./inputs/test_data/)')
    parser.add_argument('--current-filename', dest='current_filename', default=Config.current_filename,
                        help='Current CSV filename (default: current_npl.csv)')
    parser.add_argument('--proposed-filename', dest='proposed_filename', default=Config.proposed_filename,
                        help='Proposed CSV filename (default: proposed_npl.csv)')
    parser.add_argument('--output-filename', dest='output_filename', default=Config.output_filename,
                        help='Output combined CSV filename (default: combined_npl.csv)')
    parser.add_argument('--skip-rows', dest='skip_rows', type=int, default=Config.skip_rows,
                        help='Number of rows to skip before the header in input CSVs (default: 10)')
    parser.add_argument('--id-col', dest='id_col', type=str, default=Config.id_col,
                        help='Column name to use as the unique identifier (default: EPA_ID)')

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        current_filename=args.current_filename,
        proposed_filename=args.proposed_filename,
        output_filename=args.output_filename,
        skip_rows=args.skip_rows,
        id_col=args.id_col,
    )


def read_npl_csv(path: str, skip_rows: int) -> pd.DataFrame:
    """Read CSV skipping preamble rows so the header is read correctly."""
    return pd.read_csv(path, skiprows=skip_rows)


def standardize_columns(df_current: pd.DataFrame, df_proposed: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """Return two DataFrames reindexed to a shared column order.

    The column order preserves current's columns first, then adds any proposed-only
    columns after.
    """
    current_cols = list(df_current.columns)
    proposed_cols = list(df_proposed.columns)

    # Build combined column list: current columns first, then any from proposed not present
    combined_cols = current_cols + [c for c in proposed_cols if c not in current_cols]

    # Reindex dataframes to the combined columns, adding missing columns with NaN
    df_current2 = df_current.reindex(columns=combined_cols)
    df_proposed2 = df_proposed.reindex(columns=combined_cols)
    return df_current2, df_proposed2, combined_cols


def find_overlaps(df_current: pd.DataFrame, df_proposed: pd.DataFrame, id_col: str) -> Set[str]:
    if id_col not in df_current.columns or id_col not in df_proposed.columns:
        return set()
    current_ids = set(df_current[id_col].dropna().astype(str).unique())
    proposed_ids = set(df_proposed[id_col].dropna().astype(str).unique())
    return current_ids & proposed_ids


def combine_and_dedup(df_current: pd.DataFrame, df_proposed: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """Concatenate Current then Proposed and drop duplicates keeping the first (Current) record."""
    combined = pd.concat([df_current, df_proposed], ignore_index=True, sort=False)
    if id_col in combined.columns:
        combined = combined.drop_duplicates(subset=[id_col], keep='first')
    return combined


# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths
    input_dir = Path(cfg.input_path)
    output_dir = Path(cfg.output_path)
    current_path = input_dir / cfg.current_filename
    proposed_path = input_dir / cfg.proposed_filename

    # Read inputs
    logging.info(f"Reading Current CSV: {current_path} (skip {cfg.skip_rows} rows)")
    df_current = read_npl_csv(str(current_path), cfg.skip_rows)
    logging.info(f"Reading Proposed CSV: {proposed_path} (skip {cfg.skip_rows} rows)")
    df_proposed = read_npl_csv(str(proposed_path), cfg.skip_rows)

    # Standardize columns
    df_current_std, df_proposed_std, combined_cols = standardize_columns(df_current, df_proposed)
    logging.info(f"Standardized columns; combined column count: {len(combined_cols)}")

    # Collision audit
    overlaps = find_overlaps(df_current_std, df_proposed_std, cfg.id_col)
    if overlaps:
        logging.warning(f"Found {len(overlaps)} overlapping {cfg.id_col} values between Current and Proposed; Current records will be kept.")

    # Combine deterministically and drop duplicates (Current first)
    df_clean = combine_and_dedup(df_current_std, df_proposed_std, cfg.id_col)

    # Write output
    out_path = Path(cfg.output_path) / cfg.output_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_clean.to_csv(out_path, index=False)
    logging.info(f"Wrote combined CSV to: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
