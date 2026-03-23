###############################################################################
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
state = "CA"
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
# process - go to terminal and navigate to EJSCREEN-Data-Processing/scripts/utilities/validation
# in terminal, run python ejam2csv.py --state AK
# this will add ejam values as a csv file to s3 for this state
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
# The first step was to overlay block groups
# with traffic lines that fall within a 10km radius of each block group polygon 
# (line to polygon). This step relates each block group with traffic.
prepro_10km_buff <- prepro_2020 %>%
  st_transform(., crs = (state_crs)) %>%
  st_buffer(., 10000)

# run an intersection: 
bg_10km_intersection <- st_intersection(prepro_10km_buff, bg) %>%
  mutate(unique_id = paste0(OBJECTID, "_", state_code)) 
# mapview(prepro_10km_buff[1:10,])

# mapping those that fell out of the intersection to confirm they 
# have 0 pop or are not near highways 
# bg_no_traff <- bg %>%
#   filter(!(GEOID %in% bg_10km_intersection$GEOID))
# mapview(bg_no_traff)

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

# looping through block groups: 
# TODO - this take foreverrrrrr for states with ~18,000+ block groups. I need 
# to find a way to batch the loops 
bg_loop <- unique(bg_10km_intersection$GEOID)
dist_pair_list <- list()
for(i in 1:length(bg_loop)){
  bg_loop_i <- bg_loop[i]
  
  # show loop progress if i is a multiple of 10 
  if(i %% 100 == 0) {
    print(paste0("On BG GEOID: ", bg_loop_i, "; loop ", i, " out of ", length(bg_loop)))
    # tictoc::tic()
  }
  
  # finding the correct block group intersection: 
  bg_i <- bg_10km_intersection %>% 
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
    mutate(distance_m_num = as.numeric(distance_m))
  
  dist_pair_list[[i]] <- distance_df_long
  # tictoc::toc()
}

# create a data frame, and drop all geometries (we don't need them anymore)
dist_pair_df <- bind_rows(dist_pair_list) %>%
  as.data.frame() %>%
  select(-starts_with("geom"))

# remove large files from the environment to free up some memory 
rm(bg_10km_intersection)
rm(dist_pair_list)

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

# Next, each block/line combination score was multiplied by the AADT
# estimate associated with each highway segment.
prepro_2020_simple <- prepro_2020 %>%
  as.data.frame() %>%
  select(unique_id, aadt)

# The third step was to calculate the score for each block/line combination 
# using inverse distance. A maximum score of 10 was used for all distances under 
# 0.1 km. 
test_no_split_traff_pop_wt <- dist_pair_df %>%
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
  mutate(inv_dist_traff_wt = inv_dist_traff * fraction_of_total)


# and to then aggregate the results to get the total block group score.
final_wt <- test_no_split_traff_pop_wt %>%
  as.data.frame() %>%
  select(-starts_with("geom")) %>%
  # create the block group geoid
  mutate(block_group_geoid = substr(GEOID20, 0, 12)) %>%
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(inv_dist_traff_wt, na.rm = T))


# A non-zero population block
# group was assigned a score of zero when no traffic lines were found within a 
# 10km buffer.

# push this to s3 
write.csv(final_wt, paste0("./outputs/traffic/processing/", state, "_bg_summary.csv"))
put_object(
  file = paste0("./outputs/traffic/processing/", state, "_bg_summary.csv"),
  object =  paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", state, "/processing/bg_summary.csv"),
  multipart = T
)

###############################################################################
# Start comparisons w/ EJScreen
###############################################################################
## summary stats & comparisons with EJScreen values 
# mapping the output: 
test_weight_all_sf <- final_wt %>%
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
st_write(test_weight_all_sf,  paste0("./outputs/traffic/processing/", state, "_bg_summary_neighboring_states_alberts.geojson"))
put_object(
  file = paste0("./outputs/traffic/processing/", state, "_bg_summary_neighboring_states_alberts.geojson"),
  object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", state, "/processing/bg_summary_neighboring_states_alberts.geojson"),
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

