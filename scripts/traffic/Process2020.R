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
dist_pair <- c()
for(i in 1:20){
  print(paste0("Working on: ", i, " out of ", 20))
  prep_dist_i <- prep_dist_list[[i]]
  tictoc::tic()
  dist_pair_i <- st_distance(st_geometry(prep_dist_i),
                             st_sfc(prep_dist_i$line_geom,
                                    crs = st_crs(prep_dist_i)),
                             by_element = T)
  tictoc::toc()
  dist_pair <- c(dist_pair, dist_pair_i)
}

# add this as a new column 
prep_dist$dist_pair <- dist_pair

# making this a csv and pushing to s3: 
prep_dist_csv <- prep_dist %>%
  as.data.frame() %>%
  select(-contains("geom"))
# write.csv(prep_dist_csv, "./outputs/traffic/processing/ri_dist_pairs_v2.csv")

# pushing to s3
# tmp <- tempfile()
# write.csv(prep_dist_csv, file = paste0(tmp, ".csv"), row.names = F)
# on.exit(unlink(tmp))
# put_object(
#   file = paste0(tmp, ".csv"),
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_dist_pairs_v2.csv",
#   multipart = T, 
#   show_progress = T
# )

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

# test <- less_than_500_wts_pop %>%
#   filter(GEOID20 == "440030222022026" & OBJECTID == 422)
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

# test_2 <- greater_than_500_wts_pop %>%
#   filter(GEOID20 == "440010301001001")
# mapping 
# b_greater_than_500_wts_pop <- ri_b %>%
#   filter(GEOID20 %in% greater_than_500_wts_pop$GEOID20)
# mapview(b_greater_than_500_wts_pop) +
#   mapview(prepro_ri_2020)

# add the scores back together: 
all_scores <- rbind(less_than_500_wts_pop, greater_than_500_wts_pop)
# this creates 83,909 × 36
# length(unique(all_scores$hwy_bg_id))
# test3 <- all_scores %>%
#   filter(GEOID20 == "440030222022026")

###############################################################################
## Testing different weighting options for a single block group
###############################################################################
# quick sanity check here for a single block group: 440010301001
# EJScreen answer - 1,773,572.0349
weights_test <- all_scores %>%
  filter(block_group_geoid == "440010301001") %>%
  # selecting only the columns we need
  select(GEOID20, OBJECTID, ALAND20, AWATER20, POP20, aadt,
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
         # maybe distances aren't capped?
         # inverse distances * by traffic volume 
         inv_distance_m_traff = inv_distance_m*aadt, 
         inv_distance_km_traff = inv_distance_km*aadt, 
         # inverse distances * traffic volume * block population 
         inv_distance_m_traff_pop20 = inv_distance_m_traff*POP20, 
         inv_distance_km_traff_pop20 = inv_distance_km_traff*POP20, 
         # inverse distances * traffic volume * block population * population weight
         inv_distance_m_traff_pop20_wt = inv_distance_m_traff_pop20*fraction_of_total, 
         inv_distance_km_traff_pop20_wt = inv_distance_km_traff_pop20*fraction_of_total)

# comparing to EJScreen value
weights_long_sum <- weights_test %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt, sum)) %>%
  pivot_longer(., cols = inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt) %>%
  mutate(diff_sum = (value - 1773572.0349), 
         diff_abs_sum = abs(diff_sum)) %>%
  rename(sum = value)

# maybe it's a mean!
weights_long_mean <- weights_test %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt, mean)) %>%
  pivot_longer(., cols = inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt) %>%
  mutate(diff_mean = (value - 1773572.0349), 
         diff_abs_mean = abs(diff_mean))%>%
  rename(mean = value)

# maybe it's a median!
weights_long_median <- weights_test %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt, median)) %>%
  pivot_longer(., cols = inv_distance_capped_m_traff:inv_distance_km_traff_pop20_wt) %>%
  mutate(diff_median = (value - 1773572.0349), 
         diff_abs_median = abs(diff_median))%>%
  rename(median = value)


# combining 
merged_weights <- merge(weights_long_sum, weights_long_mean, by = c("name"), all = T)
merged_weights_f <- merge(merged_weights, weights_long_median, by = "name", all = T) %>%
  rename(weighting_method = name)
merged_weights_f


# small test after input Feb 4th: 
test <- all_scores %>%
  # selecting only the columns we need
  select(GEOID20, OBJECTID, block_group_geoid, 
         ALAND20, AWATER20, POP20, aadt,
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
         # maybe distances aren't capped?
         # inverse distances * by traffic volume 
         inv_distance_m_traff = inv_distance_m*aadt, 
         inv_distance_km_traff = inv_distance_km*aadt) %>%
  # 1) sum AADT/DIST for block-road pairs only within each block at first, 
  # with NO pop weighting yet, 
  group_by(GEOID20, block_group_geoid, fraction_of_total, POP20) %>%
  summarize(summed_aadt_dist = sum(inv_distance_capped_km_traff)) 


# 2) sum over the unique blocks in each BG (each block should appear only once 
# in this step), now using pop weight of each block, and maybe ACS pop count too.
test_grouped <- test %>%
  # filtering for a specific geoid
  filter(block_group_geoid == "440010301001") %>%
  # distance * block population 
  mutate(summed_aadt_dist_pop = summed_aadt_dist*POP20, 
         # distance * block population * block pop weight
         summed_aadt_dist_pop_wt = summed_aadt_dist*POP20*fraction_of_total,
         # distance * block pop weight
         summed_aadt_dist_wt = summed_aadt_dist*fraction_of_total)

