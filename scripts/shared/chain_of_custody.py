"""
Chain of Custody
PURPOSE:
    

USAGE:
    python3 chain_of_custody.py  --indicator [name] --blockgroup [blockgroup] --version [version] --pipieline [pipeline] --location [local_or_remote]

EXAMPLE:
    python3 scripts/shared/chain_of_custody.py --indicator pm25 --blockgroup XXXXXXXXXXXX --version 1.2022 --pipeline 4_2024 --location local

INPUT FILES:
    pipeline/shared/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv # Original archive
    pipeline/indicator/combined_indicator.csv # Combined blockgroup scores for the indicator
    pipeline/shared/ejscreen/envirodata_version.csv 
    pipeline/shared/ejam/ejscreen_us_pipeline # TODO
    pipeline/shared/ejscreen/EJSCREEN_pipeline_BG_with_AS_CNMI_GU_VI_US.gdb # TODO

OUTPUT FILE:
    pipeline/shared/ejscreen/bg_envirodata.csv

AUTHORSHIP:
    Eric Nost
"""
import re
import pandas
import argparse
from functools import reduce

# This helper function is copied from compare_ejscreen.py
def get_matching_columns(columns, target_pattern):
  """Finds all columns containing the indicator string (case-insensitive)."""
  pattern = re.compile(f".*{target_pattern}.*", re.IGNORECASE)
  return [c for c in columns if pattern.match(c)]

# Report results
def report(products, args):
  merged_products = reduce(
    lambda left, right: pandas.merge(left, right, on="ID", how="left"),
    products,
  )

  # Create diffs
  # Diff EJSCREEN V2.32 vs Pipeline vs Final

  # Export
  merged_products.to_csv(f"pipeline/compare/coc_report_{args.blockgroup}.csv")
  print(f"Exported to pipeline/compare/coc_report_{args.blockgroup}.csv")

# Load each dataset
def load(args):
  # Recognize the columns we need
  cols = pandas.read_csv(FILE_MAP["ejscreen"], nrows=0).columns.tolist()
  variants = [item for indicator in args.indicators for item in get_matching_columns(cols, indicator)]
  if not variants:
    print(f"No columns matching '{args.indicators}' found in {FILE_MAP["ejscreen"]}")
    return
  ejscreen_cols_needed = [c for c in variants if "T_" not in c] # Something screwy with the T_OZONE columns. Throws IndexError: list index out of range
  ejscreen_cols_needed.extend(["ID"]) # add id column

  # Load and filter
  ejscreen = pandas.read_csv(FILE_MAP["ejscreen"], usecols=ejscreen_cols_needed, dtype={"ID":str})
  ejscreen = ejscreen[ejscreen["ID"]==args.blockgroup] # Filter to bg
  ejscreen.columns = [c+"_V2.32" if c != "ID" else c for c in ejscreen.columns] # For recognition

  concatenated = []
  for path in FILE_MAP["concatenated"].values():
    this_data = pandas.read_csv(path, dtype={"block_group_geoid":str})
    this_data = this_data[this_data["block_group_geoid"]==args.blockgroup]
    this_data.rename(columns={"block_group_geoid": "ID"}, inplace=True)
    this_data.columns = this_data.columns.str.upper() # For EJSCREEN formatting
    this_data.columns = [c+"_COMBINED"  if c != "ID" else c for c in this_data.columns] # For recognition
    concatenated.append(this_data)
  concatenated_final = reduce(
    lambda left, right: pandas.merge(left, right, on="ID", how="left"),
    concatenated,
  )

  # Update cols_needed = just "raw" scores
  merged_cols = [c for c in ejscreen_cols_needed if "_" not in c]
  merged_cols.extend(["ID"])
  merged = pandas.read_csv(FILE_MAP["merged"], usecols=merged_cols, dtype={"ID":str})
  merged = merged[merged["ID"]==args.blockgroup]
  merged.columns = [c+f"_{args.version}" if c != "ID" else c for c  in merged.columns] # For recognition

  pipeline = pandas.read_csv(FILE_MAP["pipeline"], usecols=ejscreen_cols_needed, dtype={"ID":str})
  pipeline = pipeline[pipeline["ID"]==args.blockgroup] # Filter to bg
  pipeline.columns = [c+f"_{args.pipeline}" if c != "ID" else c for c in pipeline.columns] # For recognition

  #final = pandas.read_csv(FILE_MAP["ejscreen"], dtype={"ID":str}) # TBD

  report([ejscreen, concatenated_final, merged, pipeline], args)

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Audit EJSCREEN straw/pipeline stages.")
  parser.add_argument("-i", "--indicators", help="Indicators name, in EJSCREEN syntax e.g., ozone,pm25")
  parser.add_argument("-b", "--blockgroup", 
                      type = str,
                      default="local",
                      help="Which blockgroup to follow")
  parser.add_argument("-l", "--location", 
                      choices=["local", "remote"], 
                      default="local",
                      help="Where to run, i.e. local or remote")
  parser.add_argument("-v", "--version",
                      default="1.2020",
                      help="The *straw* version e.g. 1.2020")
  parser.add_argument("-p", "--pipeline",
                      default="",
                      help="The *EJSCREEN* version e.g. 4_2024")

  args = parser.parse_args()

  # Map indicators from EJSCREEN TO EJAM
  INDICATOR_MAP = {
    "pm25": "pm25",
    "ozone": "o3",
    "pwdis": "wastewater"
  }
  concat = {}
  form = "pipeline/{}/v{}/score_output/combined_{}.csv"
  if args.indicators:
    args.indicators = args.indicators.split(",")
  for indicator in args.indicators:
    this_path = form.format(INDICATOR_MAP[indicator], args.version, INDICATOR_MAP[indicator])
    concat[INDICATOR_MAP[indicator]] = this_path
  
  # Configuration of file paths
  FILE_MAP = {
    "ejscreen": "pipeline/shared/ejscreen/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv",
    "concatenated": concat,
    "merged": f"pipeline/shared/ejscreen/v{args.version}/envirodata_{args.version}.csv",
    "pipeline": f"pipeline/shared/ejam/ejscreen_us_v{args.pipeline}.csv",
    "final": f" pipeline/shared/ejscreen/EJSCREEN_{args.pipeline}_BG_with_AS_CNMI_GU_VI_US.gdb"
  }

  load(args)