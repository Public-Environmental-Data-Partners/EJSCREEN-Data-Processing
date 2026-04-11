##### PROCESS TRAFFIC SEGMENTS #################################################
# This code attempts to recreate the processing steps for the traffic proximity 
# variable for EJScreen. Here, we are loading the JSON file from the 
# pre-processing script and recreating the steps outlined in the documentation, 
# rather than the pig scripts. 
#
# Author: EmmaLi 
###############################################################################
# Helpful links for quick reference: 
# documentation: https://www.epa.gov/system/files/documents/2024-07/ejscreen-tech-doc-version-2-3.pdf
###############################################################################
# libraries: 
library(sf)
library(mapview)
library(curl)
library(tidyverse)
library(tigris)
library(aws.s3)
library(tidycensus)
library(tictoc) # <- this package is optional, just for tracking dist functions 
library(ggpubr)
library(plotly)
options(scipen = 999)
options(tigris_use_cache = T)
sf_use_s2(T)

# TODO - add this to a json file / use the one created for superfund! 
# state: 
state = "RI"
# using Alberts for CONUS
state_crs = 5070 
# custom CRS for HI, based on this EPA resource: https://www.epa.gov/waterdata/spatial-data-waters
# state_crs = "+proj=aea +lat_1=8 +lat_2=18 +lat_0=3 +lon_0=-157 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"
# custom CRS for AK
# state_crs = "+proj=aea +datum=NAD83 +false_easting=0.0 +false_northing=0.0 +lon_0=-154.0 +lat_1=55.0 +lat_2=65.0 +lat_0=50.0 +units=m"

# NOTE - CT is going to be weird, I think it's because the state changed their
# census boundaries 

# grabbing state code: 
state_codes <- states() %>%
  filter(STUSPS == state) %>%
  as.data.frame() 
state_code <- state_codes$STATEFP
state_code_simple <- str_remove(state_code, "^0+")

# find neighboring states: 
state_buff <- state_codes %>%
  st_as_sf() %>%
  st_buffer(., 50) 
# intersect with state boundaries 
state_intersects <- st_intersection(states(), state_buff)
intersect_state_stusps <- unique(state_intersects$STUSPS)
intersect_state_acro <- intersect_state_stusps[!grepl(state, intersect_state_stusps)]
intersect_state_geoid <- unique(state_intersects$GEOID)
intersect_state_codes <- intersect_state_geoid[!grepl(state_code, intersect_state_geoid)] %>%
  # remove leading zeroes 
  str_remove(., "^0+")

###############################################################################
# loading in data 
###############################################################################
# load blocks 
b_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_", 
                state_code, "_tabblock20.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the blocks faster
