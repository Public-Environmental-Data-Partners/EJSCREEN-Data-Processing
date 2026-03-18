import pandas as pd
import geopandas as gpd

# Load a chunk to test (or the whole thing if RAM allows)
df = pd.read_csv("../superfund/pipeline/test_data/downloads/hazardous_waste/HD_HANDLER/HD_HANDLER_0.csv", low_memory=False)

# 1. Filter to active TSD facilities and LQGs (Large Quantity Generators)
# Note: 'FED WASTE GENERATOR' usually uses '1' for LQG
is_tsdf = df['TSD ACTIVITY'] == 'Y'
is_lqg = df['FED WASTE GENERATOR'] == '1'
is_current = df['CURRENT RECORD'] == 'Y'
#is_accessible = df['ACCESSIBILITY'] == 'F'  # 'F' is for "fully accessible" not deleted or hidden
include_in_national_report = df['INCLUDE IN NATIONAL REPORT'] == 'Y'

df_filtered = df[is_current & include_in_national_report & (is_tsdf | is_lqg)].copy()

print(f"Original Records: {len(df)}")
print(f"Filtered Records (current/national/TSDF/LQG): {len(df_filtered)}")

# 2. Map the coordinates to a GeoDataFrame
# We use EPSG:4326 because raw Lat/Long is almost always WGS84
gdf_sites = gpd.GeoDataFrame(
    df_filtered, 
    geometry=gpd.points_from_xy(df_filtered['LOCATION LONGITUDE'], df_filtered['LOCATION LATITUDE']),
    crs="EPSG:4326"
)

# 3. Quick Debug Check
print("\n--- Top 5 Filtered Sites ---")
cols_to_show = ['HANDLER ID', 'HANDLER NAME', 'LOCATION CITY', 'TSD ACTIVITY', 'FED WASTE GENERATOR']
print(gdf_sites[cols_to_show].head())