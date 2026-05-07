"""
EJSCREEN Comparison Tool

PURPOSE:
    1. Compares a base indicator (e.g., 'OZONE' vs 'ozone') across original and replaced files.
    2. Compares all associated variants (e.g., P_OZONE, D5_OZONE) between the original
       archive and a final processed EJSCREEN file.

PURPOSE:
    Compares indicator columns (base and variants) between different stages 
    of the EJSCREEN pipeline. Handles case-sensitivity and automatic 
    discovery of variant columns (e.g., P_OZONE, B_OZONE).

MODES:
    - orig_repl: Compare archive to the swapped/merged file.
    - repl_final: Compare the swapped file to the final processed output.
    - orig_final: Compare archive directly to the final output.

USAGE:
    python compare_ejscreen.py [indicator_name] --mode [orig_repl|repl_final|orig_final]

EXAMPLE:
    python shared/compare_ejscreen.py ozone --mode orig_final

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import pandas as pd
import sys
import os
import re
import argparse

# Configuration of file paths
FILE_MAP = {
    "orig": "shared/pipeline/preprocessed_input/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv",
    "repl": "ejscreen_test_replaced.csv",
    "final": "ejscreen-dataset-creator-2.3/data/EJSCREEN_2024_USA_NewPctiles.csv" 
}

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

def run_audit(indicator, mode):
    # 1. Determine which files to compare
    if "_" not in mode:
        print("Error: Mode must be in format 'source_target' (e.g., orig_repl)")
        return
        
    source_key, target_key = mode.split('_')
    path_a = FILE_MAP.get(source_key)
    path_b = FILE_MAP.get(target_key)

    if not path_a or not path_b or not os.path.exists(path_a) or not os.path.exists(path_b):
        print(f"Error: Required files for mode '{mode}' are missing or paths are incorrect.")
        return

    print(f"\n--- Audit Mode: {mode.upper()} | Indicator: {indicator.upper()} ---")

    # 2. Inspect headers to find variants
    cols_a = pd.read_csv(path_a, nrows=0).columns.tolist()
    cols_b = pd.read_csv(path_b, nrows=0).columns.tolist()
    
    variants_a = get_matching_columns(cols_a, indicator)
    
    if not variants_a:
        print(f"No columns matching '{indicator}' found in {path_a}")
        return

    # 3. Load Data
    df_a = pd.read_csv(path_a, usecols=['ID'] + variants_a, dtype={'ID': str})
    # For file B, we only load ID and matching columns to save memory
    variants_b_needed = [get_case_insensitive_col(cols_b, v) for v in variants_a]
    variants_b_needed = [v for v in variants_b_needed if v is not None]
    
    df_b = pd.read_csv(path_b, usecols=['ID'] + variants_b_needed, dtype={'ID': str})

    print(f"{'Column (Source)':<25} | {'Changes':<10} | {'Mean Delta'}")
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
            
            count_changed = diff[diff.fillna(0) != 0].shape[0]
            mean_val = diff.mean()
            
            print(f"{col_a:<25} | {count_changed:>10,} | {mean_val:>12.6f}")
        else:
            print(f"{col_a:<25} | {'MISSING IN TARGET':>25}")

    print("-" * 65)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit EJSCREEN pipeline stages.")
    parser.add_argument("indicator", help="Indicator name (e.g., ozone)")
    parser.add_argument("--mode", 
                        choices=["orig_repl", "repl_final", "orig_final"], 
                        default="orig_repl",
                        help="The comparison stage to execute")

    args = parser.parse_args()
    run_audit(args.indicator, args.mode)