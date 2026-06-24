#!/usr/bin/env Rscript
# test_build_manifest.R
#
# Dry-run harness for R users to exercise the Python `build_manifest`
# via `reticulate` and inspect the returned named-list
#
# Usage examples: (run from the scripts/ folder):
# Rscript test_harness/test_build_manifest.R --storage-mode local --target-type indicator --name o3 --stage preprocess --version 1.0
# Rscript test_harness/test_build_manifest.R --storage-mode remote --target-type indicator --name o3 --stage score --version 1.0
# Rscript test_harness/test_build_manifest.R --storage-mode local --target-type shared --name tiger_bg --stage fetch --version 2020
# Rscript test_harness/test_build_manifest.R --storage-mode local --target-type shared --name census_block_weights --stage preprocess --version 1.0

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
  make_option(c('--target-type'), required = T, 
              dest = "target_type"),
  make_option(c('--name'), required = T),
  make_option(c('--stage'), required = T),
  make_option(c('--version'), required = T))

parser <- OptionParser(usage='%prog [options]', option_list=option_list)
args_all <- parse_args(parser, positional_arguments = FALSE)
opts <- args_all

# read in build manifest
shared_path <- "./shared"
py_build_manifest <- NULL
try({
  py_build_manifest <- import_from_path("build_manifest", path = shared_path)
}, silent = TRUE)

# if we can pull in resolve path, run checks: 
if (is.null(py_build_manifest)) {
  cat("ERROR: failed to import 'build_manifest' from", shared_path, "\n")
  quit(status = 1)
} 

# try it out! 
build_manifrst_from_args <- function(opts) {
  parts <- "NULL - did not run"
    parts <- tryCatch({
      py_build_manifest$get_stage_manifest(
        target_type = opts$target_type,
        name = opts$name,
        stage = opts$stage,
        version = opts$version,
        environment = opts$storage_mode
      )
    }, error = function(e) {
      cat("get_download_path failed:", e$message, "\n")
    })
    cat("\n--- Resolver checks ---\n")
  # return! 
  return(parts)
}
  
result <- build_manifrst_from_args(opts)
cat(toJSON(result, pretty = TRUE, auto_unbox = TRUE), "\n")
