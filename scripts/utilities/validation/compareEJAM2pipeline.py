"""
compareEJAM2pipeline.py

Purpose:
  Compare EJAM reference CSV and a pipeline CSV to identify biases and missing rows.
  The script merges the two inputs on a provided join key (which may have different
  column names in each file), computes deltas for mapped column pairs, and emits
  concise outputs for validation and debugging.

Features:
  - Load CSVs from local filesystem or from S3 (requires boto3 and AWS creds).
  - Identify 'orphan' IDs missing in either dataset before merging.
  - Merge EJAM and pipeline rows (supports different join-key names).
  - Compute deltas for mapped pairs of columns (residual, abs diff, ratio, pct diff).
  - Produce a reduced merged CSV, a summary JSON, and a plain-text simple report.

Usage examples:
  python compareEJAM2pipeline.py -p ./scripts/utilities/validation/test_files/ --dry-run
  python compareEJAM2pipeline.py -p s3://my-bucket/prefix/ --input-ejam ejam.csv --input-pipe pipe.csv

Defaults:
  - input_ejam: ri_ejam_traffic_subset.csv
  - input_pipe: ri_bg_summary.csv
  - default path: s3://pedp-data-preserved/ejscreen-data-processing/traffic/

Outputs:
  - {path}/{output_prefix}/merged_reduced.csv  (key + compared columns)
  - {path}/{output_prefix}/summary.json        (numeric summaries and orphan counts)
  - {path}/{output_prefix}/simpleReport.txt    (human-readable top/bottom diffs)

Credits:
- Design: Gemini and Anne Gunn
- Implementation: GitHub Copilot (GPT-5 mini) and Anne Gunn

Non-destructive of the input files: the script reads inputs and writes only the specified
outputs; it is safe to run in a CI/manual validation workflow.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
import pandas as pd
from dotenv import load_dotenv
import io
import json
import numpy as np


# --- Configuration --------------------------------------------------------
@dataclass
class Config:
    # State code for per-state subfolder (USPS-style short code), upper-cased
    state_code: str = ""
    input_ejam: str = "ejam_traffic_subset.csv"
    input_pipe: str = "bg_summary.csv"
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"
    # local test path to eliminate needing the runtime override
    # path = "./test_files/"
    # Join key names may differ between EJAM and pipeline outputs
    join_key_ejam: str = "ejam_uniq_id"      # column name in EJAM CSV
    join_key_pipe: str = "block_group_geoid"  # column name in pipeline CSV (ri_bg_summary.csv)
    # mapping: JSON string mapping new_pipe column names -> ejam column names
    mapping: str = json.dumps({"blk_grp_score":"traffic.score", "total_pop":"pop"})
    output_prefix: str = "comparison_results"
    dry_run: bool = False


def get_config(argv=None) -> Config:
    """Parse CLI args and environment into a Config object."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Compare EJAM CSV and pipeline CSV (local or S3) with bias summaries")
    # Preferred order: state_code, path, dry_run, then others
    parser.add_argument('--state', '--state-code', dest='state_code', type=str, required=True,
                        help='State code (e.g. RI); will be upper-cased and used to select the state subfolder')
    parser.add_argument('-p', '--path', type=str, default=Config.path,
                        help='S3 prefix or local folder containing the CSVs')
    parser.add_argument('--dry-run', action='store_true', help='If set, do not write outputs; only compute and print summaries')
    parser.add_argument('--input-ejam', type=str, default=Config.input_ejam,
                        help='EJAM CSV filename')
    parser.add_argument('--input-pipe', type=str, default=Config.input_pipe,
                        help='Pipeline CSV filename')
    parser.add_argument('--join-key-ejam', type=str, default=Config.join_key_ejam,
                        help='Column name used as unique join key in EJAM CSV (default: ejam_uniq_id)')
    parser.add_argument('--join-key-pipe', type=str, default=Config.join_key_pipe,
                        help='Column name used as unique join key in pipeline CSV (default: block_group_geoid)')
    parser.add_argument('--mapping', type=str, default=Config.mapping,
                        help='JSON string mapping new_pipe columns to ejam columns, e.g. "{\"traffic_new\": \"traffic\"}"')
    parser.add_argument('--output-prefix', type=str, default=Config.output_prefix,
                        help='Prefix for output filenames written to the path')
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

# --- Bias Analysis functions ---------------------------------------------

