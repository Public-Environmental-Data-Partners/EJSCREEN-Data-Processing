# R Programmer Guide: Using the Python Resolver and Manifest Builder

This guide shows how R users can call the shared Python utilities (`resolve_path` and
`build_manifest`) from R via the `reticulate` package. It includes examples, common
patterns, and links to the bundled test harnesses you can run to verify return shapes
before wiring them into your pipelines.

Location of test harnesses
- `scripts/test_harness/test_resolve_path.py` — dry-run resolver outputs (indicator/shared)
- `scripts/test_harness/test_build_manifest.py` — dry-run manifest builder outputs

Note: Python dictionaries returned by these modules map to named lists in R when
imported via `reticulate`. Access elements with the `$` operator.

---

## 1. Using `resolve_path` from R

Purpose: obtain `root` and `relative` path components for indicator downloads or shared
assets so your R ingestion code can be environment-agnostic.

Example (reticulate):

```R
library(reticulate)
resolve_path <- import_from_path("resolve_path", path = "./scripts/shared")

# Simple download lookup (indicator)
parts <- resolve_path$get_download_path("o3", "1.0", "raw_o3", "local")
print(parts$root)      # e.g. "./pipeline/o3/"
print(parts$relative)  # e.g. "v1.0/downloads/..."

# Shared asset lookup (preprocessed template). No interpolation is performed by resolver.
shared_ver <- resolve_path$get_dependency_version("o3", "1.0", "census_block_weights")
shared_parts <- resolve_path$get_shared_asset_path("census_block_weights", shared_ver, "preprocessed_input", environment = "local")
print(shared_parts$relative) # contains token like "...{postal}..."

```

Token replacement and building a usable path:

```R
state_postal <- "WY"
relative_filled <- gsub("\\{postal\\}", state_postal, shared_parts$relative)
full_path <- paste0(shared_parts$root, relative_filled)

# Read locally
df <- readr::read_csv(full_path)

# Or, for remote S3 URIs, stream using an S3-aware reader (do not use `file.path`)
```

Quick CLI checks (shell) to confirm what `reticulate` will receive:

```bash
python scripts/test_harness/test_resolve_path.py --storage-mode local --type indicator --name o3 --version 1.0 --key raw_o3
python scripts/test_harness/test_resolve_path.py --storage-mode local --type shared --name census_block_weights --version 1.0 --category preprocessed_input
```

These produce a small JSON object with `root` and `relative` fields; the harness adds a
`status` field for clarity.

---

## 2. Using `build_manifest` from R

Purpose: retrieve a compiled stage manifest (inputs/outputs) that already includes
`root` and `relative` entries for each declared input and output. This is useful when
you want a single manifest-driven object to drive multiple parts of a workflow.

Example (reticulate):

```R
library(reticulate)
build_manifest <- import_from_path("build_manifest", path = "./scripts/shared")

manifest <- build_manifest$get_stage_manifest(
  target_type = "indicator",
  name = "o3",
  stage = "preprocess",
  version = "1.0",
  environment = "local"
)

# Inspect the returned named list
print(names(manifest))          # typically: "inputs" "outputs"
print(manifest$inputs$primary_o3$root)
print(manifest$outputs$main_tract_averages$relative)

```

Quick CLI check (shell) to confirm manifest shape and contents:

```bash
python scripts/test_harness/test_build_manifest.py --storage-mode local --target-type indicator --name o3 --stage preprocess --version 1.0
```

The harness prints a JSON object prefixed with `status` that mirrors the manifest
structure you will get via `reticulate`.

Notes and recommendations
- The manifest entries for inputs/outputs already include `root` + `relative` so you
  can construct `full_path <- paste0(entry$root, entry$relative)` in R.
- If an entry's `relative` contains tokens (e.g. `{postal}`), perform R-side
  substitution with `gsub()` before concatenation.
- Avoid using `file.path()` for S3 URIs — prefer string concatenation to preserve
  forward slashes.

---

## 3. Where to run tests and what to expect

- Resolver test harness: `scripts/test_harness/test_resolve_path.py`
  - Use to validate `get_download_path` and `get_shared_asset_path` return shapes
  - Example: `python scripts/test_harness/test_resolve_path.py --storage-mode local --type indicator --name o3 --version 1.0 --key raw_o3`

- Manifest test harness: `scripts/test_harness/test_build_manifest.py`
  - Use to validate `get_stage_manifest` returns `inputs`/`outputs` with compiled entries
  - Example: `python scripts/test_harness/test_build_manifest.py --storage-mode local --target-type indicator --name o3 --stage preprocess --version 1.0`

I added a short R script in `scripts/test_harness/` that calls these functions via
`reticulate` and prints the R named-list shapes. Run it with `Rscript` to see what
your `reticulate` calls should return:

```bash
Rscript scripts/test_harness/test_resolve_path_reticulate.R
```

The script runs a small set of resolver and manifest checks and prints JSON-formatted
outputs to stdout. It is intended as a low-risk local verification step before you
embed `reticulate` calls in production R code.

---

Last updated: 2026-06-13
