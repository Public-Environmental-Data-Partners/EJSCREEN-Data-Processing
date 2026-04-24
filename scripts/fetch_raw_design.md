# Central Fetch Design

## The Do's

- Add one central fetch runner at `scripts/fetch_raw.py`.
- Use indicator-owned config files as the single source of truth for root paths and download definitions.
- Support both local filesystem targets and remote S3 targets.
- Download authoritative source files as-is without expanding `.zip` or `.gz` archives.
- Do download directly to the ultimate destination location, using a `.tmp` sibling file and rename for local downloads, and direct streaming for remote downloads.
- Skip content validation of downloaded archives.
- Require explicit state selection for any state-scoped download.

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

- `<indicator>`: indicator identifier such as `pm25` or `shared`
- `<storage_mode>`: `local` or `remote`
- `--download <download_key>`: required selector for one configured download entry
- `--state <postal|all>`: required only when the selected download has `scope = state`

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

- For `local`, fetch streams to a temp file in the ultimate destination folder rather than to a separate temp area.
- For `local`, the temp filename should be a clearly temporary sibling of the final output, such as a dot-prefixed or suffix-marked name.
- For `local`, rename the temp file to the final destination name only after the stream completes successfully.
- For `local`, delete the temp file if the download fails or is interrupted.
- For `remote`, fetch streams directly to the final remote destination path without a local temp-file stage.
- The local and remote behaviors are intentionally different: local rename within one directory gives a cheap and strong way to avoid exposing a partial final filename, while remote object stores typically do not support true atomic rename and usually implement rename as copy-plus-delete.
- If the destination already exists, skip the download and write an audit row with status `skipped`.
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


## Implementation plan (three phases)

We will migrate incrementally to reduce risk. Each phase makes a narrowly scoped change and includes quick validation checks before moving to the next phase.

Phase 1 — PM2.5: change transfer mechanics only

- Goal: implement the new write behavior in the smallest, easiest-to-test script before any config or folder-level refactor.
- Target file: `scripts/pm25/pm25_fetch_raw.py`.
- Changes:
  - For `local`, write the download into a `.tmp` sibling in the destination directory and rename into place on success.
  - For `remote`, stream directly to the configured remote destination (no intermediate local copy for remote mode).
  - Keep existing PM2.5 config shape and loader; do not introduce the shared `downloads` schema yet.
  - Keep logging and the per-script audit behavior until Phase 3 (temporary local audit rows may still be written under the PM2.5 root while testing).
- Tests / checks:
  - Local download lands at the configured path and is renamed atomically into place.
  - Rerun skips when the file exists.
  - Remote mode uploads to the configured S3 path and is readable (if credentials available).
  - No archive expansion or ZIP validation is performed.

Phase 2 — Shared: transfer mechanics + remove ZIP validation

- Goal: apply the same transfer behavior to the more complex shared fetch and remove content validation.
- Target file: `scripts/shared/fetch_tiger_lines_bg.py`.
- Changes:
  - Implement the same local `.tmp`-then-rename and direct-remote streaming behavior.
  - Remove per-file ZIP content validation (do not inspect archive members).
  - Preserve `--state` and state expansion behavior; make `--state <postal|all>` required for state-scoped downloads.
  - Keep the config file where it is for now; add a `downloads` block in the shared config if convenient, but do not yet require the central schema.
- Tests / checks:
  - `--download`-style invocation is not required in this phase; validate with the existing CLI (`storage_mode` and `--state`).
  - `--state RI` resolves one URL and writes to the expected destination path.
  - `--state all` resolves one destination per configured state.
  - ZIP validation has been removed: downloads complete regardless of archive member contents.

Phase 3 — Central runner, consolidated config, centralized audit

- Goal: consolidate the runner, config schema, and audit file after the I/O semantics are stable.
- Changes:
  - Add `scripts/fetch_raw.py` as the central runner; require `--download <download_key>`.
  - Migrate each indicator config to expose the common `downloads` shape (examples already in this document).
  - Centralize audit into `scripts/fetch_audit.csv` and update the runner to append rows there.
  - Implement `--download` validation, `--state` requirement rules, and template expansion using `scripts/shared/state_config.py` for state-scoped downloads.
  - Optionally, leave thin wrappers in per-indicator folders that call the central runner to preserve existing documentation or CI hooks.
- Tests / checks:
  - `scripts/fetch_raw.py pm25 local --download raw_pm25_2020` performs the PM2.5 download using the PM2.5 config.
  - `scripts/fetch_raw.py shared local --download tiger_bg_2020 --state RI` resolves to the same destination path as the legacy shared script produced.
  - The centralized `scripts/fetch_audit.csv` contains clear rows for each attempted download with `indicator` filled in.
  - Old indicator-specific fetch scripts either delegate to the central runner or are removed, and documentation is updated.

Notes on sequencing

- The primary reason for this order is diagnostic clarity: if transfer mechanics fail, it is far easier to debug in a single, simple script than after centralization and config changes.
- Keep CLI stability during Phases 1–2 to reduce the churn in callers and CI; introduce `--download` only in Phase 3.


## Open decisions already resolved for implementation

- Put the new fetch runner under `scripts`.
- Keep downloaded files compressed.
- Do not perform archive member validation.
- Stream remote downloads directly to the final remote object path.
- For local downloads, create the temp file in the final destination folder and rename it into place on success.
- Require explicit `--state <postal|all>` for state-scoped downloads.
- Require `--download <download_key>` in version 1.
- Keep canonical downstream path fields outside `downloads` and validate any duplicated values for agreement.