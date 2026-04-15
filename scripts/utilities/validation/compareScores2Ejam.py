"""compareScores2Ejam.py

Purpose:
        Compare EJAM-derived block-group scores to a second score file, summarize
        agreement between the two series, and produce both scatterplot and map-based
        validation outputs for one state.

Process summary:
        - Read two CSV files, validate the requested ID and score columns, coerce
            IDs to stripped strings, and coerce scores to numeric values.
        - Inner-join the two tables on the chosen block-group identifier and compute
            per-row score differences.
        - Print comparison diagnostics, including unmatched-ID counts and summary
            statistics for the matched rows.
        - Write a scatterplot PNG with a 1:1 reference line and an annotated stats
            panel.
        - Write a matched-rows CSV beside the requested output plot path.
        - Load the state's TIGER block-group geometry from the shared local pipeline
            inputs and write a four-panel validation figure showing the EJAM scores,
            the new scores, their differences, and a scatterplot of matched rows.

Runtime arguments:
        - --state
            Two-letter postal code used for default paths and for locating the shared
            TIGER block-group geometry.
        - --file-ejam, --id-ejam, --score-ejam
            Path, ID column, and score column for the EJAM reference CSV.
        - --file-b, --id-b, --score-new
            Path, ID column, and score column for the comparison CSV.
        - --out
            Output path for the scatterplot PNG. The matched-rows CSV and four-panel
            validation figure are written alongside this file.
        - --min-matched
            Minimum number of matched rows required for a zero exit code.

Outputs:
        - comparison scatterplot PNG
            Scatterplot of EJAM versus new scores with summary statistics.
        - matched_rows_<indicator>.csv
            Matched comparison table exported beside the requested output PNG.
        - <STATE>_map_<indicator>_ejam_new_diff.png
            Four-panel validation figure with three block-group maps and one
            scatterplot panel.

Credits:
        Designed by Anne Gunn.
        Coded by GitHub Copilot (GPT-5.4) and Anne Gunn.
"""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path
import re
import sys
import textwrap
import geopandas as gpd
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, TwoSlopeNorm


def _compute_score_summary(
    df_joined: pd.DataFrame,
    score_ejam: str,
    score_new: str,
) -> tuple[pd.DataFrame, pd.DataFrame, int, float, float, float, float]:
    before = len(df_joined)
    dropped_rows = df_joined[df_joined[[score_ejam, score_new]].isna().any(axis=1)].copy()
    df = df_joined.dropna(subset=[score_ejam, score_new]).copy()
    dropped = before - len(df)

    if dropped > 0:
        dropped_rows['missing_score_ejam'] = dropped_rows[score_ejam].isna()
        dropped_rows['missing_score_new'] = dropped_rows[score_new].isna()

    diffs = (df[score_ejam] - df[score_new]).astype(float)
    abs_diffs = diffs.abs()
    mean_abs = float(abs_diffs.mean()) if len(abs_diffs) > 0 else float('nan')
    median_abs = float(abs_diffs.median()) if len(abs_diffs) > 0 else float('nan')
    rmse = float(np.sqrt(np.nanmean((df[score_ejam] - df[score_new]) ** 2))) if len(df) > 0 else float('nan')
    try:
        pearson = float(df[score_ejam].corr(df[score_new])) if len(df) > 1 else float('nan')
    except Exception:
        pearson = float('nan')

    return df, dropped_rows, dropped, mean_abs, median_abs, rmse, pearson


def _plot_scatter_panel(
    ax: plt.Axes,
    df_joined: pd.DataFrame,
    score_ejam: str,
    score_new: str,
    title: str,
    stats_fontsize: int = 9,
) -> tuple[pd.DataFrame, pd.DataFrame, int, float, float, float, float]:
    df, dropped_rows, dropped, mean_abs, median_abs, rmse, pearson = _compute_score_summary(
        df_joined,
        score_ejam,
        score_new,
    )

    ax.scatter(df[score_ejam], df[score_new], alpha=0.6, s=20, edgecolors='none')
    mins = np.nanmin([df[score_ejam].min(), df[score_new].min()]) if len(df) > 0 else 0
    maxs = np.nanmax([df[score_ejam].max(), df[score_new].max()]) if len(df) > 0 else 1
    pad = (maxs - mins) * 0.02 if maxs != mins else 0.5
    ax.plot([mins - pad, maxs + pad], [mins - pad, maxs + pad], color='red', linestyle='--', linewidth=1)

    ax.set_xlabel(score_ejam)
    ax.set_ylabel(score_new)
    ax.set_title(title)
    ax.set_aspect('equal', adjustable='box')

    stats_txt = textwrap.dedent(f"""
        Matched rows: {len(df_joined)}\n
        Rows used: {len(df)}\n
        Dropped: {dropped}\n
        Mean abs diff: {mean_abs:.6g}\n
        Median abs diff: {median_abs:.6g}\n
        RMSE: {rmse:.6g}\n
        Pearson r: {pearson:.6g}
    """)
    props = dict(boxstyle='round', facecolor='white', alpha=0.8)
    ax.text(0.02, 0.98, stats_txt, transform=ax.transAxes, fontsize=stats_fontsize, va='top', ha='left', bbox=props)

    return df, dropped_rows, dropped, mean_abs, median_abs, rmse, pearson


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


