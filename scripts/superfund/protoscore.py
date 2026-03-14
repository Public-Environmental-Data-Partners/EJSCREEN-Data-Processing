"""Prototype script for quick scoring prototype (guerrilla mode).

Global input/output filenames (fill these in before running):
- NPL_GDB_PATH: path to the NPL site boundaries geodatabase (.gdb)
- BG_SHP_PATH: path to the Census Block Group shapefile (directory or .shp)
- BLOCKS_CSV: path to block centroids CSV

Interim outputs (written to `OUTPUT_DIR`):
- TARGETED_BG_CSV: targeted_block_groups.csv
- BLOCK_SITE_DISTANCES_CSV: block_site_distances.csv
- FINAL_BG_SCORES_CSV: final_bg_scores.csv

This file is intentionally minimal and hard-wired for rapid prototyping.
Fill in the empty strings below with your local/WSL paths and then run.
"""

# Standard imports
import logging
from pathlib import Path

import fiona
import geopandas as gpd
import pandas as pd
from pyproj import CRS

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

NPL_LAYER_NAME = 'SITE_BOUNDARIES_SF'
NPL_STATUS_COLUMN = 'NPL_STATUS_CODE'
NPL_EPA_ID_COLUMN = 'EPA_ID'
BG_GEOID_COLUMN = 'GEOID'
BLOCKS_CSV_REQUIRED_COLUMNS = (
    'GEOID20',
    'INTPTLAT20',
    'INTPTLON20',
    'POP20',
    'block_group_geoid',
    'block_group_pop',
    'fraction_of_total',
)

STATE_CONFIG = {
    # specify a metric crs (projection) only if the default EPSG:5070 is not appropriate
    'AK': {
        'fips': '02',
        'postal': 'AK',
        'name': 'Alaska',
        'metric_crs': 'EPSG:3338'
    },
    'HI': {
        'fips': '15',
        'postal': 'HI',
        'name': 'Hawaii',
        # Note there is, apparently, no single ideal projection for all of Hawaii that uses meters as units. 
        # The commonly used EPSG:3563 is accurate for the main islands but distorts the NW and SE islands,
        # while the newer ESRI:102007 is designed to minimize distortion across all islands. 
        # For this prototype, we'll use ESRI:102007, but either would be a reasonable choice for a Hawaii-specific implementation.
        # TODO: confer with EmmaLi!! and agree on this
        'metric_crs': 'ESRI:102007'
    },
    'MT': {
        'fips': '30',
        'postal': 'MT',
        'name': 'Montana',
    },
    'RI': {
        'fips': '44',
        'postal': 'RI',
        'name': 'Rhode Island',
    },
    'VT': {
        'fips': '50',
        'postal': 'VT',
        'name': 'Vermont',
    },
    'WY': {
        'fips': '56',
        'postal': 'WY',
        'name': 'Wyoming',
    }
    # Add other states as needed
}

CURRENT_STATE = 'HI'  # <-- set to the state you want to run the prototype on (must be in STATE_CONFIG)


def _validate_metric_target_crs(metric_crs, description: str) -> CRS:
    try:
        target_crs = CRS.from_user_input(metric_crs)
    except Exception as exc:
        raise RuntimeError(f'Invalid {description}: {metric_crs}: {exc}') from exc

    if not target_crs.is_projected:
        raise RuntimeError(f'{description} must be a projected CRS in meters, got non-projected CRS: {metric_crs}')

    axis_units = {axis.unit_name.lower() for axis in target_crs.axis_info if axis.unit_name}
    if axis_units and not axis_units.issubset({'metre', 'meter'}):
        raise RuntimeError(
            f'{description} must use meter units, got {", ".join(sorted(axis_units))}: {metric_crs}'
        )

    return target_crs

