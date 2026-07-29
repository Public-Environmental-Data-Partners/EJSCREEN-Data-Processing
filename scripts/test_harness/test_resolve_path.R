#!/usr/bin/env Rscript
# test_resolve_path.R
#
# Dry-run harness for R users to exercise the Python `resolve_path`
# via `reticulate` and inspect the returned named-list
#
# Usage examples: (run from the scripts/ folder):
#   Rscript test_harness/test_resolve_path.R --storage-mode local --type indicator --name o3 --version 1.2020 --key raw_o3
#   Rscript test_harness/test_resolve_path.R --storage-mode remote --type indicator --name o3 --version 1.2021 --key raw_o3
#   Rscript test_harness/test_resolve_path.R --storage-mode local --type shared --name tiger_bg --version 2020 --category downloads --key tiger_bg_2020
#   Rscript test_harness/test_resolve_path.R --storage-mode remote --type shared --name census_block_weights --version 1.0 --category preprocessed_input
# Notes:
# - See the config files for what names, stages, and versions are valid for any particular indicator or shared asset. 
#   Error messages here are not particularly diagnostic, so if you get a "not found" error, check the config files to 
#   make sure you are using a valid combination.
# - TODO: We've accumulated  naming inconsistencies as the tested code and the configurations have evolved.
#   For example, "--storage-mode" here corresponds to the 
#   '-l/--location' argument in the cli for most of the other current modules. I'm not fixing any of that 
#   today but it should be cleaned up soon to avoid confusion as more modules start to use this code's services.

# checking package requirements
if (!requireNamespace("reticulate", quietly = TRUE)) {
  stop("Please install the 'reticulate' R package to run this harness.")
}
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Please install the 'jsonlite' R package to format output (install.packages('jsonlite')).")
}
if (!requireNamespace("optparse", quietly = TRUE)) {
  stop("Please install the 'optparse' R package to format output (install.packages('optparse')).")
}

# load in packages for test harness: 
library(reticulate)
library(jsonlite)
library(optparse)

# pulling in args from the command line: 
args <- commandArgs(trailingOnly = TRUE)

option_list <- list(
  make_option(c('--storage-mode'), type='character', 
              default='local', required = T, dest = "storage_mode"),
  make_option(c('--type'), type='character', default=NULL, required = T),
  make_option(c('--name'), type='character', default=NULL, required = T),
  make_option(c('--version'), type='character', default=NULL, required = T),
  make_option(c('--key'), type='character', default=NULL),
  make_option(c('--category'), type='character', default=NULL)
)

parser <- OptionParser(usage='%prog [options]', option_list=option_list)
args_all <- parse_args(parser, positional_arguments = FALSE)
opts <- args_all

# checks for args: 
if (opts$type == "indicator"){
  if (is.null(opts$key)) {
    message("--key is required when --type is indicator")
    quit(status = 1)
  }
  if (!is.null(opts$category)){
    message("--category is not valid when --type is indicator")
    quit(status = 1)
  }
} else {
  if(is.null(opts$category)){
    message("--category is required when --type shared")
    quit(status = 1)
  }
  if(opts$category == "downloads" && is.null(opts$key)){
    message("--key is required when resolving shared downloads")
    quit(status = 1)
  }
  if(opts$category == "preprocessed_input" & !is.null(opts$key)){
    message("--key is not valid for shared preprocessed_input resolution")
    quit(status = 1)
  }
}

# read in resolve path:
shared_path <- "./shared"
py_resolve = NULL
try({
  py_resolve <- import_from_path("resolve_path", path = shared_path)
}, silent = TRUE)

# if we can pull in resolve path, run checks: 
if (is.null(py_resolve)) {
  cat("ERROR: failed to import 'resolve_path' from", shared_path, "\n")
  quit(status = 1)
} 

resolve_from_args <- function(opts) {
  parts <- "NULL - did not run"
  # if we're working with an indicator, get the download path
  if (opts$type == "indicator") {
    parts <- tryCatch({
      py_resolve$get_download_path(opts$name, 
                                   opts$version, 
                                   opts$key, 
                                   opts$storage_mode)
    }, error = function(e) {
      cat("get_download_path failed:", e$message, "\n")
    })
    cat("\n--- Resolver checks ---\n")
    # if we're working with a shared asset, get the shared asset path: 
  } else {
    parts <- py_resolve$get_shared_asset_path(asset = opts$name, 
                                              version = opts$version, 
                                              category = opts$category,
                                              asset_key = opts$key, 
                                              environment = opts$storage_mode)
  }
  
  # return! 
  return(parts)
}

result <- resolve_from_args(opts)
cat(toJSON(result, pretty = TRUE, auto_unbox = TRUE), "\n")