def _resolve_scripts_dir() -> Path:
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if parent.name == 'scripts':
            return parent
    raise RuntimeError(f'Unable to locate scripts directory from {current_path}')


SCRIPTS_DIR = _resolve_scripts_dir()
SHARED_STATE_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'state_config.py'
SHARED_PATHS_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'shared_paths_config.py'
BG_GEOID_COLUMN = 'GEOID'
SCORE_DIFF_COLUMN = 'score_diff'


def _load_shared_state_config_symbols():
    if not SHARED_STATE_CONFIG_MODULE_PATH.exists():
        raise ImportError(f'Shared state_config.py not found: {SHARED_STATE_CONFIG_MODULE_PATH}')

    module_spec = importlib.util.spec_from_file_location(
        'shared_state_config_compare_scores',
        SHARED_STATE_CONFIG_MODULE_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f'Unable to load module spec from {SHARED_STATE_CONFIG_MODULE_PATH}')

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module.get_state_config


def _load_shared_paths_config_symbols():
    if not SHARED_PATHS_CONFIG_MODULE_PATH.exists():
        raise ImportError(f'Shared shared_paths_config.py not found: {SHARED_PATHS_CONFIG_MODULE_PATH}')

    module_spec = importlib.util.spec_from_file_location(
        'shared_paths_config_compare_scores',
        SHARED_PATHS_CONFIG_MODULE_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f'Unable to load module spec from {SHARED_PATHS_CONFIG_MODULE_PATH}')

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module.get_shared_paths_config, module.resolve_local_shared_root_path


try:
    from ...shared.state_config import get_state_config
except ImportError:
    try:
        from shared.state_config import get_state_config
    except ImportError:
        get_state_config = _load_shared_state_config_symbols()

try:
    from ...shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
except ImportError:
    try:
        from shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
    except ImportError:
        get_shared_paths_config, resolve_local_shared_root_path = _load_shared_paths_config_symbols()


def _read_block_groups_geodataframe(bg_path: Path) -> gpd.GeoDataFrame:
    candidates = [str(bg_path)]
    if bg_path.suffix.lower() == '.zip':
        candidates.append(f'zip://{bg_path.as_posix()}')

    last_error = None
    for candidate in dict.fromkeys(candidates):
        try:
            return gpd.read_file(candidate)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f'Failed to read block-group data from {bg_path}: {last_error}')