def _get_current_state_settings():
    if CURRENT_STATE not in STATE_CONFIG:
        raise RuntimeError(f"CURRENT_STATE '{CURRENT_STATE}' is not present in STATE_CONFIG")

    state_config = STATE_CONFIG[CURRENT_STATE]
    metric_crs = state_config.get('metric_crs') or 'EPSG:5070'
    _validate_metric_target_crs(metric_crs, f"metric_crs for {CURRENT_STATE}")
    logging.info(
        'Current state: %s, %s, fips: %s, metric crs: %s',
        state_config['name'],
        state_config['postal'],
        state_config['fips'],
        metric_crs,
    )
    return state_config, state_config['fips'], state_config['postal'], metric_crs

current_state_config, current_state_fips, current_state_postal, current_state_crs = _get_current_state_settings()

# --- Global file path variables (fill these before running) -----------------
# Input sources
NPL_GDB_PATH = "./pipeline/test_data/downloads/NPL_Boundaries_20260217/NPL_Boundaries.gdb"  # e.g. '/mnt/c/.../NPL_Boundaries.gdb'
BG_SHP_PATH = f"./pipeline/test_data/downloads/tl_2020_{current_state_fips}_bg.zip"   # e.g. '/mnt/c/.../tl_2020_XX_bg.shp' or folder containing .shp
BLOCKS_CSV = f"./pipeline/test_data/downloads/census_block_weights_2020/census_block_weights_2020_{current_state_postal}.csv"  # e.g. '/mnt/c/.../blocks_centroids.csv'

# Working/output directory
# TODO: switch to AWS S3 if we're going to make this code production worthy
OUTPUT_DIR = f"./pipeline/test_data/{current_state_postal}/"  

# Interim output filenames (placed under OUTPUT_DIR)
TARGETED_BG_CSV = "targeted_block_groups.csv"
BLOCK_SITE_DISTANCES_CSV = "block_site_distances.csv"
FINAL_BG_SCORES_CSV = "final_bg_scores.csv"

# Optional: set a short list of block-group GEOIDs to export detailed block rows for.
# Example: EXPORT_BG_LIST = ['300010001001', '300010001002']
#EXPORT_BG_LIST = []  # <-- fill with a small list of 12-char block-group GEOIDs to auto-export after Step 4

def _require_existing_path(path_value: str, path_name: str) -> Path:
    if not path_value:
        raise RuntimeError(f'{path_name} is not configured')

    path = Path(path_value)
    if not path.exists():
        raise RuntimeError(f'{path_name} does not exist: {path}')
    return path


def _require_columns(df: pd.DataFrame, required_columns: tuple[str, ...], description: str) -> None:
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise RuntimeError(f'{description} is missing required columns: {", ".join(missing_columns)}')


def _validate_output_dir() -> None:
    if not OUTPUT_DIR:
        raise RuntimeError('OUTPUT_DIR is not configured')


def apply_metric_projection(description, input_proj, metric_crs):
    """Validate CRS metadata, log the reprojection, and return projected data."""
    target_crs = _validate_metric_target_crs(metric_crs, f'target CRS for {description}')

    if input_proj.crs is None:
        raise RuntimeError(f'{description} has no source CRS defined')

    original_crs = input_proj.crs.name or str(input_proj.crs)
    original_epsg = input_proj.crs.to_epsg()
    logging.info(
        'PROJECTION LOG: %s | State FIPS %s | Source CRS: %s (EPSG:%s) -> Target CRS: %s',
        description,
        current_state_fips,
        original_crs,
        original_epsg if original_epsg is not None else 'N/A',
        target_crs,
    )
    try:
        return input_proj.to_crs(target_crs)
    except Exception as exc:
        raise RuntimeError(f'Failed to reproject {description} to {target_crs}: {exc}') from exc


