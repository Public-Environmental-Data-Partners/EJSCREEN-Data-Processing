import pandas
import requests
import json

# --- Helper functions (moved to top) ---------------------------------------
def dumpRequestJson(obj, filename='./test_files/ejam_response.json', limit=None):
    """Pretty-print the response JSON to a file for debugging/inspection.

    If `limit` is provided, attempt to write only the first `limit` JSON objects
    (works for list-of-dicts and dict-of-lists by converting to records).
    """
    try:
        # Try to coerce to a table of records using pandas (handles dict-of-lists and list-of-dicts)
        try:
            df_tmp = pandas.DataFrame.from_dict(obj)
            if limit is not None:
                recs = df_tmp.head(limit).to_dict(orient='records')
            else:
                recs = df_tmp.to_dict(orient='records')
            to_dump = recs
        except Exception:
            # Fallback: if it's a list, slice; otherwise dump the whole object
            if isinstance(obj, list):
                to_dump = obj[:limit] if limit is not None else obj
            else:
                to_dump = obj

        with open(filename, 'w', encoding='utf-8') as fh:
            json.dump(to_dump, fh, indent=2, ensure_ascii=False)
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
    """Return the value wrapped in single quotes unless it's NA or already quoted."""
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
        import re, html
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
            url = m2.group(1).rstrip('"\'>)')
            return url
        return text_un
    except Exception:
        return s

# --- Main script body ------------------------------------------------------
# For now, the default number of record to process is small.
# Pass None if you want them all
# TODO: change this the default to None/ALL once the script is stable
def main(limit=10):
    # See EJAM API documentation: https://github.com/edgi-govdata-archiving/EJAM-API?tab=readme-ov-file
    url = "https://ejamapi-84652557241.us-central1.run.app/data"
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
    if limit is not None:
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

    out_path = ("./test_files/traffic_subset.csv")

    # Extract URL from EJAM Report anchor and ensure ejam_uniq_id values are quoted for Excel
    if 'EJAM Report' in out_df.columns:
        out_df['EJAM Report'] = out_df['EJAM Report'].apply(extract_url_from_anchor)

    if 'ejam_uniq_id' in out_df.columns:
        out_df['ejam_uniq_id'] = out_df['ejam_uniq_id'].apply(force_ejam_uniq_id_to_excel_text)

    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} rows × {len(out_df.columns)} columns to {out_path}")
    # print(out_df.head(10).to_string(index=False))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dump EJAM response and create a traffic subset CSV")
    parser.add_argument('-n', '--limit', type=int, default=10,
                        help='maximum number of DataFrame rows/JSON objects to process (default: 10)')
    args = parser.parse_args()
    main(args.limit)
