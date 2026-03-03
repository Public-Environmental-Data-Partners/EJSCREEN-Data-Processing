"""Prototype script for quick scoring prototype (guerrilla mode).

Global input/output filenames (fill these in before running):
- NPL_GDB_PATH: path to the NPL site boundaries geodatabase (.gdb)
- BG_SHP_PATH: path to the Census Block Group shapefile (directory or .shp)
- BLOCKS_SHAPE_OR_CSV: path to block centroids (point shapefile or CSV)

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
import geopandas as gpd
import fiona
import pandas as pd


# --- Global file path variables (fill these before running) -----------------
# Input sources
NPL_GDB_PATH = "./pipeline/test_data/downloads/NPL_Boundaries_20260217/NPL_Boundaries.gdb"  # e.g. '/mnt/c/.../NPL_Boundaries.gdb'
BG_SHP_PATH = "./pipeline/test_data/downloads/tl_2020_30_bg.zip"   # e.g. '/mnt/c/.../tl_2020_XX_bg.shp' or folder containing .shp
# It's not entirely clear yet if this is the right file / shape of data but it's my best guess``
BLOCKS_SHAPE_OR_CSV = "./pipeline/test_data/downloads/census_block_weights_2020_MT.csv"  # e.g. '/mnt/c/.../blocks_centroids.csv' or .shp

# Working/output directory
OUTPUT_DIR = "./pipeline/test_data"  # e.g. '/mnt/c/.../protoscore_output/'

# Interim output filenames (placed under OUTPUT_DIR)
TARGETED_BG_CSV = "targeted_block_groups.csv"
BLOCK_SITE_DISTANCES_CSV = "block_site_distances.csv"
FINAL_BG_SCORES_CSV = "final_bg_scores.csv"

# Logging default
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def _validate_paths():
    """Basic validation to ensure essential globals are set before running."""
    missing = []
    for name in ('NPL_GDB_PATH', 'BG_SHP_PATH', 'BLOCKS_SHAPE_OR_CSV', 'OUTPUT_DIR'):
        if not globals().get(name):
            missing.append(name)
    if missing:
        raise RuntimeError(f"Please set the following path variables before running: {', '.join(missing)}")
    
def normalize_census_columns(df):
        # Detect which year suffix is present (10 or 20)
    # We look for a unique indicator like 'POP'
    suffix = ""
    if any("20" in col for col in df.columns if "POP" in col):
        suffix = "20"
    elif any("10" in col for col in df.columns if "POP" in col):
        suffix = "10"
    else:
        # raise an error if expected columns aren't found
        raise ValueError("Could not detect Census year suffix in columns. Expected columns like 'POP10' or 'POP20' not found.")
        return df  # defensive return; in practice we would likely want to fail hard here
    
    # Map versioned names to normalized names
    rename_map = {
        f"GEOID{suffix}":  "block_geoid",
        f"INTPTLAT{suffix}": "block_lat",
        f"INTPTLON{suffix}": "block_lon",
        f"POP{suffix}":    "block_pop",
        f"ALAND{suffix}":  "block_aland",
        f"AWATER{suffix}": "block_awater",
    }
    
    return df.rename(columns=rename_map)





def step1_buffer_and_targeted_bgs(buffer_meters: float = 10000.0):
    """Step 1: Buffer NPL polygons and find intersecting Block Groups.

    Reads `NPL_GDB_PATH` and `BG_SHP_PATH`, reprojects both to EPSG:5070,
    builds a buffer around NPL site boundaries (`buffer_meters`), performs
    a spatial join to find Block Groups intersecting those buffers, writes
    `TARGETED_BG_CSV` under `OUTPUT_DIR`.
    
    Returns the resulting DataFrame.
    """
    _validate_paths()

    logging.info("#### Starting Step1: reading NPL geodatabase: %s", NPL_GDB_PATH)
    layers = fiona.listlayers(NPL_GDB_PATH)
    if not layers:
        raise RuntimeError(f"No layers found in NPL GDB: {NPL_GDB_PATH}")
    else:
        logging.info('NPL layers: %s', layers)
    npl_layer = layers[3]  # SITE_BOUNDARIES_SF
    logging.info("Going to use NPL layer: %s", npl_layer)
    npl_gdf = gpd.read_file(NPL_GDB_PATH, layer='SITE_BOUNDARIES_SF')
    logging.info("NPL columns: %d, rows: %d", len(npl_gdf.columns), len(npl_gdf))
   
    logging.info("Reading Block Group shapefile: %s", BG_SHP_PATH)
    try:
        bg_gdf = gpd.read_file(BG_SHP_PATH)
    except Exception:
        logging.warning("Failed to read BG shapefile directly, attempting to read as zip archive...")
        if str(BG_SHP_PATH).lower().endswith('.zip'):
            bg_gdf = gpd.read_file(f"zip://{BG_SHP_PATH}")
        else:
            raise
    logging.info("Block Group columns: %d, rows: %d", len(bg_gdf.columns), len(bg_gdf))

    # Reproject both to EPSG:5070 for metric operations
    target_crs = 'EPSG:5070'
    if npl_gdf.crs is None or bg_gdf.crs is None:
        raise RuntimeError('Source data must have CRS defined')
    npl_proj = npl_gdf.to_crs(target_crs)
    bg_proj = bg_gdf.to_crs(target_crs)

    # Buffer NPL polygons
    logging.info('Buffering NPL polygons by %s meters', buffer_meters)
    npl_proj['geometry_buffer'] = npl_proj.geometry.buffer(buffer_meters)
    npl_buffer = npl_proj.set_geometry('geometry_buffer')

    # Spatial join: block groups that intersect any buffered NPL polygon
    logging.info('Performing spatial join to find targeted block groups')
    joined = gpd.sjoin(bg_proj, npl_buffer, how='inner', predicate='intersects')
    if joined.empty:
        logging.info('No block groups found within buffer distance')
        return pd.DataFrame()

    # TODO: simplify to known column name
    # Determine a GEOID column in block groups
    geoid_candidates = ['GEOID', 'GEOID20', 'GEOID_BG', 'GEOID_BG20', 'geoid']
    geoid_col = next((c for c in geoid_candidates if c in joined.columns), None)
    if geoid_col is None:
        # pick the first object column with length >=12
        for c in joined.columns:
            if joined[c].dtype == object and joined[c].astype(str).str.len().max() >= 12:
                geoid_col = c
                break
    if geoid_col is None:
        raise RuntimeError('Could not determine block-group GEOID column')

    # TODO: simplify to known column name
    # Determine EPA ID column from NPL layer
    epa_candidates = ['EPA ID', 'EPA_ID', 'EPAID', 'SITE_ID', 'SITEID']
    epa_col = next((c for c in npl_proj.columns if c in epa_candidates), None)
    if epa_col is None:
        epa_col = next((c for c in npl_proj.columns if 'epa' in c.lower() or 'site' in c.lower()), None)
    if epa_col is None:
        # fallback to index
        epa_col = npl_proj.index.name or 'npl_index'
        npl_proj = npl_proj.reset_index().rename(columns={'index': epa_col})

    targeted = joined[[geoid_col, epa_col]].drop_duplicates().copy()
    targeted = targeted.rename(columns={geoid_col: 'GEOID_BG', epa_col: 'EPA_ID'})
    # Keep EPA_ID values as provided (exact match on `EPA_ID` column)
    targeted['EPA_ID'] = targeted['EPA_ID'].astype(str).str.strip()

    out_path = Path(OUTPUT_DIR) / TARGETED_BG_CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Writing targeted block groups to %s', out_path)
    targeted[['GEOID_BG', 'EPA_ID']].to_csv(out_path, index=False)

    return targeted


def step2_block_site_distances(targeted_df: pd.DataFrame = None, npl_layer: str = 'SITE_BOUNDARIES_SF') -> pd.DataFrame:
    """Step 2: For blocks inside targeted block groups, compute distance to each NPL polygon.

    Workflow:
      - Read (or accept) `targeted_df` containing `GEOID_BG` and `EPA_ID` (from step1).
      - Read block centroids from `BLOCKS_SHAPE_OR_CSV` (CSV or shapefile) and normalize column names.
      - Keep only blocks where `block_geoid`'s first 12 chars are in targeted GEOIDs.
      - Read NPL polygon layer `npl_layer` and detect the column that contains the EPA IDs present in `targeted_df`.
    - For each (GEOID_BG, EPA_ID) pair, compute the shortest distance from each block centroid in that BG to the polygon boundary (meters).
    - Write `BLOCK_SITE_DISTANCES_CSV` with columns: `GEOID_BLOCK`, `EPA_ID`, `distance_m`.

    Returns the produced DataFrame.
    """
    _validate_paths()

    logging.info("##############################################################")
    logging.info("#### Starting Step2: computing block-site distances")

    # Load targeted pairs if not provided
    if targeted_df is None:
        tgt_path = Path(OUTPUT_DIR) / TARGETED_BG_CSV
        if not tgt_path.exists():
            raise RuntimeError(f"Targeted BG CSV not found at {tgt_path}; run step1 first or provide targeted_df")
        targeted_df = pd.read_csv(tgt_path, dtype=str)
    # Use EPA_ID values as provided (strip whitespace only)
    targeted_df['EPA_ID'] = targeted_df['EPA_ID'].astype(str).str.strip()
    # Prepare a set of targeted EPA IDs (kept for reference)
    t_ids = set(targeted_df['EPA_ID'].unique())

    # Read block centroids (CSV or shapefile)
    blk_path = Path(BLOCKS_SHAPE_OR_CSV)
    logging.info('Reading block centroids: %s', blk_path)
    if blk_path.suffix.lower() in ('.csv', '.txt'):
        df_blocks = pd.read_csv(blk_path, dtype=str)
        # attempt to normalize census columns if they match census naming
        try:
            df_blocks = normalize_census_columns(df_blocks)
        except Exception:
            # If normalization fails, continue — user may have already normalized
            pass

        # require block_geoid and coordinates
        if not {'block_geoid', 'block_lat', 'block_lon'}.issubset(df_blocks.columns):
            raise RuntimeError('Block CSV must contain block_geoid, block_lat, block_lon (or be normalizable)')

        # build GeoDataFrame in WGS84 then project
        gdf_blocks = gpd.GeoDataFrame(
            df_blocks.copy(),
            geometry=gpd.points_from_xy(df_blocks['block_lon'].astype(float), df_blocks['block_lat'].astype(float)),
            crs='EPSG:4326'
        )
    else:
        # shapefile or other vector format
        gdf_blocks = gpd.read_file(str(blk_path))
        # try to normalize column names if needed
        try:
            gdf_blocks = normalize_census_columns(gdf_blocks)
        except Exception:
            pass
        if 'block_geoid' not in gdf_blocks.columns:
            raise RuntimeError('Block shapefile must have block_geoid (or be normalizable)')

    # create block_group_geoid and filter to targeted BGs
    gdf_blocks['block_group_geoid'] = gdf_blocks['block_geoid'].astype(str).str[:12]
    target_bgs = set(targeted_df['GEOID_BG'].astype(str).unique())
    gdf_blocks_sub = gdf_blocks[gdf_blocks['block_group_geoid'].isin(target_bgs)].copy()
    logging.info('Blocks in targeted block groups: %d (of %d)', len(gdf_blocks_sub), len(gdf_blocks))

    if gdf_blocks_sub.empty:
        logging.info('No blocks found in targeted block groups; nothing to do for Step 2')
        return pd.DataFrame()

    # project blocks to metric CRS
    target_crs = 'EPSG:5070'
    if gdf_blocks_sub.crs is None:
        # assume WGS84 if no CRS
        gdf_blocks_sub = gdf_blocks_sub.set_crs('EPSG:4326')
    gdf_blocks_sub = gdf_blocks_sub.to_crs(target_crs)

    # Read NPL polygon layer and reproject
    logging.info('Reading NPL polygon layer: %s', npl_layer)
    npl_gdf = gpd.read_file(NPL_GDB_PATH, layer=npl_layer)
    if npl_gdf.crs is None:
        raise RuntimeError('NPL layer has no CRS')
    npl_gdf = npl_gdf.to_crs(target_crs)

    # Use the known exact column name `EPA_ID` in the NPL data; fail fast if absent
    if 'EPA_ID' not in npl_gdf.columns:
        raise RuntimeError("Expected 'EPA_ID' column in NPL data (not found)")
    epa_col = 'EPA_ID'
    logging.info('Using NPL EPA ID column: %s', epa_col)

    # Normalize function for EPA IDs (remove trailing .0, strip, lowercase)
    # Build a mapping from EPA_ID (as-is) -> polygon geometry
    npl_gdf['EPA_ID_STR'] = npl_gdf[epa_col].astype(str).str.strip()
    npl_map = {row['EPA_ID_STR']: row.geometry for _, row in npl_gdf.iterrows()}
    logging.info('NPL EPA keys (normalized): %d', len(npl_map))

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
        # compute distance to polygon boundary (use polygon.boundary)
        boundary = poly.boundary if hasattr(poly, 'boundary') else poly
        # compute per-block distance (meters) and store meters
        for _, b in blocks.iterrows():
            try:
                dist_m = b.geometry.distance(boundary)
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


def step3_inverse_distance_scoring(distances_df: pd.DataFrame = None, write_csv: bool = True) -> pd.DataFrame:
    """Step 3: Convert distance (meters) to inverse-distance proximity score.

    - Proximity score = 1 / distance_in_km (distance_km = distance_m / 1000).
    - If distance < 0.1 km (i.e. < 100 meters) or distance <= 0, score is capped at 11.
    - If distance is missing/NaN, `proximity_score` will be None.
    - When `write_csv` is True, writes `BLOCK_SITE_DISTANCES_CSV` under `OUTPUT_DIR`
      with the proximity score as the last column.
    """
    _validate_paths()

    if distances_df is None:
        # Try to load distances produced by Step 2
        path = Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV
        if not path.exists():
            raise RuntimeError('No distances DataFrame provided and distances CSV not found; run step2 first')
        distances_df = pd.read_csv(path, dtype={})

    # Ensure expected column exists
    if 'distance_m' not in distances_df.columns:
        raise RuntimeError("Expected 'distance_m' column in distances DataFrame")

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
        out_path = Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure proximity_score is last column in output — reorder if necessary
        cols = [c for c in distances_df.columns if c != 'proximity_score'] + ['proximity_score']
        logging.info('Writing block-site distances with proximity_score to %s (rows=%d)', out_path, len(distances_df))
        distances_df.to_csv(out_path, index=False, columns=cols)

    return distances_df


def step4_population_weighting_aggregation(distances_with_scores_df: pd.DataFrame = None, blocks_path: str = None, write_csv: bool = True) -> pd.DataFrame:
    """Step 4: Weight block proximity scores by population fraction and aggregate to Block Group.

    - Reads `distances_with_scores_df` (or loads from `BLOCK_SITE_DISTANCES_CSV`).
    - Loads block-level population weights from `blocks_path` (or `BLOCKS_SHAPE_OR_CSV`).
    - Joins on block GEOID, multiplies `proximity_score` by population weight (fraction_of_total),
      then sums weighted scores per block group (summing across EPA sites as well).
    - Writes `FINAL_BG_SCORES_CSV` under `OUTPUT_DIR` with columns: `GEOID_BG`, `proximity_score`.
    """
    _validate_paths()

    # Load distances with scores
    if distances_with_scores_df is None:
        path = Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV
        if not path.exists():
            raise RuntimeError('No distances DataFrame provided and distances CSV not found; run step3 first')
        distances_with_scores_df = pd.read_csv(path, dtype=str)

    # Ensure required columns
    if 'GEOID_BLOCK' not in distances_with_scores_df.columns:
        raise RuntimeError("Expected 'GEOID_BLOCK' column in distances DataFrame")
    if 'proximity_score' not in distances_with_scores_df.columns:
        raise RuntimeError("Expected 'proximity_score' column in distances DataFrame")

    # Coerce proximity_score to numeric (could contain None/empty)
    distances_with_scores_df['proximity_score'] = pd.to_numeric(distances_with_scores_df['proximity_score'], errors='coerce')

    # Load block population weights
    blk_path = blocks_path or BLOCKS_SHAPE_OR_CSV
    if blk_path is None:
        raise RuntimeError('No blocks_path provided and BLOCKS_SHAPE_OR_CSV is unset')

    blk_p = Path(blk_path)
    logging.info('Reading block population/weight data: %s', blk_p)
    if blk_p.suffix.lower() in ('.csv', '.txt') or not blk_p.exists():
        # read as CSV (many test files are CSV)
        df_blocks = pd.read_csv(blk_p, dtype=str)
        try:
            df_blocks = normalize_census_columns(df_blocks)
        except Exception:
            pass
    else:
        # try vector file
        gdf_blocks = gpd.read_file(str(blk_p))
        try:
            gdf_blocks = normalize_census_columns(gdf_blocks)
        except Exception:
            pass
        df_blocks = pd.DataFrame(gdf_blocks.drop(columns=[c for c in gdf_blocks.columns if c == 'geometry']))

    # Identify or compute population fraction (weight)
    weight_col = None
    if 'fraction_of_total' in df_blocks.columns:
        df_blocks['fraction_of_total'] = pd.to_numeric(df_blocks['fraction_of_total'], errors='coerce')
        weight_col = 'fraction_of_total'
    else:
        # If fraction not present, try to compute from block_pop / block_group_pop
        raise RuntimeError('Population fraction column not found; computation from block_pop / block_group_pop not implemented in this prototype')

    if weight_col is None:
        raise RuntimeError('Could not find or compute population weight for blocks (expected column `fraction_of_total` or `block_pop`/`block_group_pop`)')

    # Ensure block geoid column exists
    if 'block_geoid' not in df_blocks.columns:
        # try GEOID-like columns
        geoid_col = next((c for c in df_blocks.columns if c.upper().startswith('GEOID')), None)
        if geoid_col is None:
            raise RuntimeError('Could not find block GEOID column in blocks data')
        df_blocks = df_blocks.rename(columns={geoid_col: 'block_geoid'})

    # Ensure block group geoid exists (for final grouping)
    if 'block_group_geoid' not in df_blocks.columns:
        df_blocks['block_group_geoid'] = df_blocks['block_geoid'].astype(str).str[:12]

    # Merge distances with block weights
    merged = distances_with_scores_df.merge(df_blocks[['block_geoid', 'block_group_geoid', weight_col]], left_on='GEOID_BLOCK', right_on='block_geoid', how='left')

    # Drop records without a proximity score
    merged = merged[merged['proximity_score'].notna()].copy()

    # Coerce weight to numeric and fill missing weights with 0
    merged[weight_col] = pd.to_numeric(merged[weight_col], errors='coerce').fillna(0.0)

    # Compute weighted score per record
    merged['weighted_score'] = merged['proximity_score'].astype(float) * merged[weight_col].astype(float)

    # Aggregate to block group: sum weighted_score (this naturally sums across multiple EPA sites)
    agg_weighted = merged.groupby('block_group_geoid', dropna=True)['weighted_score'].sum().reset_index()
    # Also compute the unweighted sum of proximity_score per block group and cap at 11
    agg_sumprox = merged.groupby('block_group_geoid', dropna=True)['proximity_score'].sum().reset_index()
    agg_sumprox['sum_proximity_scores'] = agg_sumprox['proximity_score'].clip(upper=11)
    # Merge weighted and summed proximity results
    agg = agg_weighted.merge(agg_sumprox[['block_group_geoid', 'sum_proximity_scores']], on='block_group_geoid', how='outer').fillna(0)

    if write_csv:
        out_path = Path(OUTPUT_DIR) / FINAL_BG_SCORES_CSV
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logging.info('Writing final block-group scores to %s (rows=%d)', out_path, len(agg))
        agg.to_csv(out_path, index=False, columns=['block_group_geoid', 'weighted_score', 'sum_proximity_scores'])

    return agg



if __name__ == '__main__':
    logging.info('protoscore prototype loaded — fill globals and import/run functions interactively')
    # Do not run any processing by default; this file is scaffolded for interactive use.
    try:
        _validate_paths()
    except RuntimeError as e:
        logging.info(str(e))
    else:
        logging.info('All path variables set — implement and call processing functions as needed')

    # Step 1: Buffer NPL sites and find intersecting block groups
    targeted = step1_buffer_and_targeted_bgs()

    # Sanity checks
    logging.info('Sample EPA_IDS (first 20): %s', targeted['EPA_ID'].astype(str).head(20).tolist())
    logging.info('Unique EPA_ID count: %d', targeted['EPA_ID'].astype(str).nunique())


    # Step 2: For blocks in targeted block groups, compute distance to each NPL polygon
    distances = step2_block_site_distances(targeted_df=targeted)

    # Step 3: Compute inverse-distance proximity scores and write distances CSV
    distances_with_scores = step3_inverse_distance_scoring(distances, write_csv=True)
    logging.info('Wrote %s with proximity_score column', Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV)

    # Step 4: Weight block proximity scores by population fraction and aggregate to Block Group
    final_bg_scores = step4_population_weighting_aggregation(distances_with_scores, blocks_path=BLOCKS_SHAPE_OR_CSV, write_csv=True)
    logging.info('Wrote %s with block-group weighted scores', Path(OUTPUT_DIR) / FINAL_BG_SCORES_CSV)
