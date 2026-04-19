"""ejam2csv.py

Purpose:
        Request EJAM API data for a single state and convert the response into a
        compact CSV of selected fields for one supported indicator type.

Process summary:
        - Parse runtime arguments for state, destination path, indicator type, row
            limit, and dry-run behavior.
        - Send a POST request to the EJAM API for block-group-scale results for the
            requested state.
            NB: as of this update, the API call is known to FAIL for CA and TX,
            probably due to timeout or memory issues on the server side.
        - Load the returned list-of-dicts into pandas and optionally limit the
            number of processed rows.
        - Build an output table from shared base fields plus indicator-specific
            score columns for traffic, superfund, hazardous_waste, or pm25.
            The pm25 branch currently exports the live PM-related EJAM columns
            used by the PM2.5 validation workflow.
        - Clean selected fields for easier spreadsheet use, including extracting
            report URLs from HTML anchors and forcing EJAM IDs to import as text.
        - Write the selected-field CSV and a small sample JSON dump either to a
            local folder or to an S3 destination.

Runtime arguments:
        - --state / --state-code
            Required two-letter postal code used in the API request and output
            subfolder name.
        - -p / --path
            Local folder or S3 prefix used as the output root.
        - --data-type / --type
            Indicator selector. Must be one of traffic, superfund,
            hazardous_waste, or pm25.
        - -n / --number_rows
            Optional row limit; values less than or equal to zero mean no limit.
        - --dry-run
            Print what would be written without creating output files.

Outputs:
        - ejam_{data_type}_subset.csv
            Selected EJAM fields written under {path}/{STATE}/.
            For pm25 this currently includes `pm`, `avg.pm`, and `state.avg.pm`
            when present in the API response.
        - ejam_response.json
            Small sample of the raw EJAM API response written beside the CSV.

Credits:
        Designed by Anne Gunn.
        Coded by GitHub Copilot (GPT-5.4) and Anne Gunn.
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
    # State code (USPS or FIPS-style short code); user must supply on CLI
    state_code: str = ""
    # destination path for the csv file
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"
    # number of rows to process; <=0 or None means process all rows
    number_rows: Optional[int] = 0
    dry_run: bool = False
    data_type: str = "traffic"


def get_config(argv=None) -> Config:
    """Load .env then parse a small set of CLI args and return a Config."""
    # Load environment variables (so boto3 can pick up AWS creds if later needed)
    load_dotenv()


    parser = argparse.ArgumentParser(description="Request EJAM API response and create a traffic subset CSV")
    # Order: state_code, path, number_rows, dry_run, then others
    parser.add_argument('--state', '--state-code', dest='state_code', type=str, required=True,
                        help='State code (e.g. RI); will be upper-cased and used for API request and as output folder')
    parser.add_argument('-p', '--path', type=str, default=Config.path,
                        help='S3 path prefix or local folder for output (default local example: ./output/)')
    parser.add_argument('--data-type', '--type', dest='data_type', type=str, default=Config.data_type,
                        choices=['superfund', 'traffic', 'hazardous_waste', 'pm25'],
                        help='indicator to extract from the EJAM response (default: traffic)')
    parser.add_argument('-n', '--number_rows', type=int, default=Config.number_rows,
                        help='maximum number of rows to process (default: 10); <=0 means no limit')
    parser.add_argument('--dry-run', action='store_true', help='If set, do not write any files, just show what would be done')

    # If script invoked with no args, print a one-line error and the full help text, then exit non-zero
    import sys
    if argv is None and len(sys.argv) <= 1:
        print("\n***Error: missing required parameters; at minimum --state must be provided.\n", file=sys.stderr)
        parser.print_help()
        sys.exit(2)

    args = parser.parse_args(argv)

    # Force state code to uppercase if provided
    if hasattr(args, 'state_code') and args.state_code is not None:
        args.state_code = str(args.state_code).upper()

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
def dumpRequestJson(obj, filename='ejam_response.json', path=None, state_code=None, limit=None):
    """Pretty-print the response JSON to a file for debugging/inspection.

    Writes a small sample (first 10 records) of the response to
    {path}/{state_code}/{filename} when `path` and `state_code` are provided.
    If `path` is an S3 URI (s3://...), the file is uploaded to S3. Failures are
    non-fatal and only produce a warning.
    """
    # Build content to write: prefer a small sample if possible
    sample = None
    try:
        df_tmp = pandas.DataFrame.from_dict(obj)
        sample = df_tmp.head(10).to_dict(orient='records')
    except Exception:
        # fall back to writing the full object
        sample = None

    to_write = sample if sample is not None else obj

    # If no path/state provided, default to local ./output/ folder
    if path is None:
        dest_base = './output/'
        dest_key = f"{dest_base.rstrip('/')}/{filename}"
        is_s3 = False
    else:
        # ensure state_code is uppercased if provided
        sc = (str(state_code).upper() if state_code is not None else '')
        dest_key = join_path_and_file(path, f"{sc}/{filename}") if sc else join_path_and_file(path, filename)
        is_s3 = isinstance(dest_key, str) and dest_key.lower().startswith('s3://')

    try:
        if is_s3:
            # upload to S3 using boto3
            tail = dest_key[5:]
            parts = tail.split('/', 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid S3 URI: {dest_key}")
            bucket, key = parts[0], parts[1]
            import boto3
            s3 = boto3.client('s3')
            s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(to_write, indent=2, ensure_ascii=False).encode('utf-8'))
            print(f"Wrote JSON dump to {dest_key}")
        else:
            # local filesystem: ensure parent dir exists
            p = Path(dest_key)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(to_write, fh, indent=2, ensure_ascii=False)
            print(f"Wrote JSON dump to {dest_key}")
    except Exception as e:
        print(f"Warning: couldn't write JSON dump to {dest_key}: {e}")


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

    # write into a state-specific folder and use a short filename
    output_file = f"ejam_{config.data_type}_subset.csv"
    out_path = join_path_and_file(config.path, f"{config.state_code}/{output_file}")
    limit = config.number_rows
    dry_run = config.dry_run

    # See EJAM API documentation: https://github.com/edgi-govdata-archiving/EJAM-API?tab=readme-ov-file
    url = "https://ejamapi-84652557241.us-central1.run.app/data"
    # Use configured state code (uppercased)
    request_data = {"buffer": 0, "fips": config.state_code, "scale": "blockgroup"}

    resp = requests.post(url, json=request_data)
    resp.raise_for_status()
    print("HTTP status:", resp.status_code)

    data = resp.json()
    # dump the raw JSON response to a file for inspection (limited)
    dumpRequestJson(data, path=config.path, state_code=config.state_code, limit=limit)
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

    # Build the output schema from shared base fields plus indicator-specific score columns.
    base_required = [
        "ejam_uniq_id", "valid", "invalid_msg", "pop", "ST", "statename"
    ]
    base_last = ["EJAM Report"]
    indicator_columns = {
        "traffic": [
            "ratio.to.avg.traffic.score", "ratio.to.state.avg.traffic.score",
            "traffic.score", "pctile.traffic.score", "state.pctile.traffic.score",
            "avg.traffic.score", "state.avg.traffic.score",
            "pctile.EJ.DISPARITY.traffic.score.eo", "pctile.EJ.DISPARITY.traffic.score.supp",
            "state.pctile.EJ.DISPARITY.traffic.score.eo", "state.pctile.EJ.DISPARITY.traffic.score.supp",
            "EJ.DISPARITY.traffic.score.eo", "state.EJ.DISPARITY.traffic.score.eo",
            "EJ.DISPARITY.traffic.score.supp"
        ],
        "superfund": [
            "proximity.npl", "pctile.proximity.npl"
        ],
        "hazardous_waste": [
            "proximity.tsdf", "pctile.proximity.tsdf"
        ],
        "pm25": [
            "pm",
            "avg.pm",
            "state.avg.pm",
        ],
    }
    desired = base_required + indicator_columns[config.data_type] + base_last

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
