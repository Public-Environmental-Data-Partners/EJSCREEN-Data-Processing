# Shared Scripts

Shared configuration, path-resolution, and fetch utilities to be used by all indicator
pipelines (o3, pm25, traffic, ...). Note that `build_manifest.py` and `resolve_path.py`
have both been built to serve both Python modules and R modules via reticulate.

## Key files

- `build_manifest.py`: compiles per-stage input/output manifests for `indicator`
  and `shared` targets from their config JSON files. Public entry point:
  `get_stage_manifest(target_type, name, stage, version, environment="local")`.
- `resolve_path.py`: centralized path resolver. Public functions include
  `get_download_path()`, `get_dependency_version()`, `get_shared_asset_path()`,
  `get_indicator_root()`, `get_shared_root()`, `get_shared_version_block()`,
  `get_pipeline_root()`, and `substitute_version_placeholder()`.
- `shared_config.py` / `shared_config.json`: loader and config for shared assets
  (e.g. TIGER block groups, census block weights) consumed by more than one
  indicator pipeline. Public functions: `get_shared_config()`,
  `resolve_local_shared_root_path()`.
- `state_config.py` / `state_config.json`: canonical two-letter-postal-code state
  metadata (FIPS, display name, projected metric CRS) used across pipelines.
  Public functions: `get_state_config(state_code)`, `get_state_config_list(extent)`.
- `fetch_raw.py`: central CLI to download raw indicator or shared source files.
  See the module docstring for the full argument list. Writes results to
  `fetch_audit.csv` and logs to `fetch_raw.log`.
- `compare_ejscreen.py`: standalone CLI to audit one indicator's columns across
  EJSCREEN pipeline stages (original archive vs. replaced vs. final processed
  output). Paths are currently hardcoded in the script's `FILE_MAP`.

## Not covered in this pass

- `ejscreen_merge.py`, `indicator_concat.py` — excluded pending separate review.
