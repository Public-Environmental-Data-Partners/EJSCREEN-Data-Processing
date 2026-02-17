###############################################################################
# This code attempts to recreate the processing steps for the traffic proximity 
# variable for EJScreen. Here, we are loading the JSON file from the 
# pre-processing script and recreating the steps outlined in this pig script: 
# https://github.com/USEPA-clone/ejscreen-traffic-proximity-processing/blob/main/Highway_processing_1_step1_pig.txt
#
# We are currently testing this processing using the 2020 HPMS segments for Rhode
# Island. 
# 
# Date: Jan 14th, 2026
# Author: EmmaLi Tsai
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
library(reticulate)
library(aws.s3)
library(tidycensus)
library(tictoc) # <- this package is optional, just for tracking dist functions 

options(scipen = 999)

## latest weights function 
source_python("./scripts/traffic/WeightedScoreUDF.py")

# load RI blocks 
ri_b_url <- "https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_44_tabblock20.zip"
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the RI blocks faster
curl_download(ri_b_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
ri_b <- st_read(paste0(file_loc, "/tl_2022_44_tabblock20.shp")) 

# load RI block groups (used for plotting)
ri_bg_url <- "https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_44_bg.zip"
# downloading to temporary directory: 
file_loc <- tempdir()
on.exit(unlink(tmp))
# using curl to download the RI block groups faster
curl_download(ri_bg_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
ri_bg <- st_read(paste0(file_loc, "/tl_2022_44_bg.shp"))


# reading HPMS line segments
# if in EJSCREEN-Data-Processing folder, the local root of cloned repo
file <- "./outputs/traffic/preprocessing/HPMS2020_44.json" 
prepro_ri_2020 <- st_read(file) %>% 
  st_transform(., crs = st_crs(ri_b))
# quick plot to see what we're cookin' with: 
# mapview(prepro_ri_2020)

###############################################################################
# Start of step 1 pig script
###############################################################################

## create point geometries from block dataset
ri_b_no_zero_points <- ri_b %>%
  st_centroid() %>%
  # filter for pops > 0 
  filter(POP20 > 0) %>%
  # create the block group geoid
  mutate(block_group_geoid = substr(GEOID20, 0, 12))
# checking the output: 
# mapview(ri_b_no_zero_points)

## recreate population weight table: 
bg_pops <- ri_b_no_zero_points %>%
  as.data.frame() %>%
  group_by(block_group_geoid) %>%
  mutate(block_group_pop = sum(POP20)) %>%
  select(block_group_geoid, block_group_pop) %>%
  unique()

ri_b_weights <- ri_b_no_zero_points %>%
  left_join(bg_pops, by = "block_group_geoid") %>%
  mutate(fraction_of_total = POP20 / block_group_pop)
# checking my answers - they match! 0.009615385
# ri_b_weights %>% filter(GEOID20 == "440090505002057")


## cross assign point geometries from RI into the line segment geoms, this 
# is essentially joining blocks and segments by assigning each block to 
# potentially multiple line segments. 
b_line_intersect <- st_join(ri_b_weights, prepro_ri_2020, 
                            join = st_is_within_distance, 
                            # flag here that the original pig script 
                            # assigned each highway segment a block centroid
                            dist = units::set_units(20000, "m"),
                            left = T) 
# 22,892,785
nrow(b_line_intersect)

# gut check - flag here that this returns highway segments that are outside 
# th RI area 
# test <- b_line_intersect %>% filter(GEOID20 == "440030222022026")
# hwys <- prepro_ri_2020 %>% filter(OBJECTID %in% test$OBJECTID)
# mapview(test) +
#   mapview(hwys)


## create distance pairs 
# first, make sure the crs are the same - this returns TRUE 
st_crs(prepro_ri_2020) == st_crs(b_line_intersect)

# extract line geoms and add them back in, but not as the active geometry:
hwy_geom_simp <- prepro_ri_2020 %>%
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

# add this as a new column 
# prep_dist$dist_pair <- dist_pair

# making this a csv and pushing to s3: 
# prep_dist_csv <- prep_dist %>%
#   as.data.frame() %>%
#   select(-contains("geom"))

# note v2 was creating mis-matching traffic x block distance matches 
# write.csv(dist_pair_df, "./outputs/traffic/processing/ri_dist_pairs_v3.csv")

# pushing to s3 
# TODO - actually push this!!
# put_object(
#   file = "./outputs/traffic/processing/ri_dist_pairs_v3.csv",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_dist_pairs_v3.csv",
#   multipart = T, 
#   show_progress = T
# )

# checking the output using a specific block x highway match: 
dist_pair_df %>%
  filter(GEOID20 == "440030222022026") %>%
  filter(OBJECTID == "1318")
# my answer: 8213.823, Eric's answer: 8213.822559 - sweet

###############################################################################
# Split < 500 and > 500 distance pairs, and multiply inverse distance by
# traffic volume 
###############################################################################
# filter data for pairs < 500m 
less_than_500 <- dist_pair_df %>%
  mutate(dist_pair_num = as.numeric(distance)) %>%
  filter(dist_pair_num <= 500) %>%
  # here, assuming distance in km
  mutate(dist_pair_km = dist_pair_num/1000, 
         # capping inverse distance to max 10
         inverse_distance = 1/dist_pair_km, 
         inverse_distance = case_when(inverse_distance > 10 ~ 10, 
                                      TRUE ~ inverse_distance)) %>%
  mutate(score = inverse_distance * aadt) %>%
  as.data.frame() %>%
  select(-contains("geometry"))

# After this, I think the script is simply removing block x highways 
# segments that are in the < 500 group
greater_than_500 <- dist_pair_df %>%
  # mutate(dist_pair_num = as.numeric(dist_pair)) %>%
  filter(!(GEOID20 %in% less_than_500$GEOID20))

# find the closest segment to the >500m blocks; multiple inverse distance 
# by aadt 
greater_than_500_nearest <- greater_than_500 %>% 
  as.data.frame() %>%
  # for each block, grab the closest segment
  group_by(GEOID20) %>%
  mutate(dist_pair_num = as.numeric(distance)) %>%
  arrange(dist_pair_num, desc = T) %>%
  slice(1) %>%
  mutate(dist_pair_km = dist_pair_num/1000, 
         inverse_distance = 1/dist_pair_km, 
         inverse_distance = case_when(inverse_distance > 10 ~ 10, 
                                      TRUE ~ inverse_distance)) %>%
  mutate(score = inverse_distance * aadt) %>%
  as.data.frame() %>%
  select(-contains("geometry"))


# binding them together  
all_scorest_test <- rbind(less_than_500, greater_than_500_nearest)

###############################################################################
# Option 1: test out different weighting options - Eric's method 
###############################################################################
# pulling in OG RI numbers from EJScreen: 
ptraf <- aws.s3::s3read_using(read.csv, 
                              object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_ptraf.csv") %>%
  select(-X) %>%
  mutate(ID = as.character(ID)) %>%
  rename(block_group_geoid = ID)

# Start aggregating process: 
take_median_before_grouping <- all_scorest_test %>%
  # group by census block
  group_by(GEOID20, block_group_geoid, POP20, fraction_of_total) %>%
  # take the median traffic score for that block 
  summarize(score_median = median(score, na.rm = T)) %>%
  # multiply this traffic score by the decennial 2020 pop and the pop weight 
  # for this block 
  mutate(score_weighted = score_median * POP20 * fraction_of_total) 

# group by block group geoid and sum all scores
take_median_before_grouping_bg <- take_median_before_grouping %>%
  group_by(block_group_geoid) %>%
  summarize(score_weighted_sum = sum(score_weighted, na.rm = T))

# merge to create a sf object for plotting: 
take_median_before_grouping_bg_f_sf <- take_median_before_grouping_bg %>%
  merge(., ri_bg %>% select(GEOID), by.x = "block_group_geoid", by.y = "GEOID") %>%
  st_as_sf() %>%
  left_join(., ptraf) %>%
  rowwise() %>%
  mutate(raw_ejscreen_score_diff = PTRAF - score_weighted_sum, 
         abs_ejscreen_score_diff = abs(PTRAF - score_weighted_sum), 
         pct_diff = 100 * (raw_ejscreen_score_diff / ((PTRAF + score_weighted_sum)/2)))

# relationship between ej screen and option 1: 
ggplot(take_median_before_grouping_bg_f_sf, 
       aes(x = PTRAF, y = score_weighted_sum)) + 
  theme_bw() + 
  geom_point() + 
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  ggtitle("Option 1 Comparisons - Taking a Median for Blocks & Applying Weights, Summing by Block Group")

# map of scores 
mapview(take_median_before_grouping_bg_f_sf, zcol = "score_weighted_sum") + 
  mapview(prepro_ri_2020, color = "black", lwd = 1.5)

# summary of raw differences: 
take_median_before_grouping_bg_f_sf %>%
  as.data.frame() %>%
  summarize(raw_mean_difference = mean(raw_ejscreen_score_diff, na.rm = T), 
            mean_abs_difference = mean(abs_ejscreen_score_diff, na.rm = T), 
            mean_pct_diff = mean(pct_diff, na.rm = T))

# map of differences: 
mapview(take_median_before_grouping_bg_f_sf, zcol = "pct_diff") + 
  mapview(prepro_ri_2020, color = "black", lwd = 2)

###############################################################################
## Testing different weighting options for entire dataset
###############################################################################
# this is taking every dist pair (block x traffic segment pair) and 
# running a couple different weighting options: 
weights_all <- all_scorest_test %>%
  # selecting only the columns we need
  select(GEOID20, OBJECTID, block_group_geoid, POP20, aadt,
         fraction_of_total, dist_pair_num) %>%
  # doing this by hand so it's a bit easier for my brain to handle.
  # first, take the inverse distance (in both m and km), set a cap and 
  # don't set a cap 
  mutate(inv_distance_m = 1/dist_pair_num, 
         inv_distance_capped_m = case_when(inv_distance_m > 10 ~ 10, 
                                           TRUE ~ inv_distance_m), 
         inv_distance_km = 1/(dist_pair_num/1000), 
         inv_distance_capped_km = case_when(inv_distance_km > 10 ~ 10, 
                                            TRUE ~ inv_distance_km), 
         # inverse distances * by traffic volume 
         inv_distance_capped_m_traff = inv_distance_capped_m*aadt, 
         inv_distance_capped_km_traff = inv_distance_capped_km*aadt, 
         # inverse distances * traffic volume * block population 
         inv_distance_capped_m_traff_pop20 = inv_distance_capped_m_traff*POP20, 
         inv_distance_capped_km_traff_pop20 = inv_distance_capped_km_traff*POP20, 
         # inverse distances * traffic volume * block population * population weight
         inv_distance_capped_m_traff_pop20_wt = inv_distance_capped_m_traff_pop20*fraction_of_total, 
         inv_distance_capped_km_traff_pop20_wt = inv_distance_capped_km_traff_pop20*fraction_of_total, 
         # inverse distances * traffic volume * pop weight
         inv_distance_capped_m_traff_wt = inv_distance_capped_m_traff*fraction_of_total, 
         inv_distance_capped_km_traff_wt = inv_distance_capped_km_traff*fraction_of_total, 
         # maybe distances aren't capped?
         # inverse distances * by traffic volume 
         inv_distance_m_traff = inv_distance_m*aadt, 
         inv_distance_km_traff = inv_distance_km*aadt, 
         # inverse distances * traffic volume * block population 
         inv_distance_m_traff_pop20 = inv_distance_m_traff*POP20, 
         inv_distance_km_traff_pop20 = inv_distance_km_traff*POP20, 
         # inverse distances * traffic volume * block population * population weight
         inv_distance_m_traff_pop20_wt = inv_distance_m_traff_pop20*fraction_of_total, 
         inv_distance_km_traff_pop20_wt = inv_distance_km_traff_pop20*fraction_of_total, 
         # inverse distances * traffic volume * pop weight
         inv_distance_m_traff_wt = inv_distance_m_traff*fraction_of_total, 
         inv_distance_km_traff_wt = inv_distance_km_traff*fraction_of_total)



# from here, we are grouping dist pairs by block groups and summing the
# different weighting options above 
weights_all_summed_no_grouping <- weights_all %>%
  group_by(block_group_geoid) %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, sum))%>% 
  # I think it's pretty safe to say it's capped to 10
  select(contains("_capped_"), block_group_geoid) %>%
  select(contains("_km_"), block_group_geoid) %>%
  left_join(., ptraf) 

# creating a quick plot: 
weights_all_summed_no_grouping_long <- weights_all_summed_no_grouping %>%
  pivot_longer(., cols = inv_distance_capped_km_traff:inv_distance_capped_km_traff_wt)
ggplot(weights_all_summed_no_grouping_long, aes(x = PTRAF, y = value)) + 
  geom_point() +
  facet_wrap(~name, scales = "free") +
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  theme_bw() + 
  ggtitle("Different Weighting Options - grouped by block group & summed") +
  labs(x = "EJScreen Traffic Prox", 
       y = "Block Group Estimated Traffic Prox")

# summary of raw differences: 
weights_all_summed_no_grouping_long %>%
  as.data.frame() %>%
  mutate(raw_ejscreen_score_diff = PTRAF - value, 
         abs_ejscreen_score_diff = abs(PTRAF - value), 
         pct_diff = 100 * (raw_ejscreen_score_diff / ((PTRAF + value)/2))) %>%
  group_by(name) %>%
  summarize(mean_ejscreen_score_diff = mean(raw_ejscreen_score_diff, na.rm = T), 
            mean_abs_ejscreen_score_diff = mean(abs_ejscreen_score_diff, na.rm = T), 
            mean_pct_diff = mean(pct_diff, na.rm = T))

# quick map
weights_all_summed_no_grouping_sf <- weights_all_summed_no_grouping %>%
  merge(., ri_bg %>% select(GEOID), by.x = "block_group_geoid", by.y = "GEOID") %>%
  st_as_sf() 
mapview(weights_all_summed_no_grouping_sf, zcol = "inv_distance_capped_km_traff_pop20_wt") + 
  mapview(prepro_ri_2020, color = "black", lwd = 1.5)



## maybe grouping happens for census blocks beforehand? who knows!
weights_all_summed_grouping_median <- weights_all %>%
  group_by(GEOID20, block_group_geoid) %>%
  # maybe it's a median!
  summarize(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, median)) %>%
  group_by(block_group_geoid) %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, sum)) %>%
  # rename_with(., ~paste0(., "_grouped_median")) %>% 
  # rename(block_group_geoid = block_group_geoid_grouped_median) %>%
  # I think it's pretty safe to say it's capped to 10
  select(contains("_capped_"), block_group_geoid) %>%
  select(contains("_km_"), block_group_geoid) %>%
  left_join(., ptraf) 

