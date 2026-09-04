"""
EJSCREEN Comparison Tool

PURPOSE:
    Compares indicator columns (base and variants, e.g. 'OZONE' vs 'ozone', or
    P_OZONE, D5_OZONE) between different stages of the EJSCREEN pipeline. Handles
    case-sensitivity and automatic discovery of variant columns.

MODES:
    - original_merged: Compare the EJSCREEN archive to the merged file (the file created by ejscreen_merge.py).
    - merged_final: Compare the merged file to the final processed output from EJAM.
    - original_final: Compare the EJSCREEN archive directly to the final output from EJAM.

USAGE:
    python3 compare_ejscreen.py --indicator [indicator_name] --mode [original_merged|merged_final|original_final] --location [local_or_remote] --version [straw_version] --pipeline [pipeline_version]

EXAMPLE:
    python3 scripts/shared/compare_ejscreen.py --indicators ozone,pm25 --mode original_merged --location local --version 1.2020 --pipeline 4_2024

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import pandas as pd
import os
import re
import argparse
import numpy as np


def get_matching_columns(columns, target_pattern):
    """Finds all columns containing the indicator string (case-insensitive)."""
    pattern = re.compile(f".*{target_pattern}.*", re.IGNORECASE)
    return [c for c in columns if pattern.match(c)]

def get_case_insensitive_col(columns, target):
    """Finds the actual string in a list that matches target.lower()."""
    for c in columns:
        if c.lower() == target.lower():
            return c
    return None

def run_audit(indicators, mode):
    # 1. Determine which files to compare
    if "_" not in mode:
        print("Error: Mode must be in format 'source_target' (e.g., original_merged)")
        return
        
    source_key, target_key = mode.split('_')
    path_a = FILE_MAP.get(source_key)
    path_b = FILE_MAP.get(target_key)

    if not path_a or not path_b or not os.path.exists(path_a) or not os.path.exists(path_b):
        print(f"Error: Required files for mode '{mode}' are missing or paths are incorrect.")
        return

    for indicator in indicators:
        print(f"\n--- Audit Mode: {mode.upper()} | Indicator: {indicator.upper()} ---")
        if not os.path.exists(path_a) or not os.path.exists(path_b):
            print(f"Error: Files not found")
            return
            
        # 2. Inspect headers to find variants
        cols_a = pd.read_csv(path_a, nrows=0).columns.tolist()
        cols_b = pd.read_csv(path_b, nrows=0).columns.tolist()
        
        variants_a = get_matching_columns(cols_a, indicator)
        
        if not variants_a:
            print(f"No columns matching '{indicator}' found in {path_a}")
            return
        #print(variants_a)
        #print(cols_a)
        #print(cols_b)
        variants_a = [c for c in variants_a if "T_" not in c] # Something screwy with the T_OZONE columns. Throws IndexError: list index out of range
        test = [*['ID'],*variants_a]

        # 3. Load Data
        df_a = pd.read_csv(path_a, usecols=["ID"]+test, dtype={'ID': str}) 

        # For file B, we only load ID and matching columns to save memory
        variants_b_needed = [get_case_insensitive_col(cols_b, v) for v in variants_a]
        variants_b_needed = [v for v in variants_b_needed if v is not None]
        variants_b_needed = [v for v in variants_b_needed if "T_" not in v]

        df_b = pd.read_csv(path_b, usecols=["ID"]+variants_b_needed, dtype={'ID': str})

        print(f"{'Column (Source)':<25} | {'Changes':<10} | {'Mean Delta':<10} | {'Mean % Delta'}")
        print("-" * 65)

        for col_a in variants_a:
            col_b = get_case_insensitive_col(df_b.columns, col_a)
            
            if col_b:
                # FIX: Use explicit suffixes to prevent KeyError after merge
                merged = pd.merge(
                    df_a[['ID', col_a]], 
                    df_b[['ID', col_b]], 
                    on='ID', 
                    suffixes=('_src', '_tgt')
                )
                
                # If names were identical, they are now 'ColName_src' and 'ColName_tgt'
                # If names were different (OZONE vs ozone), they stay as is
                left_col = f"{col_a}_src" if f"{col_a}_src" in merged.columns else col_a
                right_col = f"{col_b}_tgt" if f"{col_b}_tgt" in merged.columns else col_b

                # Numeric conversion
                v_a = pd.to_numeric(merged[left_col], errors='coerce')
                v_b = pd.to_numeric(merged[right_col], errors='coerce')
                diff = v_b - v_a
                pct_diff = ((v_b - v_a) / v_a) * 100
                
                
                count_changed = diff[diff.fillna(0) != 0].shape[0]
                mean_pct_diff = pct_diff.replace([np.inf, -np.inf], np.nan).mean() # Ignore inf values (0 -> >0 change)
                mean_val = diff.mean()
                
                print(f"{col_a:<25} | {count_changed:>10,} | {mean_val:>12.6f} | {mean_pct_diff:>12.6f}")
            else:
                TARGET = mode.split("_")[1]
                print(f"{col_a:<25} | {f'MISSING IN {TARGET}':>25}")

        print("-" * 65)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit EJSCREEN straw/pipeline stages.")
    parser.add_argument("-i", "--indicators", help="Indicators name, in EJSCREEN syntax e.g., ozone,pm25")
    parser.add_argument("-m", "--mode", 
                        choices=["original_merged", "merged_final", "original_final"], 
                        default="original_merged",
                        help="The comparison stage to execute")
    parser.add_argument("-v", "--version",
                        default="1.2020",
                        help="The *straw* version e.g. 1.2020")
    parser.add_argument("-p", "--pipeline",
                        default="",
                        help="The *EJSCREEN* version e.g. 4_2024")
    parser.add_argument("-l", "--location", help="Local or remote storage")

    args = parser.parse_args()
    if args.indicators:
        args.indicators = args.indicators.split(",")

    # Configuration of file paths
    FILE_MAP = {
        "original": "pipeline/shared/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv",
        "merged": f"pipeline/shared/ejscreen/v{args.version}/envirodata_{args.version}.csv",
        "final": f"pipeline/shared/ejam/ejscreen_us_v{args.pipeline}.csv"
    }

    run_audit(args.indicators, args.mode)