"""
Block Group CSV Concatenator

PURPOSE:
    This script searches through the score_output directory for files named 
    'final_bg_scores_XX.csv' where XX refers to a state/territory USPS abbreviation. 
    It extracts the 'block_group_geoid' and a user-specified indicator column 
    from each file and stacks them into a single CSV.

USAGE:
    python3 indicator_concat.py --indicator [name] --version [version] --location [local_or_remote]

EXAMPLE:
    python3 scripts/shared/indicator_concat.py --indicator o3 --version 1.2020 --location local

REQUIREMENTS:
    - pandas
    - argparse
    - pathlib

NOTE:
    The script forces 'block_group_geoid' to be read as a string to preserve 
    leading zeros, which are essential for standard FIPS/GEOID formatting.

    Currently runs only for local. Also assumes working in the directory root rather than scripts.

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import argparse
import pandas as pd
from pathlib import Path

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
    
    # Build the path cleanly using pathlib
    target_dir = Path("pipeline") / indicator / version_name / "score_output"
    
    if not target_dir.exists():
        print(f"Error: Directory {target_dir.absolute()} does not exist.")
        return

    print(f"Searching in: {target_dir.absolute()}")
    
    # Iterate through the files matching the pattern in the directory
    for file_path in target_dir.glob("final_bg_scores_*.csv"):
        try:
            # Force GEOID to string to preserve leading zeros
            df = pd.read_csv(file_path, dtype={'block_group_geoid': str})
            
            # Check if the requested indicator column actually exists
            if indicator_name not in df.columns:
                print(f"Warning: Column '{indicator_name}' not found in {file_path.name}. Skipping.")
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
            print(f"Loaded: {file_path.name}")
            
        except Exception as e:
            print(f"Error reading {file_path.name}: {e}")

    if not df_list:
        print("No valid files found to concatenate.")
        return

    # Stack all dataframes vertically
    combined_df = pd.concat(df_list, axis=0, ignore_index=True)
    
    # Define output path (saving up one level in the version folder)
    output_filename = f"combined_{indicator}.csv" 
    output_dir = Path("pipeline") / indicator / version_name / "score_output"
    output_path = output_dir / output_filename
    
    # Save output
    combined_df.to_csv(output_path, index=False)
    print(f"\nSuccess! Saved {len(combined_df)} rows to: {output_path.absolute()}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concatenate 'final_bg_scores_XX.csv' files from a directory.")
    
    # Required arguments mapped to flags
    parser.add_argument("--indicator", required=True, help="The name of the indicator to extract (e.g., o3)")
    parser.add_argument("--version", required=True, help="The version of the data (e.g., 1.2020)")
    parser.add_argument("--location", required=True, help="Local or remote storage")

    args = parser.parse_args()

    # Run the function
    concatenate_csvs(args.indicator, args.version, args.location)