weights_all_summed_grouping_median_long <- weights_all_summed_grouping_median %>%
  pivot_longer(., cols = inv_distance_capped_km_traff:inv_distance_capped_km_traff_wt)
ggplot(weights_all_summed_grouping_median_long, aes(x = PTRAF, y = value)) + 
  geom_point() +
  facet_wrap(~name, scales = "free") +
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  theme_bw() + 
  ggtitle("Different Weighting Options - grouped by blocks & summarized w/ medians, then grouped by block group & summed") +
  labs(x = "EJScreen Traffic Prox", 
       y = "Block Group Estimated Traffic Prox")

# summary of raw differences: 
weights_all_summed_grouping_median_long %>%
  as.data.frame() %>%
  mutate(raw_ejscreen_score_diff = PTRAF - value, 
         abs_ejscreen_score_diff = abs(PTRAF - value), 
         pct_diff = 100 * (raw_ejscreen_score_diff / ((PTRAF + value)/2))) %>%
  group_by(name) %>%
  summarize(mean_ejscreen_score_diff = mean(raw_ejscreen_score_diff, na.rm = T), 
            mean_abs_ejscreen_score_diff = mean(abs_ejscreen_score_diff, na.rm = T), 
            mean_pct_diff = mean(pct_diff, na.rm = T))



