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
import geopandas as gpd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, TwoSlopeNorm


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


def _resolve_scripts_dir() -> Path:
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if parent.name == 'scripts':
            return parent
    raise RuntimeError(f'Unable to locate scripts directory from {current_path}')


def _read_block_groups_geodataframe(tiger_zip_path: Path) -> gpd.GeoDataFrame:
    candidates = [str(tiger_zip_path)]
    if tiger_zip_path.suffix.lower() == '.zip':
        candidates.append(f'zip://{tiger_zip_path.as_posix()}')

    last_err = None
    for candidate in dict.fromkeys(candidates):
        try:
            gdf = gpd.read_file(candidate)
            return gdf
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f'Failed to read block-group data from {tiger_zip_path}: {last_err}')


def prepare_map_and_plot(merged_df: pd.DataFrame, state: str, out_dir: Path, indicator: str, version_a: str, version_b: str) -> None:
    # Derive FIPS from first matched geoid (first 2 digits)
    if merged_df.empty:
        raise RuntimeError('No matched rows to map')
    sample_id = merged_df['id'].astype(str).iloc[0]
    fips = sample_id[:2]

    # Resolve TIGER path relative to scripts/shared/pipeline/downloads/...
    scripts_dir = _resolve_scripts_dir()
    tiger_rel = Path('downloads') / 'tiger_lines' / '2020' / 'bg' / f'tl_2020_{fips}_bg.zip'
    tiger_path = scripts_dir / 'shared' / 'pipeline' / tiger_rel
    if not tiger_path.exists():
        raise FileNotFoundError(f'TIGER block-group ZIP not found: {tiger_path}')

    bg_gdf = _read_block_groups_geodataframe(tiger_path)
    if 'GEOID' not in bg_gdf.columns:
        raise RuntimeError(f"Expected 'GEOID' column in TIGER block-group data: {tiger_path}")
    if 'geometry' not in bg_gdf.columns:
        raise RuntimeError(f"Expected 'geometry' column in TIGER block-group data: {tiger_path}")

    map_df = merged_df.copy()
    map_df['id'] = map_df['id'].astype(str).str.strip()

    bg_gdf['GEOID'] = bg_gdf['GEOID'].astype(str).str.strip()
    bg_plot = bg_gdf.merge(map_df[['id', 'score_a', 'score_b', 'score_diff']], left_on='GEOID', right_on='id', how='left')
    state_outline = bg_gdf.dissolve()

    # Compute scales
    score_values = pd.concat([map_df['score_a'], map_df['score_b']], ignore_index=True).dropna()
    diff_values = map_df['score_diff'].dropna()
    score_scale_max = float(score_values.max()) if not score_values.empty else 0.0
    diff_max_abs = float(diff_values.abs().max()) if not diff_values.empty else 0.0

    score_scale_bound = score_scale_max if score_scale_max > 0 else 1.0
    diff_scale_bound = diff_max_abs if diff_max_abs > 1.0 else 1.0
    score_norm = Normalize(vmin=0.0, vmax=score_scale_bound)
    diff_norm = TwoSlopeNorm(vmin=-diff_scale_bound, vcenter=0.0, vmax=diff_scale_bound)

    fig = plt.figure(figsize=(15, 16), constrained_layout=True)
    outer_grid = fig.add_gridspec(2, 1, height_ratios=[1, 1.12])
    top_grid = outer_grid[0].subgridspec(1, 2, wspace=0.04)
    bottom_grid = outer_grid[1].subgridspec(1, 3, width_ratios=[1, 1, 0.9], wspace=0.18)

    ax_ejam = fig.add_subplot(top_grid[0, 0])
    ax_new = fig.add_subplot(top_grid[0, 1])
    ax_diff = fig.add_subplot(bottom_grid[0, :2])
    ax_scatter = fig.add_subplot(bottom_grid[0, 2])

    def _plot_map_panel(state_outline, bg_plot, column_name, cmap, norm, ax, title):
        bg_plot.plot(
            column=column_name,
            cmap=cmap,
            norm=norm,
            linewidth=0.05,
            edgecolor='#b8b8b8',
            legend=False,
            missing_kwds={'color': '#b3b3b3'},
            ax=ax,
        )
        state_outline.boundary.plot(ax=ax, color='#4a4a4a', linewidth=0.5)
        ax.set_title(title)
        ax.set_axis_off()

    _plot_map_panel(state_outline, bg_plot, 'score_a', 'Reds', score_norm, ax_ejam, f'{indicator} {version_a}')
    _plot_map_panel(state_outline, bg_plot, 'score_b', 'Reds', score_norm, ax_new, f'{indicator} {version_b}')
    _plot_map_panel(state_outline, bg_plot, 'score_diff', 'RdBu', diff_norm, ax_diff, 'Difference (A - B)')

    # Scatter panel: reuse the short-named standalone scatter PNG (created
    # earlier as `scatter_{indicator}_{version_a}_vs_{version_b}.png`). If it
    # doesn't exist yet, create it; then embed into the four-panel figure.
    scatter_path = out_dir / f'scatter_{indicator}_{version_a}_vs_{version_b}.png'
    if not scatter_path.exists():
        plot_scatter(
            map_df.rename(columns={'score_a': 'score_a', 'score_b': 'score_b'}),
            'score_a',
            'score_b',
            scatter_path,
            compute_stats(map_df, 'score_a', 'score_b'),
            f'{indicator} scatter',
        )
    try:
        img = plt.imread(scatter_path)
        ax_scatter.imshow(img)
        ax_scatter.set_axis_off()
    except Exception as exc_img:
        logging.warning('Failed to embed scatter image into map figure: %s', exc_img)

    score_colorbar = fig.colorbar(ScalarMappable(norm=score_norm, cmap='Reds'), ax=[ax_ejam, ax_new], fraction=0.03, pad=0.02)
    score_colorbar.set_label('Score value')
    diff_colorbar = fig.colorbar(ScalarMappable(norm=diff_norm, cmap='RdBu'), ax=ax_diff, fraction=0.03, pad=0.02)
    diff_colorbar.set_label('Score difference (A - B)')

    fig.suptitle(f'{state}: Block-group score comparison', fontsize=15, y=0.98)
    map_out = out_dir / f'{state}_map_{indicator}_{version_a}_vs_{version_b}.png'
    fig.savefig(map_out, dpi=150, bbox_inches='tight')
    plt.close(fig)
    logging.info('Map written to %s', map_out)


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

    # Allow file/out paths in config to be templates using {state} so a single
    # config file can be reused for different states via the --state override.
    if merged.get('state'):
        for key in ('file_a', 'file_b', 'out_dir'):
            val = merged.get(key)
            if isinstance(val, str):
                try:
                    merged[key] = val.format(state=merged['state'])
                except Exception as exc:
                    logging.warning('Failed to format %s with state=%s: %s', key, merged['state'], exc)

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
    compare_summary = out_dir / 'compare_summary.txt'

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

        # Compute extreme differences for reporting (file only)
        diffs = merged_df['score_diff'].dropna().astype(float)
        greatest_positive = float(diffs[diffs > 0].max()) if not diffs[diffs > 0].empty else float('nan')
        smallest_negative = float(diffs[diffs < 0].min()) if not diffs[diffs < 0].empty else float('nan')

        # Write compare summary (additional extremes included)
        with compare_summary.open('w', encoding='utf-8') as fh:
            fh.write('Comparison summary\n')
            fh.write(f"matched_rows={stats['matched_rows']}\n")
            fh.write(f"rows_used={stats['rows_used']}\n")
            fh.write(f"dropped={stats['dropped']}\n")
            fh.write(f"mean_abs_diff={stats['mean_abs_diff']:.6g}\n")
            fh.write(f"median_abs_diff={stats['median_abs_diff']:.6g}\n")
            fh.write(f"rmse={stats['rmse']:.6g}\n")
            fh.write(f"pearson={stats['pearson']:.6g}\n")
            fh.write(f"greatest_positive_diff={greatest_positive:.6g}\n")
            fh.write(f"smallest_negative_diff={smallest_negative:.6g}\n")

        logging.info('Comparison completed: matched=%d rows_used=%d', stats['matched_rows'], stats['rows_used'])
        print(f'Wrote matched rows CSV: {matched_csv}')
        print(f'Wrote scatter plot PNG: {scatter_png}')
        print(f'Wrote compare summary: {compare_summary}')

        # Attempt to produce the four-panel map (slice 3). If TIGER data is missing
        # or plotting fails, log the error but do not fail the whole run.
        try:
            prepare_map_and_plot(merged_df, merged['state'], out_dir, merged['indicator'], merged['version_a'], merged['version_b'])
        except FileNotFoundError as fnf:
            print(f'Map not produced: {fnf}')
            logging.warning('Map not produced: %s', fnf)
        except Exception as exc_map:
            print(f'Warning: map generation failed: {exc_map}')
            logging.exception('Map generation failed')

    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        logging.exception('Comparison failed')
        return 5

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
