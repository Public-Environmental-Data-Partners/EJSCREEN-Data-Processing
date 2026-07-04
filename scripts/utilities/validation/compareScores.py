#!/usr/bin/env python3
"""compareScores.py

Slice 1 implementation: config + CLI merge, validation, --dry-run, and logging.

This script intentionally implements only the configuration handling and validation
for the first slice. The actual comparison and plotting are implemented in later
slices.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import math

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import textwrap


REQUIRED_FIELDS = (
    'indicator',
    'state',
    'file_a',
    'id_a',
    'score_a',
    'version_a',
    'file_b',
    'id_b',
    'score_b',
    'version_b',
)


def load_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open('r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception as exc:
        raise RuntimeError(f'Failed to load config {path}: {exc}') from exc


def merge_config(file_config: Optional[Dict[str, Any]], cli_args: argparse.Namespace) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if file_config:
        cfg.update(file_config)

    # Map CLI names to config keys
    cli_to_key = {
        'indicator': 'indicator',
        'state': 'state',
        'file_a': 'file_a',
        'id_a': 'id_a',
        'score_a': 'score_a',
        'version_a': 'version_a',
        'file_b': 'file_b',
        'id_b': 'id_b',
        'score_b': 'score_b',
        'version_b': 'version_b',
        'out_dir': 'out_dir',
    }

    for arg_name, key in cli_to_key.items():
        val = getattr(cli_args, arg_name, None)
        if val is not None:
            cfg[key] = val

    # Normalize common path strings to plain strings
    for pkey in ('file_a', 'file_b', 'out_dir'):
        if pkey in cfg and isinstance(cfg[pkey], str):
            cfg[pkey] = cfg[pkey].strip()

    return cfg


def validate_config(cfg: Dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [f for f in REQUIRED_FIELDS if not cfg.get(f)]
    return (len(missing) == 0, missing)


def init_logging(script_dir: Path) -> None:
    log_path = script_dir / 'compareScores.log'
    handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[handler],
    )
    logging.info('=== compareScores log started ===')


def pretty_print_config(cfg: Dict[str, Any]) -> None:
    print(json.dumps(cfg, indent=2, sort_keys=True))


def pretty_print_cli_style_config(cfg: Dict[str, Any]) -> None:
    """Print a user-facing config with underscore keys converted to hyphen keys.

    This is intended for dry-run output so the displayed option names match the
    CLI hyphenated form (e.g. --version-a) while internal logging continues to
    use underscore keys.
    """
    cli_cfg = { (k.replace('_', '-')): v for k, v in cfg.items() }
    print(json.dumps(cli_cfg, indent=2, sort_keys=True))


def default_out_dir(state: str, indicator: str, version_a: str, version_b: str) -> str:
    return str(Path('output') / state / 'compare' / f'{indicator}_{version_a}_vs_{version_b}')


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compare two indicator score CSVs (slice 1: config/dry-run)')
    parser.add_argument('--config', type=str, default=None, help='Path to JSON config file (optional)')

    parser.add_argument('--indicator', type=str, help='Indicator slug (used for labels and filenames)')
    parser.add_argument('--state', type=str, help='Two-letter state postal code (required)')

    parser.add_argument('--file-a', type=str, help='Path to CSV file A')
    parser.add_argument('--id-a', type=str, help='ID column name in file A')
    parser.add_argument('--score-a', type=str, help='Score column name in file A')
    parser.add_argument('--version-a', type=str, help='Version label for file A')

    parser.add_argument('--file-b', type=str, help='Path to CSV file B')
    parser.add_argument('--id-b', type=str, help='ID column name in file B')
    parser.add_argument('--score-b', type=str, help='Score column name in file B')
    parser.add_argument('--version-b', type=str, help='Version label for file B')

    parser.add_argument('--out-dir', type=str, default=None, help='Optional output directory')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print merged config; do not run')

    return parser.parse_args(argv)


def read_csv_coerce(path: str, id_col: str, score_col: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    if id_col not in df.columns:
        raise KeyError(f"ID column '{id_col}' not found in {path}")
    if score_col not in df.columns:
        raise KeyError(f"Score column '{score_col}' not found in {path}")
    df[id_col] = df[id_col].astype(str).str.strip()
    df[score_col] = pd.to_numeric(df[score_col], errors='coerce')
    return df[[id_col, score_col]].copy()


def compute_stats(df: pd.DataFrame, score_a: str, score_b: str) -> Dict[str, float]:
    valid = df[[score_a, score_b]].dropna()
    diffs = (valid[score_a] - valid[score_b]).astype(float)
    abs_diffs = diffs.abs()
    mean_abs = float(abs_diffs.mean()) if len(abs_diffs) > 0 else float('nan')
    median_abs = float(abs_diffs.median()) if len(abs_diffs) > 0 else float('nan')
    rmse = float(np.sqrt(np.nanmean((valid[score_a] - valid[score_b]) ** 2))) if len(valid) > 0 else float('nan')
    try:
        pearson = float(valid[score_a].corr(valid[score_b])) if len(valid) > 1 else float('nan')
    except Exception:
        pearson = float('nan')
    return {
        'matched_rows': int(len(df)),
        'rows_used': int(len(valid)),
        'dropped': int(len(df) - len(valid)),
        'mean_abs_diff': mean_abs,
        'median_abs_diff': median_abs,
        'rmse': rmse,
        'pearson': pearson,
    }


def plot_scatter(df: pd.DataFrame, score_a: str, score_b: str, out_path: Path, stats: Dict[str, float], title: str) -> None:
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(df[score_a], df[score_b], alpha=0.6, s=20, edgecolors='none')
    if not df.empty:
        mins = np.nanmin([df[score_a].min(), df[score_b].min()])
        maxs = np.nanmax([df[score_a].max(), df[score_b].max()])
    else:
        mins, maxs = 0.0, 1.0
    pad = (maxs - mins) * 0.02 if maxs != mins else 0.5
    ax.plot([mins - pad, maxs + pad], [mins - pad, maxs + pad], color='red', linestyle='--', linewidth=1)
    ax.set_xlabel(score_a)
    ax.set_ylabel(score_b)
    ax.set_title(title)
    ax.set_aspect('equal', adjustable='box')

    stats_txt = textwrap.dedent(f"""
        Matched rows: {stats['matched_rows']}
        Rows used: {stats['rows_used']}
        Dropped: {stats['dropped']}
        Mean abs diff: {stats['mean_abs_diff']:.6g}
        Median abs diff: {stats['median_abs_diff']:.6g}
        RMSE: {stats['rmse']:.6g}
        Pearson r: {stats['pearson']:.6g}
    """)
    props = dict(boxstyle='round', facecolor='white', alpha=0.8)
    ax.text(0.02, 0.98, stats_txt, transform=ax.transAxes, fontsize=9, va='top', ha='left', bbox=props)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    init_logging(script_dir)

    file_cfg = None
    if args.config:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            print(f'ERROR: config file not found: {cfg_path}', file=sys.stderr)
            logging.error('Config file not found: %s', cfg_path)
            return 2
        try:
            file_cfg = load_config(cfg_path)
        except Exception as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            logging.exception('Failed to load config')
            return 3

    merged = merge_config(file_cfg, args)

    # If out_dir unspecified, propose a default (do not create yet)
    if 'out_dir' not in merged or not merged.get('out_dir'):
        if merged.get('state') and merged.get('indicator') and merged.get('version_a') and merged.get('version_b'):
            merged['out_dir'] = default_out_dir(merged['state'], merged['indicator'], merged['version_a'], merged['version_b'])

    valid, missing = validate_config(merged)
    if not valid:
        print('ERROR: missing required configuration values:', file=sys.stderr)
        for m in missing:
            print(f'  - {m}', file=sys.stderr)
        logging.error('Validation failed; missing: %s', missing)
        return 4

    logging.info('Merged run configuration: %s', json.dumps(merged))

    if args.dry_run:
        print('Dry-run: merged configuration (no files will be read or written)')
        pretty_print_cli_style_config(merged)
        logging.info('Dry-run completed')
        return 0

    # For slice 1 we stop after validation and reporting; later slices will perform reading and comparison.
    print('Configuration validated. Running comparator (slice 2)')

    # Prepare output paths
    out_dir = Path(merged['out_dir'])
    out_dir.mkdir(parents=True, exist_ok=True)

    matched_csv = out_dir / f"matched_rows_{merged['indicator']}_{merged['version_a']}_vs_{merged['version_b']}.csv"
    scatter_png = out_dir / f"scatter_{merged['indicator']}_{merged['version_a']}_vs_{merged['version_b']}.png"
    compare_log = out_dir / 'compare.log'

    try:
        # Read inputs
        if not Path(merged['file_a']).exists():
            raise FileNotFoundError(f"File A not found: {merged['file_a']}")
        if not Path(merged['file_b']).exists():
            raise FileNotFoundError(f"File B not found: {merged['file_b']}")

        df_a = read_csv_coerce(merged['file_a'], merged['id_a'], merged['score_a'])
        df_b = read_csv_coerce(merged['file_b'], merged['id_b'], merged['score_b'])

        # Normalize join column name
        df_a = df_a.rename(columns={merged['id_a']: 'id', merged['score_a']: 'score_a'})
        df_b = df_b.rename(columns={merged['id_b']: 'id', merged['score_b']: 'score_b'})

        # Inner join
        merged_df = df_a.merge(df_b, on='id', how='inner')
        merged_df['score_diff'] = merged_df['score_a'] - merged_df['score_b']

        # Write matched CSV
        merged_df.to_csv(matched_csv, index=False)

        # Compute stats and plot scatter
        stats = compute_stats(merged_df, 'score_a', 'score_b')
        axis_label_a = f"{merged['score_a']} ({merged['version_a']})"
        axis_label_b = f"{merged['score_b']} ({merged['version_b']})"
        plot_title = f"{merged['indicator']}: {merged['version_a']} vs {merged['version_b']}"
        # For plotting, use columns renamed to score_a/score_b but label axes with original names
        plot_df = merged_df.rename(columns={'score_a': axis_label_a, 'score_b': axis_label_b})
        plot_scatter(plot_df, axis_label_a, axis_label_b, scatter_png, stats, plot_title)

        # Write compare summary log
        with compare_log.open('w', encoding='utf-8') as fh:
            fh.write('Comparison summary\n')
            fh.write(f"matched_rows={stats['matched_rows']}\n")
            fh.write(f"rows_used={stats['rows_used']}\n")
            fh.write(f"dropped={stats['dropped']}\n")
            fh.write(f"mean_abs_diff={stats['mean_abs_diff']:.6g}\n")
            fh.write(f"median_abs_diff={stats['median_abs_diff']:.6g}\n")
            fh.write(f"rmse={stats['rmse']:.6g}\n")
            fh.write(f"pearson={stats['pearson']:.6g}\n")

        logging.info('Comparison completed: matched=%d rows_used=%d', stats['matched_rows'], stats['rows_used'])
        print(f'Wrote matched rows CSV: {matched_csv}')
        print(f'Wrote scatter plot PNG: {scatter_png}')
        print(f'Wrote compare summary: {compare_log}')

    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        logging.exception('Comparison failed')
        return 5

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