curl_download(b_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
b <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_tabblock20.shp"))) %>%
  st_transform(., crs = (state_crs))


# load block groups (used for plotting)
bg_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_", 
                 state_code, "_bg.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the RI block groups faster
curl_download(bg_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
bg <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_bg.shp"))) %>%
  st_transform(., crs = (state_crs)) 


# reading HPMS line segments
# if in EJSCREEN-Data-Processing folder, the local root of cloned repo
prepro_2020 <- aws.s3::s3read_using(st_read, 
                                    object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", 
                                                    state, "/preprocessing/HPMS2020_", 
                                                    state_code_simple, ".json")) %>% 
  st_transform(., crs = st_crs(b))
# quick plot to see what we're cookin' with: 
# mapview(prepro_2020)


#### SCRATCH SPACE FOR COMPARING PREPROCESSED DATA FROM ARCGIS #################
#### AND THE RESULTS FROM THE NEW PYTHON SCRIPT  ###############################
prepro_2020_arcgis <- prepro_2020
prepro_2020_python <- st_read("./outputs/traffic/preprocessing/HPMS2020_44_python.json") %>%
  # the object ID is offset for some reason
  mutate(OBJECTID = ID + 1) %>%
  st_transform(., crs = st_crs(b))%>%
  mutate(unique_id = paste0(OBJECTID, "_", state_code), 
         shape_length = units::set_units(st_length(.), "km")) 

# the number of rows in arcgis: 2369; 16 of these have urban_code = 99999
# the number of rows from python: 2324

# they completely overlap
mapview(prepro_2020_arcgis) +
  mapview(prepro_2020_python, color = "red")


# the objectIDs are offset between the two datasets
merge_test <- merge(prepro_2020_arcgis, 
                    as.data.frame(prepro_2020_python), by = c("OBJECTID"))%>%
  as.data.frame()
  
merge_test_simple <- merge_test %>%
  as.data.frame() %>%
  rename(arcgis_aadt = aadt.x,
         python_aadt = aadt.y) 
ggplot(merge_test_simple, aes(x = arcgis_aadt, y = python_aadt)) + 
  geom_point()

# which ones are missing..?
missing <- prepro_2020_arcgis %>%
  filter(!(OBJECTID %in% merge_test$OBJECTID))
mapview(missing, color = "purple", lwd = 5) +
  mapview(prepro_2020_arcgis) +
  mapview(prepro_2020_python, color = "red")

# NOTES from comparisons: 
# the python method produces ~45 fewer records in comparison to ArcGIS. The 
# objectIDs don't seem to have the same traffic scores (they are sometimes 
# the same and sometimes not?), and the python method has objectIDs that stop at 
# 2324 and the ArcGIS method goes to 2359. When I plot the output, the traffic 
# line segments seem to completely overlap. I need to do some more testing, or 
# even just try to recreate this code in R. 
# 
# I crunched some new comparisons with the data from the python method, and the 
# absolute percent difference increased by 0.70145%. Results can be found
# in the canva doc
############# END SCRATCH CODE ##################################################

# add data from neighboring states 
prepro_others <- data.frame()
for(i in 1:length(intersect_state_codes)) {
  if (length(intersect_state_codes) == 0) {
    message(paste0("No neighboring states identified for: ", state))
    break
  } 
  state_code_i <- intersect_state_codes[i]
  state_i <- intersect_state_acro[i]
  
  prepro_i <- aws.s3::s3read_using(st_read, 
                                   object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", 
                                                   state_i, "/preprocessing/HPMS2020_", 
                                                   state_code_i, ".json"))%>% 
    st_transform(., crs = st_crs(b))
  
  prepro_others <- rbind(prepro_others, prepro_i) 
}

# if statement here for when intersect_state_code is empty (HI, PR, AK)
if (length(intersect_state_codes) == 0) {
  message(paste0("No neighboring states identified for: ", state))
  prepro_2020 <- prepro_2020 %>%
    mutate(unique_id = paste0(OBJECTID, "_", state_code)) 
} else {
  # if it's not empty, bind the highway datasets together 
  prepro_2020 <- rbind(prepro_2020, prepro_others) %>%
    mutate(unique_id = paste0(OBJECTID, "_", state_code)) 

}

# pulling in OG numbers from EJScreen: 
# TODO - in the future, maybe we just query the API?
# process - go to terminal and navigate to EJSCREEN-Data-Processing/scripts/utilities/validation
# in terminal, run python ejam2csv.py --state AK
# this will add ejam values as a csv file to s3 for this state

# NOTE - for larger, more complex states, this involves using the ECHO_modules
# package in python. Otherwise, the API will timeout. Here is the specific 
# script in python: 

# from ECHO_modules.get_data import get_ejscreen
# import pandas as pd
# results = get_ejscreen(regions="CA", region_type="fips")
# results_df = pd.DataFrame(results)
# results_df.to_csv("./outputs/traffic/ca_ejam.csv", index = False)

# for CA or even larger states, we wrote a python script in 
# scripts/utilities/pull_ejscreen_data.py
# from this issue: https://github.com/edgi-govdata-archiving/EJAM-API/issues/26#issuecomment-4163529789

# pulling traffic prox scores: 
ptraf <- aws.s3::s3read_using(read.csv,
                              object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/",
                                              state,
                                              "/ejam_traffic_subset.csv")) %>%
  select(ejam_uniq_id, traffic.score) %>%
  rename(block_group_geoid = ejam_uniq_id, 
         PTRAF = traffic.score) %>%
  select(block_group_geoid, PTRAF) %>%
  # some of these states are missing leading zeroes (AK, AL, for example)
  mutate(block_group_geoid = case_when(nchar(as.character(block_group_geoid)) == 11 ~ paste0("0", block_group_geoid), 
                                       TRUE ~ as.character(block_group_geoid)))

