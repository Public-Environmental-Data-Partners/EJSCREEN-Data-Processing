"""
compareEJAM2pipeline.py

Purpose:
  Compare an EJAM reference CSV and a pipeline CSV for a single US state to
  identify biases and missing rows. The script merges the two inputs on a
  provided join key (which may have different column names in each file),
  computes deltas for mapped column pairs, and emits concise outputs for
  validation and debugging.

Features:
  - Load CSVs from local filesystem or from S3 (requires boto3 and AWS creds).
  - Identify 'orphan' IDs missing in either dataset before merging.
  - Merge EJAM and pipeline rows (supports different join-key names).
  - Compute deltas for mapped pairs of columns (residual, abs diff, ratio, pct diff).
  - Produce a reduced merged CSV, a summary JSON, and a plain-text simple report.
  - Uses a per-state folder convention: read inputs from {path}/{STATE}/ and
    write outputs to {path}/{STATE}/{output_prefix}/ by default.

Usage examples:
  # REQUIRED: specify --state
  python compareEJAM2pipeline.py --state RI -p ./scripts/utilities/validation/test_files/
  python compareEJAM2pipeline.py --state RI -p s3://my-bucket/prefix/ --dry-run

Runtime parameters:
  --state / --state-code  REQUIRED. Two-letter state short code (e.g. RI). The
                          value will be upper-cased and used to build the
                          per-state folder under the configured `--path`.
  -p / --path             S3 prefix or local folder. Inputs are read from
                          `{path}/{STATE}/` and outputs are written to
                          `{path}/{STATE}/{output_prefix}/`.
  --dry-run               If set, do not write outputs; only compute and print
                          summaries.
  --input-ejam            EJAM CSV filename (default: ejam_traffic_subset.csv)
  --input-pipe            Pipeline CSV filename (default: bg_summary.csv)

Defaults (code-level):
  - input_ejam: ejam_traffic_subset.csv (looked for under {path}/{STATE}/)
  - input_pipe: bg_summary.csv (looked for under {path}/{STATE}/)
  - outputs written to: {path}/{STATE}/{output_prefix}/
  - path default: s3://pedp-data-preserved/ejscreen-data-processing/traffic/

Outputs:
  - merged_reduced.csv -- reduced merged CSV (key + compared columns) under
    {path}/{STATE}/{output_prefix}/
  - summary.json        -- numeric summaries and orphan counts under same folder
  - simpleReport.txt    -- human-readable top/bottom differences under same folder

Credits:
- Design: Gemini and Anne Gunn
- Implementation: GitHub Copilot (GPT-5 mini) and Anne Gunn
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
    state_code: str = ""
    input_ejam: str = "ejam_traffic_subset.csv"
    input_pipe: str = "bg_summary.csv"
    path: str = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/"

    join_key_ejam: str = "ejam_uniq_id"
    join_key_pipe: str = "block_group_geoid"

    # NOTE: After merge, columns could have _ref and _pipe suffixes if the names
    # are the same in the two input datasets. Currently that doesn't happen,
    # so the mapping can use the original column names.
    # Worry about this if more comparisons are added.
    mapping: str = json.dumps({
        "blk_grp_score": "traffic.score",
        # TODO: if you want to compare more than one column to traffic.score,
        # simply add it here, like:
        # "mean_score": "traffic.score",
        "total_pop": "pop"
    })

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
        tail = path[5:]
        parts = tail.split('/', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts[0], parts[1]
        try:
            import boto3
            s3 = boto3.client('s3')
            obj = s3.get_object(Bucket=bucket, Key=key)
            return pd.read_csv(io.BytesIO(obj['Body'].read()))
        except Exception as e:
            raise RuntimeError(f"Failed to read S3 CSV {path}: {e}") from e

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Local CSV not found: {path}")
    return pd.read_csv(p)


# --- Bias Analysis functions ---------------------------------------------

def identify_orphans(df_ejam: pd.DataFrame, df_pipe: pd.DataFrame, key_ejam: str, key_pipe: str) -> dict:
    a_ids = set(df_ejam[key_ejam].dropna().astype(str).unique()) if key_ejam in df_ejam.columns else set()
    b_ids = set(df_pipe[key_pipe].dropna().astype(str).unique()) if key_pipe in df_pipe.columns else set()
    return {
        "missing_in_new": sorted(list(a_ids - b_ids)),
        "missing_in_ejam": sorted(list(b_ids - a_ids))
    }


def merge_and_clean(df_ejam: pd.DataFrame, df_pipe: pd.DataFrame, key_ejam: str, key_pipe: str) -> pd.DataFrame:
    """Perform inner join with explicit suffixes to avoid column name guessing."""
    return pd.merge(
        df_ejam,
        df_pipe,
        left_on=key_ejam,
        right_on=key_pipe,
        how='inner',
        suffixes=('_ref', '_pipe')
    )


def calculate_deltas(df: pd.DataFrame, ref_col: str, pipe_col: str, label: str) -> pd.DataFrame:
    """Compute difference metrics for a pair of columns."""
    # Coerce to numeric once
    ref_vals = pd.to_numeric(df[ref_col], errors='coerce')
    pipe_vals = pd.to_numeric(df[pipe_col], errors='coerce')

    df[f"{label}_residual"] = pipe_vals - ref_vals
    df[f"{label}_abs_diff"] = np.abs(df[f"{label}_residual"])

    # Safe division for ratios and percentage
    with np.errstate(divide='ignore', invalid='ignore'):
        df[f"{label}_ratio"] = pipe_vals / ref_vals
        df[f"{label}_pct_diff"] = (df[f"{label}_residual"] / ref_vals) * 100

    # Replace inf with NaN for clean stats
    for suffix in ["_ratio", "_pct_diff"]:
        df[f"{label}{suffix}"] = df[f"{label}{suffix}"].replace([np.inf, -np.inf], np.nan)

    return df


def summarize_bias(df: pd.DataFrame, label: str) -> dict:
    """Aggregate bias statistics for the given label."""
    res = {'label': label}

    residual = df[f"{label}_residual"]
    ratio = df[f"{label}_ratio"]
    abs_diff = df[f"{label}_abs_diff"]
    pct_diff = df[f"{label}_pct_diff"]

    res['mbe'] = float(residual.mean()) if not residual.empty else None
    res['mean_ratio'] = float(ratio.dropna().mean()) if not ratio.dropna().empty else None
    res['abs_diff_max'] = float(abs_diff.max()) if not abs_diff.empty else None
    res['pct_diff_max'] = float(pct_diff.max()) if not pct_diff.empty else None
    res['ratio_std'] = float(ratio.std()) if not ratio.empty else None
    res['n_rows'] = int(len(df))

    return res


def _write_text_to_path(text: str, out_path: str) -> None:
    if is_s3_uri(out_path):
        import boto3
        tail = out_path[5:].split('/', 1)
        s3 = boto3.client('s3')
        s3.put_object(Bucket=tail[0], Key=tail[1], Body=text.encode('utf-8'))
        return
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def export_comparison_results(df_final: pd.DataFrame, summary_data: dict, out_prefix_path: str,
                              report_specs: list) -> None:
    base_path = out_prefix_path.rstrip('/')

    # 1. Save reduced CSV (Join Key + comparison cols)
    cols_to_save = [summary_data['join_key']['ejam']]
    for spec in report_specs:
        cols_to_save.extend([spec['ref'], spec['pipe']])

    # Filter only columns that actually exist
    cols_to_save = [c for c in cols_to_save if c in df_final.columns]

    csv_buf = io.StringIO()
    df_final[cols_to_save].to_csv(csv_buf, index=False)
    _write_text_to_path(csv_buf.getvalue(), f"{base_path}/merged_reduced.csv")

    # 2. Save Summary JSON
    _write_text_to_path(json.dumps(summary_data, indent=2), f"{base_path}/summary.json")

# 3. Save Simple Text Report
    lines = ["SIMPLE COMPARISON REPORT", ""]
    for spec in report_specs:
        label = spec['label']
        abs_col = f"{label}_abs_diff"
        id_col = summary_data['join_key']['ejam']

        lines.append(f"SECTION: {label.upper()}")
        lines.append("=" * (len(label) + 9))

        if all(c in df_final.columns for c in [id_col, spec['ref'], spec['pipe'], abs_col]):
            # 5 largest
            top = df_final.sort_values(by=abs_col, ascending=False).head(5)
            lines.append("5 LARGEST absolute differences:")
            lines.append(top[[id_col, spec['ref'], spec['pipe'], abs_col]].to_string(index=False))
            lines.append("")

            # 5 smallest
            bottom = df_final.sort_values(by=abs_col, ascending=True).head(5)
            lines.append("5 SMALLEST absolute differences:")
            lines.append(bottom[[id_col, spec['ref'], spec['pipe'], abs_col]].to_string(index=False))
        else:
            lines.append(f"Error: Missing columns for {label} comparison.")

        lines.append("\n" + "-"*40 + "\n")
        lines.append("\n")

    _write_text_to_path("\n".join(lines), f"{base_path}/simpleReport.txt")


# --- Main ----------------------------------------------------------------

def main(argv=None) -> None:
    cfg = get_config(argv)
    print(f"Using path: {cfg.path}")

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

    orphans = identify_orphans(df_ejam, df_pipe, cfg.join_key_ejam, cfg.join_key_pipe)
    merged = merge_and_clean(df_ejam, df_pipe, cfg.join_key_ejam, cfg.join_key_pipe)

    try:
        mapping = json.loads(cfg.mapping)
    except Exception as e:
        print(f"Invalid mapping JSON: {e}")
        return

    summaries = {}
    report_specs = []

    # Process each comparison pair defined in mapping
    for pipe_col, ref_col in mapping.items():
        if ref_col not in merged.columns or pipe_col not in merged.columns:
            print(f"Skipping {pipe_col}: Columns not found in merged data.")
            continue

        label = pipe_col
        merged = calculate_deltas(merged, ref_col, pipe_col, label)
        summaries[label] = summarize_bias(merged, label)
        report_specs.append({'label': label, 'ref': ref_col, 'pipe': pipe_col})

    overall_summary = {
        'join_key': {'ejam': cfg.join_key_ejam, 'pipeline': cfg.join_key_pipe},
        'input_ejam': ejam_path,
        'input_pipe': pipe_path,
        'orphans': {'missing_in_pipe': len(orphans['missing_in_new']),
                    'missing_in_ejam': len(orphans['missing_in_ejam'])},
        'field_summaries': summaries,
        'merged_rows': len(merged)
    }

    out_prefix = join_path_and_file(cfg.path, f"{cfg.state_code}/{cfg.output_prefix}")
    if cfg.dry_run:
        print(json.dumps(overall_summary, indent=2))
    else:
        try:
            export_comparison_results(merged, overall_summary, out_prefix, report_specs)
            print(f"Done. Outputs written to {out_prefix}")
        except Exception as e:
            print(f"Error writing outputs: {e}")


if __name__ == '__main__':
    main()

