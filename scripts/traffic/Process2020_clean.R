###############################################################################
# This code attempts to recreate the processing steps for the traffic proximity 
# variable for EJScreen. Here, we are loading the JSON file from the 
# pre-processing script and recreating the steps outlined in this pig script: 
# https://github.com/USEPA-clone/ejscreen-traffic-proximity-processing/blob/main/Highway_processing_1_step1_pig.txt
# 
# We are currently testing this processing using the 2020 HPMS segments for Rhode
# Island & Wyoming -- this is a clean space for a promising method we found 
# mid February, 2026. We created a second version of this method later in 
# this code, with the header "Test 2: what if we followed the documentation
# instead?"
# 
###############################################################################
# Helpful links for quick reference: 
# OG repo: https://github.com/USEPA-clone/ejscreen-traffic-proximity-processing/tree/main?tab=readme-ov-file
# New repo: https://github.com/Public-Environmental-Data-Partners/EJSCREEN-Data-Processing/tree/main
# Eric's code: https://colab.research.google.com/drive/1GawHQ2tep2p-CLlxe90tp5Y8SXGdNBUh#scrollTo=Bk9h01uHA05w
###############################################################################
# libraries: 
library(sf)
library(mapview)
library(curl)
library(tidyverse)
library(tigris)
library(reticulate)
library(aws.s3)
library(tidycensus)
library(tictoc) # <- this package is optional, just for tracking dist functions 
library(ggpubr)
library(plotly)
options(scipen = 999)
options(tigris_use_cache = T)

# state: 
state = "WY"

# grabbing state code: 
state_codes <- states() %>%
  filter(STUSPS == state) %>%
  as.data.frame() 
