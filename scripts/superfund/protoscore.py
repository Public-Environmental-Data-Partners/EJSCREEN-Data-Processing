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
import re

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


def normalize_epa_id(val: object) -> str:
    """Normalize EPA ID-like values for safe comparisons.

    - convert to str, strip whitespace
    - remove trailing decimal ".0" sequences
    - lowercase for case-insensitive matching
    """
    if pd.isna(val):
        return ""
    s = str(val).strip()
    s = re.sub(r'\.0+$', '', s)
    return s.lower()


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
         print("NPL layers:", layers)
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
    # Normalize EPA IDs to a canonical form so downstream matching is consistent
    targeted['EPA_ID'] = targeted['EPA_ID'].astype(str).apply(normalize_epa_id)

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
    # Normalize targeted EPA IDs (ensure consistent format)
    targeted_df['EPA_ID'] = targeted_df['EPA_ID'].astype(str).apply(normalize_epa_id)
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

    # detect which column in npl_gdf corresponds to EPA IDs listed in targeted_df
    tgt_epa_vals = set(targeted_df['EPA_ID'].astype(str).unique())
    epa_col = None
    # Try to detect which NPL column contains the same kinds of IDs by
    # normalizing values from each candidate column and checking intersection
    for c in npl_gdf.columns:
        try:
            sample_vals = npl_gdf[c].astype(str).head(500).apply(normalize_epa_id).unique()
            vals = set(sample_vals)
        except Exception:
            continue
        if vals & set(list(tgt_epa_vals)[:500]):
            epa_col = c
            break
    if epa_col is None:
        # fallback: prefer explicit EPA ID-like columns first, then broader matches
        candidates_exact = ['EPA_ID', 'EPA ID', 'EPAID', 'SITE_ID', 'SITEID']
        epa_col = next((c for c in npl_gdf.columns if c.upper() in [e.upper() for e in candidates_exact]), None)
        if epa_col is None:
            # prefer columns containing 'epa_id' or both 'epa' and 'id' in the name
            for c in npl_gdf.columns:
                low = c.lower()
                if 'epa_id' in low or ('epa' in low and 'id' in low):
                    epa_col = c
                    break
        if epa_col is None:
            # last resort: any column with 'epa' or 'site' in the name
            for c in npl_gdf.columns:
                low = c.lower()
                if 'epa' in low or 'site' in low:
                    epa_col = c
                    break
    if epa_col is None:
        raise RuntimeError('Could not identify EPA ID column in NPL data')
    logging.info('Using NPL EPA ID column: %s', epa_col)

    # Normalize function for EPA IDs (remove trailing .0, strip, lowercase)
    # Build a mapping from normalized EPA_ID -> polygon geometry
    npl_gdf['EPA_ID_STR'] = npl_gdf[epa_col].astype(str).apply(normalize_epa_id)
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
        # normalize targeted EPA ID to match NPL keys
        epa_norm = normalize_epa_id(epa)
        poly = npl_map.get(epa_norm)
        if poly is None:
            if ids_not_found < 5:  # limit logging to first 5 missing IDs
                logging.warning('EPA ID %s (normalized %s) not found in NPL polygons; skipping', epa, epa_norm)
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
    out_path = Path(OUTPUT_DIR) / BLOCK_SITE_DISTANCES_CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logging.info('Writing block-site distances to %s (rows=%d) [distance in meters]', out_path, len(df_out))
    df_out.to_csv(out_path, index=False)

    return df_out



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
    print("Sample EPA_IDS (first 20):")
    print(targeted['EPA_ID'].astype(str).head(20).tolist())
    print("Unique EPA_ID count:", targeted['EPA_ID'].astype(str).nunique())


    # Step 2: For blocks in targeted block groups, compute distance to each NPL polygon
    distances = step2_block_site_distances(targeted_df=targeted)
