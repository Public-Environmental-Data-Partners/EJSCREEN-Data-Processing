# Architectural Specifications: Path Resolution System

This document contains three distinct sections outlining the design, validation, and integration of the centralized file path resolution engine for the data processing pipeline.

---

## Section 1: Module Specification for `resolve_path.py`
**Target Audience:** Coding AI / Code Generator
**Objective:** Implement a centralized configuration-parsing and path-resolution module. The focus is on the interface boundaries, performance constraints, and functional requirements. Do not prescribe the specific internal dictionary-traversal or string-building algorithms.

### 1. Architectural & Interoperability Requirements
* **Language Environment:** Pure Python 3.x using only standard libraries (`json`, `os`, `pathlib`). No external library dependencies.
* **R Compatibility:** The module must support execution from R via the `reticulate` package. It must expose a flat functional interface at the module root level. Under the hood, these functions should map to a single, persistent internal class instance (Singleton pattern) to cache file-system I/O.
* **Directory Context:** The module will always be executed from the `project_root/` folder context.
* **Module Location:** The resolver module will live at `./scripts/shared/resolve_path.py`.

### 2. Output Data Shape (The Dictionary Return Contract)
All functions that resolve asset storage locations must return a **Python Dictionary** that includes at least the following keys:
```python
{
    "root": "string representing the base configuration path",
    "relative": "string representing the version-specific relative path"
}

```

* **Local Context:** When `environment="local"`, the `"root"` value must be extracted from the configuration's top-level `local_root_path`.
* **Remote Context:** When `environment="remote"`, the `"root"` value must be extracted from the configuration's top-level `remote_root_path`.
* **Environment Validation:** `environment` must accept only `"local"` or `"remote"`. Any other value must raise an explicit error immediately.
* **String Safety:** S3 paths (remote) must not be processed using standard Windows-style path utilities (`os.path.join`) to avoid corrupting forward slashes into backslashes. Use cloud-safe string or posix path formatting for remote resolution.

### 3. Target Configuration Files

The module must parse configurations from two known locations based on the input arguments:

1. **Indicator Configuration:** Located at `./scripts/{indicator}/{indicator}_config.json`
2. **Shared Asset Configuration:** Located at `./scripts/shared/shared_config.json`

### 4. Required Module Interface (Exposed Functions)

#### `get_download_path(indicator: str, version: str, asset_key: str, environment: str = "local") -> dict`

* **What it does:** Looks up an indicator-specific fetch-stage output asset.
* **Behavior:** Traverses the indicator's configuration file for the matching `version`, navigates to `stages -> fetch -> outputs`, and extracts the target `asset_key` information.
* **Return Value:** A dictionary that includes `"root"` and `"relative"` keys mapped to the selected environment settings.

#### `get_dependency_version(indicator: str, version: str, dependency: str) -> str`

* **What it does:** Looks up which version of a shared asset an indicator requires.
* **Behavior:** Traverses the indicator's configuration file for the specified `version` and extracts the precise version string pinned under the `required_shared_assets` mapping for that `dependency`.
* **Return Value:** A plain string representing the version number (e.g., `"1.0"`, `"2022.1"`).

#### `get_shared_asset_path(asset: str, version: str, category: str, asset_key: str = None, environment: str = "local") -> dict`

* **What it does:** Looks up paths inside the global `shared_config.json`.
* **Behavior:** Targets the specific `asset` and its nested `version`.
* If `category` is `"preprocessed_input"`, it resolves the location from `stages -> preprocess -> outputs` using the asset output's `relative_path_template`.
* If `category` is `"downloads"`, it requires the `asset_key` and resolves from `stages -> fetch -> outputs` using the selected output's `relative_path_template`.


* **Return Value:** A dictionary that includes `"root"` and `"relative"` keys.
* *Note: Any wildcard templates containing string tokens like `{postal}` or `{fips}` must be returned intact within the `"relative"` string; the resolver does not perform token interpolation.*

---

## Section 2: Command-Line Test Harness (`test_resolve_path.py`)

**Target Audience:** System Validator / Integration Tester
**Objective:** Provide a command-line script that executes a "dry-run" configuration check against the path resolver. It prints out parsed results so that engineers can verify paths instantly without spinning up a full processing run.

### 1. Interface Requirements

The harness must be a standalone executable Python script located at `./scripts/test_harness/test_resolve_path.py` that implements Python’s standard `argparse` library. It must accept **4 core named arguments**, alongside contextual switches for sub-keys.

### 2. Command-Line Arguments

* `--environment` (Required): String matching either `local` or `remote`.
* `--type` (Required): String matching either `indicator` or `shared`. Determines which underlying resolver mechanics to trigger.
* `--name` (Required): String name of the asset domain (e.g., `o3`, `pm25`, `tiger_bg`, `census_block_weights`).
* `--version` (Required): String version identifier (e.g., `1.0`, `2020.1`).
* `--key` (Optional): String key name representing an item's sub-asset key (e.g., `raw_o3` for indicators, or `raw_weight_crosswalks` for shared downloads).
* `--category` (Optional): String matching either `downloads` or `preprocessed_input` (used strictly when `--type` is `shared`).
* **Validation Rule:** The harness should validate command shape, but invalid `--environment` and invalid shared `--category` values must be passed through so the resolver module itself performs the fast-fail semantic validation.

### 3. Execution Behavior & Sample Output

The script must import `resolve_path.py` from `./scripts/shared`, determine the user's intent from the switches, call the appropriate resolver function, and print the resulting dictionary to standard output as a formatted JSON block.

