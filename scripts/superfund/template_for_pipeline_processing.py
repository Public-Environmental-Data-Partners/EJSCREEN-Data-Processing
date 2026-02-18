"""
combine_npl_csv.py

Purpose:
  This code is intended to provide a reusable template for code to process one or more
  input CSV files (from local or S3), perform some kind of combination/merging/deduplication
  logic, and write out a final CSV (to local or S3).

Credits:
  - Derived by Anne Gunn from combine_npl_csv.py
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
    # default to our AWS S3 storage
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/superfund_npl/pipeline/"
    # for your code, default to S3 also, defaulting to local storage here for testing
    output_path: str = "./pipeline/test_data/"
    inputA_filename: str = "downloads/superfund_active_currentlyOnNPL_20260213.csv"
    inputB_filename: str = "downloads/superfund_active_proposedForNPL_20260213.csv"
    output_filename: str = "template_output.csv"
    skip_rows: int = 10
    join_key: str = "EPA_ID"

# --- Runtime arguments and help ---------------------------------------------
def get_config(argv=None) -> Config:
    # Load environment variables from a .env file so boto3 can pick up AWS creds if present
    load_dotenv()
    parser = argparse.ArgumentParser(description="Combine Current and Proposed NPL CSVs; prefer Current on EPA_ID collisions")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help='Folder containing input CSVs (default: ./inputs/test_data/)')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help='Folder to write the output CSV (default: ./inputs/test_data/)')
    parser.add_argument('--inputA-filename', dest='inputA_filename', default=Config.inputA_filename,
                        help='Input A CSV filename (default: current_npl.csv)')
    parser.add_argument('--inputB-filename', dest='inputB_filename', default=Config.inputB_filename,
                        help='Input B CSV filename (default: proposed_npl.csv)')
    parser.add_argument('--output-filename', dest='output_filename', default=Config.output_filename,
                        help='Output combined CSV filename (default: combined_npl.csv)')
    parser.add_argument('--skip-rows', dest='skip_rows', type=int, default=Config.skip_rows,
                        help='Number of rows to skip before the header in input CSVs (default: 10)')
    parser.add_argument('--join-key', dest='join_key', type=str, default=Config.join_key,
                        help='Column name to use as the unique identifier (default: EPA_ID)')

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        inputA_filename=args.inputA_filename,
        inputB_filename=args.inputB_filename,
        output_filename=args.output_filename,
        skip_rows=args.skip_rows,
        join_key=args.join_key,
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
# Put your content-specific processing functions here.



# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths (works for both local and s3 prefixes)
    inputA_path = join_path_and_file(cfg.input_path, cfg.inputA_filename)
    inputB_path = join_path_and_file(cfg.input_path, cfg.inputB_filename)

    # Read inputs
    logging.info(f"Reading inputA CSV: {inputA_path} (skip {cfg.skip_rows} rows)")
    df_inputA = read_csv_s3_or_local(inputA_path, cfg.skip_rows)
    logging.info(f"inputA CSV rows (after header): {len(df_inputA)}")
    logging.info(f"inputA CSV headers (first 5 of {df_inputA.shape[1]}): {df_inputA.columns[:5].tolist()}")
    logging.info(f"Reading inputB CSV: {inputB_path} (skip {cfg.skip_rows} rows)")
    df_inputB = read_csv_s3_or_local(inputB_path, cfg.skip_rows)
    logging.info(f"inputB CSV rows (after header): {len(df_inputB)}")
    logging.info(f"inputB CSV headers (first 5 of {df_inputB.shape[1]}): {df_inputB.columns[:5].tolist()}")

    # TODO: Add your logic here to use your worker functions to munge your
    # input(s) into your output(s)
    # For the purposes of this template, we do no processing of either file,
    # simply output the contents of inputA as a way to exercise the
    # data-writing code.
    # Deep copy that does not modify the original df_current
    df_output = df_inputA.copy()

    # Write output (s3 or local)
    out_path = join_path_and_file(cfg.output_path, cfg.output_filename)
    write_df_s3_or_local(df_output, out_path)
    logging.info(f"Wrote combined CSV to: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
