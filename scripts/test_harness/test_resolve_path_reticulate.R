#!/usr/bin/env Rscript
# test_resolve_path_reticulate.R
#
# Dry-run harness for R users to exercise the Python `resolve_path` and
# `build_manifest` modules via `reticulate` and inspect the returned named-list
# shapes. This is intended as a lightweight check you can run locally before
# wiring calls into production R scripts.
#
# Usage examples:
#   Rscript scripts/test_harness/test_resolve_path_reticulate.R        # runs both checks
#   Rscript scripts/test_harness/test_resolve_path_reticulate.R --resolver   # resolver checks only
#   Rscript scripts/test_harness/test_resolve_path_reticulate.R --manifest   # manifest check only
#
args <- commandArgs(trailingOnly = TRUE)
run_resolver <- TRUE
run_manifest <- TRUE
if (length(args) > 0) {
  run_resolver <- any(grepl("--resolver", args)) || (!any(grepl("--resolver|--manifest", args)) )
  run_manifest <- any(grepl("--manifest", args)) || (!any(grepl("--resolver|--manifest", args)) )
}

if (!requireNamespace("reticulate", quietly = TRUE)) {
  stop("Please install the 'reticulate' R package to run this harness.")
}
if (!requireNamespace("jsonlite", quietly = TRUE)) {
  stop("Please install the 'jsonlite' R package to format output (install.packages('jsonlite')).")
}

library(reticulate)
library(jsonlite)

cat("Starting R reticulate resolver/manifest harness\n")

shared_path <- "./scripts/shared"
py_resolve <- NULL
py_build_manifest <- NULL
try({
  py_resolve <- import_from_path("resolve_path", path = shared_path)
}, silent = TRUE)
try({
  py_build_manifest <- import_from_path("build_manifest", path = shared_path)
}, silent = TRUE)

if (is.null(py_resolve)) {
  cat("ERROR: failed to import 'resolve_path' from", shared_path, "\n")
} else if (run_resolver) {
  cat("\n--- Resolver checks ---\n")
  tryCatch({
    parts <- py_resolve$get_download_path("o3", "1.0", "raw_o3", "local")
    cat("get_download_path('o3', '1.0', 'raw_o3', 'local') returned:\n")
    cat(toJSON(parts, pretty = TRUE, auto_unbox = TRUE), "\n")
  }, error = function(e) {
    cat("get_download_path failed:", e$message, "\n")
  })

  tryCatch({
    dep_ver <- py_resolve$get_dependency_version("o3", "1.0", "census_block_weights")
    cat("get_dependency_version('o3','1.0','census_block_weights') -> ", dep_ver, "\n")
    shared_parts <- py_resolve$get_shared_asset_path("census_block_weights", dep_ver, "preprocessed_input", environment = "local")
    cat("get_shared_asset_path('census_block_weights', <dep_ver>, 'preprocessed_input') returned:\n")
    cat(toJSON(shared_parts, pretty = TRUE, auto_unbox = TRUE), "\n")
  }, error = function(e) {
    cat("shared asset resolution failed:", e$message, "\n")
  })
}

if (is.null(py_build_manifest)) {
  cat("\nNOTE: failed to import 'build_manifest' from", shared_path, "\n")
} else if (run_manifest) {
  cat("\n--- Manifest check ---\n")
  tryCatch({
    manifest <- py_build_manifest$get_stage_manifest(
      target_type = "indicator",
      name = "o3",
      stage = "preprocess",
      version = "1.0",
      environment = "local"
    )
    cat("get_stage_manifest('indicator','o3','preprocess','1.0','local') returned keys:\n")
    cat(paste(names(manifest), collapse = ", "), "\n")
    cat(toJSON(manifest, pretty = TRUE, auto_unbox = TRUE), "\n")
  }, error = function(e) {
    cat("get_stage_manifest failed:", e$message, "\n")
  })
}

cat("\nHarness complete.\n")
