"""
Make EJSCREEN

PURPOSE:
    This script runs up to 4 functions to produce 4 sets of files needed for the EJSCREEN web app:
    1. export - geodatabases with spatial representation of the main EJSCREEN CSVs (state and US percentiles)
    2. thresholds - geodatabase with layers representing, for each block group, the number of indicators at different percentile ranks for different indexes
    3. census - geodatabase with additional demographics for blockgroup, tract, county, and state levels
    4. lookup - CSV with summary stats (max, min, mean, std) for each additional Census variable at each geographic level (blockgroup, tract, county, state)

USAGE:
    python3 scripts/shared/make_ejscreen.py --version [EJSCREEN vintage] --location [local or remote] --state [USPS abbreviation] --functions [export thresholds census lookup all] --extent [us state both]

EXAMPLE:
    python3 scripts/shared/make_ejscreen.py --version 4_2024 --location local --functions all

INPUT FILES:
    pipeline/shared/ejam/ejscreen_us_v{version}.csv
    pipeline/shared/ejam/ejscreen_state_v{version}.csv
    pipeline/shared/ejam/ejscreen_thresholds_state_ejindexes.csv
    pipeline/shared/ejam/ejscreen_thresholds_us_ejindexes.csv
    pipeline/shared/ejam/ejscreen_thresholds_state_supplemental.csv
    pipeline/shared/ejam/ejscreen_thresholds_us_supplemental.csv
    pipeline/shared/ejam/acs_by_state.csv
    pipeline/shared/ejam/acs_by_county.csv
    pipeline/shared/ejam/acs_by_tract.csv
    pipeline/shared/ejam/acs_by_bg.csv
    pipeline/shared/ejscreen/lookup_demog.csv # Archived/original copy of EJSCREEN Census lookup table
    pipeline/shared/ejscreen/ejschema.csv # A rough draft of data types for different variables in final products
    https://raw.githubusercontent.com/Public-Environmental-Data-Partners/EJAM/refs/heads/main/data-raw/map_headernames.csv # Maps between EJAM, Census, and EJSCREEN names for environmental and socioeconomic variables

OUTPUT FILES:
    pipeline/shared/ejscreen/v{version}/EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_US.gdb
    pipeline/shared/ejscreen/v{version}/EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_STATE.gdb
    pipeline/shared/ejscreen/v{version}/EJScreen Thresholds.gdb
    pipeline/shared/ejscreen/v{version}/EJScreen_Census.gdb
    pipeline/shared/ejscreen/v{version}/lookup_demog.csv

AUTHORSHIP:
    Eric Nost
"""
import geopandas as gpd
import pandas
import argparse
import scripts.shared.resolve_path as resolve_path
import scripts.utilities.validation.validation_io as validation_paths
import logging
import tempfile
from pathlib import Path
import caffeine 

FILEMAP = {"export": "ejscreen_{}_v{}.csv", "thresholds": ["ejscreen_threshold_{}_supplemental.csv", "ejscreen_threshold_{}_ejindexes.csv"], "census": ["acs_by_blockgroup.csv", "acs_by_tract.csv", "acs_by_county.csv", "acs_by_state.csv"]}

def _read_geodataframe(tiger_zip_path: Path) -> gpd.GeoDataFrame:
  candidates = [str(tiger_zip_path)]
  if tiger_zip_path.suffix.lower() == '.zip':
    candidates.append(f'zip://{tiger_zip_path.as_posix()}')

  last_err = None
  for candidate in dict.fromkeys(candidates):
    try:
      gdf = gpd.read_file(candidate)
      return gdf
    except Exception as exc:
      last_err = exc
  raise RuntimeError(f'Failed to read data from {tiger_zip_path}: {last_err}')

