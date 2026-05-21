#!/usr/bin/env Rscript
# superfund_indicator.R
# R translation of scripts/superfund/superfund_indicator.py
# Uses reticulate to call Python config loaders for exact parity.

suppressPackageStartupMessages({
  required <- c('optparse','sf','dplyr','readr','reticulate','jsonlite')
  for (pkg in required) {
    if (!requireNamespace(pkg, quietly = TRUE)) {
      stop(sprintf('Required package "%s" is not installed. Install it and retry.', pkg))
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

%||% <- function(a,b) if (is.null(a)) b else a

script_dir <- tryCatch({
  this <- normalizePath(dirname(commandArgs(trailingOnly = FALSE)[1]))
  if (is.na(this) || this == '') '.' else this
}, error = function(e) '.')

DEFAULT_BUFFER_METERS <- 10000.0
DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME <- 'targeted_block_groups.csv'
DEFAULT_BLOCK_SITE_DISTANCES_FILENAME <- 'block_site_distances.csv'
DEFAULT_FINAL_BG_SCORES_FILENAME <- 'final_bg_scores.csv'
NPL_LAYER_NAME <- 'SITE_BOUNDARIES_SF'
ACTIVE_NPL_STATUS_CODES <- c('F','P')

option_list <- list(
  make_option(c('--state'), type='character', default='VT', help='Two-letter state code (default VT)'),
  make_option(c('--npl-boundaries-path'), type='character', default=NULL, help='Optional path to canonical .gdb directory or zip'),
  make_option(c('--block-groups-path'), type='character', default=NULL, help='Optional path or zip for TIGER block-groups'),
  make_option(c('--census-blocks-path'), type='character', default=NULL, help='Optional path for census block weights CSV'),
  make_option(c('--output-dir'), type='character', default=NULL, help='Optional output directory path or s3 URI'),
  make_option(c('--targeted-block-groups-filename'), type='character', default=DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME),
  make_option(c('--block-site-distances-filename'), type='character', default=DEFAULT_BLOCK_SITE_DISTANCES_FILENAME),
  make_option(c('--final-bg-scores-filename'), type='character', default=DEFAULT_FINAL_BG_SCORES_FILENAME),
  make_option(c('--buffer-meters'), type='double', default=DEFAULT_BUFFER_METERS)
)

parser <- OptionParser(usage='%prog storage_mode [options]', option_list=option_list)
args_all <- parse_args(parser, positional_arguments = TRUE)
pos <- args_all$args
opts <- args_all$options

if (length(pos) < 1) stop('storage_mode positional argument required: local or remote')
storage_mode <- pos[1]
if (!storage_mode %in% c('local','remote')) stop('storage_mode must be "local" or "remote"')

state_code <- toupper(trimws(opts$state))
buffer_meters <- opts$buffer_meters

is_s3_uri <- function(path) {
  is.character(path) && startsWith(tolower(path), 's3://')
}

rtrim <- function(x, ch) sub(paste0(ch, '+$'), '', x)

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

stage_geospatial_input <- function(source_path, staging_root, path_name) {
  if (is.null(source_path)) stop(paste(path_name, 'not provided'))
  if (!is_s3_uri(source_path)) {
    if (!file.exists(source_path)) stop(paste(path_name, 'not found:', source_path))
    return(normalizePath(source_path))
  }
  dir.create(staging_root, recursive=TRUE, showWarnings=FALSE)
  stage_s3_to_local(source_path, staging_root)
}

read_block_groups <- function(bg_path) {
  if (grepl('\\.zip$', bg_path, ignore.case=TRUE)) {
    tmp <- tempfile(pattern='bg_unzip_')
    dir.create(tmp)
    unzip(bg_path, exdir = tmp)
    shp <- list.files(tmp, pattern='\\.shp$', recursive=TRUE, full.names=TRUE)[1]
    if (is.na(shp)) stop('No .shp found inside block-groups zip')
    return(st_read(shp, quiet=TRUE))
  }
  st_read(bg_path, quiet=TRUE)
}

read_npl_layer <- function(npl_path) {
  if (grepl('\\.zip$', npl_path, ignore.case=TRUE)) {
    tmp <- tempfile(pattern='npl_unzip_')
    dir.create(tmp)
    unzip(npl_path, exdir = tmp)
    gdb <- list.files(tmp, pattern='\\.gdb$', recursive=TRUE, full.names=TRUE)[1]
    if (is.na(gdb)) stop('No .gdb found inside NPL zip')
    return(st_read(dsn = gdb, layer = NPL_LAYER_NAME, quiet=TRUE))
  }
  # attempt to read layer from directory / gdb
  st_read(dsn = npl_path, layer = NPL_LAYER_NAME, quiet=TRUE)
}

calculate_proximity_score <- function(distance_m) {
  if (is.na(distance_m)) return(NA_real_)
  dk <- distance_m / 1000.0
  if (dk < 0.1) return(11.0)
  1.0 / dk
}

main <- function() {
  # Use reticulate to import Python config loaders from scripts/shared and scripts/superfund
  shared_dir <- normalizePath(file.path(script_dir, '..', 'shared'))
  superfund_dir <- normalizePath(script_dir)

  py_state_mod <- import_from_path('state_config', path = shared_dir)
  py_shared_mod <- import_from_path('shared_config', path = shared_dir)
  py_super_mod <- import_from_path('superfund_config', path = superfund_dir)

  py_get_state_config <- py_state_mod$get_state_config
  py_get_shared_config <- py_shared_mod$get_shared_config
  py_get_superfund_config <- py_super_mod$get_superfund_config

  # get configs
  py_state <- py_get_state_config(state_code)
  metric_crs <- py_state$metric_crs %||% 'EPSG:5070'

  shared_cfg <- py_get_shared_config()
  super_cfg <- py_get_superfund_config()

  # resolve paths similar to Python implementation
  npl_path <- if (!is.null(opts$`npl-boundaries-path`)) opts$`npl-boundaries-path` else file.path(resolvePath(superfund_dir), super_cfg$preprocessed_npl_boundaries_relative_path)

  # block groups and census blocks path templates from shared config
  tiger_template <- shared_cfg$tiger_bg_relative_path_template
  census_template <- shared_cfg$census_block_weights_relative_path_template

  block_groups_path <- if (!is.null(opts$`block-groups-path`)) opts$`block-groups-path` else file.path(resolvePath(shared_dir), gsub('\{fips\}', py_state$fips, tiger_template))
  census_blocks_path <- if (!is.null(opts$`census-blocks-path`)) opts$`census-blocks-path` else file.path(resolvePath(shared_dir), gsub('\{postal\}', state_code, census_template))

  output_root <- if (!is.null(opts$`output-dir`)) opts$`output-dir` else if (storage_mode == 'local') file.path(resolvePath(superfund_dir), 'output') else super_cfg$remote_root_path
  output_dir <- file.path(output_root, gsub('\{postal\}', state_code, super_cfg$indicator_output_relative_path_template))
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
  if (!all(c('NPL_STATUS_CODE','EPA_ID') %in% names(npl_gdf))) stop('NPL layer missing required columns')
  npl_gdf <- st_transform(npl_gdf, crs = metric_crs)

  bg_gdf <- read_block_groups(bg_local)
  if (!('GEOID' %in% names(bg_gdf))) stop('Block-group data missing GEOID column')
  bg_gdf <- st_transform(bg_gdf, crs = metric_crs)

  blocks_df <- read_csv(blocks_local, col_types = cols(.default = col_character()))
  required_cols <- c('GEOID20','INTPTLAT20','INTPTLON20','POP20','block_group_geoid','block_group_pop','fraction_of_total')
  missing <- setdiff(required_cols, names(blocks_df))
  if (length(missing) > 0) stop('Blocks CSV missing columns: ', paste(missing, collapse=', '))
  blocks_df <- blocks_df %>% mutate(block_geoid = trimws(GEOID20), block_lat = as.numeric(INTPTLAT20), block_lon = as.numeric(INTPTLON20), block_pop = as.numeric(POP20), fraction_of_total = as.numeric(fraction_of_total))
  blocks_gdf <- st_as_sf(blocks_df, coords = c('block_lon','block_lat'), crs = 4326, remove = FALSE) %>% st_transform(metric_crs)

  # Step1: buffer and targeted block groups
  npl_active <- filter(npl_gdf, NPL_STATUS_CODE %in% ACTIVE_NPL_STATUS_CODES)
  npl_active$geometry_buffer <- st_buffer(st_geometry(npl_active), dist = buffer_meters)
  npl_buffer <- st_set_geometry(npl_active, 'geometry_buffer')

  joined <- st_join(bg_gdf, npl_buffer, join = st_intersects, left = FALSE)
  if (nrow(joined) == 0) {
    targeted <- tibble::tibble(GEOID_BG = character(), EPA_ID = character())
  } else {
    targeted <- joined %>% st_drop_geometry() %>% select(GEOID_BG = GEOID, EPA_ID = EPA_ID) %>% distinct() %>% mutate(EPA_ID = trimws(as.character(EPA_ID)))
  }
  write_csv(targeted, targeted_out)

  # Step2: block-site distances
  if (nrow(targeted) == 0) {
    distances_df <- tibble::tibble(GEOID_BLOCK = character(), EPA_ID = character(), distance_m = numeric())
  } else {
    target_bgs <- unique(as.character(targeted$GEOID_BG))
    blocks_sub <- blocks_gdf %>% filter(block_group_geoid %in% target_bgs)
    if (nrow(blocks_sub) == 0) {
      distances_df <- tibble::tibble(GEOID_BLOCK = character(), EPA_ID = character(), distance_m = numeric())
    } else {
      npl_lookup <- npl_gdf %>% select(EPA_ID, geometry) %>% mutate(EPA_ID = trimws(as.character(EPA_ID)))
      distances_list <- list()
      for (i in seq_len(nrow(targeted))) {
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
    distances_df <- distances_df %>% mutate(proximity_score = vapply(distance_m, calculate_proximity_score, numeric(1)))
  }
  write_csv(distances_df, distances_out)

  # Step4: population weighting aggregation
  if (nrow(distances_df) > 0) {
    blocks_tab <- blocks_gdf %>% st_drop_geometry() %>% select(block_geoid, block_group_geoid, fraction_of_total)
    merged <- distances_df %>% left_join(blocks_tab, by = c('GEOID_BLOCK' = 'block_geoid')) %>% filter(!is.na(proximity_score)) %>% mutate(weighted_score = as.numeric(proximity_score) * as.numeric(fraction_of_total))
    agg_targeted <- merged %>% group_by(block_group_geoid) %>% summarize(weighted_score = round(sum(weighted_score, na.rm=TRUE), 4), .groups='drop')
    all_bgs <- blocks_tab %>% select(block_group_geoid) %>% distinct()
    agg <- all_bgs %>% left_join(agg_targeted, by='block_group_geoid') %>% mutate(weighted_score = ifelse(is.na(weighted_score), 0, weighted_score))
  } else {
    agg <- tibble::tibble(block_group_geoid = character(), weighted_score = numeric())
  }
  final_df <- agg %>% rename(superfund_score = weighted_score)
  write_csv(final_df, final_out)

  message('Superfund indicator R pipeline completed successfully')
}

resolvePath <- function(p) {
  if (startsWith(p, './') || startsWith(p, '../')) normalizePath(file.path(script_dir, p), mustWork = FALSE) else p
}

tryCatch({
  main()
}, error = function(e) {
  message('Error: ', e$message)
  quit(status = 1)
})
