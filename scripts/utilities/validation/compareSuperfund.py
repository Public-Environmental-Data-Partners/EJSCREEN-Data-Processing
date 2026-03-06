"""compareSuperfund.py

Compare two CSVs of block-group weighted scores and produce summary output.

This script reads two CSV files, coerces the user-specified ID columns to
strings, inner-joins the tables on that ID, and computes summary statistics
(mean/median absolute difference, RMSE, Pearson correlation) for the paired
score columns. It writes a scatterplot (scoreA vs scoreB) with a 1:1 identity
line to a PNG file and prints a short textual summary to stdout. A CSV of the
matched rows is also written to the test output path used in examples.

Defaults are set to sample files under `test_files/` for convenience; use the
command-line arguments to point to your own CSVs and column names.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import textwrap
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from typing import Tuple


def read_csv_coerce(path: Path, id_col: str, score_col: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    # Coerce id to string and strip
    if id_col not in df.columns:
        raise KeyError(f"ID column '{id_col}' not found in {path}")
    if score_col not in df.columns:
        raise KeyError(f"Score column '{score_col}' not found in {path}")
    df[id_col] = df[id_col].astype(str).str.strip()
    # Coerce score to numeric (allow decimals, NaN if invalid)
    df[score_col] = pd.to_numeric(df[score_col], errors='coerce')
    return df[[id_col, score_col]].copy()


def summarize_and_plot(
    df_joined: pd.DataFrame,
    id_col: str,
    score_ejam: str,
    score_new: str,
    out_path: Path,
    title: str = None,
) -> None:
    # Drop rows with NaN scores
    before = len(df_joined)
    dropped_rows = df_joined[df_joined[[score_ejam, score_new]].isna().any(axis=1)].copy()
    df = df_joined.dropna(subset=[score_ejam, score_new]).copy()
    after = len(df)
    dropped = before - after

    if dropped > 0:
        dropped_rows['missing_score_ejam'] = dropped_rows[score_ejam].isna()
        dropped_rows['missing_score_new'] = dropped_rows[score_new].isna()

    diffs = (df[score_ejam] - df[score_new]).astype(float)
    abs_diffs = diffs.abs()
    mean_abs = float(abs_diffs.mean()) if len(abs_diffs) > 0 else float('nan')
    median_abs = float(abs_diffs.median()) if len(abs_diffs) > 0 else float('nan')
    rmse = float(np.sqrt(np.nanmean((df[score_ejam] - df[score_new]) ** 2))) if len(df) > 0 else float('nan')
    # Pearson correlation
    try:
        if len(df) > 1:
            pearson = float(df[score_ejam].corr(df[score_new]))
        else:
            pearson = float('nan')
    except Exception:
        pearson = float('nan')

    # Create scatter plot
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(df[score_ejam], df[score_new], alpha=0.6, s=20, edgecolors='none')
    # identity line
    mins = np.nanmin([df[score_ejam].min(), df[score_new].min()]) if len(df) > 0 else 0
    maxs = np.nanmax([df[score_ejam].max(), df[score_new].max()]) if len(df) > 0 else 1
    pad = (maxs - mins) * 0.02 if maxs != mins else 0.5
    ax.plot([mins - pad, maxs + pad], [mins - pad, maxs + pad], color='red', linestyle='--', linewidth=1)

    ax.set_xlabel(score_ejam)
    ax.set_ylabel(score_new)
    ax.set_title(title or f"{score_ejam} vs {score_new}")
    # Text box with stats
    stats_txt = textwrap.dedent(f"""
        Matched rows: {len(df_joined)}\n
        Rows used (non-NaN both scores): {len(df)}\n
        Dropped (NaN score): {dropped}\n
        Mean abs diff: {mean_abs:.6g}\n
        Median abs diff: {median_abs:.6g}\n
        RMSE: {rmse:.6g}\n
        Pearson r: {pearson:.6g}
    """)
    props = dict(boxstyle='round', facecolor='white', alpha=0.8)
    ax.text(0.02, 0.98, stats_txt, transform=ax.transAxes, fontsize=9, va='top', ha='left', bbox=props)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    # Print summary to console
    print("=== Comparison summary ===")
    print(f"Total matched rows before dropping NaNs: {len(df_joined)}")
    print(f"Rows used for stats/plot (both scores not-NaN): {len(df)}")
    print(f"Rows with NaN score dropped: {dropped}")
    if dropped > 0:
        print("Dropped rows with NaN score values:")
        print(
            dropped_rows[
                [id_col, score_ejam, score_new, 'missing_score_ejam', 'missing_score_new']
            ].to_string(index=False)
        )
    print(f"Mean absolute difference: {mean_abs:.6g}")
    print(f"Median absolute difference: {median_abs:.6g}")
    print(f"RMSE: {rmse:.6g}")
    print(f"Pearson r: {pearson:.6g}")
    print(f"Plot written to: {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two CSVs of weighted scores and plot a scatter with 1:1 line")
    parser.add_argument('--file-ejam', type=str, default='./test_files/MT/ejam_superfund_subset.csv', help='Reference CSV (A)')
    parser.add_argument('--id-ejam', type=str, default='ejam_uniq_id', help='ID column name in file A')
    parser.add_argument('--score-ejam', type=str, default='proximity.npl', help='Score column name in file A')

    parser.add_argument('--file-b', type=str, default='./test_files/MT/final_bg_scores.csv', help='Comparison CSV (B)')
    parser.add_argument('--id-b', type=str, default='block_group_geoid', help='ID column name in file B')
    parser.add_argument('--score-new', type=str, default='weighted_score', help='Score column name in file B')

    parser.add_argument('--out', type=str, default='./test_files/MT/compare_ejam_superfund_subset_vs_final_bg_scores.png', help='Output PNG path')
    parser.add_argument('--min-matched', type=int, default=5, help='Minimum matched rows required (otherwise exit non-zero)')

    args = parser.parse_args(argv)

    path_ejam = Path(args.file_ejam)
    path_b = Path(args.file_b)
    out_path = Path(args.out)

    # Read inputs
    try:
        df_ejam = read_csv_coerce(path_ejam, args.id_ejam, args.score_ejam)
    except Exception as e:
        print(f"Error reading EJAM: {e}", file=sys.stderr)
        return 2
    try:
        df_b = read_csv_coerce(path_b, args.id_b, args.score_new)
    except Exception as e:
        print(f"Error reading B: {e}", file=sys.stderr)
        return 2

    # Rename columns to common names to simplify join
    df_ejam = df_ejam.rename(columns={args.id_ejam: 'id', args.score_ejam: 'score_ejam'})
    ############### temporary ######################
    # TODO: move the rounding into the code that produces the csv file 
    # but, for now, we want to see how much this improves the graph jitter
    df_b = df_b.rename(columns={args.id_b: 'id', args.score_new: 'score_new'})
    df_b['score_new'] = df_b['score_new'].round(4)

    # Report counts before join
    unique_ejam = df_ejam['id'].nunique()
    unique_b = df_b['id'].nunique()
    print(f"Unique ids in EJAM: {unique_ejam}; in B: {unique_b}")

    # Inner join on id
    df_joined = df_ejam.merge(df_b, on='id', how='inner')
    print(f"Matched ids (inner join): {len(df_joined)}")

    # Find unmatched examples
    set_ejam = set(df_ejam['id'].unique())
    set_b = set(df_b['id'].unique())
    only_ejam = sorted(list(set_ejam - set_b))
    only_b = sorted(list(set_b - set_ejam))
    print(f"IDs only in EJAM: {len(only_ejam)}; only in B: {len(only_b)}")
    if len(only_ejam) > 0:
        print("Examples only in EJAM:", only_ejam[:5])
    if len(only_b) > 0:
        print("Examples only in B:", only_b[:5])

    # Prepare DataFrame columns for plotting
    df_joined = df_joined.rename(columns={'id': 'matched_id', 'score_ejam': args.score_ejam, 'score_new': args.score_new})

    df_sorted = df_joined.sort_values(by=args.score_ejam, ascending=True)

    print("Top 5 matched rows sorted by EJAM score:")
    print(df_sorted.head(5))

    export_df = df_sorted.copy()
    export_df['matched_id'] = '\t' + export_df['matched_id'].astype(str)
    print(export_df.to_csv('./test_files/MT/matched_rows.csv', index=False))

    # Create plot and summary
    summarize_and_plot(df_joined, 'matched_id', args.score_ejam, args.score_new, out_path, title=f"Compare {args.score_ejam} vs {args.score_new}")

    if len(df_joined) < args.min_matched:
        print(f"Fewer than {args.min_matched} matched rows ({len(df_joined)}). Exiting with code 3.")
        return 3

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
