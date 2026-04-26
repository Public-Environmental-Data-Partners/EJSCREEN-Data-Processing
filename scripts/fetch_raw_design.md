# Central Fetch Design

## The Do's

- Add one central fetch runner at `scripts/fetch_raw.py`.
- Use indicator-owned config files as the single source of truth for root paths and download definitions.
- Support both local filesystem targets and remote S3 targets.
- Download authoritative source files as-is without expanding `.zip` or `.gz` archives.
- Do download directly to the ultimate destination location, using a `.tmp` sibling file and rename for local downloads, and direct streaming for remote downloads.
- Skip content validation of downloaded archives.
- Require explicit state selection for any state-scoped downloads but support 'ALL' where appropriate.

## The Don'ts

- Do not expand archives during fetch.
- Do not inspect archive members or validate file contents after download.
- Do not infer paths from the current working directory.
- Do not make downstream processing scripts discover canonical input paths by walking the `downloads` block.

## Script location

- New central runner: `scripts/fetch_raw.py`
- Existing indicator config modules remain the place that define and resolve root paths.
- The central runner loads the selected indicator's config module rather than duplicating per-indicator path logic.

## CLI contract

### Required arguments

```text
python scripts/fetch_raw.py <indicator> <storage_mode> --download <download_key> [--state <postal|all>]
```

- `-i <indicator>`: indicator identifier such as `pm25` or `shared`
- `-m <storage_mode>`: `local` or `remote`
- `--state | -s <postal|all>`: required only when the selected download has `scope = state`
- `--download | -d <download_key>`: required selector for one configured download entry (default is `all`)

### CLI rules

- `--download` is required in version 1.
- `--state` must be omitted for `scope = single` downloads.
- `--state` must be provided for `scope = state` downloads.
- `--state all` means iterate across every configured entry in `scripts/shared/state_config.json`.
- `--state <postal>` must match a configured two-letter postal or territory code.
- The runner should fail fast on any invalid argument combination.

### Rationale for required `--download`

- `shared` is expected to accumulate several downloads.
- Some future shared downloads will likely be national single-file downloads while others will be state-scoped.
- Requiring `--download` keeps argument validation precise and avoids ambiguous whole-indicator runs.
- This keeps the CLI rule simple: validate arguments against one selected download entry.

## Config contract

### Required top-level pattern

- Each indicator that supports fetch keeps a `downloads` object in its existing config JSON.
- Existing canonical path fields outside `downloads` remain in place for downstream processing code.
- If a canonical path field duplicates a fetch relative path, the config loader should validate that the two values match.

### Common `downloads` schema

```json
"downloads": {
  "request_timeout_seconds": 120,
  "chunk_size_bytes": 1048576,
  "entries": {
    "<download_key>": {
      "scope": "single | state",
      "source_url": "...",
      "source_url_template": "...",
      "relative_path": "...",
      "relative_path_template": "..."
    }
  }
}
```

### Required fields by scope

#### `scope = single`

- Required:
  - `scope`
  - `source_url`
  - `relative_path`
- Forbidden:
  - `source_url_template`
  - `relative_path_template`

#### `scope = state`

- Required:
  - `scope`
  - `source_url_template`
  - `relative_path_template`
- Forbidden:
  - `source_url`
  - `relative_path`

### Template variables

- `scope = state` entries may use:
  - `{postal}`
  - `{fips}`
- No other template tokens are allowed in version 1.

### Shared download defaults

- `request_timeout_seconds` applies to all entries within that indicator config.
- `chunk_size_bytes` applies to all entries within that indicator config.
- Per-entry overrides are out of scope for version 1.

## Validation rules

### Config validation

- `downloads` must be a JSON object.
- `downloads.entries` must be a non-empty JSON object.
- Each download key must be unique and stable enough to use in `--download`.
- `request_timeout_seconds` must be a positive integer.
- `chunk_size_bytes` must be a positive integer.
- `relative_path` and `relative_path_template` must be relative paths.
- Relative path fields must not start with `/`, `\\`, drive letters, or `s3://`.
- `source_url` and `source_url_template` must be `http://` or `https://` URLs.
- `scope` must be exactly `single` or `state`.
- `single` entries must not contain template forms.
- `state` entries must not contain single-entry fields.
- `state` templates may only reference `{postal}` and `{fips}`.

### Runtime validation

- Fail if the indicator name is unknown.
- Fail if the selected indicator does not expose fetch-capable config.
- Fail if `--download` is missing.
- Fail if `--download` does not exist in that indicator's config.
- Fail if `--state` is supplied for a `single` download.
- Fail if `--state` is missing for a `state` download.
- Fail if `--state` is neither `all` nor a configured two-letter code.
- For `remote`, fail fast if required runtime dependencies are unavailable.

## Root path resolution

