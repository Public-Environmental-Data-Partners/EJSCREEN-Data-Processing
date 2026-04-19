# PM2.5 Scripts

This folder contains the PM2.5 indicator pipeline and its state-validation wrapper.

## Workflow

1. `pm25_fetch_raw.py` downloads the national 2020 PM2.5 `.txt.gz` source into the active PM2.5 pipeline root.
2. `pm25_preprocess.py` reads the compressed source directly and writes one annual average concentration per tract.
3. `pm25_indicator.py` expands tract scores to block groups, applies the zero-population null rule, and writes per-state `final_bg_scores.csv` outputs.
4. `run_state_validation.sh` reruns one state and, in local mode, compares the result to a PM-oriented EJAM subset file.

## Key files

- `pm25_config.json`: PM2.5 local and remote roots plus raw-download settings.
- `pm25_config.py`: validated config loader shared by the PM2.5 scripts.
- `pm25_fetch_raw.py`: raw national download step.
- `pm25_preprocess.py`: tract-level annual averaging step.
- `pm25_indicator.py`: tract-to-block-group expansion and final state output step.
- `run_state_validation.sh`: one-state validation wrapper.
- `pm25_indicator_plan.md`: implementation notes and slice history.

## Current local validation flow

Generate a PM-oriented EJAM subset CSV:

```bash
python scripts/utilities/validation/ejam2csv.py \
  --state RI \
  --data-type pm25 \
  -p scripts/utilities/validation/output
```

Then run the PM2.5 validation wrapper:

```bash
bash scripts/pm25/run_state_validation.sh RI local
```

The current working validation defaults are:

- EJAM subset file: `scripts/utilities/validation/output/{STATE}/ejam_pm25_subset.csv`
- EJAM score column: `pm`
- pipeline score column: `pm25_score`

## Output contract

- Tract-level preprocess output: `preprocessed_input/pm25_tract_annual_average.csv`
- Per-state final output: `output/indicators/{postal}/final_bg_scores.csv`
- Final columns: `block_group_geoid`, `pm25_score`
- Zero-population block groups must have null `pm25_score`