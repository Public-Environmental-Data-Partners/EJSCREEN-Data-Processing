"""
ejam2csv.py

Purpose:
  Request EJAM API data for a state and convert the JSON list-of-dicts
  response into a compact CSV of selected fields (traffic fields by default).

Features:
  - Send a POST request to the EJAM API and load the returned JSON into pandas.
  - Select a known set of traffic-related columns and write them to CSV.
  - Supports writing output to local filesystem or to S3 (s3://bucket/key).

Usage examples:
  python ejam2csv.py -p ./test_files/ --dry-run
  python ejam2csv.py -p s3://my-bucket/prefix/ -n 100

Defaults:
  - output_file: ri_ejam_traffic_subset.csv
  - path: s3://pedp-data-preserved/ejscreen-data-processing/traffic/
  - number_rows: 0 (<=0 means no limit)

Outputs:
  - Writes a CSV containing selected EJAM fields (traffic-related).
  - Optionally writes a small JSON sample of the raw API response for inspection.

Dependencies:
  - Python 3.8+
  - pandas
  - requests
  - boto3 (if writing to S3)
  - python-dotenv (optional; allows loading AWS creds from a .env file)

Credits:
    Code written by Anne Gunn with extensive help from Gemini and GitHub Copilot (GPT-5 mini).
"""

# Inputs: none required (hardcoded API request), optional CLI `-n/--limit` to limit rows
import argparse
import pandas
import requests
import json
import re
import html
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv  # needed for AWS access via .env file
from typing import Optional

# --- Configuration and CLI parsing (moved/simplified to mirror geojson2csv.py) ---
@dataclass
class Config:
    # output filename (will be joined with `path`); default writes locally under ./test_files/
    output_file: str = "ri_ejam_traffic_subset.csv"
    # number of rows to process; <=0 or None means process all rows
    number_rows: Optional[int] = 0
    dry_run: bool = False
    # destination path for the csv file
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"
    #path: str = "./test_files/"


def get_config(argv=None) -> Config:
    """Load .env then parse a small set of CLI args and return a Config."""
    # Load environment variables (so boto3 can pick up AWS creds if later needed)
    load_dotenv()


    parser = argparse.ArgumentParser(description="Request EJAM API response and create a traffic subset CSV")
    parser.add_argument('-p', '--path', type=str, default=Config.path,
                        help='S3 path prefix or local folder for output (default: ./test_files/)')
    parser.add_argument('-n', '--number_rows', type=int, default=Config.number_rows,
                        help='maximum number of rows to process (default: 10); <=0 means no limit')
    parser.add_argument('--dry-run', action='store_true', help='If set, do not write any files, just show what would be done')

    args = parser.parse_args(argv)

    # Filter out 'None' values so they don't overwrite our Dataclass defaults
    overrides = {k: v for k, v in vars(args).items() if v is not None}

    # Merge overrides into the default Config
    return Config(**overrides)


def join_path_and_file(path: str, filename: str) -> str:
    """Return a correctly-formed path:
    - If `path` is an S3 URI (s3://...), join using '/' and preserve the s3:// scheme.
    - Otherwise, use pathlib.Path to build a local filesystem path.
    """
    if isinstance(path, str) and path.lower().startswith('s3://'):
        return path.rstrip('/') + '/' + filename.lstrip('/')
    return str(Path(path) / filename)

# --- Helper functions (moved to top) ---------------------------------------
def dumpRequestJson(obj, filename='./test_files/ejam_response.json', limit=None):
    """Pretty-print the response JSON to a file for debugging/inspection.

    This is currently hardcoded to a) write locally and b) limit output to first 10 records
    """
    # Try to build a small sample representation (first 10 records) for the dump; fall back to full object
    sample = None
    try:
        df_tmp = pandas.DataFrame.from_dict(obj)
        sample = df_tmp.head(10).to_dict(orient='records')
    except Exception:
        print("Warning: couldn't coerce response to DataFrame for json dump")

    # Choose what to dump: prefer sample if available
    to_write = sample if sample is not None else obj

    # Write the dump to a file (separate try/except so failures are non-fatal)
    try:
        with open(filename, 'w', encoding='utf-8') as fh:
            json.dump(to_write, fh, indent=2, ensure_ascii=False)
    except Exception as e:
        # non-fatal: print error and continue
        print(f"Warning: couldn't write JSON dump to {filename}: {e}")


def row_to_text_space(row):
    """Create a space-joined text blob for a DataFrame row, skipping NA values."""
    parts = []
    for v in row.values:
        if pandas.isna(v):
            continue
        parts.append(str(v))
    return ' '.join(parts)


def row_to_text_comma(row):
    """Create a comma-joined text blob for a DataFrame row, skipping NA values."""
    parts = []
    for v in row.values:
        if pandas.isna(v):
            continue
        parts.append(str(v))
    return ','.join(parts)


def _format_id_for_excel_col(v):
    """Format an identifier so Excel imports it as text (e.g. ="440010301001")."""
    if pandas.isna(v):
        return v
    s = str(v)
    if s.startswith('="') and s.endswith('"'):
        return s
    return f'="{s}"'