- The central runner must not use the current working directory to resolve indicator roots.
- For `local`, the runner calls the selected indicator's existing resolver helper.
- For `remote`, the runner uses the selected indicator's configured `remote_root_path`.
- The runner joins the resolved root with the configured relative path for the selected download.

## Indicator integration contract

### Required indicator-side capabilities

- A config JSON file that contains root path settings and a `downloads` object.
- A config loader module that validates the config and exposes typed access to the `downloads` object.
- A local-root resolver helper that returns an absolute local path anchored to the indicator's script location.

### Central runner responsibilities

- Parse CLI arguments.
- Load the selected indicator's config module.
- Resolve the active root path from `indicator` and `storage_mode`.
- Resolve the selected download entry from `--download`.
- Expand state-scoped templates when `--state all` or `--state <postal>` is provided.
- For `local`, stream each file to a temp file created in the destination directory.
- For `local`, rename the completed temp file into place after the download finishes.
- For `remote`, stream each file directly to the final remote object path.
- Skip downloads whose destination already exists.
- Append one audit row per attempted file.
- Write file-based logs beside the central runner.

## Download behavior

- If the destination file already exists, skip the download and write an audit row with status `skipped`. Skip as early in the process as possible to minimize processing time for files we already have.
- For `local`, fetch streams to a temp file in the ultimate destination folder rather than to a separate temp area.
- For `local`, the temp filename should be a clearly temporary sibling of the final output, with a .tmp suffix-marked name.
- For `local`, rename the temp file to the final destination name only after the download stream completes successfully.
- For `local`, delete the temp file if the download fails or is interrupted.
- For `remote`, fetch streams directly to the final remote destination path without a local temp-file stage.
- The local and remote behaviors are intentionally different: local rename within one directory gives a cheap and strong way to avoid exposing a partial final filename, while remote object stores typically do not support true atomic rename and usually implement rename as copy-plus-delete.
- No unzip or gunzip step occurs in fetch.
- No post-download inspection occurs in fetch.

## Audit contract

- The central runner writes one centralized `fetch_audit.csv` beside `scripts/fetch_raw.py` in the `scripts` folder.
- All indicators append rows into that same audit file.
- Audit columns should stay aligned with the current broad pattern used by the existing fetch scripts:
  - `process_name`
  - `indicator`
  - `run_id`
  - `dl_started_at`
  - `dl_ended_at`
  - `filename`
  - `state`
  - `destination_path`
  - `storage_mode`
  - `status`
  - `source_url`
  - `bytes_downloaded`
  - `message`
- For `scope = single`, `state` should be `ALL`.
- For `scope = state`, `state` should be the selected postal code for each emitted row.
- `indicator` should store the selected indicator key such as `pm25` or `shared`.
- `run_id` should remain a run identifier only and should not be relied on to encode the indicator.
- The centralized audit file should include enough context to distinguish indicators cleanly, so `process_name` should identify the central fetch runner, `indicator` should identify the selected indicator, and `destination_path` should continue to capture the resolved local or remote target path.

## Logging contract

- Write a central log file beside the new script, for example `scripts/fetch_raw.log`.
- Log the selected indicator, storage mode, download key, root path, destination path, and source URL.
- Emit periodic progress logging during long downloads.
- Keep the console output minimal and proof-of-life oriented.

## Proposed config examples

### PM2.5 example

```json
{
  "local_root_path": "./pipeline/",
  "remote_root_path": "s3://pedp-data-preserved/ejscreen-data-processing/pm25/pipeline/",
  "preprocessed_tract_output_relative_path": "preprocessed_input/pm25_tract_annual_average.csv",
  "indicator_output_relative_path_template": "output/indicators/{postal}",
  "downloads": {
    "request_timeout_seconds": 120,
    "chunk_size_bytes": 1048576,
    "entries": {
      "raw_pm25_2020": {
        "scope": "single",
        "source_url": "https://ofmpub.epa.gov/rsig/rsigserver?data/FAQSD/outputs/2020_pm25_daily_average.txt.gz",
        "relative_path": "downloads/2020/2020_pm25_daily_average.txt.gz"
      }
    }
  }
}
```

### Shared example

```json
{
  "local_root_path": "shared/pipeline/",
  "remote_root_path": "s3://pedp-data-preserved/ejscreen-data-processing/shared/pipeline/",
  "downloads_subtree_name": "downloads",
  "preprocessed_input_subtree_name": "preprocessed_input",
  "tiger_bg_relative_path_template": "downloads/tiger_lines/2020/bg/tl_2020_{fips}_bg.zip",
  "census_block_weights_relative_path_template": "preprocessed_input/census_block_weights_2020/census_block_weights_2020_{postal}.csv",
  "downloads": {
    "request_timeout_seconds": 120,
    "chunk_size_bytes": 1048576,
    "entries": {
      "tiger_bg_2020": {
        "scope": "state",
        "source_url_template": "https://www2.census.gov/geo/tiger/TIGER2020/BG/tl_2020_{fips}_bg.zip",
        "relative_path_template": "downloads/tiger_lines/2020/bg/tl_2020_{fips}_bg.zip"
      }
    }
  }
}
```


