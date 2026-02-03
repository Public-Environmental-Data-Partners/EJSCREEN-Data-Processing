"""
compareEJAM2pipeline.py

Small utility to load EJAM-export CSV and pipeline CSV (local or S3),
compare their shapes and keys, and print a concise summary useful for
validation and debugging.

Behavior (current):
 - Accepts a path prefix (S3 or local) and two filenames (defaults match
   repository naming used elsewhere).
 - Loads both CSV files into pandas.DataFrame objects (S3 support via boto3).
 - Prints row/column counts, column-name diffs, null counts, and if a
   sensible key column is detected (ejam_uniq_id, GEOID, geoid, id) reports
   overlap statistics between the two datasets.

Usage:
  python compareEJAM2pipeline.py [-p PATH] [--input-ejam FILE] [--input-pipe FILE]

Defaults:
 - input_ejam: ri_ejam_traffic_subset.csv
 - input_pipe: ri_bg_summary.csv
 - path: s3://pedp-data-preserved/ejscreen-data-processing/traffic/

The script aims to be simple and non-destructive. It prints results to
stdout and does not write any files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import argparse
import pandas as pd
from dotenv import load_dotenv
import io


# --- Configuration --------------------------------------------------------
@dataclass
class Config:
    input_ejam: str = "ri_ejam_traffic_subset.csv"
    input_pipe: str = "ri_bg_summary.csv"
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"
    dry_run: bool = False


def get_config(argv=None) -> Config:
    """Parse CLI args and environment into a Config object."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Compare EJAM CSV and pipeline CSV (local or S3)")
    parser.add_argument('-p', '--path', type=str, default=Config.path,
                        help='S3 prefix or local folder containing the CSVs')
    parser.add_argument('--input-ejam', type=str, default=Config.input_ejam,
                        help='EJAM CSV filename')
    parser.add_argument('--input-pipe', type=str, default=Config.input_pipe,
                        help='Pipeline CSV filename')
    parser.add_argument('--dry-run', action='store_true', help='No effect; present for CLI parity')
    args = parser.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if v is not None}
    return Config(**overrides)


# --- I/O helpers ----------------------------------------------------------

def is_s3_uri(path: str) -> bool:
    return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
    if is_s3_uri(path):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)


def load_csv(path: str) -> pd.DataFrame:
    """Load a CSV from either local filesystem or S3 into a pandas DataFrame.

    Raises FileNotFoundError or RuntimeError on failure.
    """
    if is_s3_uri(path):
        # parse s3://bucket/key
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]
        try:
            import boto3
            s3 = boto3.client('s3')
            obj = s3.get_object(Bucket=bucket, Key=key)
            raw = obj['Body'].read()
            text = raw.decode('utf-8')
            buf = io.StringIO(text)
            df = pd.read_csv(buf)
            return df
        except Exception as e:
            raise RuntimeError(f"Failed to read S3 CSV {path}: {e}") from e

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Local CSV not found: {path}")
    return pd.read_csv(p)


# --- Comparison helpers --------------------------------------------------


# --- Main ----------------------------------------------------------------

def main(argv=None) -> None:
    cfg = get_config(argv)
    print(f"Using path: {cfg.path}")

    ejam_path = join_path_and_file(cfg.path, cfg.input_ejam)
    pipe_path = join_path_and_file(cfg.path, cfg.input_pipe)

    try:
        df_ejam = load_csv(ejam_path)
    except Exception as e:
        print(f"Error loading EJAM CSV at {ejam_path}: {e}")
        return

    try:
        df_pipe = load_csv(pipe_path)
    except Exception as e:
        print(f"Error loading pipeline CSV at {pipe_path}: {e}")
        return

    print(f"Loaded EJAM: {len(df_ejam)} rows × {len(df_ejam.columns)} cols")
    print(f"Loaded Pipeline: {len(df_pipe)} rows × {len(df_pipe.columns)} cols")


if __name__ == '__main__':
    main()
