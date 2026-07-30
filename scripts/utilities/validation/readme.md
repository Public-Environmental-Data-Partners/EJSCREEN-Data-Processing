# Validation Utilities

Scripts for validating indicator pipeline outputs against an independent
reference (the EJAM API) or against another pipeline run/version.

This readme covers `compareScores.py`, `ejam2csv.py`, and `validation_io.py`
only. Subfolders (`believedToBeObsolete/`, `examples/`, `output/`, `pipeline/`,
`prototypes/`, `test_files/`) and `ejscreen_merge.py`/`indicator_concat.py`
(which live in `scripts/shared`) are out of scope here.

## Key files

- `ejam2csv.py`: requests EJAM API results for one state and writes a compact
  CSV of selected fields for one indicator (`traffic`, `superfund`,
  `hazardous_waste`, `pm25`, or `o3`). Output lands under
  `compare/ejamOG/{STATE}/` in the resolved validation root
  (`pipeline/compare/ejamOG/{STATE}/` for `--location local`).
- `compareScores.py`: compares two indicator score CSVs (e.g. a pipeline output
  vs. an EJAM subset, or two pipeline versions). Merges a JSON `--config` file
  with CLI overrides, inner-joins on ID, and writes matched rows, a scatter
  plot, a summary text file, and (best-effort) a four-panel comparison map.
  Saved configs for existing comparisons are checked in as `compare_*.json`.
- `validation_io.py`: shared local/S3 path and I/O helpers for the two scripts
  above. Resolves the validation root via `resolve_path.get_pipeline_root()`.

## Typical flow

1. Pull an EJAM subset for a state/indicator:

   ```bash
   python3 ejam2csv.py --state WY --location local --indicator o3
   ```

2. Compare it to a pipeline output (see `compareScores.py`'s docstring for the
   full argument list, or reuse a checked-in `compare_*.json` config with
   `--config`):

   ```bash
   python3 compareScores.py --config compare_o3_WY_v0.6_ejam.json --state WY --location local
   ```

## Notes

- `-l/--location` on both scripts selects `local` or `remote` and controls
  which validation root paths are resolved against.
- `believedToBeObsolete/` holds a prior README and scripts describing an older
  `run_state_validation.sh`-based flow that no longer applies (that script has
  since been removed from `scripts/o3` and `scripts/pm25`).
