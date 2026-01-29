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

## latest weights function 
source_python("WeightedScoreUDF.py")

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
ri_b <- st_read(paste0(file_loc, "/tl_2022_44_tabblock20.shp")) %>%
  # transforming to EPSG: 26919: https://spatialreference.org/ref/epsg/26919/ 
  # TODO - maybe swap to a geographic reference system, given the distance 
  # function are calculating geodesic distances 
  st_transform(., crs = 26919)
# mapview(ri_b)

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
ri_bg <- st_read(paste0(file_loc, "/tl_2022_44_bg.shp")) %>%
  # transforming to EPSG: 26919: https://spatialreference.org/ref/epsg/26919/ 
  st_transform(., crs = 26919)
# mapview(ri_b)


# reading HPMS line segments
file <- "./outputs/traffic/preprocessing/HPMS2020_44.json" # if in EJSCREEN-Data-Processing folder, the local root of cloned repo
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
                            # TODO - flag here this was 20,000 in the OG pig script
                            dist = units::set_units(10000, "m"),
                            left = T) 
# 10153369 rows! woooooo
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
 
prep_dist <- b_line_intersect %>%
  left_join(hwy_geom_simp, by = "OBJECTID")

# the code has crashed my session before :) but should be returning geodesic 
# distances based on the function documentation. It takes ~1.5 or so to run 
prep_dist$dist_pair <- st_distance(st_geometry(prep_dist),
                                   st_sfc(prep_dist$line_geom,
                                          crs = st_crs(prep_dist)),
                                   by_element = T)

# saving main elements for now: 
# TODO - add this to s3 in the future to avoid taking up too much memory 
# on github 
# dist_pairs <- prep_dist %>%
#   as.data.frame() %>%
#   select(block_group_geoid, OBJECTID, aadt, fraction_of_total, dist_pair)
# write.csv(dist_pairs, "../../outputs/traffic/processing/ri_distpairs.csv")

# checking the output using a specific block x highway match: 
prep_dist %>%
  as.data.frame() %>% 
  select(-geometry) %>%
  filter(GEOID20 == "440030222022026") %>%
  filter(OBJECTID == "1318")
# my answer: 8213.823, Eric's answer: 8213.822559 - sweet

## split the data into > 500 and < 500 distances
# max(prep_dist$dist_pair, na.rm = T)
less_than_500 <- prep_dist %>%
  mutate(dist_pair_num = as.numeric(dist_pair)) %>%
  filter(dist_pair_num <= 500)
  # create an id for filtering in later steps 
  # mutate(hwy_bg_id = paste0(GEOID20, OBJECTID))


# running the weight function on the < 500 m distances
less_than_500_wts_pop <- less_than_500 %>%
  as.data.frame() %>%
  rowwise() %>%
  # apply weighted score UDF3 for each now
  # TODO - the script seems to imply the distance input is in m, but km seems 
  # to provide scores more similar to EJScreen.....
  mutate(wts = list(WeightedScoreUDF3(ALAND20, AWATER20, dist_pair/1000,
                                      POP20, aadt))) %>%
  # expand list column into distinct columns, and rename 
  unnest_wider(wts, names_sep = "_") %>%
  rename(adj_distance_lt = wts_1, 
         radius_lt = wts_2, 
         score_lt = wts_3, 
         weighted_score = wts_4)

# quick mappy map - ah all of the holes are where block pops are zero
# b_less_than_500_wts_pop <- ri_b %>%
#   filter(GEOID20 %in% less_than_500_wts_pop$GEOID20)
# mapview(b_less_than_500_wts_pop) + 
#   mapview(prepro_ri_2020) + 
#   mapview(ri_b, col.regions = "red")

# After this, I think the script is simply removing block x highways 
# segments that are in the < 500 group
greater_than_500 <- prep_dist %>%
  mutate(dist_pair_num = as.numeric(dist_pair)) %>%
  filter(dist_pair_num > 500) %>%
  # create an id for filtering 
  # mutate(hwy_bg_id = paste0(GEOID20, OBJECTID)) %>% 
  filter(!(GEOID20 %in% less_than_500$GEOID20))

# find the closest segment to the >500m blocks; sort to get nearest and use 
# WeightedScoreUDF function
greater_than_500_nearest <- greater_than_500 %>% 
  as.data.frame() %>%
  # for each block, grab the closest segment
  group_by(GEOID20) %>%
  arrange(dist_pair, desc = T) %>%
  slice(1) 

# apply the weights function 
greater_than_500_wts_pop <- greater_than_500_nearest %>%
  rowwise() %>%
  mutate(wts = list(WeightedScoreUDF3(ALAND20, AWATER20, dist_pair/1000,
                                      POP20, aadt))) %>%
  unnest_wider(wts, names_sep = "_") %>%
  rename(adj_distance_lt = wts_1, 
         radius_lt = wts_2, 
         score_lt = wts_3, 
         weighted_score = wts_4)

# mapping 
# b_greater_than_500_wts_pop <- ri_b %>%
#   filter(GEOID20 %in% greater_than_500_wts_pop$GEOID20)
# mapview(b_greater_than_500_wts_pop) +
#   mapview(prepro_ri_2020)

# add the scores back together: 
all_scores <- rbind(less_than_500_wts_pop, greater_than_500_wts_pop)
# this creates 83,909 × 36
# length(unique(all_scores$hwy_bg_id))

###############################################################################
# Start of step 2 pig script
###############################################################################

# -- Generate weighted scores for <= 500 set into X
# X = FOREACH A GENERATE *, (score_lt * popwgt10_lt) as blk_pop_score;
final_wts <- all_scores %>%
  # multiplying scores by block pop weights 
  # TODO - is this score_lt or the weighted_score? the weighted score 
  # also takes the full block population into account... 
  mutate(blk_pop_score = score_lt * fraction_of_total)

# group this dataset by block group, 
wts_summary <- final_wts %>% 
  group_by(block_group_geoid) %>%
  # TODO - I omitted some summary columns here but I think that is okay for 
  # a quick analysis 
  summarize(lessthan500 = sum(dist_pair_num <= 500), 
            greaterthan500 = sum(dist_pair_num > 500), 
            # based on the pig scripts, this is a sum 
            blk_grp_score = sum(blk_pop_score), 
            total_pop = sum(POP20), 
            mean_score = mean(score_lt)) 

# adding zero populations back in based on the mean value
wts_summary_zeropops <- ri_bg %>%
  rename(block_group_geoid = GEOID) %>%
  filter(!(block_group_geoid %in% wts_summary$block_group_geoid)) %>%
  mutate(blk_grp_score = mean(wts_summary$blk_grp_score)) %>%
  as.data.frame() %>%
  select(block_group_geoid, blk_grp_score) 

wts_summary_f <- bind_rows(wts_summary, wts_summary_zeropops)

# quick mapping to see what's up: 
bg_summary_sf <- merge(wts_summary_f, ri_bg, 
                       by.x = "block_group_geoid", 
                       by.y = "GEOID") %>%
  st_as_sf() %>%
  mutate(blk_score_tiles = ntile(blk_grp_score, 100))

mapview(bg_summary_sf, zcol = "blk_score_tiles", at = c(0, 50, 80, 90, 95, 100)) + 
    mapview(prepro_ri_2020, color = "black", lwd = 2) 
