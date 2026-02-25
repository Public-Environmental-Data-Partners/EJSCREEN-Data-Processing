"""
bld_distance_pairs.py

Purpose:
 Generate raw distance pairs between NPL sites and Census Block centroids for one or more, 
 partitioning the output into two sets: those within a 5,000-meter buffer and those where the 
 block is more than 5,000 meters from any NPL site.

Credits:
  - Derived by Anne Gunn this project's template file
  - Designed with substantial help from Gemini
  - Implemented by GitHub Copilot, GPT-5 mini, and Anne Gunn

"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import logging
import io


# Third-party
import pandas as pd
from dotenv import load_dotenv
from pyarrow import input_stream

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
    input_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/"
    # for your code, default to S3 also, defaulting to local storage here for testing
    output_path: str = "./pipeline/test_data/"
    npl_locations_file: str = "/superfund_npl/pipeline/npl_sites_with_coords.csv"
    census_blocks_weights_base: str = "/census_tables/census_block_weights_2020"  # we'll add suffixes for different states
    output_filename: str = "template_output.csv"
    state_list: str = "MT" # defaulting to very! small list of states for now
    # a lot of scripts won't use the preamble-skipping feature, but it is handy
    # to have the option when you need it.
    skip_rows: int = 0  # very input specific; your value would likely be 0



# --- Runtime arguments and help ---------------------------------------------
def get_config(argv=None) -> Config:
    # Load environment variables from a .env file so boto3 can pick up AWS creds if present
    load_dotenv()

    # pick up runtime arguments to override defaults.
    parser = argparse.ArgumentParser(description="*** Generate distance pairs for NPL sites and Census Block centroids ***")
    parser.add_argument('--input-path', dest='input_path', default=Config.input_path,
                        help=f'Folder containing input CSVs (default: {Config.input_path})')
    parser.add_argument('--output-path', dest='output_path', default=Config.output_path,
                        help=f'Folder to write the output CSV (default: {Config.output_path})')
    parser.add_argument('--npl-locations', dest='npl_locations_file', default=Config.npl_locations_file,
                        help=f'Input NPL locations CSV filename (default: {Config.npl_locations_file})')
    parser.add_argument('--output-filename', dest='output_filename', default=Config.output_filename,
                        help=f'Output combined CSV filename (default: {Config.output_filename})')
    parser.add_argument('--census-block-weights-base', dest='census_blocks_weights_base',
                        default=Config.census_blocks_weights_base,
                        help=f'Base filename/prefix for per-state census block weights (default: {Config.census_blocks_weights_base})')
    parser.add_argument('--state-list', dest='state_list', nargs='+', default=Config.state_list,
                        help=f'Space-separated list of 2-letter state abbreviations to process (default: {Config.state_list})')
    # Note, no skip-rows or join-key arguments here.
    # They should generally be hard-coded, not changeable at runtime.

    args = parser.parse_args(argv)
    return Config(
        input_path=args.input_path,
        output_path=args.output_path,
        npl_locations_file=args.npl_locations_file,
        census_blocks_weights_base=args.census_blocks_weights_base,
        output_filename=args.output_filename,
        state_list=args.state_list,
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


def get_state_list(state_input) -> list:
    """Normalize a space-or-comma-separated state list or a list into a list of 2-letter codes."""
    if isinstance(state_input, list):
        raw = state_input
    elif isinstance(state_input, str):
        # allow comma or space separated
        raw = state_input.replace(',', ' ').split()
    else:
        return []
    # normalize to upper-case two-letter codes
    states = [s.strip().upper() for s in raw if isinstance(s, str) and s.strip()]
    return states



# --- Main ----------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    cfg = get_config(argv)

    # Build input/output file paths (works for both local and s3 prefixes)
    npl_locations_path = join_path_and_file(cfg.input_path, cfg.npl_locations_file)

    # Read raw locations input
    logging.info(f"NPL locations CSV: {npl_locations_path}")
    try:
        df_input = read_csv_s3_or_local(npl_locations_path, cfg.skip_rows)
    except Exception as e:
        logging.error(f"Failed to read raw locations CSV at {npl_locations_path}: {e}")
        return 1
    logging.info(f"NPL locations rows (after header): {len(df_input)}")
    logging.info(f"NPL locations headers (first 5 of {df_input.shape[1]}): {df_input.columns[:5].tolist()}")

    # Build the list of states to process and iterate per-state block weights
    state_list = get_state_list(cfg.state_list)

    for state in state_list:
        # Require `EPA ID` column to be present and filter NPL locations to this state
        if 'EPA ID' not in df_input.columns:
            logging.error("Required column 'EPA ID' not found in NPL locations CSV for state {state}, skipping this state.")
            continue  # try the next state
        df_state = df_input[df_input['EPA ID'].astype(str).str[:2].str.upper() == state]
        logging.info(f"NPL locations rows for {state}: {len(df_state)}")

        blocks_filename = f"{cfg.census_blocks_weights_base}_{state}.csv"
        blocks_path = join_path_and_file(cfg.input_path, blocks_filename)

        logging.info(f"Block weights CSV: {blocks_path}")
        try:
            df_blocks = read_csv_s3_or_local(blocks_path, cfg.skip_rows)
        except Exception as e:
            logging.error(f"Failed to read block weights CSV at {blocks_path}: {e}")
            return 1
        logging.info(f"Block weights rows (after header) for {state}: {len(df_blocks)}")
        logging.info(f"Block weights headers (first 5 of {df_blocks.shape[1]}) for {state}: {df_blocks.columns[:5].tolist()}")

    # TODO: Add processing code here.
    return

    # Write output (s3 or local)
    out_path = join_path_and_file(cfg.output_path, cfg.output_filename)
    try:
        write_df_s3_or_local(df_output, out_path)
    except Exception as e:
        logging.error(f"Failed to write output CSV to {out_path}: {e}")
        return 1
    logging.info(f"Wrote output CSV to: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