def _prepare_map_geodataframe(
    df_joined: pd.DataFrame,
    state: str,
    score_ejam: str,
    score_new: str,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, int, int, float, float]:
    state_config = get_state_config(state)
    shared_paths_config = get_shared_paths_config()
    tiger_bg_path = Path(resolve_local_shared_root_path(SCRIPTS_DIR)) / shared_paths_config.tiger_bg_relative_path_template.format(
        fips=state_config.fips,
        postal=state_config.postal,
        name=state_config.name,
    )
    if not tiger_bg_path.exists():
        raise FileNotFoundError(f'TIGER block-group ZIP not found: {tiger_bg_path}')

    bg_gdf = _read_block_groups_geodataframe(tiger_bg_path)
    if BG_GEOID_COLUMN not in bg_gdf.columns:
        raise RuntimeError(f"Expected '{BG_GEOID_COLUMN}' column in TIGER block-group data: {tiger_bg_path}")
    if 'geometry' not in bg_gdf.columns:
        raise RuntimeError(f"Expected 'geometry' column in TIGER block-group data: {tiger_bg_path}")

    map_df = df_joined[['matched_id', score_ejam, score_new, SCORE_DIFF_COLUMN]].copy()
    map_df['matched_id'] = map_df['matched_id'].astype(str).str.strip()

    duplicate_geoids = map_df['matched_id'][map_df['matched_id'].duplicated()].unique().tolist()
    if duplicate_geoids:
        raise RuntimeError(
            'Cannot build map because matched rows contain duplicate block-group ids, '
            f'for example: {duplicate_geoids[:5]}'
        )

    bg_plot = bg_gdf.copy()
    bg_plot[BG_GEOID_COLUMN] = bg_plot[BG_GEOID_COLUMN].astype(str).str.strip()
    bg_plot = bg_plot.merge(
        map_df[['matched_id', score_ejam, score_new, SCORE_DIFF_COLUMN]],
        left_on=BG_GEOID_COLUMN,
        right_on='matched_id',
        how='left',
    )
    state_outline = bg_gdf.dissolve()

    matched_polygons = int(bg_plot[[score_ejam, score_new]].notna().any(axis=1).sum())
    missing_polygons = int(bg_plot[SCORE_DIFF_COLUMN].isna().sum())
    score_values = pd.concat([map_df[score_ejam], map_df[score_new]], ignore_index=True).dropna()
    diff_values = map_df[SCORE_DIFF_COLUMN].dropna()
    score_scale_max = float(score_values.max()) if not score_values.empty else 0.0
    diff_max_abs = float(diff_values.abs().max()) if not diff_values.empty else 0.0

    return state_outline, bg_plot, matched_polygons, missing_polygons, score_scale_max, diff_max_abs


def _plot_map_panel(
    state_outline: gpd.GeoDataFrame,
    bg_plot: gpd.GeoDataFrame,
    column_name: str,
    cmap: str,
    norm: Normalize | TwoSlopeNorm,
    ax: plt.Axes,
    title: str,
) -> None:
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


def plot_score_maps(
    df_joined: pd.DataFrame,
    state: str,
    out_path: Path,
    score_ejam: str,
    score_new: str,
) -> None:
    state_outline, bg_plot, matched_polygons, missing_polygons, score_scale_max, diff_max_abs = _prepare_map_geodataframe(
        df_joined,
        state,
        score_ejam,
        score_new,
    )

    score_scale_bound = score_scale_max if score_scale_max > 0 else 1.0
    diff_scale_bound = diff_max_abs if diff_max_abs > 0 else 1.0
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

    _plot_map_panel(state_outline, bg_plot, score_ejam, 'Reds', score_norm, ax_ejam, f'EJAM score\nShared range: 0 to {score_scale_bound:.6g}')
    _plot_map_panel(state_outline, bg_plot, score_new, 'Reds', score_norm, ax_new, f'New score\nShared range: 0 to {score_scale_bound:.6g}')
    _plot_map_panel(
        state_outline,
        bg_plot,
        SCORE_DIFF_COLUMN,
        'RdBu',
        diff_norm,
        ax_diff,
        'Difference score (EJAM - new)\nRed: new > EJAM | Blue: new < EJAM | White: near zero',
    )

    _plot_scatter_panel(
        ax_scatter,
        df_joined,
        score_ejam,
        score_new,
        f'{score_ejam} vs {score_new}',
        stats_fontsize=8,
    )

    score_colorbar = fig.colorbar(
        ScalarMappable(norm=score_norm, cmap='Reds'),
        ax=[ax_ejam, ax_new],
        fraction=0.03,
        pad=0.02,
    )
    score_colorbar.set_label('Score value')

    diff_colorbar = fig.colorbar(
        ScalarMappable(norm=diff_norm, cmap='RdBu'),
        ax=ax_diff,
        fraction=0.03,
        pad=0.02,
    )
    diff_colorbar.set_label('Score difference (EJAM - new)')

    fig.suptitle(
        f'{state}: Block-group score comparison\n'
        'Gray indicates missing score data',
        fontsize=15,
        y=0.98,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)

    print(f'Map matched block groups: {len(df_joined)}')
    print(f'Map polygons with matched score: {matched_polygons}; polygons without matched score: {missing_polygons}')
    print(f'Shared EJAM/new color scale: 0 to {score_scale_bound:.6g}')
    print(f'Difference color scale: {-diff_scale_bound:.6g} to {diff_scale_bound:.6g}')
    print(f'Map written to: {out_path}')


def summarize_and_plot(
    df_joined: pd.DataFrame,
    id_col: str,
    score_ejam: str,
    score_new: str,
    out_path: Path,
    title: str = None,
) -> None:
    fig, ax = plt.subplots(figsize=(7, 7))
    df, dropped_rows, dropped, mean_abs, median_abs, rmse, pearson = _plot_scatter_panel(
        ax,
        df_joined,
        score_ejam,
        score_new,
        title or f"{score_ejam} vs {score_new}",
    )

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


