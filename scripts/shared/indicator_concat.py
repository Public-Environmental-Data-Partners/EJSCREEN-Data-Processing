"""
Block Group CSV Concatenator

PURPOSE:
    This script recursively searches through a parent directory for files named 
    'final_bg_scores.csv'. It extracts the 'block_group_geoid' and a user-specified 
    indicator column from each file and stacks them into a single CSV.

USAGE:
    python indicator_concat.py [path_to_folder] [indicator_column_name]

EXAMPLE:
    python shared/indicator_concat.py o3/pipeline/output/indicators o3_score

REQUIREMENTS:
    - pandas
    - os
    - argparse

NOTE:
    The script forces 'block_group_geoid' to be read as a string to preserve 
    leading zeros, which are essential for standard FIPS/GEOID formatting.

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import os
import argparse
import pandas as pd

def concatenate_csvs(root_folder, indicator_name):
    """
    Traverses subfolders to find 'final_bg_scores.csv' and stacks them.
    Saves the final result to the root_folder.
    """
    df_list = []
    
    # Ensure the root path is absolute for consistent saving
    absolute_root = os.path.abspath(root_folder)
    
    # Walk through the directory structure
    for subdir, dirs, files in os.walk(absolute_root):
        if "final_bg_scores.csv" in files:
            file_path = os.path.join(subdir, "final_bg_scores.csv")
            
            try:
                # Force GEOID to string to preserve leading zeros
                df = pd.read_csv(file_path, dtype={'block_group_geoid': str})
                
                # Check if the requested indicator column actually exists
                if indicator_name not in df.columns:
                    print(f"Warning: Column '{indicator_name}' not found in {file_path}. Skipping.")
                    continue
                
                # Keep only the two relevant columns
                df = df[['block_group_geoid', indicator_name]]
                
                ### REWORK BELOW
                # Rename column - NB: could be done upstream...
                df.rename(columns={indicator_name: indicator_name.replace("_score", "")}, inplace=True)

                # Column name lookup - NB: again, could be done upstream. Need a more efficient, streamlined process. This here is just a hack to make sure we toggle between "o3" (EJAM) and "ozone" (EJSCREEN)
                # We need "o3" early on in order to validate via EJAM but we need "ozone" later to validate and merge with EJSCREEN
                if indicator_name == "o3_score":
                    df.rename(columns={"o3": "ozone"}, inplace=True)

                df_list.append(df)
                print(f"Loaded: {file_path}")
                
            except Exception as e:
                print(f"Error reading {file_path}: {e}")

    if not df_list:
        print("No valid files found to concatenate.")
        return

    # Stack all dataframes vertically
    combined_df = pd.concat(df_list, axis=0, ignore_index=True)
    
    # Define output path (inside the root folder)
    output_filename = f"combined_{indicator_name}.csv"
    output_path = os.path.join(absolute_root, output_filename)
    
    # Save output
    combined_df.to_csv(output_path, index=False)
    print(f"\nSuccess! Saved {len(combined_df)} rows to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concatenate 'final_bg_scores.csv' files from subfolders.")
    
    # Positional arguments
    parser.add_argument("path", help="The root directory containing subfolders")
    parser.add_argument("indicator", help="The name of the indicator column to extract")

    args = parser.parse_args()

    # Run the function
    concatenate_csvs(args.path, args.indicator)