###############################################################################
# Start processing script
###############################################################################
# First, find block groups that are within 10km of a traffic line segment: 
# NOTE - this spatial join is NOT the same as buffering lines & running an 
# intersection. There are VERY SLIGHT differences due to rounding errors, where 
# the spatial join produced 162 more matches than the buffer + intersection. 
# The spatial join seems to be more mathematically correct & it generally produced
# the same results, but will include matches that are sooooooo on the edge of 
# being included. 
# This has been documented here: https://github.com/Public-Environmental-Data-Partners/EJSCREEN-Data-Processing/issues/10#issue-4179700260
bg_10km_intersection <- st_join(bg, prepro_2020, 
                                join = st_is_within_distance, 
                                dist = units::set_units(10000, "m"),
                                left = T) 

# The second step was to compute the distance between each block
# centroid within each targeted block group from the first step and the traffic 
# lines within the same block group (so it is a distance between point to polyline). 
## create point geometries from block dataset
b_no_zero_points <- b %>%
  st_centroid() %>%
  # filter for pops > 0 
  filter(POP20 > 0) %>%
  # create the block group geoid
  mutate(block_group_geoid = substr(GEOID20, 0, 12))

# creating variables that are involved in future calculations: 
## recreate population weight table: 
bg_pops <- b_no_zero_points %>%
  as.data.frame() %>%
  group_by(block_group_geoid) %>%
  mutate(block_group_pop = sum(POP20)) %>%
  select(block_group_geoid, block_group_pop) %>%
  unique()

b_weights <- b_no_zero_points %>%
  left_join(bg_pops, by = "block_group_geoid") %>%
  mutate(fraction_of_total = POP20 / block_group_pop) %>%
  as.data.frame() %>%
  select(GEOID20, fraction_of_total)

# Each block/line combination score is multiplied by the AADT
# estimate associated with each highway segment.
prepro_2020_simple <- prepro_2020 %>%
  as.data.frame() %>%
  select(unique_id, aadt)

# looping through block groups: 
bg_intersection_filt <- bg_10km_intersection %>% filter(!is.na(unique_id))
bg_loop <- unique(bg_intersection_filt$GEOID)
dist_pair_list <- list()
for(i in 1:length(bg_loop)){
  bg_loop_i <- bg_loop[i]
  
  # show loop progress if i is a multiple of 10 
  if(i %% 100 == 0) {
    print(paste0("On BG GEOID: ", bg_loop_i, "; loop ", i, 
                 " out of ", length(bg_loop)))
  }
  
  # finding the correct block group intersection: 
  bg_i <- bg_intersection_filt %>% 
    filter(GEOID == bg_loop_i)
  b_i <- b_no_zero_points %>%
    filter(block_group_geoid == bg_loop_i)
  traff_i <- prepro_2020 %>% 
    filter(unique_id %in% bg_i$unique_id)
  
  # running distances: 
  distance_i <- st_distance(b_i, traff_i, by_element = F)
  distance_df <- distance_i %>%
    as.data.frame() 
  colnames(distance_df) <- traff_i$unique_id
  rownames(distance_df) <- b_i$GEOID20
  
  # pivot to long: 
  distance_df_long <- distance_df %>%
    tibble::rownames_to_column("GEOID20") %>%
    pivot_longer(., cols = 2:(ncol(distance_df) + 1), 
                 names_to = "unique_id", 
                 values_to = "distance_m") %>%
    mutate(distance_m_num = as.numeric(distance_m)) %>%
    # drop geometries - we don't need them here
    as.data.frame() %>%
    select(-starts_with("geom")) %>%
    # here, assuming distance in km
    mutate(dist_pair_km = distance_m_num/1000, 
           # capping inverse distance to max 10
           inverse_distance = 1/dist_pair_km, 
           inverse_distance = case_when(inverse_distance > 10 ~ 10, 
                                        TRUE ~ inverse_distance)) %>%
    # Next, each block/line combination score was multiplied by the AADT
    # estimate associated with each highway segment.
    left_join(., prepro_2020_simple, by = "unique_id") %>%
    mutate(inv_dist_traff = inverse_distance * aadt) %>%
    # The final step was to multiply each block/line combination by its block group 
    # population weight 
    merge(., b_weights, by = "GEOID20") %>%
    mutate(inv_dist_traff_wt = inv_dist_traff * fraction_of_total) %>%
    select(GEOID20, unique_id, inv_dist_traff_wt)
  
  # add it to the list! 
  dist_pair_list[[i]] <- distance_df_long
}

