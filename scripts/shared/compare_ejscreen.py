"""
EJSCREEN Data Comparison Tool (Case-Insensitive)

PURPOSE:
    Compares indicator values between the original EJSCREEN archive and a 
    modified test set. This version handles case-sensitivity issues (e.g., 
    'OZONE' in original vs 'ozone' in test) by normalizing headers.

INPUTS:
    - Original Archive: 'shared/pipeline/preprocessed_input/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv'
    - Replaced Test Set: 'ejscreen_test_replaced.csv'

USAGE:
    python compare_ejscreen.py [indicator_name]

EXAMPLE:
    python shared/compare_ejscreen.py ozone

AUTHORSHIP:
    Eric Nost and Google Gemini
"""

import pandas as pd
import sys
import os

# File paths
ORIGINAL_PATH = "shared/pipeline/preprocessed_input/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv"
REPLACED_PATH = "ejscreen_test_replaced.csv"

def get_case_insensitive_column(columns, target):
    """Returns the actual column name from a list that matches target (case-insensitive)."""
    for col in columns:
        if col.lower() == target.lower():
            return col
    return None

def compare_indicators(indicator_name):
    # 1. Path Verification
    if not os.path.exists(ORIGINAL_PATH) or not os.path.exists(REPLACED_PATH):
        print("Error: One or both files are missing.")
        return

    print(f"--- Searching for indicator: '{indicator_name}' (Case-Insensitive) ---")

    # 2. Identify actual column names in each file
    try:
        orig_cols = pd.read_csv(ORIGINAL_PATH, nrows=0).columns.tolist()
        new_cols = pd.read_csv(REPLACED_PATH, nrows=0).columns.tolist()
        
        actual_orig_col = get_case_insensitive_column(orig_cols, indicator_name)
        actual_new_col = get_case_insensitive_column(new_cols, indicator_name)
        
        if not actual_orig_col:
            print(f"Error: Could not find '{indicator_name}' in the original archive.")
            return
        if not actual_new_col:
            print(f"Error: Could not find '{indicator_name}' in the replaced test file.")
            return
            
        print(f"Found '{actual_orig_col}' in Original and '{actual_new_col}' in Replaced.")

        # 3. Load data using the identified case-sensitive names
        df_orig = pd.read_csv(ORIGINAL_PATH, usecols=['ID', actual_orig_col], dtype={'ID': str})
        df_new = pd.read_csv(REPLACED_PATH, usecols=['ID', actual_new_col], dtype={'ID': str})
        
    except Exception as e:
        print(f"Error during file reading: {e}")
        return

    # 4. Merge and Align
    comparison_df = pd.merge(
        df_orig, 
        df_new, 
        on='ID', 
        suffixes=('_original', '_new')
    )

    if comparison_df.empty:
        print("Error: No matching IDs found between files.")
        return

    # 5. Calculate Differences
    # Map the dynamic names to the calculations
    orig_vals = pd.to_numeric(comparison_df[actual_orig_col], errors='coerce')
    new_vals = pd.to_numeric(comparison_df[actual_new_col], errors='coerce')
    
    comparison_df['diff'] = new_vals - orig_vals
    
    # Summary Statistics
    count_changed = comparison_df[comparison_df['diff'].fillna(0) != 0].shape[0]
    total_rows = len(comparison_df)

    # 6. Final Report
    print("\n" + "="*50)
    print(f"COMPARISON REPORT FOR: {indicator_name.upper()}")
    print("="*50)
    print(f"Total Rows Compared:    {total_rows:,}")
    print(f"Rows with Differences: {count_changed:,} ({(count_changed/total_rows)*100:.2f}%)")
    print(f"Mean Difference:        {comparison_df['diff'].mean():.6f}")
    print(f"Max Absolute Change:    {comparison_df['diff'].abs().max():.6f}")
    
    if count_changed > 0:
        print("\nTOP 5 LARGEST DELTAS (New - Original):")
        # Renaming for display clarity
        display_df = comparison_df.nlargest(5, 'diff').copy()
        display_df = display_df.rename(columns={actual_orig_col: 'Original', actual_new_col: 'New'})
        print(display_df[['ID', 'Original', 'New', 'diff']])
    
    print("="*50)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python compare_ejscreen.py [indicator_name]")
    else:
        compare_indicators(sys.argv[1])