def step0_prepare_inputs() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Step 0: validate configured inputs and return projected GeoDataFrames."""
    _, _, _, target_crs = _get_current_state_settings()

    npl_path = _require_existing_path(NPL_GDB_PATH, 'NPL_GDB_PATH')
    bg_path = _require_existing_path(BG_SHP_PATH, 'BG_SHP_PATH')
    blocks_path = _require_existing_path(BLOCKS_CSV, 'BLOCKS_CSV')

    if blocks_path.suffix.lower() != '.csv':
        raise RuntimeError(f'BLOCKS_CSV must point to a CSV file: {blocks_path}')

    logging.info('##############################################################')
    logging.info('#### Starting Step0: preparing projected input datasets')

    # Superfund sites on the National Priorities List (NPL)
    try:
        layers = fiona.listlayers(str(npl_path))
    except Exception as exc:
        raise RuntimeError(f'Failed to inspect NPL geodatabase at {npl_path}: {exc}') from exc
    if NPL_LAYER_NAME not in layers:
        raise RuntimeError(f"NPL layer '{NPL_LAYER_NAME}' not found in {npl_path}")

    logging.info('Reading NPL layer: %s', NPL_LAYER_NAME)
    try:
        npl_gdf = gpd.read_file(str(npl_path), layer=NPL_LAYER_NAME)
    except Exception as exc:
        raise RuntimeError(f"Failed to read NPL layer '{NPL_LAYER_NAME}' from {npl_path}: {exc}") from exc
    _require_columns(npl_gdf, (NPL_STATUS_COLUMN, NPL_EPA_ID_COLUMN, 'geometry'), 'NPL layer')
    npl_gdf = apply_metric_projection('npl dataset', npl_gdf, target_crs)

    # Block group boundaries from the Census Bureau (TIGER/Line shapefile)
    logging.info('Reading block-group dataset: %s', bg_path)
    bg_source = f'zip://{bg_path}' if bg_path.suffix.lower() == '.zip' else str(bg_path)
    try:
        bg_gdf = gpd.read_file(bg_source)
    except Exception as exc:
        raise RuntimeError(f'Failed to read block-group data from {bg_path}: {exc}') from exc
    _require_columns(bg_gdf, (BG_GEOID_COLUMN, 'geometry'), 'block-group data')
    bg_gdf = apply_metric_projection('block-group dataset', bg_gdf, target_crs)

    # Census blocks centroids CSV with population weights (prepared from raw Census sources)
    logging.info('Reading blocks CSV: %s', blocks_path)
    try:
        blocks_df = pd.read_csv(blocks_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(f'Failed to read blocks CSV from {blocks_path}: {exc}') from exc
    _require_columns(blocks_df, BLOCKS_CSV_REQUIRED_COLUMNS, 'blocks CSV')

    blocks_df = blocks_df.copy()
    blocks_df['block_geoid'] = blocks_df['GEOID20'].astype(str).str.strip()
    blocks_df['block_group_geoid'] = blocks_df['block_group_geoid'].astype(str).str.strip()
    if blocks_df['block_geoid'].eq('').any():
        raise RuntimeError('Blocks CSV contains blank GEOID20 values')
    if blocks_df['block_group_geoid'].eq('').any():
        raise RuntimeError('Blocks CSV contains blank block_group_geoid values')

    try:
        blocks_df['block_lat'] = pd.to_numeric(blocks_df['INTPTLAT20'], errors='raise')
        blocks_df['block_lon'] = pd.to_numeric(blocks_df['INTPTLON20'], errors='raise')
        blocks_df['block_pop'] = pd.to_numeric(blocks_df['POP20'], errors='raise')
        blocks_df['block_group_pop'] = pd.to_numeric(blocks_df['block_group_pop'], errors='raise')
        blocks_df['fraction_of_total'] = pd.to_numeric(blocks_df['fraction_of_total'], errors='raise')
    except Exception as exc:
        raise RuntimeError(f'Blocks CSV contains invalid numeric values: {exc}') from exc

    blocks_gdf = gpd.GeoDataFrame(
        blocks_df,
        geometry=gpd.points_from_xy(blocks_df['block_lon'], blocks_df['block_lat']),
        crs='EPSG:4326',
    )
    blocks_gdf = apply_metric_projection('blocks dataset', blocks_gdf, target_crs)

    return npl_gdf, bg_gdf, blocks_gdf





def step1_buffer_and_targeted_bgs(
    npl_gdf: gpd.GeoDataFrame,
    bg_gdf: gpd.GeoDataFrame,
    buffer_meters: float = 10000.0,
) -> pd.DataFrame:
    """Step 1: Buffer projected NPL polygons and find intersecting Block Groups."""
    if npl_gdf is None:
        raise RuntimeError('npl_gdf is required')
    if bg_gdf is None:
        raise RuntimeError('bg_gdf is required')

    _validate_output_dir()
    _require_columns(npl_gdf, (NPL_STATUS_COLUMN, NPL_EPA_ID_COLUMN, 'geometry'), 'NPL GeoDataFrame')
    _require_columns(bg_gdf, (BG_GEOID_COLUMN, 'geometry'), 'Block-group GeoDataFrame')

    logging.info('#### Starting Step1: buffering projected NPL polygons and targeting block groups')
    logging.info('NPL columns: %d, rows: %d', len(npl_gdf.columns), len(npl_gdf))
    logging.info('Block Group columns: %d, rows: %d', len(bg_gdf.columns), len(bg_gdf))

    npl_active_gdf = npl_gdf[npl_gdf[NPL_STATUS_COLUMN].isin(['F', 'P'])].copy()
    logging.info('After filtering for active-ish NPL rows (F and P only): %d', len(npl_active_gdf))

    logging.info('Buffering NPL polygons by %s meters', buffer_meters)
    npl_active_gdf['geometry_buffer'] = npl_active_gdf.geometry.buffer(buffer_meters)
    npl_buffer = npl_active_gdf.set_geometry('geometry_buffer')

    logging.info('Performing spatial join to find targeted block groups')
    joined = gpd.sjoin(bg_gdf, npl_buffer, how='inner', predicate='intersects')
    if joined.empty:
        logging.info('No block groups found within buffer distance')
        return pd.DataFrame()

    targeted = joined[[BG_GEOID_COLUMN, NPL_EPA_ID_COLUMN]].drop_duplicates().copy()
    targeted = targeted.rename(columns={BG_GEOID_COLUMN: 'GEOID_BG', NPL_EPA_ID_COLUMN: 'EPA_ID'})
    # Keep EPA_ID values as provided (exact match on `EPA_ID` column)
    targeted['EPA_ID'] = targeted['EPA_ID'].astype(str).str.strip()

    out_path = Path(OUTPUT_DIR) / TARGETED_BG_CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Writing targeted block groups to %s', out_path)
    targeted[['GEOID_BG', 'EPA_ID']].to_csv(out_path, index=False)

    return targeted


def step2_block_site_distances(
    targeted_df: pd.DataFrame,
    blocks_gdf: gpd.GeoDataFrame,
    npl_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Step 2: For blocks inside targeted block groups, compute distance to each NPL polygon."""
    if targeted_df is None:
        raise RuntimeError('targeted_df is required')
    if blocks_gdf is None:
        raise RuntimeError('blocks_gdf is required')
    if npl_gdf is None:
        raise RuntimeError('npl_gdf is required')

    _require_columns(targeted_df, ('GEOID_BG', 'EPA_ID'), 'targeted_df')
    _require_columns(blocks_gdf, ('block_geoid', 'block_group_geoid', 'geometry'), 'Blocks GeoDataFrame')
    _require_columns(npl_gdf, (NPL_EPA_ID_COLUMN, 'geometry'), 'NPL GeoDataFrame')
    if blocks_gdf.crs is None or npl_gdf.crs is None:
        raise RuntimeError('Projected inputs must have CRS defined before distance calculations')
    if blocks_gdf.crs != npl_gdf.crs:
        raise RuntimeError(f'Blocks and NPL inputs must share the same projected CRS: blocks={blocks_gdf.crs}, npl={npl_gdf.crs}')

    logging.info("##############################################################")
    logging.info("#### Starting Step2: computing block-site distances")

    # Use EPA_ID values as provided (strip whitespace only)
    targeted_df = targeted_df.copy()
    targeted_df['EPA_ID'] = targeted_df['EPA_ID'].astype(str).str.strip()
    target_bgs = set(targeted_df['GEOID_BG'].astype(str).unique())
    gdf_blocks_sub = blocks_gdf[blocks_gdf['block_group_geoid'].astype(str).isin(target_bgs)].copy()
    logging.info('Blocks in targeted block groups: %d (of %d)', len(gdf_blocks_sub), len(blocks_gdf))

    if gdf_blocks_sub.empty:
        logging.info('No blocks found in targeted block groups; nothing to do for Step 2')
        return pd.DataFrame()

    npl_lookup = npl_gdf[[NPL_EPA_ID_COLUMN, 'geometry']].copy()
    npl_lookup[NPL_EPA_ID_COLUMN] = npl_lookup[NPL_EPA_ID_COLUMN].astype(str).str.strip()
    npl_map = {row[NPL_EPA_ID_COLUMN]: row.geometry for _, row in npl_lookup.iterrows()}
    logging.info('NPL EPA keys: %d', len(npl_map))

    # For each targeted pair (BG, EPA_ID), compute distances for all blocks in that BG
    records = []
    # group blocks by block_group_geoid for faster access
    blocks_by_bg = {bg: df for bg, df in gdf_blocks_sub.groupby('block_group_geoid')}

    # Counters for diagnostics: how many targeted rows processed and EPA IDs matched
    rows_processed = 0
    ids_found = 0
    ids_not_found = 0

    for _, row in targeted_df.iterrows():
        rows_processed += 1
        bg = str(row['GEOID_BG'])
        epa = str(row['EPA_ID'])
        if bg not in blocks_by_bg:
            continue
        blocks = blocks_by_bg[bg]
        # match on EPA_ID exactly (strip only)
        epa_key = epa.strip()
        poly = npl_map.get(epa_key)
        if poly is None:
            if ids_not_found < 5:  # limit logging to first 5 missing IDs
                logging.warning('EPA ID %s not found in NPL polygons; skipping', epa)
            ids_not_found += 1
            continue
        ids_found += 1
        # Changed this from calculating distance to the boundary
        # to calculating distance to the polygon itself, which should 
        # more accurate for blocks that are inside the polygon (distance=0).
        # Note that this didn't change any distances for the small sample (Montana)
        # I'm currently working with. But Gemini indicated it could be a material improvement.
        # boundary = poly.boundary if hasattr(poly, 'boundary') else poly
        # compute per-block distance (meters) and store meters
        for _, b in blocks.iterrows():
            try:
                dist_m = b.geometry.distance(poly)
            except Exception as e:
                logging.error('Distance computation failed for block %s to EPA %s: %s', b.get('block_geoid'), epa, e)
                dist_m = float('nan')
            records.append({'GEOID_BLOCK': b['block_geoid'], 'EPA_ID': epa, 'distance_m': dist_m if pd.notna(dist_m) else None})

    # Diagnostic summary
    logging.info('Step2 summary: rows_processed=%d, ids_found=%d, ids_not_found=%d', rows_processed, ids_found, ids_not_found)

    df_out = pd.DataFrame.from_records(records)
    # NOTE: do not write distances here — postpone until Step 3 where
    # proximity scores will be computed and the CSV will be written.
    logging.info('Step2 produced %d distance records (distance in meters)', len(df_out))
    return df_out


