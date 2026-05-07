"""
EJSCREEN Data Swapper
PURPOSE:
    1. Loads a copy of a local EJSCREEN CSV archive.
    2. For a specified indicator (e.g., 'ozone'):
        - Wipes all data values in variant columns (e.g., 'P_OZONE', 'B_OZONE') 
          but keeps the headers.
        - Completely drops the base indicator column to allow for replacement.
    3. Merges local data from the concat script via an outer join on 'ID'.

USAGE:
    python script_name.py [local_concat_csv] [indicator_name]

EXAMPLE:
    python shared/ejscreen_merge.py o3/pipeline/output/indicators/combined_o3_score.csv ozone

INPUT FILE:
    shared/pipeline/preprocessed_input/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import os
import sys
import pandas as pd
import re
import numpy as np

# File configuration
EJSCREEN_PATH = "shared/pipeline/preprocessed_input/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv"

def main(local_csv_path, indicator_name):
    # 1. Path Verification
    if not os.path.exists(EJSCREEN_PATH):
        print(f"Error: EJSCREEN archive not found at {EJSCREEN_PATH}")
        return
    if not os.path.exists(local_csv_path):
        print(f"Error: Local CSV {local_csv_path} not found.")
        return

    print(f"--- Step 1: Loading EJSCREEN archive ---")
    # Low memory False helps with the large number of EJSCREEN columns
    df_main = pd.read_csv(EJSCREEN_PATH, dtype={'ID': str}, low_memory=False)
    
    # 2. Handle Indicator Variants
    print(f"--- Step 2: Wiping data for variants of '{indicator_name}' ---")
    
    # Regex to find any column containing the indicator name
    pattern = re.compile(f".*{indicator_name}.*", re.IGNORECASE)
    all_matching_cols = [c for c in df_main.columns if pattern.match(c)]
    
    # Identify the exact base column to drop (the one that matches the input exactly)
    # We drop this because your concat script provides the new 'ground truth' values
    base_col_to_drop = next((c for c in all_matching_cols if c.lower() == indicator_name.lower()), None)
    
    # Identify variants to wipe (everything else that matched the regex)
    variants_to_wipe = [c for c in all_matching_cols if c != base_col_to_drop]

    # Wipe values but keep headers
    if variants_to_wipe:
        print(f"Wiping data (keeping headers) for: {variants_to_wipe}")
        df_main[variants_to_wipe] = np.nan
    
    # Drop the base column
    if base_col_to_drop:
        print(f"Dropping base column for replacement: {base_col_to_drop}")
        df_main = df_main.drop(columns=[base_col_to_drop])

    # 3. Load local concat data and join
    print(f"--- Step 3: Merging local scores ---")
    df_local = pd.read_csv(local_csv_path, dtype={'block_group_geoid': str})
    
    # Rename local ID to 'ID' to match EJSCREEN primary key
    df_local = df_local.rename(columns={'block_group_geoid': 'ID'})

    # Recase indicator column....
    df_local = df_local.rename(columns={indicator_name: indicator_name.upper()})

    # Outer join on ID
    # This places your new indicator data into the dataframe
    df_final_test = pd.merge(df_main, df_local, on='ID', how='outer')

    # 4. Save Output
    output_name = "ejscreen_test_replaced.csv"
    df_final_test.to_csv(output_name, index=False)
    
    print(f"\nSuccess!")
    print(f"Final file saved: {output_name}")
    print(f"Total columns: {len(df_final_test.columns)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python script.py [local_concat_csv] [indicator_name]")
    else:
        main(sys.argv[1], sys.argv[2])