# bind data together to form a data frame: 
dist_pair_df <- bind_rows(dist_pair_list) 

# remove large files from the environment to free up some memory 
rm(bg_10km_intersection)
rm(dist_pair_list)
rm(bg_intersection_filt)

# group the data by block groups & sum the scores to create the final 
# weighted score
final_wt <- dist_pair_df %>%
  mutate(block_group_geoid = substr(GEOID20, 0, 12)) %>%
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(inv_dist_traff_wt, na.rm = T))

# A non-zero population block
# group was assigned a score of zero when no traffic lines were found within a 
# 10km buffer.
# TODO - currently, this is just NA. We need just one more step to merge the 
# bg data frame to this final_wt function, and replace NAs with zeros. 

# push this to s3 
write.csv(final_wt, paste0("./outputs/traffic/processing/", state, "_bg_summary.csv"))
put_object(
  file = paste0("./outputs/traffic/processing/", state, "_bg_summary.csv"),
  object =  paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", 
                   state, "/processing/bg_summary.csv"),
  multipart = T
)

## read it back in: 
final_wt <- aws.s3::s3read_using(read.csv,
                     object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", 
                                     state, "/processing/bg_summary.csv"))
###############################################################################
# Start comparisons w/ EJScreen
###############################################################################
## summary stats & comparisons with EJScreen values 
# mapping the output: 
test_weight_all_sf <- final_wt %>%
  # some of these states are missing leading zeroes (AK, AL, for example)
  mutate(block_group_geoid = case_when(nchar(as.character(block_group_geoid)) == 11 ~ paste0("0", block_group_geoid), 
                                       TRUE ~ as.character(block_group_geoid))) %>%
  merge(., bg %>% select(GEOID), by.x = "block_group_geoid", 
        by.y = "GEOID") %>%
  merge(., ptraf, by = "block_group_geoid") %>%
  st_as_sf() %>%
  mutate(diff_estimate_minus_ptraf = weighted_score - PTRAF, 
         abs_diff_estimate_minus_ptraf = abs(weighted_score - PTRAF), 
         pct_diff_estimate_praf = 100*(diff_estimate_minus_ptraf) / ((weighted_score + PTRAF)/2),
         pct_abs_diff_estimate_praf = 100*(abs_diff_estimate_minus_ptraf) / ((weighted_score + PTRAF)/2),
         weighted_score_ntile = ntile(weighted_score, 100), 
         ptraf_ntile = ntile(PTRAF, 100))


# adding to s3 for comparisons 
st_write(test_weight_all_sf, paste0("./outputs/traffic/processing/",
                                    state, "_bg_summary_neighboring_states_alberts.geojson"))
put_object(
  file = paste0("./outputs/traffic/processing/", 
                state, "_bg_summary_neighboring_states_alberts.geojson"),
  object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", 
                  state, "/processing/bg_summary_neighboring_states_alberts.geojson"),
  multipart = T
)

# mean difference: 
mean(test_weight_all_sf$diff_estimate_minus_ptraf, na.rm = T) 

# mean absolute difference: 
mean(test_weight_all_sf$abs_diff_estimate_minus_ptraf, na.rm = T) 

# mean percent difference: 
mean(test_weight_all_sf$pct_diff_estimate_praf, na.rm = T) 

# mean absolute percent difference 
mean(test_weight_all_sf$pct_abs_diff_estimate_praf, na.rm = T) 

# mapping latest weighted scores: 
mapview(test_weight_all_sf, zcol = "weighted_score") + 
  mapview(prepro_2020, color = "black", lwd = 1.5)

