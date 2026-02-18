"""
combine_npl_csv.py

Purpose:
  Read two National Priorities List (NPL) CSV files (Current and Proposed),
  normalize and concatenate them, prefer Current records when EPA_ID duplicates
  occur, and write a clean combined CSV.

Behavior summary:
  - By default the script reads/writes under S3 prefixes (uses boto3):
      input_path default: s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/downloads
      output_path default: s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/
    Supply --input-path and --output-path to override; those paths may be local
    directories or S3 URIs (starting with s3://).
  - Default filenames (overrideable via CLI):
      current:  superfund_active_currentlyOnNPL_20260213.csv
      proposed: superfund_active_proposedForNPL_20260213.csv
      combined: combined_npl_20260213.csv
  - The script skips a configurable number of preamble rows (default: 10) to reach
    the CSV header.
  - It standardizes columns (Current columns first), concatenates Current + Proposed,
    and uses drop_duplicates(subset=[EPA_ID], keep='first') so Current records win
    on ID collisions.
  - When S3 URIs are used the script uses boto3 for GET/PUT; the script loads
    environment variables from a .env file (via python-dotenv) so AWS creds in
    .env are available to boto3.

Usage examples:
  # Default (uses S3 defaults):
  python scripts/superfund/combine_npl_csv.py

  # Local override example:
  python scripts/superfund/combine_npl_csv.py \
    --input-path ./inputs/test_data/ \
    --output-path ./outputs/ \
    --current-filename current_npl.csv \
    --proposed-filename proposed_npl.csv \
    --combined-filename combined_npl.csv

Notes:
  - The unique ID column default is EPA_ID (underscore). Use --id-col to change.
  - Logging is to stderr by default (basicConfig); the script logs input row counts,
    number of overlaps (and samples of overlapping IDs), and the final output row count.
  - boto3 must be installed to access S3; S3 read/write errors raise RuntimeError.

Credits:
  - Designed by Anne Gunn and Gemimi
  - Implemented by GitHub Copilot, GPT-5 mini, and Anne Gunn

"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import logging
import io
from typing import Tuple, List, Set

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
    # default to the s3 prefix used elsewhere in the project; can be overridden on CLI
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    output_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    current_filename: str = "downloads/superfund_active_currentlyOnNPL_20260213.csv"
    proposed_filename: str = "downloads/superfund_active_proposedForNPL_20260213.csv"
    combined_filename: str = "combined_npl_20260213.csv"
    skip_rows: int = 10
    id_col: str = "EPA_ID"




# --- S3/local path helpers -----------------------------------------------
def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
    """Join a path (S3 or local) with a filename into a usable path string."""
    if is_s3_uri(path):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)


def read_npl_csv(path: str, skip_rows: int) -> pd.DataFrame:
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


def write_df_to_path(df: pd.DataFrame, out_path: str) -> None:
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

# --- Helper functions ----------------------------------------------------
def get_config(argv=None) -> Config:
    # Load environment variables from a .env file so boto3 can pick up AWS creds if present
    load_dotenv()
    parser = argparse.ArgumentParser(description="Combine Current and Proposed NPL CSVs; prefer Current on EPA_ID collisions")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help='Folder containing input CSVs (default: ./inputs/test_data/)')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help='Folder to write the output CSV (default: ./inputs/test_data/)')
    parser.add_argument('--current-filename', dest='current_filename', default=Config.current_filename,
                        help='Current CSV filename (default: current_npl.csv)')
    parser.add_argument('--proposed-filename', dest='proposed_filename', default=Config.proposed_filename,
                        help='Proposed CSV filename (default: proposed_npl.csv)')
    parser.add_argument('--combined-filename', dest='combined_filename', default=Config.combined_filename,
                        help='Output combined CSV filename (default: combined_npl.csv)')
    parser.add_argument('--skip-rows', dest='skip_rows', type=int, default=Config.skip_rows,
                        help='Number of rows to skip before the header in input CSVs (default: 10)')
    parser.add_argument('--id-col', dest='id_col', type=str, default=Config.id_col,
                        help='Column name to use as the unique identifier (default: EPA_ID)')

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        current_filename=args.current_filename,
        proposed_filename=args.proposed_filename,
        combined_filename=args.combined_filename,
        skip_rows=args.skip_rows,
        id_col=args.id_col,
    )


def standardize_columns(df_current: pd.DataFrame, df_proposed: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """Return two DataFrames reindexed to a shared column order.

    The column order preserves current's columns first, then adds any proposed-only
    columns after.
    """
    current_cols = list(df_current.columns)
    proposed_cols = list(df_proposed.columns)

    # Build combined column list: current columns first, then any from proposed not present
    combined_cols = current_cols + [c for c in proposed_cols if c not in current_cols]

    # Reindex dataframes to the combined columns, adding missing columns with NaN
    df_current2 = df_current.reindex(columns=combined_cols)
    df_proposed2 = df_proposed.reindex(columns=combined_cols)
    return df_current2, df_proposed2, combined_cols


def find_overlaps(df_current: pd.DataFrame, df_proposed: pd.DataFrame, id_col: str) -> Set[str]:
    if id_col not in df_current.columns or id_col not in df_proposed.columns:
        return set()
    current_ids = set(df_current[id_col].dropna().astype(str).unique())
    proposed_ids = set(df_proposed[id_col].dropna().astype(str).unique())
    return current_ids & proposed_ids


def combine_and_dedup(df_current: pd.DataFrame, df_proposed: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """Concatenate Current then Proposed and drop duplicates keeping the first (Current) record."""
    combined = pd.concat([df_current, df_proposed], ignore_index=True, sort=False)
    if id_col in combined.columns:
        combined = combined.drop_duplicates(subset=[id_col], keep='first')
    return combined


# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths (works for both local and s3 prefixes)
    current_path = join_path_and_file(cfg.input_path, cfg.current_filename)
    proposed_path = join_path_and_file(cfg.input_path, cfg.proposed_filename)

    # Read inputs
    logging.info(f"Reading Current CSV: {current_path} (skip {cfg.skip_rows} rows)")
    df_current = read_npl_csv(current_path, cfg.skip_rows)
    logging.info(f"Current CSV rows (after header): {len(df_current)}")
    # Log first five header values (column names) for quick inspection
    logging.info(f"Current CSV headers (first 5 of {df_current.shape[1]}): {df_current.columns[:5].tolist()}")
    logging.info(f"Reading Proposed CSV: {proposed_path} (skip {cfg.skip_rows} rows)")
    df_proposed = read_npl_csv(proposed_path, cfg.skip_rows)
    logging.info(f"Proposed CSV rows (after header): {len(df_proposed)}")
    logging.info(f"Proposed CSV headers (first 5 of {df_proposed.shape[1]}): {df_proposed.columns[:5].tolist()}")

    # Standardize columns
    df_current_std, df_proposed_std, combined_cols = standardize_columns(df_current, df_proposed)
    logging.info(f"Standardized columns; combined column count: {len(combined_cols)}")

    # Collision audit
    overlaps = find_overlaps(df_current_std, df_proposed_std, cfg.id_col)
    if overlaps:
        logging.warning(f"Found {len(overlaps)} overlapping {cfg.id_col} values between Current and Proposed; Current records will be kept.")
        logging.warning(f"Overlapping IDs are: {list(overlaps)[:10]}...")  # Log first 10
    else:
        logging.info(f"No overlapping {cfg.id_col} values found between Current and Proposed.")

    # Combine deterministically and drop duplicates (Current first)
    df_clean = combine_and_dedup(df_current_std, df_proposed_std, cfg.id_col)
    logging.info(f"Final combined rows (after dedup on {cfg.id_col}): {len(df_clean)}")

    # Write output (s3 or local)
    out_path = join_path_and_file(cfg.output_path, cfg.combined_filename)
    write_df_to_path(df_clean, out_path)
    logging.info(f"Wrote combined CSV to: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
