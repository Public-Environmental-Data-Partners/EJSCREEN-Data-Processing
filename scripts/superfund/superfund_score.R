#!/usr/bin/env Rscript
# superfund_score.R
# R translation of scripts/superfund/superfund_score.py
# Uses reticulate to call Python config loaders for parity with the Python
# implementation. This wrapper mirrors the Python CLI: `-l/--location`,
# `-s/--state` (required, or 'all'), and `-o/--output-dir`.

# Short usage:
#   Purpose: Run the Superfund proximity scoring pipeline for one or more states.
#   Usage:   Rscript scripts/superfund/superfund_score.R -l <local|remote> -s <STATE|all>
#   Required R packages: optparse, sf, dplyr, readr, reticulate, jsonlite
#
#   Examples:
#     Rscript scripts/superfund/superfund_score.R -l local -s NJ
#     Rscript scripts/superfund/superfund_score.R -l local -s all
#     Rscript scripts/superfund/superfund_score.R -l remote -s NJ
# 
# TODO - need to test other states when have wifi, and other states 

suppressPackageStartupMessages({
  required <- c('optparse','sf','dplyr','readr','reticulate','jsonlite')
  for (pkg in required) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
      stop(sprintf('Required package "%s" is not installed. Install it and retry.', 
                   pkg))
    }
  }
  # aws.s3 is optional and only used if s3 URIs are provided
  has_aws <- requireNamespace('aws.s3', quietly = TRUE)
})

library(optparse)
library(sf)
library(dplyr)
library(readr)
library(reticulate)

# %||% <- function(a,b) if (is.null(a)) b else a

# TODO - npl boundary pull also is selecting weird states
script_dir <- getwd()
message(paste0("Working From: ", script_dir))

# TODO - I think we can nix this function?
# script_dir <- tryCatch({
#   this <- normalizePath(dirname(commandArgs(trailingOnly = FALSE)[1]))
#   if (is.na(this) || this == '') '.' else this
# }, error = function(e) '.')

DEFAULT_BUFFER_METERS <- 10000.0
DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME <- 'targeted_block_groups.csv'
DEFAULT_BLOCK_SITE_DISTANCES_FILENAME <- 'block_site_distances.csv'
DEFAULT_FINAL_BG_SCORES_FILENAME <- 'final_bg_scores.csv'
NPL_LAYER_NAME <- 'SITE_BOUNDARIES_SF'
ACTIVE_NPL_STATUS_CODES <- c('F','P')

option_list <- list(
  make_option(c('-l','--location'), type='character', default='local', 
              help='Select location: local or remote (default: local)'),
  make_option(c('-s','--state'), type='character', default=NULL, 
              help="Required two-letter state code or 'all'"),
  make_option(c('-o','--output-dir'), type='character', default=NULL, 
              help='Optional output directory path or s3 URI'),
  make_option(c('--targeted-block-groups-filename'), type='character', 
              default=DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME),
  make_option(c('--block-site-distances-filename'), type='character', 
              default=DEFAULT_BLOCK_SITE_DISTANCES_FILENAME),
  make_option(c('--final-bg-scores-filename'), type='character', 
              default=DEFAULT_FINAL_BG_SCORES_FILENAME),
  make_option(c('--buffer-meters'), type='double', 
              default=DEFAULT_BUFFER_METERS)
)

parser <- OptionParser(usage='%prog [options]', option_list=option_list)
args_all <- parse_args(parser, positional_arguments = FALSE)
opts <- args_all

# check for state
# TODO - good candidate for a shared R function 
if (is.null(opts$state) || trimws(opts$state) == '') stop('The -s/--state argument is required (use a two-letter postal code or "all").')

# checks for local vs remote 
# TODO - good candidate for a shared R function 
location <- tolower(trimws(opts$location))
if (!location %in% c('local','remote')) stop('Invalid location: must be "local" or "remote"')

