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
# TODO - add ACS block group pops, and figure out how to handle CT 
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
    rename(block_group_geoid = GEOID, 
           acs_2022_bg_pop = estimate) %>%
    select(block_group_geoid, acs_2022_bg_pop)

  # quick check of block group geoids 
  print(paste0("2022 ACS BG GEOIDS: ", length(unique(state_i_bg_2022$block_group_geoid)), 
               "; ", "Decennial BG GEOIDS:", length(unique(b_weights_i$block_group_geoid)), 
               "; for state: ", state_i$stusps))
  
  # break if these do not have the same number of block groups 
  # TODO - figure out how to handle CT - this currently breaks (as expected!)
  if(length(unique(state_i_bg_2022$block_group_geoid)) != length(unique(b_weights_i$block_group_geoid))){
    break
  }
  
  # mergin: 
  b_weights_i_merged <- merge(b_weights_i, 
                              state_i_bg_2022, 
                              by = "block_group_geoid", 
                              all = T)
  
  # binding 
  weights_df <- bind_rows(weights_df, b_weights_i_merged)
}


# adding to s3: 
tmp <- tempfile()
write.csv(weights_df, paste0(tmp, ".csv"), row.names = F)
on.exit(unlink(tmp))
put_object(
  file = paste0(tmp, ".csv"),
  object = "s3://pedp-data-preserved/ejscreen-data-processing/census_tables/census_block_weights_2020.csv",
  multipart = T
)
