import pandas
import requests
import json

# --- Helper functions (moved to top) ---------------------------------------
def dumpRequestJson(obj, filename='ejam_response.json', limit=None):
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

    # --- Simple exact-match selection of known traffic-related columns ---
    # exact column names to include (in this order)
    desired = [
        "EJSCREEN Map ejam_uniq_id","valid invalid_msg","pop","ST","statename","REGION",
        "ratio.to.avg.traffic.score","ratio.to.state.avg.traffic.score",
        "traffic.score","pctile.traffic.score","state.pctile.traffic.score",
        "avg.traffic.score","state.avg.traffic.score",
        "pctile.EJ.DISPARITY.traffic.score.eo","pctile.EJ.DISPARITY.traffic.score.supp",
        "state.pctile.EJ.DISPARITY.traffic.score.eo","state.pctile.EJ.DISPARITY.traffic.score.supp",
        "EJ.DISPARITY.traffic.score.eo","state.EJ.DISPARITY.traffic.score.eo","EJ.DISPARITY.traffic.score.supp"
    ]

    out_df = pandas.DataFrame(index=df.index)
    missing = []
    for col in desired:
        if col in df.columns:
            out_df[col] = df[col]
        else:
            out_df[col] = pandas.NA
            missing.append(col)

    # If the API provided a 'valid' (or similar) column, prefer that over heuristic extraction
    if 'valid invalid_msg' in missing:
        for c in df.columns:
            if 'valid' in c.lower():
                # copy and normalize to capitalize True/False
                out_df['valid invalid_msg'] = df[c].astype(str).str.capitalize()
                if 'valid invalid_msg' in missing:
                    missing.remove('valid invalid_msg')
                break

    # If key fields are missing, try to heuristically extract them from the raw row text
    if any(c in missing for c in ["EJSCREEN Map ejam_uniq_id", "valid invalid_msg", "pop", "ST"]):
        import re

        combined_space = df.apply(row_to_text_space, axis=1)
        combined_comma = df.apply(row_to_text_comma, axis=1)
        fips_re = re.compile(r"(\d{12})")
        # general valid match but avoid tokens immediately preceded by '=' (to skip validate_regids=FALSE)
        valid_re_general = re.compile(r"(?<!=)\b(True|False)\b", re.IGNORECASE)
        # simple valid regex for searching after the fips substring
        valid_re = re.compile(r"\b(True|False)\b", re.IGNORECASE)
        # token that reliably appears before the desired 'True' in the HTML-like output
        ejscreen_token_re = re.compile(r"EJSCREEN", re.IGNORECASE)
        # match e.g. '1276 RI' or '1276, RI' or '1276 RI Rhode'
        pop_st_re = re.compile(r"(\d{1,7})\s*,?\s*([A-Z]{2})")

        # add a separate fips column for convenience
        if 'fips' not in out_df.columns:
            out_df['fips'] = pandas.NA

        for idx in df.index:
            txts = [combined_space.iloc[idx], combined_comma.iloc[idx]]
            txt_found = ""
            for txt in txts:
                if not isinstance(txt, str):
                    continue
                # try FIPS (capture end position so we can prefer valid flags that occur after it)
                fips_pos = None
                if pandas.isna(out_df.at[idx, 'EJSCREEN Map ejam_uniq_id']):
                    m = fips_re.search(txt)
                    if m:
                        out_df.at[idx, 'EJSCREEN Map ejam_uniq_id'] = m.group(1)
                        out_df.at[idx, 'fips'] = m.group(1)
                        fips_pos = m.end()
                        txt_found += 'f'
                # try valid flag: first prefer a match after the EJSCREEN token (this avoids matching validate_regids=FALSE inside URLs)
                if pandas.isna(out_df.at[idx, 'valid invalid_msg']):
                    m = None
                    e_pos = ejscreen_token_re.search(txt)
                    if e_pos:
                        m = valid_re.search(txt[e_pos.end():])
                    if not m and fips_pos:
                        m = valid_re.search(txt[fips_pos:])
                    if not m:
                        m = valid_re_general.search(txt)
                    if m:
                        out_df.at[idx, 'valid invalid_msg'] = m.group(1).capitalize()
                        txt_found += 'v'
                # try pop + state
                if pandas.isna(out_df.at[idx, 'pop']) or pandas.isna(out_df.at[idx, 'ST']):
                    m = pop_st_re.search(txt)
                    if m:
                        out_df.at[idx, 'pop'] = m.group(1)
                        out_df.at[idx, 'ST'] = m.group(2)
                        txt_found += 'p'
                # if we've found all three in this txt, break
                if 'f' in txt_found and 'v' in txt_found and 'p' in txt_found:
                    break

    # report missing columns
    if missing:
        print("Warning: the following requested columns were not found and were filled with NA:")
        for m in missing:
            print(" -", m)

    out_path = ("./traffic_subset.csv")

    # Ensure a single 'fips' column: copy from the original ID if present, then drop the original
    if 'EJSCREEN Map ejam_uniq_id' in out_df.columns:
        if 'fips' not in out_df.columns:
            out_df['fips'] = out_df['EJSCREEN Map ejam_uniq_id']
        else:
            out_df['fips'] = out_df['fips'].fillna(out_df['EJSCREEN Map ejam_uniq_id'])
        # remove the duplicate original ID column
        out_df = out_df.drop(columns=['EJSCREEN Map ejam_uniq_id'], errors='ignore')

    # Format the `fips` column for Excel so it imports as text (e.g. ="440010301001")
    if 'fips' in out_df.columns:
        out_df['fips'] = out_df['fips'].apply(_format_id_for_excel_col)

    # Reorder columns so `fips` is the first column
    cols = list(out_df.columns)
    if 'fips' in cols:
        cols = ['fips'] + [c for c in cols if c != 'fips']
        out_df = out_df[cols]

    out_df.to_csv(out_path, index=False)
    print(f"Wrote {len(out_df)} rows × {len(out_df.columns)} columns to {out_path}")
    print(out_df.head(10).to_string(index=False))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dump EJAM response and create a traffic subset CSV")
    parser.add_argument('-n', '--limit', type=int, default=10,
                        help='maximum number of DataFrame rows/JSON objects to process (default: 10)')
    args = parser.parse_args()
    main(args.limit)
