###############################################################################
# Generate Census Block Weights Table
# 
# This code creates a national file of census block weights file and adds this 
# to s3. To do this, we loop through states, pull 2020 census data, and 
# calculate the percentage of the census block group population that belong
# to a given block. This table is often seen in EJScreen data processing script 
# to weight datasets. 
# 
# TODO - translate this code to match the pipeline 
# TODO - create args for code for: local vs remote, location (req), and state (opt) 
# TODO - keep national file push 
# TODO - add data for the territories 
# 
# Outputs: all from 2020 decennial census 
#   - state_abb: abbreviated state name 
#   - STATEFP20: state number 
#   - GEOID20: census block geoid
#   - ALAND20: area of block that is land 
#   - AWATER20: area of block that is water 
#   - INTPTLAT20: latitude of census block centroid
#   - INTPLON20: longitude of census block centroid
#   - HOUSING20: number of houses in census block 
#   - POP20: number of people in census block 
#   - block_group_geoid: census block group geoid 
#   - block_group_pop: census block group population 
#   - fraction_of_total: percentage of the census block population for a given 
#       block (POP20 / block_group_pop)
#   - acs_2022_bg_pop: block group population from the 2022 ACS estimate, with the 
#       goal of making it easier to override null values for the indicators 
#   - block_group_geoid_2022: block group FIPS for the 2022 ACS. Note for CT, which 
#       changed their FIPS codes in 2022, we used a crosswalk developed by the 
#       CT data collaborative to identify the corresponding 2020 FIPS match. 
#       I can confirm the spatial boundaries did not change, it was just simply 
#       a swap in census codes. 
# 
# Date: Feb 24th, 2026 
# Updated: June 3rd, 2026
# Author: EmmaLi Tsai
###############################################################################
# libraries 
library(tigris)
library(janitor)
library(tidyverse)
library(curl)
library(sf)
library(tidycensus)
library(aws.s3)
library(data.table)
# cache tigris files:
options(tigris_use_cache = TRUE)
options(scipen = 9999)

# setting ACS year for block group populations
acs_year = 2022
###############################################################################
# pull state IDs and a clean acronym for looping: 
state_acro <- states() %>%
  as.data.frame() %>%
  janitor::clean_names() %>%
  arrange(stusps) %>%
  select(statefp, stusps) %>%
  # TODO - there is definitely a way to collect populations for these territories, 
  # I just can't figure it out right now 
  filter(!(stusps %in% c("VI", "MP", "GU", "AS")))