## Implementation plan (phased)

We will migrate incrementally to reduce risk. Each phase makes a narrowly scoped change and includes quick validation checks before moving to the next phase. The plan below reflects the updated approach: copy the working PM2.5 script into a central runner first, add `--download` immediately, then integrate other indicators one at a time.

Phase 1 — PM2.5 (transfer mechanics)

- Status: completed (implement and validate local `.tmp` write + remote direct streaming in `scripts/pm25/pm25_fetch_raw.py`).
- Goal: validate the new write behavior in the simplest, easiest-to-test indicator.
- Target file: `scripts/pm25/pm25_fetch_raw.py`.
- Key changes validated in this phase:
  - For `local`, write the download into a `.tmp` sibling in the destination directory and rename into place on success.
  - For `remote`, stream directly to the configured remote destination (no intermediate local copy for remote mode).
  - Keep existing PM2.5 config shape and loader; do not introduce the shared `downloads` schema yet.
  - No archive expansion or ZIP validation is performed.

Phase 2 — Centralize runner and audit (copy PM2.5 -> central)

- Goal: create the central runner by copying the tested PM2.5 fetch into `scripts/fetch_raw.py`, add the `--indicator` and `--download` CLI arguments immediately, and switch audit output to the script folder.
- Target file: `scripts/fetch_raw.py` (initially a copy of `scripts/pm25/pm25_fetch_raw.py`).
- Changes:
  - Create `scripts/fetch_raw.py` as a copy of the PM2.5 script and adapt it to accept the required `--indicator <indicator_key>` and optional `--download <download_key>`.
  - Make `--indicator` required in this phase and `--download` optional. `--state` is unsupported for now.
  - Configure the central runner to read PM2.5's config when `indicator=pm25` so downloads work immediately using existing PM2.5 config values.
  - Change audit output so `fetch_audit.csv` is appended in the `scripts` folder (alongside the runner and its logs).
  - Preserve existing per-indicator config files; the central runner should load each indicator's config module to resolve roots and download entries.
- Tests / checks:
  - `python scripts/fetch_raw.py pm25 local --download raw_pm25_2020` downloads the PM2.5 file and appends one row to `scripts/fetch_audit.csv`.
  - Logs are written beside `scripts/fetch_raw.py` and `scripts/fetch_audit.csv` is populated.

Phase 3 — O3 integration -- just works

- Goal: add `o3` as the next indicator and validate its downloads via the central runner.
- Changes:
  - Ensure `scripts/o3/o3_fetch_raw.py` behavior is reproduced (or retire it) and that the central runner can load the O3 config and perform its configured `--download` entries.
  - No `--state` handling expected for O3; focus on making `--download` work for O3 entries.
- Tests / checks:
  - `python scripts/fetch_raw.py o3 local --download <o3_download_key>` succeeds and writes an audit row to `scripts/fetch_audit.csv`.

Phase 4 — Shared integration and state handling

- Goal: integrate the `shared` indicator into the central runner and add `--state` handling for state-scoped downloads.
- Changes:
  - Extend the central runner to validate `--state` for `scope=state` downloads and to expand templates when `--state` is `all` or a postal code.
  - Migrate or add a `downloads` block in the shared config to expose the shared download entries to the central runner.
  - Remove any ZIP-content validation from shared fetch logic and treat shared fetch as transfer-only; downstream preprocessors (in `scripts/shared/`) should perform any required archive inspection or extraction.
- Tests / checks:
  - `python scripts/fetch_raw.py shared local --download tiger_bg_2020 --state RI` resolves to the expected destination path and writes an audit row.
  - `--state all` iterates across all configured states.

Notes on sequencing

- Rationale: copying the working PM2.5 script into `scripts/fetch_raw.py` and enabling `--download` immediately reduces integration risk and lets us migrate indicators one at a time.
- `--download` will be required starting in Phase 2 so each run is explicit; `--state` support is postponed until Phase 4 when `shared` is integrated.
- This sequencing lets us preserve working I/O semantics while incrementally centralizing config and audit handling.


## Open decisions already resolved for implementation

- Put the new fetch runner under `scripts`.
- Keep downloaded files compressed.
- Do not perform archive member validation.
- Stream remote downloads directly to the final remote object path.
- For local downloads, create the temp file in the final destination folder and rename it into place on success.
- Require explicit `--state <postal|all>` for state-scoped downloads.
- Require `--download <download_key>` in version 1.
- Keep canonical downstream path fields outside `downloads` and validate any duplicated values for agreement.