# maybe grouping happens for census blocks beforehand? who knows!
weights_all_summed_grouping_mean <- weights_all %>%
  group_by(GEOID20, block_group_geoid) %>%
  # maybe it's a mean!
  summarize(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, mean)) %>%
  group_by(block_group_geoid) %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, sum)) %>%
  # rename_with(., ~paste0(., "_grouped_median")) %>% 
  # rename(block_group_geoid = block_group_geoid_grouped_median) %>%
  # I think it's pretty safe to say it's capped to 10
  select(contains("_capped_"), block_group_geoid) %>%
  select(contains("_km_"), block_group_geoid) %>%
  left_join(., ptraf) 

weights_all_summed_grouping_mean_long <- weights_all_summed_grouping_mean %>%
  pivot_longer(., cols = inv_distance_capped_km_traff:inv_distance_capped_km_traff_wt)
ggplot(weights_all_summed_grouping_mean_long, aes(x = PTRAF, y = value)) + 
  geom_point() +
  facet_wrap(~name, scales = "free") +
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  theme_bw() + 
  ggtitle("Different Weighting Options - grouped by blocks & summarized w/ means, then grouped by block group & summed") +
  labs(x = "EJScreen Traffic Prox", 
       y = "Block Group Estimated Traffic Prox")

weights_all_summed_grouping_mean_long %>%
  as.data.frame() %>%
  mutate(raw_ejscreen_score_diff = PTRAF - value, 
         abs_ejscreen_score_diff = abs(PTRAF - value), 
         pct_diff = 100 * (raw_ejscreen_score_diff / ((PTRAF + value)/2))) %>%
  group_by(name) %>%
  summarize(mean_ejscreen_score_diff = mean(raw_ejscreen_score_diff, na.rm = T), 
            mean_abs_ejscreen_score_diff = mean(abs_ejscreen_score_diff, na.rm = T), 
            mean_pct_diff = mean(pct_diff, na.rm = T))