def _sanitize_indicator_slug(raw_value: str) -> str:
    cleaned = re.sub(r'[^a-z0-9]+', '_', raw_value.strip().lower()).strip('_')
    return cleaned or 'comparison'


def _infer_indicator_slug(path_ejam: Path, path_b: Path, score_ejam: str, score_new: str) -> str:
    ejam_stem = path_ejam.stem.lower()
    if ejam_stem.startswith('ejam_') and ejam_stem.endswith('_subset'):
        return _sanitize_indicator_slug(ejam_stem[len('ejam_'):-len('_subset')])

    score_slug_map = {
        'proximity.tsdf': 'hazardous_waste',
        'proximity.npl': 'superfund',
    }
    if score_ejam in score_slug_map:
        return score_slug_map[score_ejam]

    new_score_slug_map = {
        'hazardous_waste_score': 'hazardous_waste',
        'superfund_score': 'superfund',
    }
    if score_new in new_score_slug_map:
        return new_score_slug_map[score_new]

    path_parts = [part.lower() for part in path_b.parts]
    for candidate in ('hazardous_waste', 'superfund', 'traffic'):
        if candidate in path_parts:
            return candidate

    return _sanitize_indicator_slug(score_ejam.replace('.', '_'))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two CSVs of weighted scores and plot a scatter with 1:1 line")
    parser.add_argument('--state', type=str, default='MT', help='Two-letter postal code used to build the default output-folder paths')
    parser.add_argument('--file-ejam', type=str, default=None, help='Reference CSV (A). Default: ./output/{STATE}/ejam_superfund_subset.csv')
    parser.add_argument('--id-ejam', type=str, default='ejam_uniq_id', help='ID column name in file A')
    parser.add_argument('--score-ejam', type=str, default='proximity.npl', help='Score column name in file A')

    parser.add_argument('--file-b', type=str, default=None, help='Comparison CSV (B). Default: ../../superfund/pipeline/test_data/{STATE}/final_bg_scores.csv')

    parser.add_argument('--id-b', type=str, default='block_group_geoid', help='ID column name in file B')
    parser.add_argument('--score-new', type=str, default='weighted_score', help='Score column name in file B')

    parser.add_argument('--out', type=str, default=None, help='Output PNG path. Default: ./output/{STATE}/{state}_compare_ejam_SF_subset_to_weighted_scores.png')
    parser.add_argument('--min-matched', type=int, default=5, help='Minimum matched rows required (otherwise exit non-zero)')

    args = parser.parse_args(argv)

    state = args.state.upper()
    if len(state) != 2:
        parser.error('--state must be a two-letter postal code')
    else:
        print(f"Using state code: {state}")

    path_ejam = Path(args.file_ejam or f'./output/{state}/ejam_superfund_subset.csv')
    path_b = Path(args.file_b or f'../../superfund/pipeline/test_data/{state}/final_bg_scores.csv')
    out_path = Path(args.out or f'./output/{state}/compare_ejam_superfund_subset_vs_final_bg_scores_{state}.png')
    indicator_slug = _infer_indicator_slug(path_ejam, path_b, args.score_ejam, args.score_new)
    map_out_path = out_path.parent / f'{state}_map_{indicator_slug}_ejam_new_diff.png'

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
    df_joined[SCORE_DIFF_COLUMN] = (df_joined[args.score_ejam] - df_joined[args.score_new]).astype(float)

    df_sorted = df_joined.sort_values(by=args.score_ejam, ascending=True)

    print("Top 5 matched rows sorted by EJAM score:")
    print(df_sorted.head(5))

    export_df = df_sorted.copy()
    export_df['matched_id'] = '\t' + export_df['matched_id'].astype(str)
    matched_rows_path = out_path.with_name(f'matched_rows_{indicator_slug}.csv')
    matched_rows_path.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(matched_rows_path, index=False)
    print(f"Matched rows written to: {matched_rows_path}")

    # Create plot and summary
    summarize_and_plot(df_joined, 'matched_id', args.score_ejam, args.score_new, out_path, title=f"{state}: Compare {args.score_ejam} vs {args.score_new}")
    plot_score_maps(df_joined, state, map_out_path, args.score_ejam, args.score_new)

    if len(df_joined) < args.min_matched:
        print(f"Fewer than {args.min_matched} matched rows ({len(df_joined)}). Exiting with code 3.")
        return 3

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