# loop through states
weights_df <- data.frame() 
for (i in 1:nrow(state_acro)) {
  state_i <- state_acro[i,]
  state_num_i <- state_i$statefp
  print(paste0("Working on state: ", state_i$stusps, "; ", i, 
               " out of ", nrow(state_acro)))
  
  # load blocks: 
  state_i_b_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_",
                          state_num_i, 
                          "_tabblock20.zip")
  # downloading to temporary directory: 
  file_loc <- tempdir()
  on.exit(unlink(tmp))
  # using curl to download the blocks faster
  curl_download(state_i_b_url, 
                destfile = paste0(file_loc, ".zip"))
  unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
  file.remove(paste0(file_loc, ".zip"))
  # reading it from the download: 
  state_i_b <- st_read(paste0(file_loc, "/tl_2020_", state_num_i, "_tabblock20.shp")) %>%
    # create the block group geoid
    mutate(block_group_geoid = substr(GEOID20, 0, 12))
  
  
  # group decennial block data by census block group geoid, and sun 
  # populations to create the weights table
  bg_pops_i <- state_i_b %>%
    as.data.frame() %>%
    # group by block group geoid and find total pop from block pops:
    group_by(block_group_geoid) %>%
    mutate(block_group_pop = sum(POP20)) %>%
    select(block_group_geoid, block_group_pop) %>%
    unique()
  
  # add total block group populations to block table to calculate weight 
  b_weights_i <- state_i_b %>%
    left_join(bg_pops_i, by = "block_group_geoid") %>%
    # this is the weight! 
    mutate(fraction_of_total = POP20 / block_group_pop) %>%
    # remove geometries
    as.data.frame() %>%
    select(-geometry) %>%
    # add a clean state name for easier filtering 
    mutate(state_abb = state_i$stusps) %>%
    relocate(state_abb, .before = STATEFP20) %>%
    select(state_abb, STATEFP20, GEOID20, ALAND20:fraction_of_total)
  
  # adding the 2022 ACS block group population, which should help identify
  # NULL values. Note the BG ACS files in Tiger 
  # (https://www2.census.gov/geo/tiger/TIGER2022/BG/) do not actually contain 
  # populations, so I need to grab them from the census 
  state_i_bg_2022 <- tidycensus::get_acs(
    geography = "block group", 
    variables = "B01003_001", 
    state = state_num_i,
    year = as.numeric(acs_year),
    geometry = F) %>%
    rename(block_group_geoid_2022 = GEOID, 
           acs_2022_bg_pop = estimate) %>%
    select(block_group_geoid_2022, acs_2022_bg_pop)

  # quick check of block group geoids 
  print(paste0("2022 ACS BG GEOIDS: ", length(unique(state_i_bg_2022$block_group_geoid_2022)), 
               "; ", "Decennial BG GEOIDS:", length(unique(b_weights_i$block_group_geoid)), 
               "; for state: ", state_i$stusps))
  
  # break if these do not have the same number of block groups 
  # I think this is the code I need: https://github.com/Public-Environmental-Data-Partners/EJAM/blob/main/data-raw/datacreate_blockwts.R
  # however, a raw file gets loaded in here and I don't have access to it. 
  # Based on my understanding & considering the actual bounaries didn't change, 
  # we can remap them pretty easily: 
  if(length(unique(state_i_bg_2022$block_group_geoid_2022)) != length(unique(b_weights_i$block_group_geoid))){
    print("Fixing FIPS in CT")
    # reading in CT crosswalk
    ct_xwalk <- data.table::fread("https://raw.githubusercontent.com/CT-Data-Collaborative/2022-block-crosswalk/main/2022blockcrosswalk.csv")
    # tidying and cleaning fips codes: 
    ct_xwalk_tidy <- ct_xwalk %>%
      as.data.frame() %>%
      mutate(block_fips_2020 = as.character(block_fips_2020), 
             block_fips_2022 = as.character(block_fips_2022), 
             block_fips_2020 = case_when(nchar(block_fips_2020) == 14 ~ paste0("0", block_fips_2020)), 
             block_fips_2022 = case_when(nchar(block_fips_2022) == 14 ~ paste0("0", block_fips_2022)),
             block_group_2020 = substr(block_fips_2020, 1, 12), 
             block_group_2022 = substr(block_fips_2022, 1, 12)) %>%
      select(block_group_2020, block_group_2022) 
    
    state_i_bg_2022_ct <- merge(state_i_bg_2022, 
                                ct_xwalk_tidy, 
                                by.x = "block_group_geoid_2022", 
                                by.y = "block_group_2022", 
                                all = T)
    
    b_weights_i_merged <- merge(b_weights_i, 
                                state_i_bg_2022_ct, 
                                by.x = "block_group_geoid", 
                                by.y = "block_group_2020",
                                all = T)
    
  } else {
    # mergin: 
    b_weights_i_merged <- merge(b_weights_i, 
                                state_i_bg_2022, 
                                by.x = "block_group_geoid", 
                                by.y = "block_group_geoid_2022",
                                all = T) %>%
      mutate(block_group_geoid_2022 = block_group_geoid)
    
  }
  

  
  # binding 
  weights_df <- bind_rows(weights_df, b_weights_i_merged)
}

# Trimming any extra duplicates 
weights_df_uniq <- weights_df %>%
  unique() %>%
  select(-VINTAGE)

# adding to s3: 
tmp <- tempfile()
write.csv(weights_df_uniq, paste0(tmp, ".csv"), row.names = F)
on.exit(unlink(tmp))
put_object(
  file = paste0(tmp, ".csv"),
  object = "s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020_w2022pops.csv",
  multipart = T
)


### extra code to check output against previous file in s3: 
# # looking at differences against our original file: 
# test <- aws.s3::s3read_using(read.csv, 
#                              object = "s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020.csv")
# # fixing geoids & leading 0s
# test_tidy <- test %>%
#   mutate(GEOID20 = as.character(GEOID20), 
#          GEOID20 = case_when(nchar(GEOID20) == 14 ~ paste0("0", GEOID20), 
#                                    TRUE ~ GEOID20))
# # which ones got dropped? 
# missing_from_update <- test_tidy %>%
#   filter(!(GEOID20 %in% weights_df_uniq$GEOID20))
# 
# # there are 5,911 observations that are missing 
# unique(missing_from_update$state_abb)
# # "VI" "MP" "GU" "AS" --- as expected. Great!
# 
# # extra checks to confirm issues raised in:
# # https://github.com/Public-Environmental-Data-Partners/EJSCREEN-Data-Processing/issues/15
# # FIPS: 300290017032 & 300679806001
# mt_data <- weights_df_uniq %>% 
#   filter(state_abb == "MT")