# NOTE - I'm not replacing block groups with 0 pops (like the airport) with the 
# mean value yet

# combining different scores 
weights_all_options <- merge(weights_all_summed_no_grouping, 
                             weights_all_summed_grouping_median %>%
                               rename_with(~paste0("grouped_median_", .),
                                           .cols = contains("_distance_")),
                             by = c("block_group_geoid", "PTRAF")) %>%
  merge(., weights_all_summed_grouping_mean %>%
          rename_with(~paste0("grouped_mean_", .),
                      .cols = contains("_distance_"))) 

# # quick mapping to see what's up: 
bg_summary_sf <- merge(weights_all_options, ri_bg,
                       by.x = "block_group_geoid",
                       by.y = "GEOID") %>%
  st_as_sf() 
mapview(bg_summary_sf, 
        zcol = "inv_distance_capped_km_traff_pop20_wt") 

# adding to s3 for comparisons 
# st_write(bg_summary_sf, "./outputs/traffic/processing/ri_bg_summary_test_v4.geojson")
# put_object(
#   file = "./outputs/traffic/processing/ri_bg_summary_test_v4.geojson",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/RI/bg_summary_test_v4.geojson",
#   multipart = T
# )

###############################################################################
## Pushing v4 through comparison pipeline 
###############################################################################
# process - go to terminal and navigate to EJSCREEN-Data-Processing/scripts/utilities/validation
# change line 62 of geojson2csv.py to the name of the file pushed 
# in terminal, run python geojson2csv.py --state RI
# in script, make any necessary comparison changes to compreEJAM2pipeline.py
# in terminal, run python compareEJAM2pipeline.py --state RI

