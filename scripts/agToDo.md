# Checklist: Configuration & Path Resolution Refactor

## Phase 1: Environment & Schema Setup
- [x] Create a new Git branch for the refactor: `dataVersioning`
- [x] Update `scripts/o3/o3_config.json` to match the new schema structure (including `local_root_path`, `remote_root_path`, and stage definitions for inputs/outputs)
- [x] Update `scripts/shared/shared_config.json` to match the new global asset schema

## Phase 2: Core Path Resolution (The Machinist)
- [x] Prompt AI to generate `scripts/shared/resolve_path.py` based on Section 1 specifications
- [x] Prompt AI to generate the CLI test harness `test_resolve_path.py` based on Section 2 specifications
- [x] Test `resolve_path.py` using the harness against `o3_config.json`:
    - [x] Verify local resolution returns accurate `{"root": ..., "relative": ...}` dictionary
    - [x] Verify remote resolution handles S3 paths correctly without Windows backslash errors
- [x] Test `resolve_path.py` using the harness against `shared_config.json` (e.g., checking dependency version resolution logic)

## Phase 3: Workflow Mapping (The Architect)
- [x] Prompt AI to generate `scripts/shared/build_manifest.py` based on the manifest specification
- [x] Prompt AI to generate the CLI test harness `test_manifest.py` 
- [x] Test `build_manifest.py` using the harness for the `o3` `preprocess` stage:
    - [x] Verify that it auto-resolves global shared dependencies (e.g., pulling correct version of `census_block_weights`)
    - [x] Verify that auxiliary output folders are cleanly mapped in the final dictionary

## Phase 4: Tracer Bullet Integration (O3 Testing)
- [x] Refactor the `fetch_raw` script to consume paths via `resolve_path.py`
- [x] Refactor `scripts/o3/o3_preprocess.py` (Python) to pull its execution map via `build_manifest.py`
- [x] Refactor `scripts/o3/o3_indicator.py` (Python or R) to pull its execution map via `build_manifest.py`
- [x] Run a full end-to-end integration test of the `o3` indicator pipeline:
    - [x] Verify full run locally
    - [x] Verify full run remotely (dry-run or execution on S3 via `fsspec`/R cloud tools)

## Phase 5: Prepare for and merge with `indicators` branch
- [ ] Migrate the remaining indicator configuration files (`pm25_config.json`, etc.) to the new schema
- [ ] Run the `test_manifest.py` harness against all newly migrated indicators to catch syntax/schema errors early
- [ ] Update execution scripts for all remaining indicators to consume the new `build_manifest` workflow
- [ ] change `xx_indictator.py` name to `xx_score.py` as each indicator's code is upgraded to data versioning
- [ ] Run final end-to-end testing across the entire pipeline universe
- [ ] Merge branch into `indicators`
