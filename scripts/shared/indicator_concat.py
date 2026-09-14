"""
Block Group CSV Concatenator

PURPOSE:
    This script searches through the score_output directory for files named 
    'final_bg_scores_XX.csv' where XX refers to a state/territory USPS abbreviation. 
    It extracts the 'block_group_geoid' and a user-specified indicator column 
    from each file and stacks them into a single CSV.

USAGE:
    python scripts/shared/indicator_concat.py --indicator [name] --version [version] --location [local|remote]

EXAMPLE:
    python scripts/shared/indicator_concat.py --indicator o3 --version 1.2020 --location local

REQUIREMENTS:
    Must be run within the project virtual environment (`uv run` or activated `.venv`) to have access to
    all required Python packages (e.g. pandas) and be runnable from any folder.

NOTE:
    The script forces 'block_group_geoid' to be read as a string to preserve 
    leading zeros, which are essential for standard FIPS/GEOID formatting.

    Supports both local and remote S3 storage through fsspec.

AUTHORSHIP:
    Eric Nost and Google Gemini
    Updated for remote IO by Anne Gunn and GitHub CoPilot agents
"""

import argparse
import importlib
import pandas as pd
from pathlib import Path
import sys

import scripts.shared.resolve_path as resolve_path


def load_fsspec_module():
    return importlib.import_module("fsspec")


def initialize_runtime_dependencies(location):
    if location != "remote":
        return
    importlib.import_module("s3fs")
    dotenv = importlib.import_module("dotenv")
    dotenv.load_dotenv()


def is_s3_uri(path):
    return isinstance(path, str) and path.lower().startswith("s3://")


def join_root_and_relative_path(root_path, relative_path):
    if is_s3_uri(root_path):
        return root_path.rstrip("/") + "/" + relative_path.lstrip("/")
    return str(Path(root_path) / Path(relative_path))


def get_file_name(path):
    return path.rstrip("/").rsplit("/", 1)[-1]


def concatenate_csvs(indicator, version, location):
    """
    Finds all 'final_bg_scores_XX.csv' in the target directory and stacks them.
    Saves the final result to the parent version folder.
    """
    df_list = []
    
    # Let users set the indicator in plain terms e.g. o3 then translate to the column name
    indicator_name = f"{indicator}_score" 
    # Let users set the version name in plain terms e.g. 1.2020 then translate to the specific path
    version_name = f"v{version}"
    
    initialize_runtime_dependencies(location)
    indicator_root = resolve_path.get_indicator_root(indicator, version, location)

    # Resolve the configured root instead of relying on the current working directory.
    target_dir = join_root_and_relative_path(
        indicator_root,
        f"{version_name}/score_output",
    )
    target_pattern = join_root_and_relative_path(
        target_dir,
        "final_bg_scores_*.csv",
    )
    
    if not is_s3_uri(target_dir) and not Path(target_dir).exists():
        print(f"Error: Directory {Path(target_dir).absolute()} does not exist.")
        return 1

    display_target_dir = target_dir if is_s3_uri(target_dir) else str(Path(target_dir).absolute())
    print(f"Searching in: {display_target_dir}")
    
    fsspec = load_fsspec_module()
    # fsspec expands the pattern for both local and remote filesystems.
    file_count = 0
    for file_handle in fsspec.open_files(target_pattern, mode="rb"):
        file_name = get_file_name(file_handle.path)
        try:
            with file_handle as input_stream:
                # Force GEOID to string to preserve leading zeros
                df = pd.read_csv(input_stream, dtype={'block_group_geoid': str})
            
            # Check if the requested indicator column actually exists
            if indicator_name not in df.columns:
                print(f"Warning: Column '{indicator_name}' not found in {file_name}. Skipping.")
                continue
            
            # Keep only the two relevant columns
            df = df[['block_group_geoid', indicator_name]]
            
            # Rename column - removing "_score"
            df.rename(columns={indicator_name: indicator}, inplace=True)

            # Column name lookup - toggle between "o3" (EJAM) and "ozone" (EJSCREEN)
            # We need "o3" early on to validate via EJAM but "ozone" later to merge with EJSCREEN
            # Same with wastewater...
            if indicator == "o3":
                df.rename(columns={"o3": "ozone"}, inplace=True)
            elif indicator == "wastewater":
                df.rename(columns={"wastewater": "pwdis"}, inplace=True)

            df_list.append(df)
            file_count += 1
            print(f"Loaded: {file_name}")
            
        except Exception as e:
            print(f"Error reading {file_name}: {e}")

    if df_list:
            print(f"Total files loaded: {file_count}")
    else:
        print("No valid files found to concatenate.")
        return 1


    # Stack all dataframes vertically
    combined_df = pd.concat(df_list, axis=0, ignore_index=True)
    
    # Define output path (saving up one level in the version folder)
    output_filename = f"combined_{indicator}.csv" 
    output_path = join_root_and_relative_path(target_dir, output_filename)
    
    # fsspec does not create local parent directories automatically.
    if not is_s3_uri(output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Use the same output stream handling for local and remote destinations.
    with fsspec.open(output_path, "w", encoding="utf-8", newline="") as output_stream:
        combined_df.to_csv(output_stream, index=False)

    display_output_path = output_path if is_s3_uri(output_path) else str(Path(output_path).absolute())
    print(f"\nSuccess! Saved {len(combined_df)} rows to: {display_output_path}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concatenate 'final_bg_scores_XX.csv' files from a directory.")
    
    # Required arguments mapped to flags
    parser.add_argument("--indicator", required=True, help="The name of the indicator to extract (e.g., o3)")
    parser.add_argument("--version", required=True, help="The version of the data (e.g., 1.2020)")
    parser.add_argument("--location", required=True, help="Local or remote storage")

    args = parser.parse_args()

    # Run the function
    sys.exit(concatenate_csvs(args.indicator, args.version, args.location))