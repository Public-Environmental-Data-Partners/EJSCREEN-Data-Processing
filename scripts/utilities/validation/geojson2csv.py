"""
geojson2csv.py

Purpose:
  Read new-pipeline traffic GeoJSON (by default from S3) and extract feature properties
  into a compact CSV to be written back to same location.
  Geometry is ignored; only feature properties are output.

Usage:
  - Configure the `Config` dataclass or run with command-line options:
      python geojson2csv.py -p <path-or-s3-prefix> -n <number-of-rows> [--dry-run]
  - If `-p/--path` starts with `s3://`, input/output will be read/written on S3.

Dependencies:
  - Python 3.8+
  - pandas
  - boto3 (if reading/writing S3)
  - python-dotenv (optional, for loading AWS creds from a .env file)

Author / Credit:
  Code written by Anne Gunn with help from EmmaLi, GitHub Copilot, and Gemini.
"""

import argparse
import boto3
from dataclasses import dataclass
import json
from pathlib import Path
import pandas as pd
from typing import Optional

from dotenv import load_dotenv # needed for AWS access via .env file

@dataclass
class Config:
    #TODO: when we start processing more than one state, filename prefix will need to be a parameter
    input_file: str = "ri_bg_summary.geojson"
    output_file: str = "ri_bg_summary.csv"
    number_rows: int = 0  # <=0 means no limit
    dry_run: bool = False
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"
    #path: str = "./test_files/"

def get_config(argv=None) -> Config:

    # get AWS credentials from environment (including .env)
    load_dotenv()

    # Build config by merging defaults with CLI arguments.
    parser = argparse.ArgumentParser(description="GeoJSON to CSV converter (properties only, no geometry).")
    # We only add arguments for things we actually want to override at runtime
    parser.add_argument('-p', '--path', type=str, default=Config.path, help='S3 path prefix for input/output files')
    parser.add_argument('-n', '--number_rows', type=int, default=Config.number_rows, help='Number of rows to write to CSV; <=0 means no limit (default: 10)')
    parser.add_argument('--dry-run', action='store_true', help='If set, do not write any files, just show what would be done')

    args = parser.parse_args(argv)

    # Filter out 'None' values so they don't overwrite our Dataclass defaults
    overrides = {k: v for k, v in vars(args).items() if v is not None}

    # Merge overrides into the default Config
    return Config(**overrides)

# --- Helper functions ------------------------------------------------------
def join_path_and_file(path: str, filename: str) -> str:
    """Return a correctly-formed path:
    - If `path` is an S3 URI (s3://...), join using '/' and preserve the s3:// scheme.
    - Otherwise, use pathlib.Path to build a local filesystem path.
    """
    full_path = ''
    if isinstance(path, str) and path.lower().startswith('s3://'):
        full_path = path.rstrip('/') + '/' + filename.lstrip('/')
    else:
        full_path = str(Path(path) / filename)
    return full_path

def load_geojson_properties(path: str) -> pd.DataFrame:
    """Load a GeoJSON file and return a DataFrame made from feature properties.

    The geometry is ignored. Expects a GeoJSON FeatureCollection with a 'features' list
    where each feature has a 'properties' mapping.

    Supports local filesystem paths and S3 URIs of the form s3://bucket/key.
    If an S3 URI is provided this function attempts to use boto3 and the
    environment (including values loaded from a .env file) for credentials.
    """
    obj = None
    # If the path is an S3 URI, stream the object from S3
    print("path:", path)
    if isinstance(path, str) and path.lower().startswith('s3://'):
        print("path is s3 uri")
        s3tail = path[5:]
        parts = s3tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]

        s3 = boto3.client('s3')
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            # Read entire object into memory and parse as JSON (consistent with local-file behavior)
            body = resp['Body'].read()
            text = body.decode('utf-8')
            obj = json.loads(text)
        except Exception as e:
            raise RuntimeError(f"***Failed to read S3 object s3://{bucket}/{key}: {e}") from e
    else:
        print("path is local")
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"GeoJSON file not found: {path}")

        with p.open('r', encoding='utf-8') as fh:
            obj = json.load(fh)

    # Support either a top-level FeatureCollection or a raw list of features
    if isinstance(obj, dict) and 'features' in obj and isinstance(obj['features'], list):
        features = obj['features']
    elif isinstance(obj, list):
        features = obj
    else:
        raise ValueError('Unsupported GeoJSON structure: expected FeatureCollection or list')

    props = []
    for i, feat in enumerate(features):
        if isinstance(feat, dict):
            # Some files may store properties at the top level of each element
            if 'properties' in feat and isinstance(feat['properties'], dict):
                props.append(feat['properties'])
            else:
                # If the feature itself looks like a properties dict, use it (but skip geometry)
                # Remove geometry key if present
                feat_copy = dict(feat)
                feat_copy.pop('geometry', None)
                props.append(feat_copy)
        else:
            # skip non-dict features
            continue

    # Create DataFrame from the properties records
    if not props:
        # empty DataFrame
        return pd.DataFrame()

    df = pd.DataFrame.from_records(props)
    return df