def _load_and_join(args, file, id, geotype="bg"):
  state = args.state
  location = args.location

  # Load data based on FILEMAP
  print(file)
  data = pandas.read_csv("pipeline/shared/ejam/" + file, dtype={id:str})

  # Filter if requested
  if state:
    import json
    with open('scripts/shared/state_config.json', 'r') as file:
      fips = json.load(file)
    this_state_fips = fips[state]["fips"]
    data = data[data[id].str.startswith(this_state_fips)]
  # Join with geometry
  # Ensure tiger lines are available - NOTE: unclear what vintage tiger lines we need, 2020 or something else. If something else, need to config that in shared_config.json
  # TODO: script to compile tiger lines for USA?
  # The following is borrowed from CompareScores.py - SUGGESTING WE NEED IT IN A SHARED PLACE
  # Derive FIPS from first matched geoid (first 2 digits)
  if data.empty:
      raise RuntimeError('No matched rows to map')
  #sample_id = data['ID'].astype(str).iloc[0]
  try:
    fips = this_state_fips #sample_id[:2] # State case
  except: 
    fips = "us"  # No state

  # Resolve TIGER path via shared asset config so resolution works for
  # both local and remote locations.
  shared_info = resolve_path.get_shared_asset_path(
      asset=f'tiger_{geotype}', version='2020', category='downloads', asset_key=f'tiger_{geotype}_2020', environment=location
  )
  # Ensure the shared root is fully resolved for local environments
  if location == 'local':
      try:
          shared_root = resolve_path.get_shared_root(f'tiger_{geotype}', '2020', environment=location)
      except Exception:
          # Fallback to the returned root if resolving fails
          shared_root = shared_info.get('root')
  else:
      shared_root = shared_info.get('root')

  # Format the returned relative template with state-specific placeholders
  raw_relative = shared_info.get('relative')
  try:
      formatted_relative = raw_relative.format(fips=fips, postal=state)
  except Exception:
      formatted_relative = raw_relative

  tiger_path_str = validation_paths.join_root_and_relative_path(shared_root, formatted_relative)

  # Log both the raw shared asset template, the formatted relative, and the resolved path
  logging.info('TIGER raw relative template -> %s', raw_relative)
  logging.info('TIGER formatted relative -> %s', formatted_relative)
  logging.info('TIGER resolved path -> %s', tiger_path_str)

  # If remote, copy to a temporary local file for geopandas to read.
  if validation_paths.is_s3_uri(tiger_path_str):
      if not validation_paths.exists_s3_or_local(tiger_path_str):
          raise FileNotFoundError(f'TIGER ZIP not found: {tiger_path_str}')
      fsspec = validation_paths.load_fsspec_module()
      tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
      tmp.close()
      try:
          with fsspec.open(tiger_path_str, 'rb') as fh:
              with open(tmp.name, 'wb') as outfh:
                  outfh.write(fh.read())
          gdf = _read_geodataframe(Path(tmp.name))
      finally:
          try:
              Path(tmp.name).unlink()
          except Exception:
              pass
  else:
      tiger_path = Path(tiger_path_str)
      if not tiger_path.exists():
          raise FileNotFoundError(f'TIGER ZIP not found: {tiger_path}')
      gdf = _read_geodataframe(tiger_path)
  if 'GEOID' not in gdf.columns:
      raise RuntimeError(f"Expected 'GEOID' column in TIGER data: {tiger_path}")
  if 'geometry' not in gdf.columns:
      raise RuntimeError(f"Expected 'geometry' column in TIGER data: {tiger_path}")


  # Proceed with joining
  data[id] = data[id].astype(str).str.strip() # May need to fix geoids here

  gdf['GEOID'] = gdf['GEOID'].astype(str).str.strip()

  output = gdf[["GEOID", "geometry"]].merge(data, left_on='GEOID', right_on=id, how='left') # Only need GEOID and geometry from the TIGER files

  del gdf, data # clear memory

  return output

def _data_typer(input):
  # This function maps column names to standard data types.
  # Goal is to use shorts instead of floats where we can
  # Load schema
  schema = pandas.read_csv("pipeline/shared/ejscreen/ejschema.csv")
  schema = schema[["column", "pandas"]].set_index('column')['pandas'].to_dict()
  schema = {k: v for k, v in schema.items() if k in input.columns}
  input = input.astype(schema)
  return input