# check out RI/comparison_results for merged_reduced, and summary reports!

# pull in merged reduced; 
merged_reduced <- aws.s3::s3read_using(read.csv, 
                                       object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/RI/comparison_results/merged_reduced.csv")

###############################################################################
## taking a deeper look at a single block group: 
###############################################################################
# finding a block group that is near the urban center 
blockgroup <- merged_reduced %>%
  filter(ejam_uniq_id == "440070025003")
blockgroup_geoid = blockgroup$ejam_uniq_id
blockgroup_score = blockgroup$traffic.score

# pulling in block group and block information: 
ri_bg_filt <- ri_bg %>%
  filter(GEOID == blockgroup_geoid) %>%
  select(GEOID, ALAND, AWATER)
ri_b_filt <- ri_b_weights %>%
  filter(block_group_geoid == blockgroup_geoid) %>%
  select(GEOID20, block_group_geoid, POP20, ALAND20, AWATER20, 
         block_group_pop, fraction_of_total)

# pull in scores: 
scores_filt <- all_scorest_test %>%
  filter(block_group_geoid == blockgroup_geoid) %>%
  # bring in some of the extra information from census blocks 
  merge(., ri_b_filt, by = c("GEOID20", "block_group_geoid", 
                             "POP20", "fraction_of_total"))

# pull in highway segments: 
highway_filt <- prepro_ri_2020 %>%
  filter(OBJECTID %in% scores_filt$OBJECTID)

# quick map: 
mapview(ri_bg_filt) + 
  mapview(ri_b_filt) + 
  mapview(highway_filt)

# noodling space for different weighting and aggregation options: 
scores_filt %>%
  group_by(GEOID20, block_group_geoid, fraction_of_total, POP20, 
           ALAND20, AWATER20) %>%
  summarize(pct_land = ALAND20 / (ALAND20 + AWATER20), 
            score_lt = median(score * POP20 * pct_land)) %>%
  mutate(score_wt = score_lt * fraction_of_total) %>%
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(score_wt)) %>%
  mutate(diff = blockgroup_score - weighted_score)