def step3_inverse_distance_scoring(distances_df: pd.DataFrame, write_csv: bool = True) -> pd.DataFrame:
    """Step 3: Convert distance (meters) to inverse-distance proximity score.

    - Proximity score = 1 / distance_in_km (distance_km = distance_m / 1000).
    - If distance < 0.1 km (i.e. < 100 meters) or distance <= 0, score is capped at 11.
    - If distance is missing/NaN, `proximity_score` will be None.
    - When `write_csv` is True, writes `BLOCK_SITE_DISTANCES_CSV` under `OUTPUT_DIR`
      with the proximity score as the last column.
    """
    if distances_df is None:
        raise RuntimeError('distances_df is required')

    # Ensure expected column exists
    if 'distance_m' not in distances_df.columns:
        raise RuntimeError("Expected 'distance_m' column in distances DataFrame")

    distances_df = distances_df.copy()

    def calculate_proximity_score(d):
        try:
            if pd.isna(d):
                return None
            d = float(d)
        except Exception:
            return None
        # distance in meters -> convert to km
        km = d / 1000.0
        # proximity score is inverse of distance in km
        if km < 0.1:
            return 11.0

        proximity_score = 1.0 / km
        # No further capping specified beyond the <0.1km rule
        return float(proximity_score)

    distances_df['proximity_score'] = distances_df['distance_m'].apply(calculate_proximity_score)

    if write_csv:
        _validate_output_dir()
        out_path = Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure proximity_score is last column in output — reorder if necessary
        cols = [c for c in distances_df.columns if c != 'proximity_score'] + ['proximity_score']
        logging.info('Writing block-site distances with proximity_score to %s (rows=%d)', out_path, len(distances_df))
        distances_df.to_csv(out_path, index=False, columns=cols)

    return distances_df


