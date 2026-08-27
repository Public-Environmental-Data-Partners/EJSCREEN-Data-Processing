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
# TEST: RI case (FIPS = 43)

# Step 1: EJSCREEN export

import geopandas
import pandas

def export():
  # Load data
  data = pandas.read_csv("pipeline/shared/ejam/ejscreen_v3_2024.csv") # national/usa by default # TODO: ensure state
  # Join with geometry
  # Ensure tiger lines are available - NOTE: unclear what vintage tiger lines we need, 2020 or something else. If something else, need to config that in shared_config.json
  # TODO: compile tiger lines
  # Export as gdb
  gdb_path = "pipeline/shared/ejscreen/usa" # TODO: ensure state  
  data.to_file(gdb_path, layer="ejscreen", driver="OpenFileGDB") # Lookup correct layer name

def thresholds():
  # Compare with https://colab.research.google.com/drive/1jaWCaAv1ZPg4zKoUv5REuKwIUaOPTJdY
  # EJAM paths:
  #ejscreen_threshold_us_supplemental.csv
  #ejscreen_threshold_state_supplemental.csv
  #ejscreen_threshold_state_ejindexes.csv
  #ejscreen_threshold_us_ejindexes.csv
  return

def census():
  # acs_by_x.csv where x = state, county, tract, blockgroup
  return

def lookup():
  # schema table?
  return