###############################################################################
## what if there is no < 500 and > 500m split 
###############################################################################
# pulling in OG RI numbers from EJScreen: 
ptraf <- aws.s3::s3read_using(read.csv, 
                              object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_ptraf.csv") %>%
  select(-X) %>%
  mutate(ID = as.character(ID)) %>%
  rename(block_group_geoid = ID)

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
  merge(., ri_b_weights, by = c("GEOID20", "block_group_geoid", 
                                "POP20", "fraction_of_total")) %>%
  group_by(GEOID20, block_group_geoid, fraction_of_total, POP20, 
           ALAND20, AWATER20) %>%
  # this is a sum!!! 
  summarize(score_lt = sum(score)) %>%
  mutate(score_wt = score_lt * fraction_of_total) %>%
  group_by(block_group_geoid) %>%
  summarize(weighted_score = sum(score_wt)) 

# mapping: 
test_weight_all_sf <- test_weight_all %>%
  merge(., ri_bg %>% select(GEOID), by.x = "block_group_geoid", 
        by.y = "GEOID") %>%
  merge(., ptraf, by.x = "block_group_geoid", by.y = "block_group_geoid") %>%
  st_as_sf() %>%
  mutate(diff_estimate_minus_ptraf = weighted_score - PTRAF, 
         abs_diff_estimate_minus_ptraf = abs(weighted_score - PTRAF))

mapview(test_weight_all_sf, zcol = "diff_estimate_minus_ptraf", 
        col.regions = RColorBrewer::brewer.pal(9, "RdBu")) + 
  mapview(prepro_ri_2020, color = "black", lwd = 1.5)

test_weight_all_df <- test_weight_all_sf %>%
  as.data.frame() 
ggplot(test_weight_all_df, aes(x = PTRAF, y = weighted_score)) + 
  geom_point() + 
  geom_abline(intercept = 0, slope = 1, color = "red", lty = "dashed") + 
  theme_bw() + 
  labs(x = "EJScreen Traffic Prox Score", y = "Estimated Weighted Score") + 
  ggtitle("EJScreen Score Test - No 500m Split, sum inverse dist * aadt for blocks before grouping by block group * pop weight")
  
# adding to s3 for comparisons 
# st_write(test_weight_all_sf, "./outputs/traffic/processing/ri_bg_summary_v5.geojson")
# put_object(
#   file = "./outputs/traffic/processing/ri_bg_summary_v5.geojson",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/RI/bg_summary_test_v5.geojson",
#   multipart = T
# )

# making sure results are identical - woo! 
# test <- aws.s3::s3read_using(st_read, 
#                              object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/RI/bg_summary_test_v5.geojson")
# head(test)
# head(test_weight_all_df)