def export_block_details_for_block_groups(
    block_group_geoids,
    blocks_gdf: gpd.GeoDataFrame,
    distances_df: pd.DataFrame,
    out_csv: str = None,
    write_csv: bool = True,
) -> pd.DataFrame:
    """Export detailed per-block rows for the provided block-group GEOIDs.

    - block_group_geoids: iterable of block-group GEOID strings (12-char strings expected).
    - blocks_gdf: prepared GeoDataFrame from Step 0.
    - distances_df: DataFrame from Step 2 or Step 3.
    - out_csv: optional output filename; defaults to OUTPUT_DIR/detailed_blocks_for_bgs.csv.
    """
    if blocks_gdf is None:
        raise RuntimeError('blocks_gdf is required')
    if distances_df is None:
        raise RuntimeError('distances_df is required')

    # Normalize and validate block_group_geoids
    if not block_group_geoids:
        raise RuntimeError('Please supply one or more block-group GEOIDs to export')
    bg_set = set(str(g).strip() for g in block_group_geoids)

    distances_df = distances_df.copy()

    # Ensure expected distance columns exist (or will be added)
    # Cast numeric columns where appropriate
    _require_columns(distances_df, ('GEOID_BLOCK',), 'distances_df')
    for col in ('distance_m', 'proximity_score'):
        if col in distances_df.columns:
            distances_df[col] = pd.to_numeric(distances_df[col], errors='coerce')
    if 'proximity_score' not in distances_df.columns:
        distances_df['proximity_score'] = pd.NA

    df_blocks = pd.DataFrame(blocks_gdf.drop(columns='geometry', errors='ignore')).copy()
    _require_columns(
        df_blocks,
        ('block_geoid', 'block_group_geoid', 'block_group_pop', 'block_pop', 'fraction_of_total'),
        'Blocks GeoDataFrame',
    )
    df_blocks['block_geoid'] = df_blocks['block_geoid'].astype(str).str.strip()
    df_blocks['block_group_geoid'] = df_blocks['block_group_geoid'].astype(str).str.strip()
    for col in ('block_group_pop', 'block_pop', 'fraction_of_total'):
        df_blocks[col] = pd.to_numeric(df_blocks[col], errors='raise')

    # Filter to requested block groups
    df_sel = df_blocks[df_blocks['block_group_geoid'].isin(bg_set)].copy()
    missing_bgs = bg_set - set(df_sel['block_group_geoid'].unique())
    if missing_bgs:
        logging.warning('The following requested block-groups were not found in blocks data: %s', sorted(missing_bgs))

    if df_sel.empty:
        logging.info('No blocks found for requested block-groups; returning empty DataFrame')
        cols = ['block_group_geoid', 'block_geoid', 'block_group_pop', 'block_pop', 'fraction_of_total', 'EPA_ID', 'distance_m', 'proximity_score', 'weighted_score']
        return pd.DataFrame(columns=cols)

    # Left join blocks to distances so zero-pop blocks without distances are kept
    merged = df_sel.merge(distances_df, left_on='block_geoid', right_on='GEOID_BLOCK', how='left', suffixes=('', '_dist'))

    # Ensure numeric columns
    if 'distance_m' in merged.columns:
        merged['distance_m'] = pd.to_numeric(merged['distance_m'], errors='coerce')
    if 'proximity_score' in merged.columns:
        merged['proximity_score'] = pd.to_numeric(merged['proximity_score'], errors='coerce')

    # Compute weighted_score: where proximity_score is present, multiply by fraction_of_total
    # If proximity_score is missing, weighted_score will be 0.0 (no contribution)
    merged['weighted_score'] = merged['proximity_score'].fillna(0.0) * merged['fraction_of_total'].astype(float)

    # Select and order desired columns
    out_cols = [
        'block_group_geoid',
        'block_geoid',
        'block_group_pop',
        'block_pop',
        'fraction_of_total',
        'EPA_ID',
        'distance_m',
        'proximity_score',
        'weighted_score',
    ]

    # Ensure all out_cols exist in DataFrame
    for c in out_cols:
        if c not in merged.columns:
            merged[c] = pd.NA

    result = merged[out_cols].copy()

    # Write CSV if requested
    if write_csv:
        _validate_output_dir()
        out_path = Path(out_csv) if out_csv else Path(OUTPUT_DIR) / 'detailed_blocks_for_bgs.csv'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logging.info('Writing detailed block rows for %d block-groups to %s (rows=%d)', len(bg_set), out_path, len(result))
        result.to_csv(out_path, index=False)

    return result


