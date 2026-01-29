import json
from pathlib import Path
import pandas as pd
from typing import Optional


# --- Helper functions ------------------------------------------------------
def load_geojson_properties(path: str) -> pd.DataFrame:
    """Load a GeoJSON file and return a DataFrame made from feature properties.

    The geometry is ignored. Expects a GeoJSON FeatureCollection with a 'features' list
    where each feature has a 'properties' mapping.
    """
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

    The CSV will include column names derived from the DataFrame fields.
    """
    out_p = Path(out_path)

    if limit is None:
        use_df = df
    elif limit <= 0:
        # treat non-positive as 'no limit'
        use_df = df
    else:
        use_df = df.head(limit)

    out_p.parent.mkdir(parents=True, exist_ok=True)
    use_df.to_csv(out_p, index=False)


# helper to format identifiers so Excel imports them as text (e.g. ="440010301001")
def _format_id_for_excel_col(v):
    if pd.isna(v):
        return v
    s = str(v)
    if s.startswith('="') and s.endswith('"'):
        return s
    return f'="{s}"'


# --- Main ------------------------------------------------------------------
def main(limit: int = 10, input_geojson: str = 'ri_bg_summary.geojson', output_csv: str = 'ri_bg_summary.csv', dry_run: bool = False) -> None:
    """Read input GeoJSON, ignore geometry, and write CSV of properties limited to `limit` rows.

    limit <= 0 means no limit (write all rows). Default limit is 10.
    If dry_run is True, do not write any files — just show what would be written.
    """
    try:
        df = load_geojson_properties(input_geojson)
    except Exception as e:
        print(f"Error loading GeoJSON '{input_geojson}': {e}")
        raise

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

    # show a small preview
    if not used_df.empty:
        print(used_df.head(10).to_string(index=False))
    else:
        print("No rows written (empty input file)")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Convert a GeoJSON (FeatureCollection) to CSV of properties (ignore geometry).')
    parser.add_argument('-i', '--input', default='ri_bg_summary.geojson', help='input GeoJSON file path (default: ri_bg_summary.geojson)')
    parser.add_argument('-o', '--output', default='ri_bg_summary.csv', help='output CSV file path (default: ri_bg_summary.csv)')
    parser.add_argument('-n', '--limit', type=int, default=10,
                        help='maximum number of rows to write to CSV; <=0 means no limit; default 10')
    parser.add_argument('--dry-run', action='store_true', help='do not write files, only show what would be written')

    args = parser.parse_args()
    main(limit=args.limit, input_geojson=args.input, output_csv=args.output, dry_run=args.dry_run)