The optional `"status"` field shown in the examples below belongs to the harness output, not to the resolver contract itself.

#### Example Command 1 (Indicator Verification):

```bash
python ./scripts/test_harness/test_resolve_path.py --environment remote --type indicator --name o3 --version 1.0 --key raw_o3

```

#### Expected Output stdout:

```json
{
  "status": "DRY_RUN_RESOLVED",
  "root": "s3://pedp-data-preserved/ejscreen-data-processing/pipeline/o3/",
  "relative": "v1.0/downloads/2020/2020_ozone_daily_8hour_maximum.txt.gz"
}

```

#### Example Command 2 (Shared Preprocessed Asset Verification):

```bash
python ./scripts/test_harness/test_resolve_path.py --environment local --type shared --name census_block_weights --version 1.0 --category preprocessed_input

```

#### Expected Output stdout:

```json
{
  "status": "DRY_RUN_RESOLVED",
  "root": "./pipeline/shared/",
  "relative": "census_block_weights/1.0/preprocessed_input/census_block_weights_2020_{postal}.csv"
}

```

---

## Section 3: R Developer Integration Guide

**Target Audience:** Team R Programmers
**Objective:** Recommend an ergonomic, low-friction pattern for integrating the Python path resolver into existing R data-frame scripts. This approach honors the system requirement for uniform CSV input/output without hardcoding environments.

### 1. Introduction to the Python Dictionary Bridge

When calling our centralized `resolve_path` module from R using `reticulate`, any Python dictionary returned by the module is automatically transformed into a **named list** in R.

This means you do not have to guess positional array index values. You can cleanly extract your execution components using standard R dollar-sign (`$`) syntax:

```R
library(reticulate)
resolve_path <- import_from_path("resolve_path", path = "./scripts/shared")

# Call the resolver
parts <- resolve_path$get_download_path("o3", "1.0", "raw_o3", "local")

# Access components natively by name
print(parts$root)      # Output: "./pipeline/o3/"
print(parts$relative)  # Output: "v1.0/downloads/..."

```

### 2. Recommended Runtime Strategy for Plain CSV Processing

To keep your data analysis code identical across local development machines and remote server runs, it is highly recommended to isolate the file ingestion step.

By combining the resolver names with a clean conditional reader, you completely eliminate the need to manually move files around or write messy local file-download loops.

Here is a clean pattern you can adapt for your indicator processing scripts:

```R
library(reticulate)
library(readr)
# library(s3fs) # Un-comment if streaming directly from S3 paths

# 1. Establish Environment Context (Define once at the top of your run)
run_env <- "local" # or "remote"
indicator_name <- "o3"
indicator_ver  <- "1.0"

# 2. Import the path utility
resolve_path <- import_from_path("resolve_path", path = "./scripts/shared")

# 3. Resolve the primary indicator input CSV file
input_parts <- resolve_path$get_download_path(indicator_name, indicator_ver, "raw_o3", run_env)
full_input_path <- paste0(input_parts$root, input_parts$relative)

# 4. Resolve the shared tracking asset version & location
shared_ver <- resolve_path$get_dependency_version(indicator_name, indicator_ver, "census_block_weights")
shared_parts <- resolve_path$get_shared_asset_path(
  "census_block_weights",
  shared_ver,
  "preprocessed_input",
  environment = run_env
)

# Handle token replacements for templates if applicable
state_postal <- "WY"
target_relative <- gsub("\\{postal\\}", state_postal, shared_parts$relative)
full_shared_path <- paste0(shared_parts$root, target_relative)

# 5. Clean, Unified Ingestion Invariant
# Your actual processing logic stays pure; only the reader respects the switch
if (run_env == "remote") {
  # Stream CSV data directly out of S3 via cloud tools
  raw_data   <- s3fs::s3_read_csv(full_input_path)
  weight_map <- s3fs::s3_read_csv(full_shared_path)
} else {
  # Standard local file system read
  raw_data   <- read_csv(full_input_path)
  weight_map <- read_csv(full_shared_path)
}

# =====================================================================
# YOUR DATA PROCESSING LOGIC GOES HERE
# =====================================================================
# The remainder of your script is 100% focused on math and mutations.
# e.g., output_df <- raw_data %>% inner_join(weight_map, by="block_id") ...

```

### 3. Benefits of this Pattern for R Workflows

* **Zero Local File Spoilage:** You no longer need to maintain temporary download paths or execute explicit system file deletes after a remote run.
* **Config-Driven Operations:** If a version changes or a data path moves, you don't touch a single line of your R script. The adjustments are managed entirely inside the central JSON config files.
* **Readability:** Anyone reviewing your script can see exactly where data enters the script boundaries and how the paths are mapped.

### 4. Open Path-Join Detail

The examples above assume that `root` can be concatenated directly with `relative`. If the configuration standard changes to omit trailing separators from `root`, the runtime examples should be updated to insert the separator explicitly in a cloud-safe way.

### 5. Stage-Oriented Configuration Model

The resolver now assumes a stage-oriented configuration layout:

* **Indicator fetch assets:** `versions -> {version} -> stages -> fetch -> outputs -> {asset_key}`
* **Shared fetch assets:** `assets -> {asset} -> {version} -> stages -> fetch -> outputs -> {asset_key}`
* **Shared preprocess assets:** `assets -> {asset} -> {version} -> stages -> preprocess -> outputs -> {asset}`

The resolver does not implement fallback support for older top-level `downloads` or `preprocessed_input_relative_path_template` layouts.