# mapping weighted scores percentiles: 
mapview(test_weight_all_sf, zcol = "weighted_score_ntile", 
        at = c(0, 50, 80, 90, 95, 100)) + 
  mapview(prepro_2020, color = "black", lwd = 1.5)

# mapping ptraf percentiles: 
mapview(test_weight_all_sf, zcol = "ptraf_ntile", 
        at = c(0, 50, 80, 90, 95, 100)) + 
  mapview(prepro_2020, color = "black", lwd = 1.5)

# mapping percent difference
mapview(test_weight_all_sf, zcol = "pct_diff_estimate_praf", 
        at = seq(from = -200, to = 200, by = 50),
        col.regions = RColorBrewer::brewer.pal(11, "RdBu")) + 
  mapview(prepro_2020 %>%
            filter(state_code == state_code), color = "black", lwd = 1.5)

# mapview(test_weight_all_sf %>% filter(block_group_geoid == "560399677042"))

# making a plot: 
test_weight_all_df <- test_weight_all_sf %>%
  as.data.frame() 
ggplot(test_weight_all_df, aes(x = PTRAF, y = weighted_score)) + 
  geom_point() + 
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") +
  theme_bw() + 
  labs(x = "EJScreen Traffic Prox Score", y = "Estimated Weighted Score") + 
  ggtitle(paste0("EJScreen Score Test - following documentation for ", state)) +
  ggpubr::stat_cor(label.y = max(test_weight_all_sf$PTRAF, na.rm = T)) +
  ggpubr::stat_regline_equation(label.y = max(test_weight_all_sf$PTRAF, na.rm = T) - 500000)
# ggplotly(x)


## digging deeper into block group geoids with larger % differeces
large_pct_diffs <- test_weight_all_sf %>%
  # this is where we're overestimating
  filter(pct_abs_diff_estimate_praf > 10)
mapview(large_pct_diffs)

# what are the block characteristics of these locations? 
b_large_pct_diffs <- b_weights %>%
  as.data.frame() %>%
  filter(block_group_geoid %in% large_pct_diffs$block_group_geoid) %>%
  mutate(type = "large_diff")

# where are we super close? 
small_pct_diffs <- test_weight_all_sf %>%
  filter(pct_abs_diff_estimate_praf < 10)
mapview(small_pct_diffs)

# what are the block characteristics of these locations? 
b_small_pct_diffs <- b_weights %>%
  as.data.frame() %>%
  filter(block_group_geoid %in% small_pct_diffs$block_group_geoid)%>%
  mutate(type = "small_diff")

# binding the small & big groups together for easier comparisons 
b_diff_groups <- bind_rows(b_large_pct_diffs, b_small_pct_diffs) %>%
  select(GEOID20, block_group_geoid, type, ALAND20, AWATER20, HOUSING20, 
         fraction_of_total, POP20, block_group_pop) %>%
  pivot_longer(., cols = ALAND20:block_group_pop)

ggplot(b_diff_groups, aes(x = name, y = value, color = type, 
                          fill = type)) + 
  geom_violin(alpha = 0.5) + 
  facet_wrap(~name, scales = "free") +
  theme_bw()

# what about the distribution of differences?
large_diffs_distpairs <- dist_pair_df %>% 
  filter(GEOID20 %in% b_large_pct_diffs$GEOID20)
small_diffs_distpairs <- dist_pair_df %>%
  filter(GEOID20 %in% b_small_pct_diffs$GEOID20)
ggplot(large_diffs_distpairs, aes(x = distance_m_num)) + 
  geom_histogram()+ 
  theme_bw() + 
  labs(x = "Distance (m)", 
       y = "Number of DistPairs") + 
  ggtitle(paste0("Block Groups w/ Large Absolute % Difference: >10% ", state))
ggplot(small_diffs_distpairs, aes(x = distance_m_num)) + 
  geom_histogram() + 
  theme_bw() + 
  labs(x = "Distance (m)", 
       y = "Number of DistPairs") + 
  ggtitle(paste0("Block Groups w/ Small Absolute % Difference: < 10% ", state))