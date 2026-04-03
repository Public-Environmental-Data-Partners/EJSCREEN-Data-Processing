"""
join_npl_with_coords.py

Purpose:
  Enrich the cleaned combined NPL CSV with coordinate values from a reference
  CSV.
Behavior:


Usage (examples):
  python scripts/superfund/join_npl_with_coords.py
    --input-path s3://.../ \
    --combined-filename combined_npl_20260213.csv \
    --reference-filename superfund_coords_20260213.csv \
    --combined-with-coords-filename combined_with_coords_20260213.csv

  # Local override example
  python scripts/superfund/join_npl_with_coords.py \
    --input-path ./inputs/test_data/ \
    --combined-filename combined_npl_20260213.csv \
    --reference-filename npl_reference_coords.csv \
    --combined-with-coords-filename combined_with_coords_20260213.csv

Notes:
  - Defaults target the project's S3 prefix and pipeline filenames; override
    with CLI flags to run locally.
  - boto3 must be installed to access S3; S3 read/write errors raise RuntimeError.
  - .env is loaded (via python-dotenv) so AWS creds in a .env file are used by boto3.

Credits:
  - Designed by Anne Gunn and Gemimi
  - Implemented by GitHub Copilot, GPT-5 mini, and Anne Gunn
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import logging
import io

# Third-party
import pandas as pd
from dotenv import load_dotenv

# Optional/cloud SDK
try:
    import boto3
except Exception:
    boto3 = None


# --- Config ---------------------------------------------------------------
@dataclass
class Config:
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    output_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    combined_filename: str = "npl_current_and_proposed.csv"
    reference_filename: str = "distilled_npl_site_locations.csv"
    combined_with_coords_filename: str = "npl_sites_with_coords.csv"
    combined_id_col: str = "EPA ID"  # key in combined NPL file
    reference_id_col: str = "EPA ID"  # key in reference file
    reference_lat_col: str = "Latitude"
    reference_lon_col: str = "Longitude"


def get_config(argv=None) -> Config:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Join combined NPL with reference coords (S3 or local)")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help='S3 prefix or local folder containing input files')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help='S3 prefix or local folder to write outputs')
    parser.add_argument('--combined-filename', dest='combined_filename', default=Config.combined_filename,
                        help='Combined NPL filename (default from pipeline)')
    parser.add_argument('--reference-filename', dest='reference_filename', default=Config.reference_filename,
                        help='Reference coord filename')
    parser.add_argument('--combined-with-coords-filename', dest='combined_with_coords_filename',
                        default=Config.combined_with_coords_filename,
                        help='Output filename for combined+coords')

    # NOTE: ID/lat/lon column names are configured in the Config dataclass defaults
    # and are intentionally not exposed as runtime flags to keep usage simpler.

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        combined_filename=args.combined_filename,
        reference_filename=args.reference_filename,
        combined_with_coords_filename=args.combined_with_coords_filename,
        # use ID / lat / lon values from Config dataclass defaults
    )


# --- S3/local helpers (same pattern used in combine_npl_csv.py) ----------
def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
    if is_s3_uri(path):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)


def read_csv_s3_or_local(path: str, dtype=None) -> pd.DataFrame:
    """Read a CSV from S3 or local filesystem into a DataFrame.

    Accepts an optional pandas dtype mapping which is forwarded to pd.read_csv.
    """
    if is_s3_uri(path):
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]
        if boto3 is None:
            raise RuntimeError('boto3 not installed; cannot read from S3')
        s3 = boto3.client('s3')
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            return pd.read_csv(io.BytesIO(obj['Body'].read()), dtype=dtype)
        except Exception as e:
            # Log the exception message and re-raise a runtime error with the same message
            logging.error(f"Error fetching S3 object {path}: {e}")
            raise RuntimeError(f"Failed to read S3 CSV {path}: {e}") from e

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Local CSV not found: {path}")
    return pd.read_csv(p, dtype=dtype)


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
    """Write DataFrame to a local path or upload to S3."""
    if is_s3_uri(out_path):
        tail = out_path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {out_path}")
        bucket, key = parts[0], parts[1]
        if boto3 is None:
            raise RuntimeError('boto3 not installed; cannot write to S3')
        s3 = boto3.client('s3')
        csv_text = df.to_csv(index=False)
        s3.put_object(Bucket=bucket, Key=key, Body=csv_text.encode('utf-8'))
        return

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_p, index=False)




# --- Core logic ----------------------------------------------------------

def split_valid_and_invalid_coords(df: pd.DataFrame, lat_col: str, lon_col: str):
    """Return (df_valid, df_invalid) where valid rows have non-empty lat and lon.

    A value is considered missing if it is NaN, an empty string after stripping,
    or the literal text 'nan' (case-insensitive).
    """
    if lat_col not in df.columns or lon_col not in df.columns:
        raise ValueError(f"Latitude/Longitude columns not found in DataFrame: {lat_col}, {lon_col}")

    lat = df[lat_col]
    lon = df[lon_col]

    # Convert to string for text-based checks but keep original NaN detection
    lat_str = lat.astype(str).str.strip()
    lon_str = lon.astype(str).str.strip()

    lat_missing = pd.isna(lat) | (lat_str == '') | (lat_str.str.lower() == 'nan')
    lon_missing = pd.isna(lon) | (lon_str == '') | (lon_str.str.lower() == 'nan')

    invalid_mask = lat_missing | lon_missing
    df_invalid = df.loc[invalid_mask].copy()
    df_valid = df.loc[~invalid_mask].copy()
    return df_valid, df_invalid


def filter_to_us_states(df: pd.DataFrame, state_col: str = 'State') -> pd.DataFrame:
    """Filter DataFrame to rows whose state codes are in the US set (50 states + DC + PR).

    Logs the number of rejected rows and a sorted list of rejected state codes.
    Returns the filtered DataFrame (only rows with allowed state codes).
    """
    # 50 states + DC + PR
    VALID_STATES = {
        'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS',
        'KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY',
        'NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV',
        'WI','WY','DC','PR'
    }

    if state_col not in df.columns:
        logging.error(f"State column '{state_col}' not found in DataFrame; cannot filter by state.")
        return df.copy()

    # Normalize state values to uppercase stripped strings
    state_vals = df[state_col].astype(str).str.strip().str.upper()

    # Consider missing/empty/'nan' as invalid
    missing_mask = pd.isna(df[state_col]) | (state_vals == '') | (state_vals == 'NAN')

    # Valid if state code is in VALID_STATES
    valid_mask = state_vals.isin(VALID_STATES) & (~missing_mask)

    df_valid = df.loc[valid_mask].copy()
    df_invalid = df.loc[~valid_mask].copy()

    # Gather rejected state codes (exclude blank/'NAN')
    rejected_codes = sorted({s for s in state_vals.loc[~valid_mask].unique() if s and s != 'NAN'})
    rejected_count = len(df_invalid)
    logging.info(f"Rejected {rejected_count} rows outside valid US states (kept only 50 states + DC + PR). Rejected state codes: {rejected_codes}")

    return df_valid


# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Resolve paths (S3 or local)
    combined_path = join_path_and_file(cfg.input_path, cfg.combined_filename)
    reference_path = join_path_and_file(cfg.input_path, cfg.reference_filename)

    logging.info(f"Loading combined NPL file: {combined_path}")
    try:
        df_combined = read_csv_s3_or_local(combined_path)
    except Exception as e:
        logging.error(f"Failed to read combined NPL file at {combined_path}: {e}")
        return 1
    logging.info(f"Loaded combined rows: {len(df_combined)}")

    logging.info(f"Loading reference coordinate file: {reference_path}")
    # Read lat/lon from the reference as strings so we preserve exact formatting
    try:
        df_ref = read_csv_s3_or_local(reference_path, dtype={cfg.reference_lat_col: str, cfg.reference_lon_col: str})
    except Exception as e:
        logging.error(f"Failed to read reference CSV at {reference_path}: {e}")
        return 1
    logging.info(f"Loaded reference rows: {len(df_ref)}")

    # --- Enrichment: left join combined NPL (left) with reference coords (right)
    # Warn if reference contains duplicate keys (should be deduped per spec)
    dup_count = int(df_ref[cfg.reference_id_col].duplicated().sum())
    if dup_count > 0:
        logging.warning(f"Reference file contains {dup_count} duplicate {cfg.reference_id_col} values; results may be unexpected.")

    # Perform left join on the configured ID column
    df_merged = df_combined.merge(
        df_ref[[cfg.reference_id_col, cfg.reference_lat_col, cfg.reference_lon_col]],
        on=cfg.combined_id_col,
        how='left'
    )

    # Split into valid and invalid coordinate rows
    try:
        df_valid_coords, df_invalid_coords = split_valid_and_invalid_coords(df_merged, cfg.reference_lat_col, cfg.reference_lon_col)
    except Exception as e:
        logging.error(f"Coordinate validation failed: {e}")
        return 1

    # Log and write invalid rows if any
    invalid_count = len(df_invalid_coords)
    if invalid_count:
        logging.warning(f"Found {invalid_count} rows with missing lat or lon; writing them to an invalid-rows CSV for inspection.")
        base = cfg.combined_with_coords_filename.rsplit('.', 1)[0]
        invalid_filename = f"{base}_invalid_coords.csv"
        invalid_path = join_path_and_file(cfg.output_path, invalid_filename)
        try:
            write_df_s3_or_local(df_invalid_coords, invalid_path)
            logging.info(f"Wrote invalid coordinate rows to: {invalid_path}")
        except Exception as e:
            logging.error(f"Failed to write invalid coordinate rows to {invalid_path}: {e}")
            return 1
    else:
        logging.info("No invalid lat/long rows found.")

    # From here on, work with rows that have valid lat & lon only
    df_merged = df_valid_coords

    # TODO: select only the records for the 50 US states, DC and PR, based on 2 letter state code
    # in the State column of the combined NPL file.
    df_merged = filter_to_us_states(df_merged, state_col='State')

    # Report on the number of rows we have filtered out
    if len(df_merged) != len(df_combined):
        logging.info(f"Note {len(df_combined) - len(df_merged)} rows have been filtered out during processing")
        logging.info(f"Original number rows={len(df_combined)},  output rows={len(df_merged)}")
    else:
        logging.info(f"All {len(df_merged)} rows in original file have been passed to output file")

    # Add CWEIGHT column and set to 1 for all rows
    # Note odd capitalization that matches old Pig script input requirement
    df_merged['Cweight'] = 1

    # Select only the requested output columns in the specified order
    out_cols = [cfg.combined_id_col, cfg.reference_lat_col, cfg.reference_lon_col, 'CWEIGHT']
    # If any expected column is missing after the merge, raise a clear error
    missing_out_cols = [c for c in out_cols if c not in df_merged.columns]
    if missing_out_cols:
        logging.error(f"Missing expected output columns after merge: {missing_out_cols}")
        return 1

    df_final = df_merged[out_cols].copy()


    # Write output
    out_path = join_path_and_file(cfg.output_path, cfg.combined_with_coords_filename)
    try:
        write_df_s3_or_local(df_final, out_path)
    except Exception as e:
        logging.error(f"Failed to write enriched combined file to {out_path}: {e}")
        return 1
    logging.info(f"Wrote filtered/enriched file to: {out_path}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
