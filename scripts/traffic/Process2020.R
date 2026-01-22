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

# load RI blocks 
ri_b_url <- "https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_44_tabblock20.zip"
# downloading to temporary directory: 
file_loc <- tempdir()
# using curl to download the RI blocks faster
curl_download(ri_b_url, 
              destfile = paste0(file_loc, ".zip"))
unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
file.remove(paste0(file_loc, ".zip"))
# reading it from the download: 
ri_b <- st_read(paste0(file_loc, "/tl_2022_44_tabblock20.shp")) %>%
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
# Start of steps: 
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

# the code has crashed my session :) but should be returning geodesic 
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


## checking out the latest weights function: 
source_python("scripts/traffic/WeightedScoreUDF.py")
mini_test <- prep_dist %>%
  as.data.frame() %>%
  select(-geometry) %>% 
  slice(1) %>%
  mutate(dist_pair_km = dist_pair / 1000)

# this returns a tuple, which is essentially a list in R
mini_test_wt <- unlist(WeightedScoreUDF1(mini_test$ALAND20, mini_test$AWATER20, 
                                         mini_test$dist_pair_km, 
                                         mini_test$POP20, mini_test$aadt))

names(mini_test_wt) <- c("adj_distance_lt", "radius_lt", 
                         "score_lt", "weighted_score")
mini_test_wt
# adj_distance_lt: 5.0 
# radius_lt: 500
# score_lt = 2040.4 
# weighted_score = 46929.2 