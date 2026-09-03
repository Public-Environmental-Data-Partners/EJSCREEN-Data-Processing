  # To my knowledge, we do not have a script that compiles TIGER (bg) files for the entire US. Usually when mapping we need just one state at a time.
  # Path to tiger files TODO: ensure remote support, currently only would work locally

import tempfile
import zipfile
import geopandas as gpd
import pandas as pd
from pathlib import Path
import argparse

def do_tiger(geo):
  # Define your directory path
  directory_path = Path(f"pipeline/shared/downloads/tiger_lines/2020/{geo}") # Support tract in the future

  # Define the path to existing ZIP file
  zip_path = Path(f"pipeline/shared/downloads/tiger_lines/2020/bg/tl_2020_us_{geo}.zip")

  # Safely delete if it exists
  zip_path.unlink(missing_ok=True)

  # Proceed with merging state-level bg TIGER zips. Find all .zip files in the directory
  zip_files = list(directory_path.glob("*.zip"))

  # Read each zip file into a GeoDataFrame and store them in a list
  gdf_list = []
  for zip_file in zip_files:
    # GeoPandas handles zip paths using the zip:// prefix
    # Format: zip://path/to/file.zip
    path_str = f"zip://{zip_file.resolve()}"
    gdf = gpd.read_file(path_str)
    gdf_list.append(gdf)

  # Concatenate all GeoDataFrames into a single merged one
  if gdf_list:
    merged_gdf = gpd.GeoDataFrame(pd.concat(gdf_list, ignore_index=True))
  else:
    merged_gdf = gpd.GeoDataFrame()
    
  merged_gdf = merged_gdf.reset_index(drop=True) # Reset index numbering

  # Create a temporary directory
  with tempfile.TemporaryDirectory() as temp_dir:
    temp_dir = Path(temp_dir)

    # Export your GeoDataFrame here, e.g.:
    # gdf.to_file(temp_dir / "myshapefile.shp")
    merged_gdf.to_file(temp_dir / f'tl_2020_us_{geo}.shp', driver='ESRI Shapefile')

    # Compress all files in the temp directory into a zip file
    with zipfile.ZipFile(f"pipeline/shared/downloads/tiger_lines/2020/{geo}/tl_2020_us_{geo}.zip", "w", compression=zipfile.ZIP_DEFLATED) as zipf:
      for f in temp_dir.glob("*"):
        if f.is_file():
          zipf.write(f, arcname=f.name)

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Compile state-level zipped TIGER files for blockgroups or tracts.")
  parser.add_argument("-g", "--geography", help="Geographic level i.e. bg or tract")
  parser.add_argument("-l", "--location", help="Local or remote storage") # Currently only works for local

  args = parser.parse_args()

  do_tiger(args.geography)