def identify_orphans(df_ejam: pd.DataFrame, df_new_pipe: pd.DataFrame, key_ejam: str, key_new: str) -> dict:
    """Return dict with lists of IDs missing in either dataset before merging.

    key_ejam: column name in EJAM (reference) DataFrame
    key_new: column name in pipeline (new) DataFrame
    """
    a_ids = set(df_ejam[key_ejam].dropna().astype(str).unique()) if key_ejam in df_ejam.columns else set()
    b_ids = set(df_new_pipe[key_new].dropna().astype(str).unique()) if key_new in df_new_pipe.columns else set()
    missing_in_new = sorted(list(a_ids - b_ids))
    missing_in_ejam = sorted(list(b_ids - a_ids))
    return {"missing_in_new": missing_in_new, "missing_in_ejam": missing_in_ejam}


def merge_and_clean(df_ejam: pd.DataFrame, df_new_pipe: pd.DataFrame, key_ejam: str, key_new: str) -> pd.DataFrame:
    """Inner join on the provided keys (which may have different names) and return merged DataFrame.

    The function performs merge with left_on=key_ejam and right_on=key_new and uses suffixes
    to keep columns distinct when names collide.
    """
    if key_ejam not in df_ejam.columns:
        raise KeyError(f"Join key '{key_ejam}' not found in EJAM DataFrame")
    if key_new not in df_new_pipe.columns:
        raise KeyError(f"Join key '{key_new}' not found in new pipeline DataFrame")
    merged = pd.merge(df_ejam, df_new_pipe, left_on=key_ejam, right_on=key_new, how='inner', suffixes=('_ref', '_new'))
    return merged


def _resolve_merged_col(merged: pd.DataFrame, ref_name: str, new_name: str) -> tuple:
    """Given original column names, determine actual merged column names.

    Returns (ref_col, new_col) as they appear in merged DataFrame.
    """
    # Prefer exact appearances first
    ref_col = ref_name if ref_name in merged.columns else None
    new_col = new_name if new_name in merged.columns else None
    # If not found, try suffixes
    if ref_col is None and f"{ref_name}_ref" in merged.columns:
        ref_col = f"{ref_name}_ref"
    if new_col is None and f"{new_name}_new" in merged.columns:
        new_col = f"{new_name}_new"
    # Also try the opposite (in case mapping used same name)
    if ref_col is None and f"{ref_name}_new" in merged.columns:
        ref_col = f"{ref_name}_new"
    if new_col is None and f"{new_name}_ref" in merged.columns:
        new_col = f"{new_name}_ref"
    return ref_col, new_col


def calculate_deltas(df_merged: pd.DataFrame, ref_col: str, new_col: str, label: str) -> pd.DataFrame:
    """Append delta columns for a specific ref/new column pair.

    Creates columns: {label}_residual, {label}_abs_diff, {label}_ratio, {label}_pct_diff
    Uses NaN for ratios where ref is zero or missing. Coerces values to numeric.
    """
    if ref_col not in df_merged.columns or new_col not in df_merged.columns:
        raise KeyError(f"Columns not found in merged DataFrame: {ref_col}, {new_col}")

    # Coerce to numeric (float), preserving NaN for non-numeric values
    ref_vals = pd.to_numeric(df_merged[ref_col], errors='coerce').astype('float64')
    new_vals = pd.to_numeric(df_merged[new_col], errors='coerce').astype('float64')

    residual = new_vals - ref_vals
    # abs_diff using numpy to be robust if residual is ndarray-like
    abs_diff = np.abs(residual)

    # Compute ratio safely: use NaN where ref_vals == 0 or ref is NaN
    with np.errstate(divide='ignore', invalid='ignore'):
        raw_ratio = new_vals / ref_vals
    # Ensure we have a pandas Series so .replace is available and index preserved
    ratio = pd.Series(raw_ratio, index=df_merged.index)
    ratio = ratio.replace([np.inf, -np.inf], np.nan)

    # Percentage difference relative to ref; NaN when ref is zero/NaN
    raw_pct = (residual / ref_vals) * 100
    pct_diff = pd.Series(raw_pct, index=df_merged.index)
    pct_diff = pct_diff.replace([np.inf, -np.inf], np.nan)

    df_merged[f"{label}_residual"] = residual
    df_merged[f"{label}_abs_diff"] = abs_diff
    df_merged[f"{label}_ratio"] = ratio
    df_merged[f"{label}_pct_diff"] = pct_diff
    # Valid-ratio mask: True where reference > 0 and both values are finite
    try:
        # ensure a pandas Series so .notna() is available to static checkers
        ref_numeric = pd.Series(pd.to_numeric(df_merged[ref_col], errors='coerce'), index=df_merged.index)
        valid_ratio_mask = (ref_numeric > 0) & ref_numeric.notna()
    except Exception:
        valid_ratio_mask = pd.Series([False] * len(df_merged), index=df_merged.index)
    df_merged[f"{label}_valid_ratio"] = valid_ratio_mask

    return df_merged


