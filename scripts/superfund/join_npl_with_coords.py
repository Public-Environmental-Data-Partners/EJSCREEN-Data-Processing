"""
join_npl_with_coords.py

Purpose:
  Enrich the cleaned combined NPL CSV with coordinate values from a reference
  CSV. The script is careful to detect inconsistent coordinates for the same
  EPA ID in the reference and will warn and default to the first seen pair.

Behavior:
  - Reads the combined NPL CSV (left table) and a reference coordinate CSV
    (right table). Both sources can be local files or S3 URIs (s3://...).
  - Performs an integrity check on the reference: if an EPA ID has multiple
    distinct Primary Latitude/Primary Longitude pairs a warning is logged and
    the first pair is used.
  - Renames Primary Latitude/Primary Longitude to Latitude/Longitude in the
    reference, coerces ID columns to strings, and performs a left join of the
    combined table (left) to the deduplicated reference (right) matching
    left_on='EPA ID' and right_on='EPA ID'. The reference ID column is dropped
    after the join.
  - Logs the number of rows missing coordinates after the join and verifies
    that the output row count equals the input combined row count.

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


# --- S3/local helpers (same pattern used in combine_npl_csv.py) ----------
def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
    if is_s3_uri(path):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)


def read_csv_s3_or_local(path: str) -> pd.DataFrame:
    """Read a CSV from S3 or local filesystem into a DataFrame."""
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
            return pd.read_csv(io.BytesIO(obj['Body'].read()))
        except Exception as e:
            # Log the exception message and re-raise a runtime error with the same message
            logging.error(f"Error fetching S3 object {path}: {e}")
            raise RuntimeError(f"Failed to read S3 CSV {path}: {e}") from e

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Local CSV not found: {path}")
    return pd.read_csv(p)


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


# --- Config ---------------------------------------------------------------
@dataclass
class Config:
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    output_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    combined_filename: str = "combined_npl_20260213.csv"
    reference_filename: str = "downloads/406242.csv"
    combined_with_coords_filename: str = "combined_with_coords_20260213.csv"
    combined_id_col: str = "EPA ID"           # key in combined NPL file
    reference_id_col: str = "EPA ID"         # key in reference file
    reference_lat_col: str = "Primary Latitude"
    reference_lon_col: str = "Primary Longitude"


# --- Core logic ----------------------------------------------------------

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
    parser.add_argument('--combined-with-coords-filename', dest='combined_with_coords_filename', default=Config.combined_with_coords_filename,
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
        # keep ID / lat / lon values from Config dataclass defaults
     )


def integrity_check_and_dedup_reference(df_ref: pd.DataFrame, id_col: str, lat_col: str, lon_col: str) -> pd.DataFrame:
    """Check for multiple coordinate pairs per ID, warn if found, and return a deduped ref DF.

    Keeps the first encountered lat/lon pair for each ID.
    """
    if id_col not in df_ref.columns:
        raise KeyError(f"Reference ID column not found: {id_col}")
    if lat_col not in df_ref.columns or lon_col not in df_ref.columns:
        raise KeyError(f"Reference latitude/longitude columns not found: {lat_col}, {lon_col}")

    # Identify IDs with multiple unique (lat,lon) pairs
    grouped = df_ref.groupby(id_col)
    conflicts = []
    for eid, grp in grouped:
        unique_pairs = grp[[lat_col, lon_col]].drop_duplicates()
        if len(unique_pairs) > 1:
            conflicts.append(eid)
            logging.warning(f"Conflict found for ID {eid}: Multiple coordinate pairs detected. Defaulting to first encountered.")

    # Deduplicate by keeping first occurrence of each ID
    df_ref_dedup = df_ref.drop_duplicates(subset=[id_col], keep='first').copy()
    return df_ref_dedup


def rename_ref_latlon(df_ref: pd.DataFrame, lat_col: str, lon_col: str) -> pd.DataFrame:
    rename_map = {}
    if lat_col in df_ref.columns:
        rename_map[lat_col] = 'Latitude'
    if lon_col in df_ref.columns:
        rename_map[lon_col] = 'Longitude'
    if rename_map:
        df_ref = df_ref.rename(columns=rename_map)
    return df_ref


def coerce_id_types(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df[col] = df[col].astype(str)
    return df


def enrich_combined_with_coords(df_combined: pd.DataFrame, df_ref: pd.DataFrame, combined_id_col: str, reference_id_col: str) -> pd.DataFrame:
    # Ensure ID columns are string
    df_combined = df_combined.copy()
    df_ref = df_ref.copy()
    if combined_id_col not in df_combined.columns:
        raise KeyError(f"Combined ID column not found: {combined_id_col}")
    if reference_id_col not in df_ref.columns:
        raise KeyError(f"Reference ID column not found: {reference_id_col}")

    df_combined[combined_id_col] = df_combined[combined_id_col].astype(str)
    df_ref[reference_id_col] = df_ref[reference_id_col].astype(str)

    merged = pd.merge(
        df_combined,
        df_ref,
        left_on=combined_id_col,
        right_on=reference_id_col,
        how='left',
        suffixes=("","_ref")
    )

    # Drop the redundant reference ID column
    if reference_id_col in merged.columns:
        merged = merged.drop(columns=[reference_id_col])

    return merged


# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Resolve paths (S3 or local)
    combined_path = join_path_and_file(cfg.input_path, cfg.combined_filename)
    reference_path = join_path_and_file(cfg.input_path, cfg.reference_filename)

    logging.info(f"Loading combined NPL file: {combined_path}")
    df_combined = read_csv_s3_or_local(combined_path)
    logging.info(f"Loaded combined rows: {len(df_combined)}")

    logging.info(f"Loading reference coordinate file: {reference_path}")
    df_ref = read_csv_s3_or_local(reference_path)
    logging.info(f"Loaded reference rows: {len(df_ref)}")

    # Integrity check + deduplicate reference file
    df_ref_dedup = integrity_check_and_dedup_reference(df_ref, cfg.reference_id_col, cfg.reference_lat_col, cfg.reference_lon_col)

    # Rename lat/lon to normalized names
    df_ref_dedup = rename_ref_latlon(df_ref_dedup, cfg.reference_lat_col, cfg.reference_lon_col)

    # Keep only the reference ID and the normalized Latitude/Longitude columns.
    # This ensures the merged output will only gain the two coordinate columns.
    cols_to_keep = [cfg.reference_id_col]
    for col in ['Latitude', 'Longitude']:
        if col in df_ref_dedup.columns:
            cols_to_keep.append(col)
        else:
            logging.warning(f"Reference file missing expected column: {col}")

    df_ref_dedup = df_ref_dedup[cols_to_keep].copy()
    logging.info(f"Reduced reference to columns: {cols_to_keep}")

    # Perform the left join (enrichment)
    # Keep a snapshot of combined columns so we can verify no unexpected columns are added
    combined_columns_before = set(df_combined.columns)
    merged = enrich_combined_with_coords(df_combined, df_ref_dedup, cfg.combined_id_col, cfg.reference_id_col)

    # Verification: ensure only Latitude/Longitude were added (if anything)
    final_columns = set(merged.columns)
    added_columns = final_columns - combined_columns_before
    allowed_added = {"Latitude", "Longitude"}
    unexpected_added = added_columns - allowed_added
    if unexpected_added:
        logging.error(f"Unexpected columns added from reference: {unexpected_added}")
        raise RuntimeError(f"Unexpected columns added from reference: {unexpected_added}")
    else:
        logging.info(f"Columns added from reference (if any): {sorted(list(added_columns & allowed_added))}")

    # Audit: missing coordinates
    lat_missing = merged['Latitude'].isna() if 'Latitude' in merged.columns else pd.Series([True]*len(merged))
    lon_missing = merged['Longitude'].isna() if 'Longitude' in merged.columns else pd.Series([True]*len(merged))
    n_missing = int((lat_missing | lon_missing).sum())
    logging.info(f"Rows with missing Latitude/Longitude: {n_missing} of {len(merged)}")

    # Row count verification
    if len(merged) != len(df_combined):
        logging.error(f"Row count mismatch after join: combined had {len(df_combined)} rows, merged has {len(merged)} rows")
        raise RuntimeError("Row count mismatch after enrichment; aborting to avoid data corruption")

    # Write output
    out_path = join_path_and_file(cfg.output_path, cfg.combined_with_coords_filename)
    write_df_s3_or_local(merged, out_path)
    logging.info(f"Wrote enriched combined file to: {out_path}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
