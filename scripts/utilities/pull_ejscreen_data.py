#### PULL EJSCREEN DATA #######################################################
# Pulled from this issue: https://github.com/edgi-govdata-archiving/EJAM-API/issues/26#issuecomment-4163529789
# 
# This set of code makes it easier to pull data for larger states, such as CA 
# and TX.Adding it here for referece, but needs documentation!
#
# Author: Eric w/ minor edits from EmmaLi 
##############################################################################
# Imports
import requests
import pandas as pd
import time

def get_all_ca_records(base_url):
    query_url = f"{base_url}/query"
    
    all_records = []
    offset = 0
    limit = 2000  # Standard max record count for ArcGIS Online
    
    print("Starting data retrieval for California...")
    
    while True:
        params = {
            'where': "ST_ABBREV = 'CA'",
            'outFields': '*',
            'f': 'json',
            'returnGeometry': 'false',
            'resultOffset': offset,
            'resultRecordCount': limit,
            'orderByFields': 'OBJECTID' # Ordering ensures consistent pagination
        }
        
        try:
            response = requests.get(query_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            features = data.get('features', [])
            
            if not features:
                break
                
            # Extract attributes from each feature
            batch = [f['attributes'] for f in features]
            all_records.extend(batch)
            
            print(f"Retrieved {len(all_records)} records...")
            
            # Check if there are more records to fetch
            if data.get('exceededTransferLimit'):
                offset += len(features)
            else:
                # If the limit isn't exceeded, we've reached the end
                break
                
            # Brief sleep to be polite to the server
            time.sleep(0.2)
            
        except requests.exceptions.RequestException as e:
            print(f"Error during request: {e}")
            break

    # Convert to Pandas DataFrame
    df = pd.DataFrame(all_records)
    return df

# Target Service URL
service_url = "https://services2.arcgis.com/w4yiQqB14ZaAGzJq/ArcGIS/rest/services/EJScreen_US_Percentiles_Block_Group_gdb_V_2.32_(Parent)_view/FeatureServer/0 "

# Execute
df_ca = get_all_ca_records(service_url)

if not df_ca.empty:
    print("\nRetrieval Complete!")
    print(f"Total Records Found: {len(df_ca)}")
    print(df_ca.head())
else:
    print("No records retrieved.")
    
# Save: 
results_df = pd.DataFrame(df_ca)
results_df.to_csv("./outputs/traffic/ca_ejam.csv", index = False)