def summarize_bias(df_deltas: pd.DataFrame, label: str) -> dict:
    """Aggregate bias statistics for the given label from delta columns."""
    res = {}
    res['label'] = label
    res_cols = {
        'residual': f"{label}_residual",
        'abs_diff': f"{label}_abs_diff",
        'ratio': f"{label}_ratio",
        'pct_diff': f"{label}_pct_diff",
    }
    # Ensure columns exist
    for k, col in res_cols.items():
        if col not in df_deltas.columns:
            res[f"error_{k}"] = f"Missing column {col}"
            return res

    residual = df_deltas[res_cols['residual']]
    ratio = df_deltas[res_cols['ratio']]
    abs_diff = df_deltas[res_cols['abs_diff']]
    pct_diff = df_deltas[res_cols['pct_diff']]

    # Mean Bias Error (MBE)
    res['mbe'] = float(residual.mean(skipna=True)) if len(residual) > 0 else None
    # Mean Ratio: prefer using the precomputed valid-ratio mask (ref > 0)
    valid_mask_col = f"{label}_valid_ratio"
    if valid_mask_col in df_deltas.columns:
        valid_ratio_mask = df_deltas[valid_mask_col].astype(bool)
    else:
        valid_ratio_mask = ~ratio.isna()

    if valid_ratio_mask.any():
        mean_ratio = float(ratio[valid_ratio_mask].mean())
    else:
        mean_ratio = None
    res['mean_ratio'] = mean_ratio

    # Outliers and consistency
    res['abs_diff_max'] = float(abs_diff.max(skipna=True)) if len(abs_diff) > 0 else None
    res['abs_diff_min'] = float(abs_diff.min(skipna=True)) if len(abs_diff) > 0 else None
    res['pct_diff_max'] = float(pct_diff.max(skipna=True)) if len(pct_diff) > 0 else None
    res['pct_diff_min'] = float(pct_diff.min(skipna=True)) if len(pct_diff) > 0 else None
    res['ratio_std'] = float(ratio.std(skipna=True)) if len(ratio) > 0 else None

    # counts
    res['n_rows'] = int(len(df_deltas))
    res['n_valid_ratio'] = int(valid_ratio_mask.sum())

    return res


def _write_text_to_path(text: str, out_path: str) -> None:
    """Write text to local path or S3 key."""
    if is_s3_uri(out_path):
        tail = out_path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI: {out_path}")
        bucket, key = parts[0], parts[1]
        import boto3
        s3 = boto3.client('s3')
        s3.put_object(Bucket=bucket, Key=key, Body=text.encode('utf-8'))
        return
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(text)