# tidy state code, and flag if ALL is selected
# TODO - good candidate for a shared R function 
state_code <- opts$state
if (tolower(trimws(state_code)) != 'all') state_code <- toupper(trimws(state_code))
buffer_meters <- opts$`buffer-meters`

# check to see if a path is an AWS S3 path
# TODO - good candidate for a shared R function 
is_s3_uri <- function(path) {
  is.character(path) && startsWith(tolower(path), 's3://')
}

# TODO - do we need this?
rtrim <- function(x, ch) sub(paste0(ch, '+$'), '', x)

# save object to s3
# TODO - good candidate for a shared R function 
stage_s3_to_local <- function(s3_uri, dest_dir) {
  if (!has_aws) stop('aws.s3 package required to handle s3:// URIs')
  parsed <- sub('^s3://', '', s3_uri)
  parts <- strsplit(parsed, '/', fixed=TRUE)[[1]]
  bucket <- parts[1]
  key <- paste(parts[-1], collapse='/')
  dest <- file.path(dest_dir, basename(key))
  dir.create(dest_dir, recursive=TRUE, showWarnings=FALSE)
  aws.s3::save_object(object = key, bucket = bucket, file = dest)
  dest
}

# stage the output 
# TODO - good candidate for a shared R function 
stage_geospatial_input <- function(source_path, staging_root, path_name) {
  if (is.null(source_path)) stop(paste(path_name, 'not provided'))
  if (!is_s3_uri(source_path)) {
    if (!file.exists(source_path)) stop(paste(path_name, 'not found:', source_path))
    return(normalizePath(source_path))
  }
  dir.create(staging_root, recursive=TRUE, showWarnings=FALSE)
  stage_s3_to_local(source_path, staging_root)
}

# read block groups into session 
# TODO - good candidate for a shared R function 
read_block_groups <- function(bg_path) {
  if (grepl('.zip$', bg_path, ignore.case=TRUE)) {
    tmp <- tempfile(pattern='bg_unzip_')
    dir.create(tmp)
    unzip(bg_path, exdir = tmp)
    shp <- list.files(tmp, pattern='.shp$', recursive=TRUE, full.names=TRUE)[1]
    if (is.na(shp)) stop('No .shp found inside block-groups zip')
    return(st_read(shp, quiet=TRUE))
  }
  st_read(bg_path, quiet=TRUE)
}

# function to read the npl layer from the download script 
read_npl_layer <- function(npl_path) {
  if (grepl('.zip$', npl_path, ignore.case=TRUE)) {
    tmp <- tempfile(pattern='npl_unzip_')
    dir.create(tmp)
    unzip(npl_path, exdir = tmp)
    gdb <- list.files(tmp, pattern='.gdb$', recursive=TRUE, full.names=TRUE)[1]
    if (is.na(gdb)) stop('No .gdb found inside NPL zip')
    return(st_read(dsn = gdb, layer = NPL_LAYER_NAME, quiet=TRUE))
  }
  # attempt to read layer from directory / gdb
  st_read(dsn = npl_path, layer = NPL_LAYER_NAME, quiet=TRUE)
}

# function to calculate the prox score
calculate_proximity_score <- function(distance_m) {
  if (is.na(distance_m)) return(NA_real_)
  dk <- distance_m / 1000.0
  # TODO - shouldn't this be 10?
  if (dk < 0.1) return(11.0)
  1.0 / dk
}

resolvePath <- function(p) {
  if (startsWith(p, './') || startsWith(p, '../')) normalizePath(file.path(script_dir, p), mustWork = FALSE) else p
}