def _export_gdb(output, path, layer):
  # Remove ID columns that pyogrio maps to database IDs
  id_columns_to_drop = ['OBJECTID', 'GEOID', 'Shape__Area', 'Shape__Length'] # SA and SL are reserved in the ArcGIS world
  output = output.drop(columns=[col for col in id_columns_to_drop if col in output.columns], errors='ignore')

  # Reset indices
  output.reset_index(drop=True, inplace=True) # This resequences the index, which may have gotten out of order through joins. Ensures we can write.

  # Formating
  output = output.round(3)
  output = _data_typer(output)
  schema = gpd.io.file.infer_schema(output)
  #print(schema)
  #schema['properties']['your_int_column'] = 'int32:4'  # or 'int64' based on size requirements
  # Could at least set int to int64, otherwise they're cast to float in export to gdb

  # Output
  output.to_file(path, layer=layer, driver="OpenFileGDB", geometry_type="Polygon", TARGET_ARCGIS_VERSION="ARCGIS_PRO_3_2_OR_LATER") # TODO: schema=schema, lookup correct layer names, ALIASES (possibly only possible with arcpy / within ArcGIS)

def export(args):
  extent = args.extent
  version = args.version

  if args.extent == "both":
    for e in ["us", "state"]:
      # Send to load and join:
      output = _load_and_join(args, FILEMAP["export"].format(e, version), "ID")

      # Export as gdb
      gdb_export_path = f"pipeline/shared/ejscreen/v{version}/EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_{e.upper()}.gdb"
      _export_gdb(output, gdb_export_path, f"EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_{e.upper()}")
  else:
    output = _load_and_join(args, FILEMAP["export"].format(extent, version), "ID")

    # Export as gdb
    gdb_export_path = f"pipeline/shared/ejscreen/v{version}/EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_{extent.upper()}.gdb"
    _export_gdb(output, gdb_export_path, f"EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_{extent.upper()}")

def thresholds(args):
  # Compare with https://colab.research.google.com/drive/1jaWCaAv1ZPg4zKoUv5REuKwIUaOPTJdY
  # EJAM paths:
  #ejscreen_threshold_us_supplemental.csv
  #ejscreen_threshold_state_supplemental.csv
  #ejscreen_threshold_state_ejindexes.csv
  #ejscreen_threshold_us_ejindexes.csv
  version = args.version

  gdb_export_path = f"pipeline/shared/ejscreen/v{version}/EJScreen Thresholds.gdb"

  if args.extent == "both":
    for e in ["us", "state"]:
      for f in FILEMAP["thresholds"]:
        f = f.format(e)
        output = _load_and_join(args, f, "ID")
        # Export as gdb
        _export_gdb(output, gdb_export_path, f.replace(".csv", "").replace("ejscreen_threshold_", ""))
  else:
    for f in FILEMAP["thresholds"]:
      f = f.format(args.extent)
      output = _load_and_join(args, f, "ID")
      # Export as gdb
      _export_gdb(output, gdb_export_path, f.replace(".csv", "").replace("ejscreen_threshold_", ""))