def export_comparison_results(df_final: pd.DataFrame, summary_data: dict, out_prefix_path: str, emit_columns=None, report_specs=None) -> None:
    """Write merged DataFrame and summary JSON to out_prefix_path (S3 or local).

    out_prefix_path is a folder/prefix; the function will create two files:
      {out_prefix_path.rstrip('/')}/merged.csv
      {out_prefix_path.rstrip('/')}/summary.json
    If out_prefix_path is an S3 URI the files will be uploaded to that bucket/key prefix.
    """
    # write a reduced merged CSV (key and compared columns) to avoid large outputs
    merged_name = out_prefix_path.rstrip('/') + '/' + 'merged_reduced.csv'
    summary_name = out_prefix_path.rstrip('/') + '/' + 'summary.json'

    # Write reduced merged CSV (only key and selected columns) if emit_columns provided,
    # otherwise fall back to full merged DataFrame.
    to_write_df = df_final
    if emit_columns is not None:
        # only include columns that actually exist in df_final
        cols = [c for c in emit_columns if c in df_final.columns]
        to_write_df = df_final[cols]

    if is_s3_uri(merged_name):
        # write to buffer and upload
        buf = io.StringIO()
        to_write_df.to_csv(buf, index=False)
        buf.seek(0)
        tail = merged_name[5:]
        parts = tail.split('/', 1)
        bucket, key = parts[0], parts[1]
        import boto3
        s3 = boto3.client('s3')
        s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode('utf-8'))
        print(f"Uploaded merged CSV to {merged_name}")
    else:
        p = Path(merged_name)
        p.parent.mkdir(parents=True, exist_ok=True)
        to_write_df.to_csv(p, index=False)
        print(f"Wrote reduced merged CSV to {merged_name}")

    # Write summary JSON
    summary_text = json.dumps(summary_data, indent=2)
    _write_text_to_path(summary_text, summary_name)
    print(f"Wrote summary JSON to {summary_name}")

    # Create a simple text report if requested
    if report_specs:
        lines = []
        lines.append("SIMPLE COMPARISON REPORT")
        lines.append("")
        # For each requested section (e.g. 'score', 'population') create headings and two tables
        for section_name, spec in report_specs.items():
            label = spec.get('label')
            ref_col = spec.get('ref')
            new_col = spec.get('new')
            id_col = spec.get('id_col', list(df_final.columns)[0])
            abs_col = f"{label}_abs_diff"

            lines.append(f"SECTION: {section_name.upper()}")
            lines.append("")

            if abs_col in df_final.columns and ref_col in df_final.columns and new_col in df_final.columns:
                df_tmp = df_final[[id_col, ref_col, new_col, abs_col]].copy()
                df_tmp[ref_col] = pd.to_numeric(df_tmp[ref_col], errors='coerce')
                df_tmp[new_col] = pd.to_numeric(df_tmp[new_col], errors='coerce')
                df_tmp[abs_col] = pd.to_numeric(df_tmp[abs_col], errors='coerce')

                # 5 largest absolute differences
                top = df_tmp.sort_values(by=abs_col, ascending=False).head(5)
                lines.append("5 largest absolute differences")
                col_names = ["id", ref_col, new_col, "abs_diff"]
                rows = []
                for _, r in top.iterrows():
                    idv = str(r[id_col])
                    refv = f"{r[ref_col]:.6g}" if pd.notna(r[ref_col]) else ""
                    newv = f"{r[new_col]:.6g}" if pd.notna(r[new_col]) else ""
                    absv = f"{r[abs_col]:.6g}" if pd.notna(r[abs_col]) else ""
                    rows.append([idv, refv, newv, absv])
                if rows:
                    widths = [max(len(str(c)), max((len(row[i]) for row in rows), default=0)) for i, c in enumerate(col_names)]
                    header_line = '  '.join(col_names[i].ljust(widths[i]) for i in range(len(col_names)))
                    lines.append(header_line)
                    lines.append('-' * (sum(widths) + 2 * (len(widths)-1)))
                    for row in rows:
                        line = '  '.join(row[i].ljust(widths[i]) for i in range(len(row)))
                        lines.append(line)
                else:
                    lines.append("(no rows)")

                lines.append("")
                # Bottom 5 smallest absolute differences
                bottom = df_tmp.sort_values(by=abs_col, ascending=True).head(5)
                lines.append("5 smallest absolute differences")
                rows = []
                for _, r in bottom.iterrows():
                    idv = str(r[id_col])
                    refv = f"{r[ref_col]:.6g}" if pd.notna(r[ref_col]) else ""
                    newv = f"{r[new_col]:.6g}" if pd.notna(r[new_col]) else ""
                    absv = f"{r[abs_col]:.6g}" if pd.notna(r[abs_col]) else ""
                    rows.append([idv, refv, newv, absv])
                if rows:
                    widths = [max(len(str(c)), max((len(row[i]) for row in rows), default=0)) for i, c in enumerate(col_names)]
                    header_line = '  '.join(col_names[i].ljust(widths[i]) for i in range(len(col_names)))
                    lines.append(header_line)
                    lines.append('-' * (sum(widths) + 2 * (len(widths)-1)))
                    for row in rows:
                        line = '  '.join(row[i].ljust(widths[i]) for i in range(len(row)))
                        lines.append(line)
                else:
                    lines.append("(no rows)")
            else:
                lines.append(f"No delta column found for label '{label}' (expected {abs_col}, {ref_col}, {new_col})")

            lines.append('')

        report_text = '\n'.join(lines)
        report_path = out_prefix_path.rstrip('/') + '/' + 'simpleReport.txt'
        _write_text_to_path(report_text, report_path)
        print(f"Wrote simple report to {report_path}")


# --- Main ----------------------------------------------------------------