# EJScreen answer - 1,773,572.0349
sum(test_grouped$summed_aadt_dist)  
# 2,796,347; this is the same as inv_distance_capped_km_traff from merged_weights_f
sum(test_grouped$summed_aadt_dist_pop)
# 110,178,597
sum(test_grouped$summed_aadt_dist_pop_wt)
# 4,305,675
sum(test_grouped$summed_aadt_dist_wt)
# 70,854.4

###############################################################################
## Testing different weighting options for entire dataset
###############################################################################
# this is taking every dist pair (block x traffic segment pair) and 
# running a couple different weighting options: 
weights_all <- all_scores %>%
  # selecting only the columns we need
  select(GEOID20, OBJECTID, block_group_geoid, ALAND20, AWATER20, POP20, aadt,
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
weights_all_summed <- weights_all %>%
  group_by(block_group_geoid) %>%
  summarise(across(inv_distance_capped_m_traff:inv_distance_km_traff_wt, sum))

# NOTE - I'm not replacing block groups with 0 pops (like the airport) with the 
# mean value yet
# # quick mapping to see what's up: 
bg_summary_sf <- merge(weights_all_summed, ri_bg,
                       by.x = "block_group_geoid",
                       by.y = "GEOID") %>%
  st_as_sf() 

mapview(bg_summary_sf, zcol = "inv_distance_capped_km_traff") +
  mapview(prepro_ri_2020, color = "black", lwd = 2)


# adding to s3 for comparisons 
# st_write(bg_summary_sf, "./outputs/traffic/processing/ri_bg_summary_v2.geojson")
# put_object(
#   file = "./outputs/traffic/processing/ri_bg_summary_v2.geojson",
#   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_summary_v2.geojson",
#   multipart = T
# )


# this code essentially does the exact same thing as above, but just involves
# more steps 
# ri_b_weights_simple <- ri_b_weights %>%
#   select(GEOID20, block_group_geoid, POP20, fraction_of_total) %>%
#   as.data.frame() 
# 
# weights_all_block <- weights_all %>%
#   group_by(GEOID20) %>%
#   reframe(block_inv_distance_capped_km_traff = inv_distance_capped_km_traff) %>%
#   left_join(., ri_b_weights_simple) %>%
#   mutate(block_inv_distance_capped_km_traff_pop = block_inv_distance_capped_km_traff * POP20, 
#          block_inv_distance_capped_km_traff_wt = block_inv_distance_capped_km_traff * fraction_of_total, 
#          block_inv_distance_capped_km_traff_pop_wt = block_inv_distance_capped_km_traff * POP20 * fraction_of_total) %>%
#   group_by(block_group_geoid) %>%
#   summarise(across(block_inv_distance_capped_km_traff_pop:block_inv_distance_capped_km_traff_pop_wt, sum))

###############################################################################
# OLD CODE ------ Start of step 2 pig script
###############################################################################
# # -- Generate weighted scores for <= 500 set into X
# # X = FOREACH A GENERATE *, (score_lt * popwgt10_lt) as blk_pop_score;
# final_wts <- all_scores %>%
#   # multiplying scores by block pop weights 
#   # TODO - is this score_lt or the weighted_score from weightedUDF? the weighted 
#   # score also takes the full block population into account... 
#   mutate(blk_pop_score = score_lt * fraction_of_total)
#   # or maybe, there is no weighting by block population! 
#   # mutate(blk_pop_score = score_lt )
#   
# 
# # group this dataset by block group, 
# wts_summary <- final_wts %>% 
#   group_by(block_group_geoid) %>%
#   # TODO - I omitted some summary columns here but I think that is okay for 
#   # a quick analysis 
#   summarize(blk_grp_score = sum(blk_pop_score), 
#             # lessthan500 = sum(dist_pair_num <= 500), 
#             # greaterthan500 = sum(dist_pair_num > 500), 
#             # based on the pig scripts, this is a sum 
#             # total_pop = sum(POP20), 
#             mean_score = mean(score_lt)) 
# 
# # adding zero populations back in based on the mean value
# wts_summary_zeropops <- ri_bg %>%
#   rename(block_group_geoid = GEOID) %>%
#   filter(!(block_group_geoid %in% wts_summary$block_group_geoid)) %>%
#   mutate(blk_grp_score = mean(wts_summary$blk_grp_score)) %>%
#   as.data.frame() %>%
#   select(block_group_geoid, blk_grp_score) 
# 
# wts_summary_f <- bind_rows(wts_summary, wts_summary_zeropops)
# 
# # quick mapping to see what's up: 
# bg_summary_sf <- merge(wts_summary_f, ri_bg, 
#                        by.x = "block_group_geoid", 
#                        by.y = "GEOID") %>%
#   st_as_sf() %>%
#   mutate(blk_score_tiles = ntile(blk_grp_score, 100))
# 
# mapview(bg_summary_sf, zcol = "blk_score_tiles", at = c(0, 50, 80, 90, 95, 100)) + 
#   mapview(prepro_ri_2020, color = "black", lwd = 2) 
# 
# 
# # bg_summary_sf
# # st_write(bg_summary_sf, "../../outputs/traffic/processing/ri_bg_summary.geojson")
# # tmp <- tempfile()
# # write.csv(bg_summary_sf, paste0(tmp, ".csv"), row.names = F)
# # on.exit(unlink(tmp))
# # put_object(
# #   file = paste0(tmp, ".csv"),
# #   object = "s3://pedp-data-preserved/ejscreen-data-processing/traffic/ri_bg_summary.geojson",
# #   multipart = T
# # )