def census(args):
  version = args.version
  # acs_by_x.csv where x = state, county, tract, blockgroup
  censuslookup = {"state": {"fips":"statefips", "geotype": "state"}, 
            "county": {"fips":"countyfips", "geotype": "county"}, 
            "tract": {"fips":"tractfips", "geotype": "tract"}, 
            "blockgroup":{"fips":"bgfips", "geotype": "bg"}}
  lookup_demog = pandas.read_csv("pipeline/shared/ejscreen/lookup_demog.csv")
  # get column names from EJAM
  headernames = pandas.read_csv("https://raw.githubusercontent.com/Public-Environmental-Data-Partners/EJAM/refs/heads/main/data-raw/map_headernames.csv")

  for f in FILEMAP["census"]:
    # infer name and id
    id = f.replace("acs_by_", "").replace(".csv", "")
    print(id)
    id_cap = id.capitalize()
    geotype = censuslookup[id]["geotype"]
    id = censuslookup[id]["fips"]
    output = _load_and_join(args, f, id, geotype)

    # Exclude env indicator columns. Use only those in exisiting lookup_demog table. Use correct census syntax.
    dataT = output.T.reset_index()
    dataT.rename(columns={"index": "FIELD_NAME"},inplace=True)
    cols = dataT[["FIELD_NAME"]].merge(headernames[["acsname", "rname", "shortlabel", "longname"]], left_on="FIELD_NAME", right_on="rname", how="left") # only keep the ACS variables we have from EJAM. 
    cols = lookup_demog[["FIELD_NAME",	"DESCRIPTION",	"CATEGORY"]].merge(cols, left_on="FIELD_NAME", right_on="acsname", how="inner") # only keep variables that overlap, that are in both the existing look up and in EJAM's outputs
    core_cols = ['GEOID', 'geometry', 'bgfips', 'tractfips', 'countyfips', 'statefips']
    rcols = list(cols["rname"]) + core_cols
    output = output.loc[:, output.columns.isin(rcols)] # Keep only these Census columns
    # Rename columns based on mapping
    cols.rename(columns={"FIELD_NAME_x": "FIELD_NAME"}, inplace=True)
    mapping = cols[["FIELD_NAME", "rname"]].set_index('rname')['FIELD_NAME'].to_dict()
    output = output.rename(columns=mapping) 

    # Export as gdb
    f = f.replace("acs_by_", "").replace(".csv", "")
    gdb_export_path = f"pipeline/shared/ejscreen/v{version}/EJScreen_Census.gdb"
    _export_gdb(output, gdb_export_path, f'by_{id_cap}')

def lookup(args):
  """
  updating lookup_demog See: https://services.arcgis.com/EXyRv0dqed53BmG2/ArcGIS/rest/services/EJScreen_Census/FeatureServer/6

  This table (attributes only, no geo) includes every field in the Additional Demographics widget, a description (see map header names?), category (e.g. Access), AND summary stats (max, mean, min, std) for each geo level. Plan: we are only keeping fields/variables EJAM gives us.
  """
  from functools import reduce

  # Load existing lookup_demog table (ACS 2022), sourced from above feature server
  lookup_demog = pandas.read_csv("pipeline/shared/ejscreen/lookup_demog.csv")
  #print(lookup_demog.head())

  namelookup = {"blockgroup": "BG", "tract": "TR", "county": "CNTY", "state": "ST"}
  # Load EJAM Census results (acs_by_x.csv)
  results = []
  for f in FILEMAP["census"]:
    this_data = pandas.read_csv(f"pipeline/shared/ejam/{f}")
    # EJAM calculates percentages 0-1, but we need 0-100
    percentage_cols = [c for c in this_data.columns if "pct" in c]
    # Multiply columns by 100
    this_data[percentage_cols] = this_data[percentage_cols] * 100
    # Calculate max, min, mean, std for each variable
    this_result = this_data.agg(['max', 'min', 'mean', 'std']).T.reset_index()
    # Renaming columns to match lookup
    id = f.replace("acs_by_", "").replace(".csv", "")
    abv = namelookup[id]
    this_result.rename(columns={'min': f"{abv}_MIN", 'max': f"{abv}_MAX", 'mean':f"{abv}_MEAN", 'std':f"{abv}_STD", 'index': "FIELD_NAME"}, inplace=True)
    results.append(this_result)

  merged_df = reduce(lambda left, right: pandas.merge(left, right, on="FIELD_NAME", how="inner"), results)

  # Lookup header names
  # Load EJAM mapheadernames
  headernames = pandas.read_csv("https://raw.githubusercontent.com/Public-Environmental-Data-Partners/EJAM/refs/heads/main/data-raw/map_headernames.csv")

  # Goal: merged_df -> headernames -> lookup_demog
  merged_df = merged_df.merge(headernames[["acsname", "rname", "shortlabel", "longname"]], left_on="FIELD_NAME", right_on="rname", how="left") # only keep the ACS variables we have from EJAM. This may be where duplication is happening?

  merged_df = lookup_demog[["FIELD_NAME",	"DESCRIPTION",	"CATEGORY"]].merge(merged_df, left_on="FIELD_NAME", right_on="acsname", how="inner") # only keep variables that overlap, that are in both the existing look up and in EJAM's outputs
  merged_df.drop(columns={"FIELD_NAME_y"}, inplace=True)
  merged_df.rename(columns={"FIELD_NAME_x": "FIELD_NAME"}, inplace=True)

  #print(merged_df)
  # Not sure why, but we are getting duplicates in the outputs. Remove here, but investigate why it's happening. Some issue with the join, I assume.
  merged_df.drop_duplicates(inplace=True)
  merged_df = _data_typer(merged_df)
  merged_df.to_csv(f"pipeline/shared/ejscreen/v{args.version}/lookup_demog.csv", index=False)

  # Debug
  debug = merged_df.merge(lookup_demog, on="FIELD_NAME", how="left", suffixes=("", "_old"))
  newcols = ["BG_MIN",	"BG_MEAN",	"BG_STD",	"TR_MAX",	"TR_MIN",	"TR_MEAN",	"TR_STD",	"CNTY_MAX",	"CNTY_MIN",	"CNTY_MEAN",	"CNTY_STD",	"ST_MAX",	"ST_MIN",	"ST_MEAN",	"ST_STD"]
  oldcols = [c+"_old" for c in newcols]
  diffcols = [c+"_DIFF" for c in newcols]
  #debug[diffcols] = None
  debug[diffcols] = debug[oldcols]- debug[newcols].values # Difference from old to new... Should expect some differences due to new ACS data (if that's the version we're working with)

  # Some wonky results...need to investigate further
  debug.to_csv(f"pipeline/shared/ejscreen/v{args.version}/lookup_demog_debug.csv", index=False)