def step4_population_weighting_aggregation(
    distances_with_scores_df: pd.DataFrame,
    blocks_gdf: gpd.GeoDataFrame,
    write_csv: bool = True,
) -> pd.DataFrame:
    """Step 4: Weight block proximity scores by population fraction and aggregate to Block Group.

    - Joins on block GEOID, multiplies `proximity_score` by population weight (fraction_of_total),
        then sums weighted scores per block group (summing across EPA sites as well).
    - Writes `FINAL_BG_SCORES_CSV` under `OUTPUT_DIR` with columns: `block_group_geoid`,
        `weighted_score`, where `weighted_score` is rounded to 4 decimal places after
        block-level scores have been summed to the block-group level.
    - Final output includes every valid block group present in the prepared blocks GeoDataFrame;
        block groups that never appear in the targeted scoring path receive `weighted_score = 0`.
    """
    if distances_with_scores_df is None:
        raise RuntimeError('distances_with_scores_df is required')
    if blocks_gdf is None:
        raise RuntimeError('blocks_gdf is required')

    # Ensure required columns
    if 'GEOID_BLOCK' not in distances_with_scores_df.columns:
        raise RuntimeError("Expected 'GEOID_BLOCK' column in distances DataFrame")
    if 'proximity_score' not in distances_with_scores_df.columns:
        raise RuntimeError("Expected 'proximity_score' column in distances DataFrame")

    blocks_df = pd.DataFrame(blocks_gdf.drop(columns='geometry', errors='ignore')).copy()
    _require_columns(blocks_df, ('block_geoid', 'block_group_geoid', 'fraction_of_total'), 'Blocks GeoDataFrame')

    # Coerce proximity_score to numeric (could contain None/empty)
    distances_with_scores_df = distances_with_scores_df.copy()
    distances_with_scores_df['proximity_score'] = pd.to_numeric(distances_with_scores_df['proximity_score'], errors='coerce')

    blocks_df['fraction_of_total'] = pd.to_numeric(blocks_df['fraction_of_total'], errors='raise')
    blocks_df['block_group_geoid'] = blocks_df['block_group_geoid'].astype('string').str.strip()

    all_block_groups = blocks_df[['block_group_geoid']].copy()
    all_block_groups['block_group_geoid'] = all_block_groups['block_group_geoid'].astype('string').str.strip()
    all_block_groups = all_block_groups[
        all_block_groups['block_group_geoid'].notna() &
        all_block_groups['block_group_geoid'].ne('')
    ].drop_duplicates().reset_index(drop=True)
    logging.info('Prepared %d block groups from blocks source for final output universe', len(all_block_groups))

    # Merge targeted distance rows with block weights
    merged = distances_with_scores_df.merge(
        blocks_df[['block_geoid', 'block_group_geoid', 'fraction_of_total']],
        left_on='GEOID_BLOCK',
        right_on='block_geoid',
        how='left',
    )

    # Drop records without a proximity score
    merged = merged[merged['proximity_score'].notna()].copy()

    # Coerce weight to numeric and fill missing weights with 0
    merged['fraction_of_total'] = pd.to_numeric(merged['fraction_of_total'], errors='coerce').fillna(0.0)

    # Compute weighted score per record
    merged['weighted_score'] = merged['proximity_score'].astype(float) * merged['fraction_of_total'].astype(float)

    # Aggregate to block group: sum weighted_score (this naturally sums across multiple EPA sites)
    agg_targeted = merged.groupby('block_group_geoid', dropna=True)['weighted_score'].sum().reset_index()
    agg_targeted['weighted_score'] = agg_targeted['weighted_score'].round(4)

    # Expand final output to all block groups from the blocks source; untargeted groups remain zero.
    agg = all_block_groups.merge(agg_targeted, on='block_group_geoid', how='left')
    agg['weighted_score'] = agg['weighted_score'].fillna(0.0)

    if write_csv:
        _validate_output_dir()
        out_path = Path(OUTPUT_DIR) / FINAL_BG_SCORES_CSV
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logging.info(
            'Writing final block-group scores to %s (rows=%d, targeted_groups=%d)',
            out_path,
            len(agg),
            len(agg_targeted),
        )
        agg.to_csv(out_path, index=False, columns=['block_group_geoid', 'weighted_score'])

    return agg