def write_csv(df: pd.DataFrame, out_path: str, limit: Optional[int] = 10) -> None:
    """Write df to CSV; if limit is None, write all rows. If limit > 0, write up to limit rows.

    Supports writing to local filesystem paths or to S3 URIs (s3://bucket/key).
    For S3, the DataFrame is first written to a temporary local CSV and then
    uploaded via boto3.upload_file. The temporary file is removed afterwards.
    """
    # Determine which rows to write
    if limit is None or limit <= 0:
        use_df = df
    else:
        use_df = df.head(limit)

    # If destination is S3, write to a temp file then upload
    if isinstance(out_path, str) and out_path.lower().startswith('s3://'):
        import tempfile
        import os
        tmp_path = None
        try:
            # create a temp file to write the CSV
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
            tmp_path = Path(tmp.name)
            tmp.close()
            use_df.to_csv(tmp_path, index=False)

            # parse s3 uri
            tail = out_path[5:]
            parts = tail.split('/', 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid S3 URI: {out_path}")
            bucket, key = parts[0], parts[1]

            s3 = boto3.client('s3')
            s3.upload_file(Filename=str(tmp_path), Bucket=bucket, Key=key)
            print(f"Wrote {len(use_df)} rows × {len(use_df.columns)} columns to {out_path}")
        finally:
            # clean up temp file if it was created
            try:
                if tmp_path is not None and tmp_path.exists():
                    os.unlink(str(tmp_path))
            except Exception:
                pass
        return

    # Otherwise treat as local filesystem path
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    use_df.to_csv(out_p, index=False)
    print(f"Wrote {len(use_df)} rows × {len(use_df.columns)} columns to {out_path}")


# helper to format identifiers so Excel imports them as text (e.g. ="440010301001")
def _format_id_for_excel_col(v):
    if pd.isna(v):
        return v
    s = str(v)
    if s.startswith('="') and s.endswith('"'):
        return s
    return f'="{s}"'


# --- Main ------------------------------------------------------------------
def main(argv=None) -> None:
    config = get_config(argv)

    print(f"Will be reading from and writing to path: {config.path}")
    input_geojson = join_path_and_file(config.path, config.input_file)
    output_csv = join_path_and_file(config.path, config.output_file)
    limit = config.number_rows
    dry_run = config.dry_run

    try:
        df = load_geojson_properties(input_geojson)
    except Exception as e:
        print(f"***Error loading GeoJSON '{input_geojson}': {e}")
        exit(1)

    orig_n = len(df)
    # Interpret non-positive limit as no limit
    if limit is None or limit <= 0:
        proc_n = orig_n
        used_df = df
    else:
        proc_n = min(limit, orig_n)
        used_df = df.head(proc_n)

    print(f"Loaded GeoJSON with {orig_n} feature(s); writing {proc_n} row(s) to CSV '{output_csv}' (dry_run={dry_run})")

    # operate on a copy to avoid SettingWithCopy warnings
    used_df = used_df.copy()

    # Force Excel to import block_group_geoid as text if present
    if 'block_group_geoid' in used_df.columns:
        used_df['block_group_geoid'] = used_df['block_group_geoid'].apply(_format_id_for_excel_col)

    # Dry-run: don't write files, just print preview and exit
    if dry_run:
        print("--dry-run: no files will be written.")
        if not used_df.empty:
            print(used_df.head(min(10, len(used_df))).to_string(index=False))
        return

    write_csv(used_df, output_csv, limit=None)


if __name__ == '__main__':
    main()