def main(argv=None) -> None:
    cfg = get_config(argv)
    print(f"Using path: {cfg.path}")

    # Assume inputs live under a per-state subfolder
    ejam_path = join_path_and_file(cfg.path, f"{cfg.state_code}/{cfg.input_ejam}")
    pipe_path = join_path_and_file(cfg.path, f"{cfg.state_code}/{cfg.input_pipe}")

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

    # Ensure join keys exist (they may have different names in each file)
    key_ejam = cfg.join_key_ejam
    key_new = cfg.join_key_pipe
    if key_ejam not in df_ejam.columns:
        print(f"Join key '{key_ejam}' not found in EJAM dataframe columns: {df_ejam.columns.tolist()}")
        return
    if key_new not in df_pipe.columns:
        print(f"Join key '{key_new}' not found in pipeline dataframe columns: {df_pipe.columns.tolist()}")
        return

    orphans = identify_orphans(df_ejam, df_pipe, key_ejam, key_new)
    print(f"Orphans: {len(orphans['missing_in_new'])} missing in new pipeline, {len(orphans['missing_in_ejam'])} missing in ejam")

    merged = merge_and_clean(df_ejam, df_pipe, key_ejam, key_new)
    print(f"Merged rows (inner join): {len(merged)}")

    # Parse mapping
    try:
        mapping = json.loads(cfg.mapping)
    except Exception as e:
        print(f"Invalid mapping JSON provided: {e}")
        return

    summaries = {}
    # For each mapping pair (new_col -> ref_col)
    for new_col, ref_col in mapping.items():
        # Resolve how columns appear in merged dataframe
        ref_col_merged, new_col_merged = _resolve_merged_col(merged, ref_col, new_col)
        if not ref_col_merged or not new_col_merged:
            print(f"Skipping pair (new={new_col}, ref={ref_col}) - couldn't find merged columns: ref={ref_col_merged}, new={new_col_merged}")
            continue
        label = new_col  # use new column name as label
        merged = calculate_deltas(merged, ref_col_merged, new_col_merged, label)
        summaries[label] = summarize_bias(merged, label)
        print(f"Computed deltas and summary for {label}")

    # Aggregate overall summary
    overall_summary = {
        'join_key': {'ejam': key_ejam, 'pipeline': key_new},
        'input_ejam': ejam_path,
        'input_pipe': pipe_path,
        'orphans': {'missing_in_new': len(orphans['missing_in_new']), 'missing_in_ejam': len(orphans['missing_in_ejam'])},
        'field_summaries': summaries,
        'merged_rows': len(merged)
    }

    # build emit_columns: include the ejam key and each resolved ref/new column pair
    emit_columns = None
    mapping_info = {}
    try:
        emit_pairs = []
        for new_col, ref_col in mapping.items():
            ref_col_merged, new_col_merged = _resolve_merged_col(merged, ref_col, new_col)
            if ref_col_merged and new_col_merged:
                emit_pairs.append((ref_col_merged, new_col_merged))
                # store mapping info keyed by label (new_col)
                mapping_info[new_col] = {'label': new_col, 'ref': ref_col_merged, 'new': new_col_merged}
        if emit_pairs:
            # prefer ejam key column name as the primary key in output
            emit_columns = [key_ejam]
            for a, b in emit_pairs:
                # include ref then new
                emit_columns.extend([a, b])
    except Exception:
        emit_columns = None

    # prepare report_specs by heuristics: find which mapping entries correspond to score and population
    report_specs = {}
    # heuristics on names
    def is_score(name):
        n = name.lower()
        return 'score' in n or 'traffic' in n
    def is_pop(name):
        n = name.lower()
        return 'pop' in n or 'population' in n

    # find candidates
    score_spec = None
    pop_spec = None
    for k, info in mapping_info.items():
        # check both ref and new names
        if score_spec is None and (is_score(info['label']) or is_score(info['ref']) or is_score(info['new'])):
            score_spec = {'label': info['label'], 'ref': info['ref'], 'new': info['new'], 'id_col': key_ejam}
        if pop_spec is None and (is_pop(info['label']) or is_pop(info['ref']) or is_pop(info['new'])):
            pop_spec = {'label': info['label'], 'ref': info['ref'], 'new': info['new'], 'id_col': key_ejam}

    # fallback if not found
    if score_spec is None and mapping_info:
        first = next(iter(mapping_info.values()))
        score_spec = {'label': first['label'], 'ref': first['ref'], 'new': first['new'], 'id_col': key_ejam}
    if pop_spec is None and len(mapping_info) > 1:
        # pick second if available
        second = list(mapping_info.values())[1]
        pop_spec = {'label': second['label'], 'ref': second['ref'], 'new': second['new'], 'id_col': key_ejam}

    if score_spec:
        report_specs['score'] = score_spec
    if pop_spec:
        report_specs['population'] = pop_spec

    # Write outputs unless dry-run
    # write outputs under the per-state subfolder
    out_prefix = join_path_and_file(cfg.path, f"{cfg.state_code}/{cfg.output_prefix}")
    if cfg.dry_run:
        print(json.dumps(overall_summary, indent=2))
    else:
        try:
            export_comparison_results(merged, overall_summary, out_prefix, emit_columns=emit_columns, report_specs=report_specs)
        except Exception as e:
            print(f"Error writing outputs: {e}")


if __name__ == '__main__':
    main()

