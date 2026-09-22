"""
EJSCREEN MERGE
PURPOSE:
    1. Loads a copy of a local EJSCREEN CSV archive.
    2. For a specified indicator (e.g., 'ozone'):
        - Wipes all data values in variant columns (e.g., 'P_OZONE', 'B_OZONE') 
          but keeps the headers.
        - Completely drops the base indicator column to allow for replacement.
    3. Merges local data from the concat script via an outer join on 'ID'.

USAGE:
    python3 ejscreen_merge.py  --indicators [name] --version [version] --location [local_or_remote]

EXAMPLE:
    python3 scripts/shared/ejscreen_merge.py --indicators ozone,pm25 --version 1.2020 --location local

INPUT FILE:
    pipeline/shared/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv

OUTPUT FILE:
    pipeline/shared/ejscreen/v{version}/envirodata_{version}.csv

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import os
import sys
import pandas as pd
import re
import numpy as np
import argparse
from pathlib import Path

# File configuration
EJSCREEN_PATH = "pipeline/shared/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv"

def ejscreen_merge(indicators, version, location):
    # 1. Path Verification
    if not os.path.exists(EJSCREEN_PATH):
        print(f"Error: EJSCREEN archive not found at {EJSCREEN_PATH}")
        return
    
    print(f"--- Step 1: Loading EJSCREEN archive ---")
    # Low memory False helps with the large number of EJSCREEN columns
    df_main = pd.read_csv(EJSCREEN_PATH, dtype={'ID': str}, low_memory=False)

    # print(indicators, version, location)

    for indicator in indicators:
        local_csv_path = f"pipeline/{indicator}/v{version}/score_output/combined_{indicator}.csv"

        if not os.path.exists(local_csv_path):
            print(f"Error: Local CSV {local_csv_path} not found.")
            return
    
        # 2. Handle Indicator Variants
        print(f"--- Step 2: Wiping data for variants of '{indicator}' ---")

        # O3->OZONE. NB: This should be automatically handled somewhere in a config...
        if indicator == "o3":
            indicator = "ozone"
        if indicator == "wastewater":
            indicator = "pwdis"
        
        # Regex to find any column containing the indicator name
        pattern = re.compile(f".*{indicator}.*", re.IGNORECASE)
        all_matching_cols = [c for c in df_main.columns if pattern.match(c)]
        
        # Identify the exact base column to drop (the one that matches the input exactly)
        base_col_to_drop = next((c for c in all_matching_cols if c.lower() == indicator.lower()), None)
        
        # Identify variants to wipe (everything else that matched the regex)
        variants_to_wipe = [c for c in all_matching_cols if c != base_col_to_drop]

        # Wipe values for this indicator but keep headers
        if variants_to_wipe:
            print(f"Wiping data (keeping headers) for: {variants_to_wipe}")
            df_main[variants_to_wipe] = np.nan
        
        # Drop the base column
        if base_col_to_drop:
            print(f"Dropping base column for replacement: {base_col_to_drop}")
            df_main = df_main.drop(columns=[base_col_to_drop])

        # 3. Load local concat data and join
        print(f"--- Step 3: Merging in local scores ---")
        df_local = pd.read_csv(local_csv_path, dtype={'block_group_geoid': str})
        
        # Rename local ID to 'ID' to match EJSCREEN primary key
        df_local = df_local.rename(columns={'block_group_geoid': 'ID'})

        # Recase indicator column....
        df_local = df_local.rename(columns={indicator: indicator.upper()})

        # Outer join on ID
        # This places new indicator data into the dataframe
        df_main = pd.merge(df_main, df_local, on='ID', how='left')

    # 4. Process further for EJAM
    # Filter to environmental indicators only (including lead paint)
    # EJSCREEN syntax (e.g. OZONE) is ok as long as we use the fixcolnames function in EJAM
    df_main = df_main[["ID", "PM25","OZONE","DSLPM","RSEI_AIR","PTRAF","PRE1960PCT","PNPL","PRMP","PTSDF","UST", "PWDIS", "DWATER", "NO2"]]

    # 5. Save Output
    file_path = Path(f"pipeline/shared/ejscreen/v{version}/envirodata_{version}.csv")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    df_main.to_csv(file_path, index=False)
    
    print(f"\nSuccess!")
    print(f"Final file saved: {file_path}")
    print(f"Total columns: {len(df_main.columns)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 ejscreen_merge.py  --indicators [name] --version [version] --location [local_or_remote]")
    else:
        parser = argparse.ArgumentParser(description="Merges processed EJSCREEN data with a local archive")
        # add arguments
        parser.add_argument("-i", "--indicators", type=str, help="Comma-separated list of indicator(s), in EJAM syntax, e.g. o3,pm25")
        parser.add_argument("-v", "--version", help="The version of the indicator(s) e.g. 1.2020")
        parser.add_argument("-l", "--location", type=str, help="Local or remote storage")

        # 3. Parse the arguments
        args = parser.parse_args()

        if args.indicators:
            args.indicators = args.indicators.split(",")

        # 4. Access the arguments
        ejscreen_merge(args.indicators, args.version, args.location)