state_code <- state_codes$STATEFP
###############################################################################
# load blocks 
b_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_", state_code, "_tabblock20.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the blocks faster
curl_download(b_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
b <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_tabblock20.shp")))


# load block groups (used for plotting)
bg_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_", state_code, "_bg.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the RI block groups faster
curl_download(bg_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
bg <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_bg.shp")))

# reading HPMS line segments
# if in EJSCREEN-Data-Processing folder, the local root of cloned repo
file <- paste0("./outputs/traffic/preprocessing/HPMS2020_", state_code, ".json") 
prepro_2020 <- st_read(file) %>% 
  st_transform(., crs = st_crs(b))
# quick plot to see what we're cookin' with: 
mapview(prepro_2020)


# pulling in OG WY numbers from EJScreen: 
ptraf <- aws.s3::s3read_using(read.csv,
                              object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/WY/ejam_traffic_subset.csv") %>%
  mutate(ID = as.character(ejam_uniq_id)) %>%
  rename(block_group_geoid = ID,
         PTRAF = traffic.score) %>%
  select(block_group_geoid, PTRAF)

# for RI (need to make this cleaner)
# ptraf <- aws.s3::s3read_using(read.csv, 
#                               object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_ptraf.csv") %>%
#   select(-X) %>%
#   mutate(ID = as.character(ID)) %>%
#   rename(block_group_geoid = ID)
###############################################################################
# Start of step 1 pig script
###############################################################################

## create point geometries from block dataset
b_no_zero_points <- b %>%
  st_centroid() %>%
  # filter for pops > 0 
  filter(POP20 > 0) %>%
  # create the block group geoid
  mutate(block_group_geoid = substr(GEOID20, 0, 12))

## recreate population weight table: 
bg_pops <- b_no_zero_points %>%
  as.data.frame() %>%
  group_by(block_group_geoid) %>%
  mutate(block_group_pop = sum(POP20)) %>%
  select(block_group_geoid, block_group_pop) %>%
  unique()

b_weights <- b_no_zero_points %>%
  left_join(bg_pops, by = "block_group_geoid") %>%
  mutate(fraction_of_total = POP20 / block_group_pop)


## cross assign point geometries into the line segment geoms, this 
# is essentially joining blocks and segments by assigning each block to 
# potentially multiple line segments. 
b_line_intersect <- st_join(b_weights, prepro_2020, 
                            join = st_is_within_distance, 
                            # flag here that the original pig script 
                            # assigned each highway segment a block centroid
                            dist = units::set_units(20000, "m"),
                            left = T) 

## create distance pairs 
# first, make sure the crs are the same - this returns TRUE 
st_crs(prepro_2020) == st_crs(b_line_intersect)

# extract line geoms and add them back in, but not as the active geometry:
hwy_geom_simp <- prepro_2020 %>%
  mutate(line_geom = st_geometry(.)) %>%
  st_drop_geometry() %>%
  select(OBJECTID, line_geom)

# join highway segments back in 
prep_dist <- b_line_intersect %>%
  left_join(hwy_geom_simp, by = "OBJECTID")


# the code has crashed my session before :) but should be returning geodesic 
# distances based on the function documentation. It takes ~1.5 hours or so to 
# run 
# prep_dist$dist_pair <- st_distance(st_geometry(prep_dist),
#                                    st_sfc(prep_dist$line_geom,
#                                           crs = st_crs(prep_dist)),
#                                    by_element = T)

# split the data into groups of 20, make a list, and run dist functions on
# list entries:  
grouped_data <- gl(20, nrow(prep_dist) / 20)
prep_dist_list <- split(prep_dist, grouped_data)
dist_pair_df <- data.frame()
for(i in 1:20){
  print(paste0("Working on: ", i, " out of ", 20))
  prep_dist_i <- prep_dist_list[[i]]
  tictoc::tic()
  dist_pair_i <- prep_dist_i %>%
    mutate(distance = st_distance(st_geometry(prep_dist_i),
                                  st_sfc(prep_dist_i$line_geom,
                                         crs = st_crs(prep_dist_i)),
                                  by_element = T)) %>%
    as.data.frame() %>%
    select(GEOID20, block_group_geoid, POP20, fraction_of_total, OBJECTID, 
           aadt, distance)
  tictoc::toc()
  dist_pair_df <- rbind(dist_pair_df, dist_pair_i)
}

# writing
# write.csv(dist_pair_df, paste0("./outputs/traffic/processing/", state, "_dist_pairs.csv"))

# pushing to s3 
# some of these are really big & take quite a bit of time 
# put_object(
#   file = paste0("./outputs/traffic/processing/", state, "_dist_pairs.csv"),
#   object = paste0("s3://pedp-data-preserved/ejscreen-data-processing/traffic/", state, "_dist_pairs.csv"),
#   multipart = T, 
#   show_progress = T
# )

###############################################################################
## Test: what if there is no < 500 and > 500m split?
###############################################################################
# what if there is no < 500 and > 500m split? 
test_no_split <- dist_pair_df %>%
  mutate(dist_pair_num = as.numeric(distance)) %>%
  # here, assuming distance in km
  mutate(dist_pair_km = dist_pair_num/1000, 
         # capping inverse distance to max 10
         inverse_distance = 1/dist_pair_km, 
         inverse_distance = case_when(inverse_distance > 10 ~ 10, 
                                      TRUE ~ inverse_distance)) %>%
  mutate(score = inverse_distance * aadt) 

# test summarizing all distance pairs without the split
test_weight_all <- test_no_split %>%
  # bring in some of the extra information from census blocks 
  merge(., b_weights, by = c("GEOID20", "block_group_geoid", 
                             "POP20", "fraction_of_total")) %>%
  group_by(GEOID20, block_group_geoid, fraction_of_total, POP20, 
           ALAND20, AWATER20) %>%
  # maybe the score is a sum?
  summarize(score_lt = sum(score)) %>%
  # multiply the score by the block group weight: 
  mutate(score_wt = score_lt * fraction_of_total) %>%
  # group by block group geoid and sum the weights: 
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(score_wt, na.rm = T)) 


## summary stats: 
# mapping the output: 
test_weight_all_sf <- test_weight_all %>%
  merge(., bg %>% select(GEOID), by.x = "block_group_geoid", 
        by.y = "GEOID") %>%
  merge(., ptraf, by = "block_group_geoid") %>%
  st_as_sf() %>%
  mutate(diff_estimate_minus_ptraf = weighted_score - PTRAF, 
         abs_diff_estimate_minus_ptraf = abs(weighted_score - PTRAF), 
         pct_diff_estimate_praf = 100*(diff_estimate_minus_ptraf) / ((weighted_score + PTRAF)/2),
         weighted_score_ntile = ntile(weighted_score, 100), 
         ptraf_ntile = ntile(PTRAF, 100))


# adding to s3 for comparisons 
# WY latest file is v1; RI latest file is v6
# st_write(test_weight_all_sf, "./outputs/traffic/processing/wy_bg_summary.geojson")
# put_object(
#   file = "./outputs/traffic/processing/wy_bg_summary.geojson",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/WY/bg_summary_test_v1.geojson",
#   multipart = T
# )

# mean difference: 
mean(test_weight_all_sf$diff_estimate_minus_ptraf, na.rm = T) 

# mean absolute difference: 
mean(test_weight_all_sf$abs_diff_estimate_minus_ptraf, na.rm = T) 

# mean percent difference: 
mean(test_weight_all_sf$pct_diff_estimate_praf, na.rm = T) 

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
  mapview(prepro_2020, color = "black", lwd = 1.5)

# making a plot: 
test_weight_all_df <- test_weight_all_sf %>%
  as.data.frame() 
ggplot(test_weight_all_df, aes(x = PTRAF, y = weighted_score)) + 
  geom_point() + 
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  theme_bw() + 
  labs(x = "EJScreen Traffic Prox Score", y = "Estimated Weighted Score") + 
  ggtitle(paste0("EJScreen Score Test - No 500m Split, sum inverse dist * aadt for 
          blocks before grouping by block group * pop weight; ", state)) + 
  ggpubr::stat_cor(label.y = max(test_weight_all_sf$PTRAF, na.rm = T)) + 
  ggpubr::stat_regline_equation(label.y = max(test_weight_all_sf$PTRAF, na.rm = T) - 500000)
# ggplotly(x)

# space to check out specific block groups: 
test <- dist_pair_df %>%
  filter(block_group_geoid == "560079680001") %>%
  filter(!is.na(distance)) %>%
  mutate(dist_pair_num = as.numeric(distance),
         inv_dist = 1/dist_pair_num,
         inv_dist_aadt = inv_dist*aadt) %>%
  merge(., b_weights, by = c("GEOID20", "block_group_geoid", 
                             "POP20", "fraction_of_total"))

## digging deeper into block group geoids with larger % differeces
large_pct_diffs <- test_weight_all_sf %>%
  # this is where we're overestimating
  filter(pct_diff_estimate_praf > 30)
mapview(large_pct_diffs)

# what are the block characteristics of these locations? 
b_large_pct_diffs <- b_weights %>%
  as.data.frame() %>%
  filter(block_group_geoid %in% large_pct_diffs$block_group_geoid) %>%
  mutate(type = "large_diff")

# where are we super close? 
small_pct_diffs <- test_weight_all_sf %>%
  filter(pct_diff_estimate_praf < 5 & pct_diff_estimate_praf > -5)
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
  filter(block_group_geoid %in% b_large_pct_diffs$block_group_geoid)
small_diffs_distpairs <- dist_pair_df %>%
  filter(block_group_geoid %in% b_small_pct_diffs$block_group_geoid)
ggplot(large_diffs_distpairs, aes(x = as.numeric(distance))) + 
  geom_histogram()+ 
  theme_bw() + 
  labs(x = "Distance (m)", 
       y = "Number of DistPairs") + 
  ggtitle(paste0("Block Groups w/ Large % Difference: ", state))
ggplot(small_diffs_distpairs, aes(x = as.numeric(distance))) + 
  geom_histogram() + 
  theme_bw() + 
  labs(x = "Distance (m)", 
       y = "Number of DistPairs") + 
  ggtitle(paste0("Block Groups w/ Small % Difference: ", state))





###############################################################################
## Test 2: what if we followed the documentation instead?
###############################################################################
###############################################################################
# This code attempts to recreate the processing steps for the traffic proximity 
# variable for EJScreen. Here, we are loading the JSON file from the 
# pre-processing script and recreating the steps outlined in the documentation, 
# rather than the pig scripts!
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
library(reticulate)
library(aws.s3)
library(tidycensus)
library(tictoc) # <- this package is optional, just for tracking dist functions 
library(ggpubr)
library(plotly)
options(scipen = 999)
options(tigris_use_cache = T)

# state: 
state = "RI"

# grabbing state code: 
state_codes <- states() %>%
  filter(STUSPS == state) %>%
  as.data.frame() 
state_code <- state_codes$STATEFP
###############################################################################
# load blocks 
b_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_", state_code, "_tabblock20.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the blocks faster
curl_download(b_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
b <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_tabblock20.shp")))


# load block groups (used for plotting)
bg_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_", state_code, "_bg.zip")
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the RI block groups faster
curl_download(bg_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
bg <- st_read(paste0(file_loc, paste0("/tl_2022_", state_code, "_bg.shp")))

# reading HPMS line segments
# if in EJSCREEN-Data-Processing folder, the local root of cloned repo
file <- paste0("./outputs/traffic/preprocessing/HPMS2020_", state_code, ".json") 
prepro_2020 <- st_read(file) %>% 
  st_transform(., crs = st_crs(b))
# quick plot to see what we're cookin' with: 
mapview(prepro_2020)


# pulling in OG WY numbers from EJScreen: 
# ptraf <- aws.s3::s3read_using(read.csv,
#                               object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/WY/ejam_traffic_subset.csv") %>%
#   mutate(ID = as.character(ejam_uniq_id)) %>%
#   rename(block_group_geoid = ID,
#          PTRAF = traffic.score) %>%
#   select(block_group_geoid, PTRAF)

# for RI (need to make this cleaner)
ptraf <- aws.s3::s3read_using(read.csv,
                              object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_ptraf.csv") %>%
  select(-X) %>%
  mutate(ID = as.character(ID)) %>%
  rename(block_group_geoid = ID)

###############################################################################
# MAYBE EVERYTHNG IS WRONGGGG
###############################################################################
# documentation: https://www.epa.gov/system/files/documents/2024-07/ejscreen-tech-doc-version-2-3.pdf

# The first step was to overlay block groups
# with traffic lines that fall within a 10km radius of each block group polygon (line to polygon). This step
# relates each block group with traffic.
prepro_10km_buff <- prepro_2020 %>%
  st_buffer(., 10000)
sf_use_s2(F)
bg_10km_intersection <- st_intersection(prepro_10km_buff, bg)
sf_use_s2(T)

# mapping those that fell out of the intersection: 
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
bg_loop <- unique(bg_10km_intersection$GEOID)
dist_pair_df <- data.frame()
for(i in 1:length(bg_loop)){
  bg_loop_i <- bg_loop[i]
  print(paste0("On BG GEOID: ", bg_loop_i, "; loop ", i, " out of ", length(bg_loop)))
  
  # finding the correct block group intersection: 
  bg_i <- bg_10km_intersection %>% filter(GEOID == bg_loop_i)
  b_i <- b_no_zero_points %>% filter(block_group_geoid == bg_loop_i)
  traff_i <- prepro_2020 %>% filter(OBJECTID %in% bg_i$OBJECTID)
  
  # running distances: 
  tictoc::tic()
  distance_i <- st_distance(b_i, traff_i, by_element = F)
  distance_df <- distance_i %>%
    as.data.frame() 
  colnames(distance_df) <- traff_i$OBJECTID
  rownames(distance_df) <- b_i$GEOID20
  # pivot to long: 
  distance_df_long <- distance_df %>%
    tibble::rownames_to_column("GEOID20") %>%
    pivot_longer(., cols = 2:(ncol(distance_df) + 1), 
                 names_to = "OBJECTID", 
                 values_to = "distance_m") %>%
    mutate(distance_m_num = as.numeric(distance_m))
  
  tictoc::toc()
  dist_pair_df <- rbind(dist_pair_df, distance_df_long)
}

# The third step was to calculate the score for each block/line combination 
# using inverse distance. A maximum score of 10 was used for all distances under 
# 0.1 km. 
test_no_split <- dist_pair_df %>%
  # here, assuming distance in km
  mutate(dist_pair_km = distance_m_num/1000, 
         # capping inverse distance to max 10
         inverse_distance = 1/dist_pair_km, 
         inverse_distance = case_when(inverse_distance > 10 ~ 10, 
                                      TRUE ~ inverse_distance)) 

# Next, each block/line combination score was multiplied by the AADT
# estimate associated with each highway segment.
test_no_split_traff <- test_no_split %>%
  left_join(., prepro_2020 %>% as.data.frame() %>% 
              mutate(OBJECTID = as.character(OBJECTID)), 
            by = "OBJECTID") %>%
  mutate(inv_dist_traff = inverse_distance * aadt)

# The final step was to multiply each block/line combination by its block group population
# weight 
## recreate population weight table: 
bg_pops <- b_no_zero_points %>%
  as.data.frame() %>%
  group_by(block_group_geoid) %>%
  mutate(block_group_pop = sum(POP20)) %>%
  select(block_group_geoid, block_group_pop) %>%
  unique()

b_weights <- b_no_zero_points %>%
  left_join(bg_pops, by = "block_group_geoid") %>%
  mutate(fraction_of_total = POP20 / block_group_pop)

# multiplying block group pop weight table: 
test_no_split_traff_pop_wt <- merge(test_no_split_traff, 
                                    b_weights, by = "GEOID20") %>%
  mutate(inv_dist_traff_wt = inv_dist_traff * fraction_of_total)


# and to then aggregate the results to get the total block group score.
final_wt <- test_no_split_traff_pop_wt %>%
  as.data.frame() %>%
  select(-starts_with("geom")) %>%
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(inv_dist_traff_wt, na.rm = T))


# A non-zero population block
# group was assigned a score of zero when no traffic lines were found within a 
# 10km buffer.


## summary stats: 
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
# WY latest file is v1; RI latest file is v6
# st_write(test_weight_all_sf, "./outputs/traffic/processing/ri_bg_summary_v7.geojson")
# put_object(
#   file = "./outputs/traffic/processing/ri_bg_summary_v7.geojson",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/RI/bg_summary_test_v7.geojson",
#   multipart = T
# )

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
  mapview(prepro_2020, color = "black", lwd = 1.5)

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
