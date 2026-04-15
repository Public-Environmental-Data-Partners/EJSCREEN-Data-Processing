###############################################################################
# Generate Census Block Weights Table
# 
# This code creates a national file of census block weights file and adds this 
# to s3. To do this, we loop through states, pull 2020 census data, and 
# calculate the percentage of the census block group population that belong
# to a given block. This table is often seen in EJScreen data processing script 
# to weight datasets. 
# 
# The decennial census populations come from https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/
# I can confirm these are the exact same population estimates as the redistricting 
# file noted in the EJScreen documentation.
# 
# NOTE - On April 15th, we discovered an error in our calculations, where we 
# were not using the ACS block group populations in the weight calculation (just 
# the decennial census block & block group populations)
# Weight calculation = 2020 decennial block population / 2022 ACS block group population estimate 
# 
# NOTE: we can make this a function in the future! 
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
# 
# Date: Feb 24th, 2026
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
# cache tigris files:
options(tigris_use_cache = TRUE)

# setting ACS year for block group populations
acs_year = 2022
###############################################################################
# pull state IDs and a clean acronym for looping: 
state_acro <- states() %>%
  as.data.frame() %>%
  janitor::clean_names() %>%
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
  state_i_b_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/TABBLOCK20/tl_2022_",
                          state_num_i, 
                          "_tabblock20.zip")
  # downloading to temporary directory: 
  file_loc <- tempdir()
  on.exit(unlink(tmp))
  # using curl to download the RI blocks faster
  curl_download(state_i_b_url, 
                destfile = paste0(file_loc, ".zip"))
  unzip(zipfile = paste0(file_loc, ".zip"), exdir = file_loc) 
  file.remove(paste0(file_loc, ".zip"))
  # reading it from the download: 
  state_i_b <- st_read(paste0(file_loc, "/tl_2022_", state_num_i, "_tabblock20.shp")) %>%
    # create the block group geoid
    mutate(block_group_geoid = substr(GEOID20, 0, 12))
  
  
  # note that I also pulled block groups from here, but they don't have 
  # population estimates 
  # state_i_bg_url <- paste0("https://www2.census.gov/geo/tiger/TIGER2022/BG/tl_2022_", state_num_i, 
  #                         "_bg.zip")
  
  # this was the old code, where i simply grouped the decennial block data by 
  # census block group geoid, and summed populations to create the weights table
  # bg_pops_i <- state_i_b %>%
  #   as.data.frame() %>%
  #   # group by block group geoid and find total pop from block pops: 
  #   group_by(block_group_geoid) %>%
  #   mutate(block_group_pop = sum(POP20)) %>%
  #   select(block_group_geoid, block_group_pop) %>%
  #   unique()
  
  # create block group table - this is the new method I'm testing using 2022
  # ACS data. 
  bg_pops_i <- tidycensus::get_acs(
    geography = "block group", 
    variables = "B01003_001", 
    state = state_num_i,
    year = as.numeric(acs_year),
    geometry = F) %>%
    rename(block_group_geoid = GEOID, 
           block_group_pop = estimate) %>%
    select(block_group_geoid, block_group_pop)
    
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
  
  # binding 
  weights_df <- bind_rows(weights_df, b_weights_i)
  
}

# quick check against the weights we already have: 
# test <- weights_df %>%
#   filter(state_abb == "RI")
# test

# NOTE:
# Code above generated this file: s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020_2022acsbg_pops.csv
# The previous version created this file: s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020.csv


# adding to s3: 
tmp <- tempfile()
write.csv(weights_df, paste0(tmp, ".csv"), row.names = F)
on.exit(unlink(tmp))
put_object(
  file = paste0(tmp, ".csv"),
  object = "s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020_2022acsbg_pops.csv",
  multipart = T
)
