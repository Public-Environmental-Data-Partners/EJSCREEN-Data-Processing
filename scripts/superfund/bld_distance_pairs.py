"""
bld_distance_pairs.py

Purpose:
 Generate raw distance pairs between NPL sites and Census Block centroids for one or more, 
 partitioning the output into two sets: those within a 5,000-meter buffer and those where the 
 block is more than 5,000 meters from any NPL site.

Credits:
  - Derived by Anne Gunn this project's template file
  - Designed with substantial help from Gemini
  - Implemented by GitHub Copilot, GPT-5 mini, and Anne Gunn

"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import logging
import io


# Third-party
import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from dotenv import load_dotenv
from pyarrow import input_stream

# Optional/cloud SDK; boto3 is required for S3 access. Import at module level
# so failures surface early. If boto3 is missing, operations that need it will
# raise NameError when attempted; this also makes debugging easier.
try:
    import boto3
except Exception as _e:  # pragma: no cover - environment dependent
    boto3 = None


# --- Configuration dataclass ---------------------------------------------
@dataclass
class Config:
    # default to our AWS S3 storage
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/"
    # for your code, default to S3 also, defaulting to local storage here for testing
    output_path: str = "./pipeline/test_data/"
    npl_locations_file: str = "/superfund_npl/pipeline/npl_sites_with_coords.csv"
    census_blocks_weights_base: str = "/census_tables/census_block_weights_2020"  # we'll add suffixes for different states
    short_pairs_filename: str = "pairs_le5000.csv"
    long_pairs_filename: str = "pairs_gt5000.csv"
    state_list: str = "MT" # defaulting to very! small list of states for now
    # a lot of scripts won't use the preamble-skipping feature, but it is handy
    # to have the option when you need it.
    skip_rows: int = 0  # very input specific; your value would likely be 0



# --- Runtime arguments and help ---------------------------------------------
def get_config(argv=None) -> Config:
    # Load environment variables from a .env file so boto3 can pick up AWS creds if present
    load_dotenv()

    # pick up runtime arguments to override defaults.
    parser = argparse.ArgumentParser(description="*** Generate distance pairs for NPL sites and Census Block centroids ***")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help=f'Folder containing input CSVs (default: {Config.input_path})')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help=f'Folder to write the output CSV (default: {Config.output_path})')
    parser.add_argument('--npl-locations', dest='npl_locations_file', default=Config.npl_locations_file,
                        help=f'Input NPL locations CSV filename (default: {Config.npl_locations_file})')
    parser.add_argument('--census-block-weights-base', dest='census_blocks_weights_base',
                        default=Config.census_blocks_weights_base,
                        help=f'Base filename/prefix for per-state census block weights (default: {Config.census_blocks_weights_base})')
    parser.add_argument('--state-list', dest='state_list', nargs='+', default=Config.state_list,
                        help=f'Space-separated list of 2-letter state abbreviations to process (default: {Config.state_list})')
    parser.add_argument('--short-pairs-filename', dest='short_pairs_filename', default=Config.short_pairs_filename,
                        help=f'Filename for aggregated <=5000m pairs CSV (default: {Config.short_pairs_filename})')
    # Note, no skip-rows or join-key arguments here.
    # They should generally be hard-coded, not changeable at runtime.

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        npl_locations_file=args.npl_locations_file,
        census_blocks_weights_base=args.census_blocks_weights_base,
        state_list=args.state_list,
        short_pairs_filename=args.short_pairs_filename,
    )



# --- S3/local path helpers -----------------------------------------------
def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
    """Join a path (S3 or local) with a filename into a usable path string."""
    if is_s3_uri(path):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)


def read_csv_s3_or_local(path: str, skip_rows: int) -> pd.DataFrame:
    """Read CSV either from local filesystem or from S3 (via boto3) depending on path.

    `path` may be a local path or an S3 URI like s3://bucket/prefix/file.csv
    """
    if is_s3_uri(path):
        # parse bucket/key
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]
        try:
            if boto3 is None:
                raise RuntimeError('boto3 not available; cannot read from S3')
            s3 = boto3.client('s3')
            obj = s3.get_object(Bucket=bucket, Key=key)
            return pd.read_csv(io.BytesIO(obj['Body'].read()), skiprows=skip_rows)
        except Exception as e:
            raise RuntimeError(f"Failed to read S3 CSV {path}: {e}") from e

    # local file
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Local CSV not found: {path}")
    return pd.read_csv(p, skiprows=skip_rows)


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
    """Write DataFrame to local file or upload to S3 depending on out_path."""
    if is_s3_uri(out_path):
        tail = out_path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {out_path}")
        bucket, key = parts[0], parts[1]
        try:
            if boto3 is None:
                raise RuntimeError('boto3 not available; cannot write to S3')
            s3 = boto3.client('s3')
            csv_text = df.to_csv(index=False)
            s3.put_object(Bucket=bucket, Key=key, Body=csv_text.encode('utf-8'))
            return
        except Exception as e:
            raise RuntimeError(f"Failed to write CSV to S3 {out_path}: {e}") from e

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_p, index=False)
    return

# --- Worker functions ----------------------------------------------------

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


def get_state_list(state_input) -> list:
    """Normalize a space-or-comma-separated state list or a list into a list of 2-letter codes."""
    if isinstance(state_input, list):
        raw = state_input
    elif isinstance(state_input, str):
        # allow comma or space separated
        raw = state_input.replace(',', ' ').split()
    else:
        return []
    # normalize to upper-case two-letter codes
    states = [s.strip().upper() for s in raw if isinstance(s, str) and s.strip()]
    return states


# --- GIS processing helpers ----------------------------------------------
def prepare_gdfs(df_state: pd.DataFrame, df_blocks: pd.DataFrame):
    """Prepare GeoDataFrames for NPL sites and block centroids in EPSG:5070.

    Expects:
      - df_state to have columns: 'EPA ID', 'Latitude', 'Longitude', 'Cweight'
      - df_blocks to have columns: 'id', 'lat', 'lng'
    """
    # verify required columns
    req_npl = {'EPA ID', 'Latitude', 'Longitude'}
    if not req_npl.issubset(df_state.columns):
        logging.error("df_state missing required columns: %s", req_npl - set(df_state.columns))
        return None, None

    # Expect normalized census column names (normalize_census_columns should be called earlier)
    required_block_cols = {'block_geoid', 'block_lat', 'block_lon'}
    if not required_block_cols.issubset(df_blocks.columns):
        missing = required_block_cols - set(df_blocks.columns)
        logging.error("df_blocks missing required normalized columns: %s", missing)
        return None, None

    # build GeoDataFrames in WGS84 then project to EPSG:5070 (meters)
    gdf_npl = gpd.GeoDataFrame(
        df_state.copy(),
        geometry=gpd.points_from_xy(df_state['Longitude'], df_state['Latitude']),
        crs='EPSG:4326'
    ).to_crs(epsg=5070)

    gdf_blocks = gpd.GeoDataFrame(
        df_blocks.copy(),
        geometry=gpd.points_from_xy(df_blocks['block_lon'], df_blocks['block_lat']),
        crs='EPSG:4326'
    ).to_crs(epsg=5070)

    # add projected X/Y for fast numeric distance calculations
    gdf_npl['nx'] = gdf_npl.geometry.x
    gdf_npl['ny'] = gdf_npl.geometry.y
    gdf_blocks['bx'] = gdf_blocks.geometry.x
    gdf_blocks['by'] = gdf_blocks.geometry.y

    return gdf_npl, gdf_blocks


def find_pairs_within(gdf_npl: gpd.GeoDataFrame, gdf_blocks: gpd.GeoDataFrame, radius_m: float = 5000.0) -> pd.DataFrame:
    """Find all block-NPL pairs where distance <= radius_m (meters).

    Returns a pandas.DataFrame with columns from both inputs and a `distance_m` column.
    This function uses a vectorized spatial join via buffered geometries to avoid Python loops.
    """
    if gdf_npl is None or gdf_blocks is None:
        return pd.DataFrame()

    # create buffer geometry around blocks for join; keep bx/by for numeric distance
    blocks_buf = gdf_blocks.copy()
    blocks_buf['geometry_buffer'] = blocks_buf.geometry.buffer(radius_m)
    blocks_for_join = blocks_buf.set_geometry('geometry_buffer')

    # spatial join: blocks (left) intersects NPL (right)
    joined = gpd.sjoin(blocks_for_join, gdf_npl, how='inner', predicate='intersects')
    if joined.empty:
        return pd.DataFrame()

    # joined contains block columns (including bx,by) and npl columns (including nx,ny)
    # compute Euclidean distance in projected meters
    joined['distance_m'] = np.hypot(joined['bx'] - joined['nx'], joined['by'] - joined['ny'])

    # keep only pairs truly within radius (defensive)
    pairs = joined[joined['distance_m'] <= radius_m].copy()

    # select and normalize output columns
    # Start with all original block columns (preserve their order), then add NPL columns, then distance
    block_cols = [c for c in gdf_blocks.columns if c not in ('geometry', 'bx', 'by')]
    # Keep only those block columns that actually exist in the joined pairs
    block_cols = [c for c in block_cols if c in pairs.columns]

    npl_cols = []
    for c in ('EPA ID', 'Latitude', 'Longitude', 'Cweight'):
        if c in pairs.columns:
            npl_cols.append(c)

    out_cols = npl_cols + ['distance_m'] + block_cols
    # final defensive filter to include only columns present
    out_cols = [c for c in out_cols if c in pairs.columns]
    return pairs[out_cols]


def write_pairs_csv(df_pairs: pd.DataFrame, cfg, state: str):
    """Write pairs DataFrame to CSV for the given state using existing writer helper."""
    if df_pairs is None or df_pairs.empty:
        logging.info("No pairs to write for %s", state)
        return
    filename = f"{state}_pairs_le5000.csv"
    out_path = join_path_and_file(cfg.output_path, filename)
    # ensure coordinate precision: format float columns to 6 decimals
    # Pandas to_csv will handle numeric formatting via float_format
    try:
        # use the helper to write; helper expects a DataFrame
        write_df_s3_or_local(df_pairs, out_path)
        logging.info("Wrote <=5000m pairs for %s to %s (rows=%d)", state, out_path, len(df_pairs))
    except Exception as e:
        logging.error("Failed to write pairs CSV for %s: %s", state, e)

def applyWeighting(block_aland, block_awater, distance_m, block_pop, fraction_of_total):
    # This code is really just a swag since we don't have the original SAIC code to work from.
    # But we do have a sense of what this code should do, so let's giv'er a go.
    inv_distance = 1.0  # mathematicallly, this doesn't make much sense but we simply need some value to return if distance is zero
    # guard against division by zero
    if distance_m > 0:
        inv_distance = 1.0 / distance_m

    weighted_score = 0.0
    if block_pop > 0:
        #weighted_score = block_pop * fraction_of_total * inv_distance  # try 1, values waaaay too small
        weighted_score = block_pop * inv_distance

    return inv_distance, weighted_score


def generate_weighted_scores(df_pairs: pd.DataFrame) -> pd.DataFrame:
    """Add a `weighted_score` column to `df_pairs` by applying `applyWeighting`.

    This function defensively checks for required input columns and then iterates
    through rows.

    TODO: This code will almost certainly be a performance bottleneck and will
    likely need to be optimized using vectorized operations or parallel processing
    before we start processing larger cross products.
    """
    if df_pairs is None or df_pairs.empty:
        return df_pairs
    # require specific input columns including the standardized distance column 'distance_m'
    required = ['block_aland', 'block_awater', 'distance_m', 'block_pop', 'fraction_of_total']
    missing = [c for c in required if c not in df_pairs.columns]
    if missing:
        logging.warning("generate_weighted_scores: missing columns %s; skipping weight computation", missing)
        return df_pairs

    def _row_weight(row):
        # call applyWeighting and return whatever it returns (expected: (inv_distance, weighted_score))
        return applyWeighting(
            row['block_aland'],
            row['block_awater'],
            row['distance_m'],
            row['block_pop'],
            row['fraction_of_total']
        )

    df_out = df_pairs.copy()

    # apply and expand into two columns
    scores_out = df_out.apply(_row_weight, axis=1, result_type='expand')
    scores_out.columns = ['inv_distance', 'weighted_score']
    df_out = pd.concat([df_out.reset_index(drop=True), scores_out.reset_index(drop=True)], axis=1)

    return df_out


def aggregate_blockgroup_scores(df_all_pairs: pd.DataFrame, cfg: Config) -> None:
    """Aggregate weighted scores to block-group level and write CSV.

    Assumptions:
      - `df_all_pairs` contains `block_geoid` and a pre-computed `block_group_pop` column
      - `df_all_pairs` contains `weighted_score` computed earlier and NPL `Latitude`/`Longitude`
    """
    if df_all_pairs is None or df_all_pairs.empty:
        logging.info("aggregate_blockgroup_scores: no data to aggregate")
        return

    df_bg = df_all_pairs.copy()

    # derive block-group GEOID (first 12 chars) if not already present
    if 'block_group_geoid' not in df_bg.columns:
        if 'block_geoid' in df_bg.columns:
            df_bg['block_group_geoid'] = df_bg['block_geoid'].astype(str).str[:12]
        else:
            logging.error("aggregate_blockgroup_scores: neither 'block_group_geoid' nor 'block_geoid' present; skipping")
            return

    # Sum weighted scores by EPA ID + block group
    df_bg_scores = (
        df_bg.groupby(['EPA ID', 'block_group_geoid'], dropna=False, as_index=False)
        ['weighted_score']
        .sum()
        .rename(columns={'weighted_score': 'sum_weighted_score'})
    )

    # block_group_pop assumed present in df_bg; take first non-null per block_group_geoid
    if 'block_group_pop' in df_bg.columns:
        df_bg_pop = (
            df_bg[['block_group_geoid', 'block_group_pop']]
            .drop_duplicates(subset=['block_group_geoid'])
            .reset_index(drop=True)
        )
    else:
        logging.warning("aggregate_blockgroup_scores: 'block_group_pop' not found; leaving as NaN in output")
        df_bg_pop = pd.DataFrame(columns=['block_group_geoid', 'block_group_pop'])

    # NPL site coords (Latitude/Longitude) per EPA ID
    npl_coords = (
        df_bg[['EPA ID', 'Latitude', 'Longitude']]
        .drop_duplicates(subset=['EPA ID'])
        .reset_index(drop=True)
    )

    # Merge pieces together
    df_result = df_bg_scores.merge(df_bg_pop, on='block_group_geoid', how='left')
    df_result = df_result.merge(npl_coords, on='EPA ID', how='left')

    # Reorder columns as requested
    out_cols = ['EPA ID', 'Latitude', 'Longitude', 'block_group_geoid', 'block_group_pop', 'sum_weighted_score']
    out_cols = [c for c in out_cols if c in df_result.columns]
    df_result = df_result[out_cols]

    out_filename = 'blockgroup_scores.csv'
    out_path = join_path_and_file(cfg.output_path, out_filename)
    try:
        write_df_s3_or_local(df_result, out_path)
        logging.info("Wrote block-group aggregated scores to: %s (rows=%d)", out_path, len(df_result))
    except Exception as e:
        logging.error("Failed to write block-group scores CSV to %s: %s", out_path, e)



# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths (works for both local and s3 prefixes)
    npl_locations_path = join_path_and_file(cfg.input_path, cfg.npl_locations_file)

    # Read raw locations input
    logging.info(f"NPL locations CSV: {npl_locations_path}")
    try:
        df_npl_locations = read_csv_s3_or_local(npl_locations_path, cfg.skip_rows)
    except Exception as e:
        logging.error(f"Failed to read raw locations CSV at {npl_locations_path}: {e}")
        return 1
    logging.info(f"NPL locations rows (after header): {len(df_npl_locations)}")
    logging.info(f"NPL locations headers (first 5 of {df_npl_locations.shape[1]}): {df_npl_locations.columns[:5].tolist()}")
    # Require `EPA ID` column to be present
    if 'EPA ID' not in df_npl_locations.columns:
        logging.error("Required column 'EPA ID' not found in NPL locations CSV, cannot proceed.")
        return 1

    # Build the list of states to process and iterate per-state block weights
    # SERIOUS TODO: this by-state logic is backwards. We have to process multiple
    # states worth of blocks to make sure we get cross-border pairs.
    # However, going with this for now to see if we can get numbers right for
    # non-border sites.
    state_list = get_state_list(cfg.state_list)

    # accumulate per-state pairs for a single aggregated output
    pairs_accum = []
    for state in state_list:
        # filter NPL locations to this state only
        df_state = df_npl_locations[df_npl_locations['EPA ID'].astype(str).str[:2].str.upper() == state]
        logging.info(f"NPL locations rows for {state}: {len(df_state)}")

        blocks_filename = f"{cfg.census_blocks_weights_base}_{state}.csv"
        blocks_path = join_path_and_file(cfg.input_path, blocks_filename)

        logging.info(f"Block weights CSV: {blocks_path}")
        try:
            df_blocks = read_csv_s3_or_local(blocks_path, cfg.skip_rows)
        except Exception as e:
            logging.error(f"Failed to read block weights CSV at {blocks_path}: {e}")
            return 1
        # Normalize census-year-specific column names to year-agnostic descriptive names
        try:
            df_blocks = normalize_census_columns(df_blocks)
        except Exception as e:
            logging.error("Failed to normalize census columns for %s: %s", state, e)
            continue
        logging.info(f"Block weights rows (after header) for {state}: {len(df_blocks)}")
        logging.info(f"Block weights headers (first 5 of {df_blocks.shape[1]}) for {state}: {df_blocks.columns[:5].tolist()}")

        # --- GIS processing for this state: find pairs <= 5000m ------------
        # Prepare GeoDataFrames (projects to EPSG:5070)
        gdf_npl, gdf_blocks = prepare_gdfs(df_state, df_blocks)
        if gdf_npl is None or gdf_blocks is None:
            logging.error("Failed to prepare GeoDataFrames for %s, skipping state.", state)
            continue

        # Find all pairs within 5000 meters
        df_pairs = find_pairs_within(gdf_npl, gdf_blocks, radius_m=5000.0)

        df_pairs = generate_weighted_scores(df_pairs)

        # Accumulate pairs for aggregated output
        if df_pairs is not None and not df_pairs.empty:
            pairs_accum.append(df_pairs)

        # TODO: Handle pairs > 5000m (gt5000) and the 'hole' rule (ensure each block appears in one group or another).

    # End per-state loop

    # Concatenate accumulated pairs and write single aggregated file
    if pairs_accum:
        df_all_pairs = pd.concat(pairs_accum, ignore_index=True)
        out_path = join_path_and_file(cfg.output_path, cfg.short_pairs_filename)
        try:
            write_df_s3_or_local(df_all_pairs, out_path)
            logging.info("Wrote aggregated <=5000m pairs to: %s (rows=%d)", out_path, len(df_all_pairs))
        except Exception as e:
            logging.error(f"Failed to write aggregated pairs CSV to {out_path}: {e}")
            return 1
        # Also produce block-group level aggregated scores. Assumes `block_group_pop` exists
        try:
            aggregate_blockgroup_scores(df_all_pairs, cfg)
        except Exception as e:
            logging.error("Failed to produce block-group aggregated scores: %s", e)
            # do not fail the whole run for block-group aggregation
    else:
        logging.info("No <=5000m pairs found for any state; no aggregated file written.")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
