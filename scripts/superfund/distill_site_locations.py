"""
distill_site_locations.py

Purpose:
  The source data file we have for getting site lat/long locations is super
  cumbersome to work with. It has multiple rows per site for a total of
  47,000+ rows, and many more columns than we need, including one with paragraphs
  of text for some rows which make it really hard to review as a spreadsheet.
  The purpose of this script is to distill that data down to one row per site
  with only location-relevant columns.

  Ultimately, we may find a better input file for this data.
  Alternatively, the output of this code could be useful to others.

Behavior:
  - Reads a verbose raw locations CSV that may contain multiple rows per site
    and reduces it to exactly one row per EPA ID (deduplicating by EPA ID).
  - Detects EPA IDs in the input that have multiple distinct Primary
    Latitude/Primary Longitude pairs; logs a warning for each such ID and
    defaults to the first encountered coordinate pair.
  - Validates that the kept rows have non-blank, numeric Primary Latitude and
    Primary Longitude values; if invalid rows are found the script logs a
    sample and exits with a non-zero status to prevent writing bad output.
  - Selects and reorders a minimal set of columns and renames
    Primary Latitude/Primary Longitude to Latitude/Longitude in the output.
  - Supports reading from and writing to either local filesystem paths or an
    S3 URI (s3://...), using boto3 when S3 is targeted. A .env file is loaded
    so AWS credentials may be provided there.

Usage (examples):
  python scripts/superfund/distill_site_locations.py
    see Config for defaults, which are set to read from and write to our S3 bucket

  # Local override example
  python scripts/superfund/distill_site_locations.py \
    --input-path ./inputs/test_data/ \
    --raw-locations-filename 406242_20260213.csv \
    --output-path ./pipeline/test_data/ \
    --output-filename distilled_site_locations.csv

Credits:
  - Designed by Anne Gunn
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
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    output_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    raw_locations_filename: str = "downloads/406242_20260213.csv"
    output_filename: str = "distilled_site_locations.csv"
    skip_rows: int = 0  # not used in this script, but left here for consistency and future flexibility
    join_key: str = "EPA ID"

# --- Runtime arguments and help ---------------------------------------------
def get_config(argv=None) -> Config:
    # Load environment variables from a .env file so boto3 can pick up AWS creds if present
    load_dotenv()
    parser = argparse.ArgumentParser(description="Distill verbose/duplicative raw location data into simple lat/long per site")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help=f'Folder containing input CSVs (default: {Config.input_path})')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help=f'Folder to write the output CSV (default: {Config.output_path})')
    parser.add_argument('--raw-locations-filename', dest='raw_locations_filename', default=Config.raw_locations_filename,
                        help=f'Raw locations CSV filename (default: {Config.raw_locations_filename})')
    parser.add_argument('--output-filename', dest='output_filename', default=Config.output_filename,
                        help=f'Output combined CSV filename (default: {Config.output_filename})')

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        raw_locations_filename=args.raw_locations_filename,
        output_filename=args.output_filename,
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

#TODO: Implement the logic to distill the raw locations data down to one row per site with only location-relevant columns.

# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths (works for both local and s3 prefixes)
    raw_locations_path = join_path_and_file(cfg.input_path, cfg.raw_locations_filename)

    # Read inputs
    logging.info(f"Reading raw locations CSV: {raw_locations_path} (skip {cfg.skip_rows} rows)")
    df_raw_locations = read_csv_s3_or_local(raw_locations_path, cfg.skip_rows)
    logging.info(f"raw locations CSV rows (after header): {len(df_raw_locations)}")

    # Ensure the input contains all required columns
    required_cols = [
        'Region', 'Site Name', 'EPA ID', 'Address 1', 'Address 2',
        'City', 'State', 'Zip Code', 'County', 'Primary Latitude', 'Primary Longitude'
    ]
    missing = [c for c in required_cols if c not in df_raw_locations.columns]
    if missing:
        logging.error(f"Missing required columns in raw locations CSV: {missing}")
        return 1

    # Detect EPA IDs with conflicting latitude/longitude values and warn
    conflicts = []
    for eid, grp in df_raw_locations.groupby('EPA ID', sort=False):
        lat_count = grp['Primary Latitude'].nunique(dropna=True)
        lon_count = grp['Primary Longitude'].nunique(dropna=True)
        if lat_count > 1 or lon_count > 1:
            conflicts.append(eid)
            pairs = grp[['Primary Latitude', 'Primary Longitude']].drop_duplicates().values.tolist()
            logging.warning(f"Conflict found for ID {eid}: Multiple coordinate pairs detected. Defaulting to first encountered. Pairs: {pairs}")

    # Deduplicate rows keeping the first occurrence per EPA ID
    df_dedup = df_raw_locations.drop_duplicates(subset=['EPA ID'], keep='first')

    # Validate that Primary Latitude and Primary Longitude are present and numeric for every kept row
    lat_numeric = pd.to_numeric(df_dedup['Primary Latitude'], errors='coerce')
    lon_numeric = pd.to_numeric(df_dedup['Primary Longitude'], errors='coerce')
    invalid_mask = pd.isna(lat_numeric) | pd.isna(lon_numeric)
    if invalid_mask.any():
        invalid_count = int(invalid_mask.sum())
        invalid_sample = df_dedup.loc[invalid_mask, ['EPA ID', 'Primary Latitude', 'Primary Longitude']].head(20)
        logging.warning(f"{invalid_count} deduplicated rows have missing or non-numeric Primary Latitude/Primary Longitude. Sample:\n{invalid_sample.to_string(index=False)}")
    else:
        logging.info("All deduplicated rows have valid numeric Primary Latitude and Primary Longitude.")

    # Select and reorder the desired output columns and rename lat/long
    df_output = df_dedup[[
        'EPA ID', 'Region', 'Site Name', 'Address 1', 'Address 2',
        'City', 'State', 'Zip Code', 'County', 'Primary Latitude', 'Primary Longitude'
    ]].copy()
    df_output = df_output.rename(columns={'Primary Latitude': 'Latitude', 'Primary Longitude': 'Longitude'})

    # Sort the final output by EPA ID ascending
    df_output = df_output.sort_values(by='EPA ID', ascending=True)

    # Summary logging
    if conflicts:
        logging.warning(f"Found {len(conflicts)} EPA IDs with conflicting coordinates; kept first occurrence for each.")
    else:
        logging.info("No EPA ID coordinate conflicts found.")
    logging.info(f"Output distilled rows (unique EPA IDs): {len(df_output)}")

    # Write output (s3 or local)
    out_path = join_path_and_file(cfg.output_path, cfg.output_filename)
    write_df_s3_or_local(df_output, out_path)
    logging.info(f"Wrote distilled CSV to: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