# This is a hack but ChatGPT says it is a common one and works better than quoting
def force_ejam_uniq_id_to_excel_text(v):
    if pandas.isna(v):
        return v
    s = str(v)
    # preface the long string of digits with a tab and Excel will see it as text
    return f"\t{s}"


# helper to extract href URL from an HTML anchor tag; returns original value if not an anchor
def extract_url_from_anchor(s):
    """Extract the URL from an HTML <a href="..."> anchor string.

    If s is NA or not a string containing an href, returns s unchanged.
    """
    try:
        if pandas.isna(s):
            return s
        text = str(s)
        # quick check for anchor
        if '<a' not in text.lower() or 'href' not in text.lower():
            return text

        # unescape HTML entities first
        text_un = html.unescape(text)
        # find href= followed by single or double quote
        m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', text_un, re.IGNORECASE)
        if m:
            return m.group(1)
        # fallback: try to extract http(s)://... pattern
        m2 = re.search(r'(https?://\S+)', text_un)
        if m2:
            # strip trailing characters like '"' or '>'
            url = m2.group(1).rstrip("\"'>")
            return url
        return text_un
    except Exception:
        return s

# --- Main script body ------------------------------------------------------
def write_csv(df, out_path: str, limit: Optional[int] = None) -> None:
    """Write df to CSV; if out_path is S3 URI (s3://...), write to a temp file and upload.

    If limit is None or <=0, write all rows; otherwise write up to `limit` rows.
    """
    # Determine which rows to write
    if limit is None or (isinstance(limit, int) and limit <= 0):
        use_df = df
    else:
        use_df = df.head(limit)

    # If destination is S3, write to a temp file then upload
    if isinstance(out_path, str) and out_path.lower().startswith('s3://'):
        import tempfile
        import os
        tmp_path = None
        try:
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

            import boto3
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


def main(argv=None) -> None:
    # Use simplified config/CLI parsing
    config = get_config(argv)

    print(f"Will be writing to path: {config.path}")

    out_path = join_path_and_file(config.path, config.output_file)
    limit = config.number_rows
    dry_run = config.dry_run

    # See EJAM API documentation: https://github.com/edgi-govdata-archiving/EJAM-API?tab=readme-ov-file
    url = "https://ejamapi-84652557241.us-central1.run.app/data"
    # TODO: make state abbreviation a runtime parameter
    request_data = {"buffer": 0, "fips": "RI", "scale": "blockgroup"}

    resp = requests.post(url, json=request_data)
    resp.raise_for_status()
    print("HTTP status:", resp.status_code)

    data = resp.json()
    # dump the raw JSON response to a file for inspection (limited)
    dumpRequestJson(data, limit=limit)
    print("response type:", type(data))

    # The API returns a list-of-dicts; construct DataFrame directly
    df = pandas.DataFrame(data)
    if df is None:
        print("***No/bad data returned by API; processing halted.")
        exit(1)
    full_data_len = len(df)
    print(f"Loaded data with {full_data_len} rows and {len(df.columns)} columns")

    # limit number of rows we are going to process, if requested
    if limit is None or limit <= 0:
        print("No row limit specified; processing all rows")
    else:
        df = df.head(limit)
        print(f"Processing is being limited to first {limit} rows")

    # --- Simple exact-match selection of known traffic-related columns ---
    # exact column names to include (in this order)
    desired = [
        "ejam_uniq_id","valid","invalid_msg","pop","ST","statename",
        "ratio.to.avg.traffic.score","ratio.to.state.avg.traffic.score",
        "traffic.score","pctile.traffic.score","state.pctile.traffic.score",
        "avg.traffic.score","state.avg.traffic.score",
        "pctile.EJ.DISPARITY.traffic.score.eo","pctile.EJ.DISPARITY.traffic.score.supp",
        "state.pctile.EJ.DISPARITY.traffic.score.eo","state.pctile.EJ.DISPARITY.traffic.score.supp",
        "EJ.DISPARITY.traffic.score.eo","state.EJ.DISPARITY.traffic.score.eo",
        "EJ.DISPARITY.traffic.score.supp","EJAM Report"
    ]

    out_df = pandas.DataFrame(index=df.index)
    missing = []
    for col in desired:
        if col in df.columns:
            out_df[col] = df[col]
        else:
            out_df[col] = pandas.NA
            missing.append(col)

    if (len(missing) > 0):
        print(f"***Warning: We are missing {len(missing)} desired columns:")
        print(" ", missing)
    else:
        print("All desired columns found in API response.")

    # Clean up fields to work better in Excel
    if 'EJAM Report' in out_df.columns:
        out_df['EJAM Report'] = out_df['EJAM Report'].apply(extract_url_from_anchor)

    if 'ejam_uniq_id' in out_df.columns:
        out_df['ejam_uniq_id'] = out_df['ejam_uniq_id'].apply(force_ejam_uniq_id_to_excel_text)

    # Dry-run: don't write output file, just print preview and exit
    if dry_run:
        print(f"--dry-run: no files will be written. Would write to: {out_path}")
        if not out_df.empty:
            print(out_df.head(min(10, len(out_df))).to_string(index=False))
    else:
        # Let'er rip (write locally or to S3 depending on out_path)
        write_csv(out_df, out_path, limit=None)
         # print(out_df.head(10).to_string(index=False))


if __name__ == '__main__':
    main()