def parse_args(argv: list[str] | None = None):
  """Parse command-line arguments."""

  parser = argparse.ArgumentParser(
    description=(
        "Create final EJSCREEN products"
    )
  )

  parser.add_argument(
    "-l", "--location",
    default="local",
    help=(
        "Select local or remote storage mode. "
        "Only local mode is currently implemented."
    ),
  )

  parser.add_argument(
    "-s", "--state",
    type=str,
    help=(
        "Optional two-letter postal abbreviation. "
        "If omitted, all geographies are processed."
    ),
  )

  parser.add_argument(
    "-e", "--extent",
    choices = ["us", "state", "both"],
    default="us",
    help="The extent across which to produce exports i.e. US percentiles or State percentiles. Default: us", # Currently not available for state - have not exported this from EJAM.
  )

  parser.add_argument(
    "-v", "--version",
    type=str,
    choices = ["3_2022", "4_2024"],
    default="3_2022",
    help="EJSCREEN vintage. Default: 3_2022",
  )

  parser.add_argument(
    "-f", "--functions",
    choices = list(FUNCTIONS.keys()) + ["all"],
    default="all",
    help="Functions to run. Default: all",
  )

  args = parser.parse_args(argv)

  return args

FUNCTIONS = {"export":export, "thresholds": thresholds, "census":census, "lookup":lookup} 

def main(argv=None):
  args = parse_args(argv)

  # Prep outputs
  Path(f"pipeline/shared/ejscreen/v{args.version}").mkdir(parents=True, exist_ok=True)

  # Delete any existing gdbs here
  import os
  import shutil
  for gdb_path in Path(f"pipeline/shared/ejscreen/v{args.version}").rglob("*.gdb"):
    if gdb_path.is_dir():
      print(f"Deleting geodatabase: {gdb_path}")
      shutil.rmtree(gdb_path) # Deletes the folder and all contents safely

  # Run requested functions
  if args.functions:
    if args.functions == "all":
       for f in FUNCTIONS.keys():
          print(f)
          FUNCTIONS[f](args)
    else:
      args.functions = args.functions.split(",")
      for f in args.functions:
        print(f)
        FUNCTIONS[f](args)

if __name__ == "__main__":
    raise SystemExit(main())