if __name__ == '__main__':
    logging.info('Running Step 0 input preparation')

    npl_gdf, bg_gdf, blocks_gdf = step0_prepare_inputs()

    # Step 1: Buffer NPL sites and find intersecting block groups
    targeted = step1_buffer_and_targeted_bgs(npl_gdf=npl_gdf, bg_gdf=bg_gdf)

    # Sanity checks
    logging.info('Sample EPA_IDS (first 20): %s', targeted['EPA_ID'].astype(str).head(20).tolist())
    logging.info('Unique EPA_ID count: %d', targeted['EPA_ID'].astype(str).nunique())


    # Step 2: For blocks in targeted block groups, compute distance to each NPL polygon
    distances = step2_block_site_distances(targeted_df=targeted, blocks_gdf=blocks_gdf, npl_gdf=npl_gdf)

    # Step 3: Compute inverse-distance proximity scores and write distances CSV
    distances_with_scores = step3_inverse_distance_scoring(distances, write_csv=True)
    logging.info('Wrote %s with proximity_score column', Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV)

    # Step 4: Weight block proximity scores by population fraction and aggregate to Block Group
    final_bg_scores = step4_population_weighting_aggregation(distances_with_scores, blocks_gdf=blocks_gdf, write_csv=True)
    logging.info('Wrote %s with block-group weighted scores', Path(OUTPUT_DIR) / FINAL_BG_SCORES_CSV)

    # For detailed debugging: export detailed block rows for specific block-group GEOIDs
    # if EXPORT_BG_LIST:
    #   export_result = export_block_details_for_block_groups(EXPORT_BG_LIST, blocks_gdf=blocks_gdf, distances_df=distances_with_scores, out_csv=None, write_csv=True)
