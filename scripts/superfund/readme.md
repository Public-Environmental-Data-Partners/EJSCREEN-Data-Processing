Superfund workflow

This folder now follows the same high-level pattern as pm25:

- `python scripts/fetch_raw.py --indicator superfund --mode local`
	- Downloads the raw NPL boundaries ZIP archive defined in `superfund_config.json`.
- `python scripts/superfund/superfund_preprocess.py local`
	- Extracts the ZIP archive into one canonical local `.gdb` directory under `pipeline/preprocessed_input`.
- `python scripts/superfund/superfund_score.py -l local -s MT`
	- Reads the canonical local `.gdb` together with shared inputs and writes state indicator outputs.

Notes:
- The preprocess output is always local so the indicator step can rely on one stable extracted `.gdb` path across differing development environments.
- Shared TIGER block-group ZIP files and shared census block weights continue to come from the shared pipeline.
 
Notes on the CLI change:
- `--state` (`-s`) is now required and accepts the special value `all` to run the indicator across all configured states.
- The script prefers `-l/--location` (`local` or `remote`) instead of a positional storage mode; legacy positional is still accepted.

Example: run all configured states locally
```
python scripts/superfund/superfund_score.py -l local -s all
```