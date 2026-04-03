##### PREPROCESS TRAFFIC SEGMENTS ##############################################
# This code attempts to recreate the preprocessing steps for the traffic proximity 
# variable for EJScreen. Here, we tidy the HPMS segments and create a JSON
# file that can be used for the processing script. 
# 
# Note this was previously done in ArcGIS, and this python script attempts
# to recreate the methods in an open source format. 
#
# Author: Eric w/ minor edits from EmmaLi 
################################################################################
# Imports
import pandas, geopandas
import requests
import geopandas as gpd
import pandas as pd

# Import HPMS traffic segments from ArcGIS feature server
def get_all_arcgis_features(base_url):
    query_url = f"{base_url}/query"

    # 1. Get all Object IDs first
    print("Fetching all Object IDs...")
    id_params = {
        'where': '1=1',
        'returnIdsOnly': 'true',
        'f': 'json'
    }
    id_response = requests.get(query_url, params=id_params)
    id_response.raise_for_status()
    all_ids = id_response.json().get('objectIds', [])

    total_records = len(all_ids)
    print(f"Total records found: {total_records}")

    # 2. Split IDs into chunks of 1000
    chunk_size = 1000
    id_chunks = [all_ids[i:i + chunk_size] for i in range(0, total_records, chunk_size)]

    all_features = []

    # 3. Fetch each chunk by ID
    for i, chunk in enumerate(id_chunks):
        # Convert ID list to comma-separated string
        ids_str = ",".join(map(str, chunk))

        params = {
            'objectIds': ids_str,
            'outFields': '*',
            'f': 'geojson',
            'returnGeometry': 'true'
        }

        print(f"Downloading chunk {i+1}/{len(id_chunks)}...")
        response = requests.get(query_url, params=params)
        response.raise_for_status()

        data = response.json()
        all_features.extend(data.get('features', []))

    # 4. Wrap into a GeoDataFrame
    feature_collection = {
        "type": "FeatureCollection",
        "features": all_features
    }

    gdf = gpd.GeoDataFrame.from_features(feature_collection)
    gdf.set_crs(epsg=4326, inplace=True)

    return gdf

# URL
# Change from HPMS_FULL_RI_2020 to some other state...
url = "https://geo.dot.gov/server/rest/services/Hosted/HPMS_FULL_RI_2020/FeatureServer/0" 

gdf_full = get_all_arcgis_features(url)
print(f"\nFinal count: {len(gdf_full)} records.")

# Create subset of "Major" highway segments using functional class (f_system in 
# (1, 2, 3) or (f_system = 4 and urban_code <> 99999)
gdf_full['f'] = gdf_full['f_system'].astype("Int64") # Convert data format to integer
gdf_full['u'] = gdf_full['urban_code'].astype("Int64") # Convert data format to integer
subset = gdf_full[(gdf_full['f'].isin([1,2,3])) | ((gdf_full["f"]==4) & (gdf_full['u'] != 99999))]

# Remove records where AADT is NULL or AADT = 0 or Shape_length = 0
subset['a'] = subset['aadt'].astype("Int64") # Convert data format to integer
subset['s'] = pandas.to_numeric(subset['SHAPE__Length'], errors="coerce") # Convert data format
s2 = subset.query('a > 0') # Remove records where AADT = null or AADT = 0
s2 = s2.query('s > 0') # Remove records where shape length = 0

# Dissolve redundant and partial overlapping segments
# First, add new column: REPEAT_TEST
# Calculate: REPEAT_TEST = Route_ID & AADT
s2["REPEAT_TEST"] = s2['route_id'].astype(str) + "-" + s2['a'].astype(str)
s2["REPEAT_TEST"]


# Run ArcGIS Dissolve tool (dissolve by REPEAT_TEST; first­_state_code,
# first_urban_code, mean_AADT), "No Multipart", "No Unsplit: HPMS2020_1_clean,
# … HPMS2020_72_clean. Note switching StateAbb to StateFIPS here for state 
# identifier. Note that state abbreviations are changed to FIPS codes for each 
# file (for example, AL to 1, WY to 56, PR to 72)
agg = {"state_code": "first", "u": "first", "f": "first", "a": "mean"}
#final = s2.groupby(by="REPEAT_TEST").agg(agg)
final = s2.dissolve(by="REPEAT_TEST", aggfunc=agg)

# Add ID (long int, calculated from OBJECTID)
final['ID'] = final.index

# Tidying and exporting 
# rename
final.rename(columns = {'u': 'urban_code', 'f': 'f_system', 'a': 'aadt'}, inplace = True)
# export to json
gdf = geopandas.GeoDataFrame(final, crs="EPSG:4326")
gdf.to_file("test.json", driver="GeoJSON")
