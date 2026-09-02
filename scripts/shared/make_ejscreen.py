"""
The main EJSCREEN data layers are produced by EJAM as ejscreen_export.csv (and I assume there is a version of this with the state scores). This csv file needs to be converted to a geodatabase file first by joining with the appropriate geometry (i.e. tiger lines).

Then there are at least three 'supplemental' data products required for EJSCREEN see https://docs.google.com/spreadsheets/d/1elB32pR9bU17pzKZSFetI_YB4i84D-b7Z0NoE2Zw_FM/edit?gid=21519598#gid=21519598 and Public-Environmental-Data-Partners/EJAM#395

Four thresholds layers for the threshold map widget in EJSCREEN:
us_ejindexes: all blockgroups and for each, the percentile rank for each EJIndex PLUS a series of 100 columns P1-P100 that count the number of indexes that "hit" that value. For instance, BG 010010201001 is in the 4th percentile for P_D2_OZONE and none of the other P_D2_indicator percentile ranks are 4. So P4 = 1. Looks like this: https://www.arcgis.com/home/item.html?id=bc1eab48bb554e5b8a42a19390fd98cf&dataTabView=fields&sublayer=2#data
state_ejindexes: same, but state percentiles
us_supplemental: same, but national supplemental rather than D2 EJ indexes.
state_supplemental: same, but state supplemental rather than D2 EJ indexes.
Each of these is currently represented with geometry (polygon) so would need to be converted from CSV to GBD, though it may pay to test whether the spatial dimension is really necessary.
Four census layers for the "additional demographics" and maybe the side-by-side maps in EJSCREEN:
extensive ACS results by block group, tract, county, and state. For example: https://www.arcgis.com/home/item.html?id=138e199d0e0c499587011ccb3f7d0480&dataTabView=fields&sublayer=2#data
Again, each of these layers is currently represented with geometry (polygon) so would need to be converted from CSV to GBD, though it may pay to test whether the spatial dimension is really necessary.
A simple lookup for indicator names, see https://services.arcgis.com/EXyRv0dqed53BmG2/ArcGIS/rest/services/EJScreen_Lookup_USBG/FeatureServer/4/query?where=FIELD_NAME+IS+NOT+NULL&objectIds=&resultType=none&outFields=*&returnIdsOnly=false&returnUniqueIdsOnly=false&returnCountOnly=false&returnDistinctValues=false&cacheHint=false&collation=&orderByFields=&groupByFieldsForStatistics=&returnAggIds=false&outStatistics=&having=&resultOffset=&resultRecordCount=5&sqlFormat=none&f=pjson&token=
"""

# Process: MVP, refactor
# Structure: four separate functions so that each supplemental file can be created separately, or some combination of them (thresholds, export, census, lookup, all). Potentially additional options to specific us vs state contexts (usa, state, both)
# Versions (v3.2022.3, v4.2024.0)
# TEST: RI case (FIPS = 44)

# Step 1: EJSCREEN export

import geopandas as gpd
import pandas
import argparse
import scripts.shared.resolve_path as resolve_path
import scripts.utilities.validation.validation_io as validation_paths
import logging
import tempfile
from pathlib import Path

FILEMAP = {"export": "ejscreen_{}_v{}.csv", "thresholds": ["ejscreen_threshold_{}_supplemental.csv", "ejscreen_threshold_{}_ejindexes.csv"], "census": ["acs_by_blockgroup.csv", "acs_by_tract.csv", "acs_by_county.csv", "acs_by_state.csv"], "lookup":"ejscreen_{}_pctile_lookup.csv"}

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
  data = pandas.read_csv("pipeline/shared/ejam/" + file, dtype={id:str}) # national/usa by default # TODO: ensure state percentiles
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
  fips = this_state_fips #sample_id[:2]

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
          raise FileNotFoundError(f'TIGER block-group ZIP not found: {tiger_path_str}')
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

  data[id] = data[id].astype(str).str.strip() # May need to fix geoids here

  gdf['GEOID'] = gdf['GEOID'].astype(str).str.strip()

  output = gdf[["GEOID", "geometry"]].merge(data, left_on='GEOID', right_on=id, how='left') # Only need GEOID and geometry from the TIGER files

  return output

def _export_gdb(output, version, f):
  gdb_export_path = f"pipeline/shared/ejscreen/v{version}/{f}.gdb" # TODO: ensure state percentiles can be done
  Path(f"pipeline/shared/ejscreen/v{version}").mkdir(parents=True, exist_ok=True)
  output.to_file(gdb_export_path, layer=f"{f}", driver="OpenFileGDB", geometry_type="Polygon", TARGET_ARCGIS_VERSION="ARCGIS_PRO_3_2_OR_LATER") # Lookup correct layer names

def export(args):
  # TODO: handle state percentiles here
  extent = args.extent
  version = args.version

  # Send to load and join:
  output = _load_and_join(args, FILEMAP["export"].format(extent, version), "ID")
  print(output.head())
  print(type(output))
  print(output.geometry)

  # Export as gdb
  _export_gdb(output, version, f"EJSCREEN_{version}_BG_with_AS_CNMI_GU_VI_{extent}")

def thresholds(args):
  print("thresholds")
  # Compare with https://colab.research.google.com/drive/1jaWCaAv1ZPg4zKoUv5REuKwIUaOPTJdY
  # EJAM paths:
  #ejscreen_threshold_us_supplemental.csv
  #ejscreen_threshold_state_supplemental.csv
  #ejscreen_threshold_state_ejindexes.csv
  #ejscreen_threshold_us_ejindexes.csv
  version = args.version
  
  if args.extent == "both":
    for e in ["us", "state"]:
      for f in FILEMAP["thresholds"]:
        f = f.format(e)
        output = _load_and_join(args, f, "ID")
        # Export as gdb
        _export_gdb(output, version, f.replace(".csv", ""))
  else:
    for f in FILEMAP["thresholds"]:
      f = f.format(args.extent)
      output = _load_and_join(args, f, "ID")
      # Export as gdb
      _export_gdb(output, version, f.replace(".csv", ""))


def census(args):
  print("census")
  version = args.version
  # acs_by_x.csv where x = state, county, tract, blockgroup
  lookup = {"state": {"fips":"statefips", "geotype": "state"}, 
            "county": {"fips":"countyfips", "geotype": "county"}, 
            "tract": {"fips":"tractfips", "geotype": "tract"}, 
            "blockgroup":{"fips":"bgfips", "geotype": "bg"}}
  for f in FILEMAP["census"]:
    # infer id
    id = f.replace("acs_by_", "").replace(".csv", "")
    print(id)
    geotype = lookup[id]["geotype"]
    id = lookup[id]["fips"]
    output = _load_and_join(args, f, id, geotype)
    # Export as gdb
    f = f.replace("acs_by_", "").replace(".csv", "")
    _export_gdb(output, version, f)
  return

def lookup(args):
  print("lookup")
  # schema table?
  return

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