main <- function() {
  # print(script_dir)
  # Use reticulate to import Python config loaders from scripts/shared and 
  # scripts/superfund
  # TODO - move to scripts/ folder
  shared_dir <- normalizePath(file.path(script_dir, 'scripts/shared'))
  superfund_dir <- normalizePath(file.path(script_dir, "scripts/superfund"))
  
  py_state_mod <- import_from_path('state_config', path = shared_dir)
  py_shared_mod <- import_from_path('shared_config', path = shared_dir)
  py_super_mod <- import_from_path('superfund_config', path = superfund_dir)
  
  py_get_state_config <- py_state_mod$get_state_config
  py_get_shared_config <- py_shared_mod$get_shared_config
  py_get_superfund_config <- py_super_mod$get_superfund_config
  
  # get configs
  py_state <- py_get_state_config(state_code)
  shared_cfg <- py_get_shared_config()
  super_cfg <- py_get_superfund_config()
  
  # Determine whether to run a single-state or all-states
  # Note that we depend on this code
  # in each scoring module to know which state to process. We may want to move
  # that wisdom to the configuration file.
  # TODO: The "EJScreen Technical Documentation for Version 2.3, July 31, 2024" pdf
  # that has been our bible for these implementations says, on page 58, that this
  # indicator/index should be supported for CONUS, AK, HI, PR, Guam, and the USVI.
  # It omits mention of DC. However, I think the right logic here would be to 
  # ask for a state list with extent=US-52 (which includes DC). It seems unlikely
  # to me that DC would not be supported unless we are all supposed to 
  # simply know that the District has no superfund sites? The 
  # state configs for Guam and USVI need to be added to the list explicitly. 
  # I can't see coming up with a named extent for that grouping.
  # I'm using only US-52 for now since I don't know if we have block weight
  # data for the territories.
  states <- NULL
  if (is.character(state_code) && tolower(state_code) == 'all') {
    if (!is.null(py_state_mod$get_state_config_list)) {
      py_get_state_config_list <- py_state_mod$get_state_config_list
      states <- py_get_state_config_list('us-52')
    } else {
      stop('Shared state module does not expose get_state_config_list; cannot run "all"')
    }
  } else {
    states <- list(py_state)
  }
  
  # Iterate states
  for (i in 1:length(states)) {
    st <- states[[i]]
    postal <- st$postal
    fips <- st$fips
    metric_crs <- if (!is.null(st$metric_crs)) st$metric_crs else 'EPSG:5070'
    
    # resolve paths similar to Python implementation
    # TODO - does this path get normalized?
    npl_path <- if (!is.null(opts$`npl-boundaries-path`)) {
      opts$`npl-boundaries-path`
    } else {
      file.path(normalizePath(superfund_dir, winslash = "/"), "pipeline",
                super_cfg$preprocessed_npl_boundaries_relative_path)
    }
    
    # block groups and census blocks path templates from shared config
    tiger_template <- shared_cfg$tiger_bg_relative_path_template
    census_template <- shared_cfg$census_block_weights_relative_path_template
    
    block_groups_path <- if (!is.null(opts$`block-groups-path`)) opts$`block-groups-path` else file.path(resolvePath(shared_dir), "pipeline", gsub('{fips}', fips, tiger_template, fixed = T))
    census_blocks_path <- if (!is.null(opts$`census-blocks-path`)) opts$`census-blocks-path` else file.path(resolvePath(shared_dir), "pipeline", gsub('{postal}', postal, census_template, fixed = T))
    
    output_root <- if (!is.null(opts$`output-dir`)) opts$`output-dir` else if (location == 'local') file.path(resolvePath(superfund_dir), 'output') else super_cfg$remote_root_path
    output_dir <- file.path(output_root, gsub('{postal}', postal, super_cfg$indicator_output_relative_path_template, fixed = T))
    dir.create(output_dir, recursive=TRUE, showWarnings=FALSE)
    
    targeted_out <- file.path(output_dir, opts$`targeted-block-groups-filename`)
    distances_out <- file.path(output_dir, opts$`block-site-distances-filename`)
    final_out <- file.path(output_dir, opts$`final-bg-scores-filename`)
    
    # staging
    stg <- tempdir()
    npl_local <- if (is_s3_uri(npl_path)) stage_s3_to_local(npl_path, file.path(stg, 'npl')) else npl_path
    bg_local <- if (is_s3_uri(block_groups_path)) stage_s3_to_local(block_groups_path, file.path(stg, 'bg')) else block_groups_path
    blocks_local <- if (is_s3_uri(census_blocks_path)) stage_s3_to_local(census_blocks_path, file.path(stg, 'blocks')) else census_blocks_path
    
    # read inputs
    npl_gdf <- read_npl_layer(npl_local)
    if (!all(c('NPL_STATUS_CODE','EPA_ID') %in% names(npl_gdf))) 
      stop('NPL layer missing required columns')
    npl_gdf <- st_transform(npl_gdf, crs = metric_crs)
    
    bg_gdf <- read_block_groups(bg_local)
    if (!('GEOID' %in% names(bg_gdf))) stop('Block-group data missing GEOID column')
    bg_gdf <- st_transform(bg_gdf, crs = metric_crs)
    
    # TODO - I manually added this since shared_preprocess.R isn't functional (yet)
    # this also currently contains the updated numbers to make it easier to filter 
    # for 0 2022 ACS pops 
    blocks_df <- read.csv(blocks_local) %>%
      select(-X)
    required_cols <- c('GEOID20','INTPTLAT20','INTPTLON20','POP20',
                       'block_group_geoid','block_group_pop','fraction_of_total')
    missing <- setdiff(required_cols, names(blocks_df))
    if (length(missing) > 0) stop('Blocks CSV missing columns: ', paste(missing, collapse=', '))
    blocks_df <- blocks_df %>% mutate(block_geoid = trimws(GEOID20), 
                                      block_lat = as.numeric(INTPTLAT20), 
                                      block_lon = as.numeric(INTPTLON20), 
                                      block_pop = as.numeric(POP20), 
                                      fraction_of_total = as.numeric(fraction_of_total))
    blocks_gdf <- st_as_sf(blocks_df, coords = c('block_lon','block_lat'), 
                           # TODO - flag of a hard coded CRS 
                           crs = 4326, remove = FALSE) %>% st_transform(metric_crs)
    
    # Step1: buffer and targeted block groups
    npl_active <- filter(npl_gdf, NPL_STATUS_CODE %in% ACTIVE_NPL_STATUS_CODES)
    # need to cast due to some weird groms: 
    # npl_active <- st_cast(npl_active, "MULTIPOLYGON")
    npl_active <- npl_active %>%
      st_cast(., "MULTIPOLYGON") %>%
      st_buffer(., dist = buffer_meters)
    # npl_active$geometry_buffer <- st_buffer(npl_active, dist = buffer_meters)
    npl_buffer <- npl_active %>%
      st_transform(., crs = st_crs(bg_gdf))
    # st_geometry(npl_buffer) <- "geometry_buffer.geometry_buffer.Shape"
    
    # join them via intersection: 
    joined <- st_join(bg_gdf, npl_buffer, 
                      join = st_intersects, left = FALSE)
    
    if (nrow(joined) == 0) {
      targeted <- tibble::tibble(GEOID_BG = character(), EPA_ID = character())
    } else {
      targeted <- joined %>% st_drop_geometry() %>% select(GEOID_BG = GEOID, EPA_ID = EPA_ID) %>% distinct() %>% mutate(EPA_ID = trimws(as.character(EPA_ID)))
    }
    write_csv(targeted, targeted_out)
    
    # Step2: block-site distances
    if (nrow(targeted) == 0) {
      distances_df <- tibble::tibble(GEOID_BLOCK = character(), 
                                     EPA_ID = character(), distance_m = numeric())
    } else {
      target_bgs <- unique(as.character(targeted$GEOID_BG))
      blocks_sub <- blocks_gdf %>% filter(block_group_geoid %in% target_bgs)
      if (nrow(blocks_sub) == 0) {
        distances_df <- tibble::tibble(GEOID_BLOCK = character(), 
                                       EPA_ID = character(), distance_m = numeric())
      } else {
        npl_lookup <- npl_gdf %>% 
          rename("geometry" = "Shape") %>%
          st_as_sf() %>%
          select(EPA_ID, geometry) %>% 
          mutate(EPA_ID = trimws(as.character(EPA_ID)))
        distances_list <- list()
        # TODO - this is really freakin slow 
        for (i in seq_len(nrow(targeted))) {
          if(i %% 1000 == 0) {
            message("On: ", i, "; out of: ", nrow(targeted))
          }
          bg <- as.character(targeted$GEOID_BG[i])
          epa <- as.character(targeted$EPA_ID[i])
          poly_row <- filter(npl_lookup, EPA_ID == epa)
          if (nrow(poly_row) == 0) next
          blocks_for_bg <- filter(blocks_sub, block_group_geoid == bg)
          if (nrow(blocks_for_bg) == 0) next
          d <- st_distance(st_geometry(blocks_for_bg), st_geometry(poly_row)[1])
          d_num <- as.numeric(d)
          distances_list[[length(distances_list) + 1]] <- tibble::tibble(GEOID_BLOCK = blocks_for_bg$block_geoid, EPA_ID = epa, distance_m = d_num)
        }
        distances_df <- bind_rows(distances_list)
      }
    }
    write_csv(distances_df, distances_out)
    
    # Step3: inverse-distance scoring
    if (nrow(distances_df) > 0) {
      distances_df <- distances_df %>% 
        mutate(proximity_score = vapply(distance_m, calculate_proximity_score, numeric(1)))
    }
    write_csv(distances_df, distances_out)
    
    # Step4: population weighting aggregation
    if (nrow(distances_df) > 0) {
      # we don't need geoms anymore 
      blocks_tab <- blocks_gdf %>% 
        st_drop_geometry() %>% 
        select(block_geoid, block_group_geoid, fraction_of_total, 
               acs_2022_bg_pop)
      # merge
      merged <- distances_df %>% 
        left_join(blocks_tab, by = c('GEOID_BLOCK' = 'block_geoid')) %>% 
        filter(!is.na(proximity_score)) %>% 
        mutate(weighted_score = as.numeric(proximity_score) * as.numeric(fraction_of_total)) %>%
        # TODO - add filter here to capture instances where acs 2022 pop == 0 
        mutate(weighted_score = case_when(acs_2022_bg_pop == 0 ~ NA, 
                                          TRUE ~ weighted_score))
      # summarizin 
      agg_targeted <- merged %>% 
        group_by(block_group_geoid) %>%
        summarize(weighted_score = round(sum(weighted_score), 4), 
                  .groups='drop')
      
      # get all the distinct stuffs 
      # all_bgs <- blocks_tab %>% 
      #   select(block_group_geoid) %>% 
      #   distinct()
      
      agg <- agg_targeted 
        # left_join(agg_targeted, by='block_group_geoid') %>% 
        # this makes the NAs zero, but I think they should be NA
        # TODO - ^ check this -- also the code below creates too many NAs now?
        # mutate(weighted_score = ifelse(is.na(weighted_score), 0, weighted_score))
      
    } else {
      agg <- tibble::tibble(block_group_geoid = character(), 
                            weighted_score = numeric())
    }
    final_df <- agg %>% 
      rename(superfund_score = weighted_score)
    # TODO - why is this goign to a random s3 folder on my computerrrrrr
    # update: I think I fixed it. I'm a wizard 
    write_csv(final_df, final_out)
    
  } # end for (st in states)
  
  message('Superfund indicator R pipeline completed successfully')
}


tryCatch({
  main()
}, error = function(e) {
  message('Error: ', e$message)
  quit(status = 1)
})


# quick plottin
# library(mapview)

# bg_gdf_simp <- bg_gdf %>%
#   select(GEOID)
# 
# final_df_sf <- merge(final_df, bg_gdf_simp,
#                      by.x = "block_group_geoid",
#                      by.y = "GEOID") %>%
#   st_as_sf()
# 
# mapview(final_df_sf, zcol = "superfund_score") +
#   mapview(npl_gdf)

# final_df %>%
#   filter(block_group_geoid == '340019900000')




