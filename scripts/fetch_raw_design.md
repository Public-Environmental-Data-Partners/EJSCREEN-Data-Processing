# Central Fetch Design

## Purpose

- Add one central fetch runner at `scripts/fetch_raw.py`.
- Use indicator-owned config files as the single source of truth for root paths and download definitions.
- Support both local filesystem targets and remote S3 targets.
- Download authoritative source files as-is without expanding `.zip` or `.gz` archives.
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
- Stream each file to a local temp file.
- Copy the completed temp file to the local or remote destination.
- Skip downloads whose destination already exists.
- Append one audit row per attempted file.
- Write file-based logs beside the central runner.

## Download behavior

- Fetch streams to a temp file first.
- After the stream completes, copy the temp file to the final destination.
- Delete the temp file whether the copy succeeds or fails.
- If the destination already exists, skip the download and write an audit row with status `skipped`.
- No unzip or gunzip step occurs in fetch.
- No post-download inspection occurs in fetch.

## Audit contract

- The central runner writes one `fetch_audit.csv` under the active indicator root.
- Audit columns should stay aligned with the current broad pattern used by the existing fetch scripts:
  - `process_name`
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

## Implementation outline

### Slice 1: central runner skeleton

Deliverables:

1. Add `scripts/fetch_raw.py`.
2. Add CLI parsing for `indicator`, `storage_mode`, `--download`, and `--state`.
3. Move the reusable download, local/S3 write, logging, and audit helpers into the central runner.

Checks:

1. Unknown indicator fails fast.
2. Unknown download key fails fast.
3. Invalid `--state` combinations fail fast.

### Slice 2: PM2.5 migration

Deliverables:

1. Keep PM2.5 config in one file.
2. Update the PM2.5 config loader to expose the common `downloads` shape.
3. Point the central runner at the PM2.5 config module.

Checks:

1. `raw_pm25_2020` downloads to the configured local path.
2. A rerun skips cleanly.
3. Remote mode resolves the configured S3 path.

### Slice 3: shared migration

Deliverables:

1. Move TIGER fetch metadata into `scripts/shared/shared_paths_config.json`.
2. Update the shared config loader to validate the new `downloads` block.
3. Keep `tiger_bg_relative_path_template` as the canonical downstream path field.
4. Validate that the canonical TIGER path and the download relative path template match.

Checks:

1. `--download tiger_bg_2020 --state RI` resolves one expected URL and one expected destination path.
2. `--download tiger_bg_2020 --state all` resolves one URL and destination path per configured state.
3. Omitted `--state` for `tiger_bg_2020` fails fast.

### Slice 4: retire indicator-specific fetch scripts

Deliverables:

1. Replace direct use of `scripts/pm25/pm25_fetch_raw.py` and `scripts/shared/fetch_tiger_lines_bg.py` with the central runner.
2. Either remove the old fetch scripts or leave thin wrappers that call the central runner with fixed arguments during transition.
3. Update README or per-indicator notes if they reference the old fetch entry points.

Checks:

1. Existing fetch workflows still succeed through the new entry point.
2. No fetch flow relies on current working directory path behavior.

## Open decisions already resolved for implementation

- Put the new fetch runner under `scripts`.
- Keep downloaded files compressed.
- Do not perform archive member validation.
- Require explicit `--state <postal|all>` for state-scoped downloads.
- Require `--download <download_key>` in version 1.
- Keep canonical downstream path fields outside `downloads` and validate any duplicated values for agreement.