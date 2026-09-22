# This script attempts to fix geoids that have had leading zeros accidentally removed. 
# Specifcially, it targets the combined output for an indicator e.g. combined_wastewater.csv

# Usage: python3 shared/fix_geoids.py --indicator wastewater --version 1.2021

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

def main():
  parser = argparse.ArgumentParser(description="A script to fix geoids.")

  # String argument with a short flag, long flag, and help message
  parser.add_argument("-i", "--indicator", type=str, required=True, help="The name of the indicator e.g. 'wastewater'")
  
  # Version e.g. 1.2021
  parser.add_argument("-v", "--version", type=str, default="1.2020", required=True, help="The version of the combined dataset e.g. '1.2020'")
  
  # Parse the arguments from the shell execution
  args = parser.parse_args()

  p = Path(f"../pipeline/{args.indicator}/v{args.version}/score_output/combined_{args.indicator}.csv") # Assumes scripts directory active

  df = pd.read_csv(p, dtype={"block_group_geoid": str})

  # For block groups, we should have 12 digits
  df["block_group_geoid"] = df["block_group_geoid"].astype(str).str.zfill(12)

  df.to_csv(p, index=False) # overwrite existing file

if __name__ == "__main__":
    main()