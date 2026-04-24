# Ozone Scripts

This folder contains the Ozone (O3) indicator pipeline and its state-validation wrapper.

## Workflow

1. `o3_fetch_raw.py` downloads the national 2020 O3 `.txt.gz` source into the active O3 pipeline root.
2. `o3_preprocess.py` reads the compressed source directly and writes one annual average concentration per tract.
3. `o3_indicator.py` expands tract scores to block groups, applies the zero-population null rule, and writes per-state `final_bg_scores.csv` outputs.
4. `run_state_validation.sh` reruns one state and, in local mode, compares the result to an Ozone-oriented EJAM subset file.

## Key files

- `o3_config.json`: O3 local and remote roots plus raw-download settings.
- `o3_config.py`: validated config loader shared by the O3 scripts.
- `o3_fetch_raw.py`: raw national download step.
- `o3_preprocess.py`: tract-level annual averaging step.
- `o3_indicator.py`: tract-to-block-group expansion and final state output step.
- `run_state_validation.sh`: one-state validation wrapper.
- `o3_indicator_plan.md`: implementation notes and slice history. DOES NOT EXIST.

## Current local validation flow

Generate an Ozone-oriented EJAM subset CSV:

```bash
python scripts/utilities/validation/ejam2csv.py \
  --state RI \
  --data-type o3 \
  -p scripts/utilities/validation/output
```

Then run the O3 validation wrapper:

```bash
bash scripts/o3/run_state_validation.sh RI local
```

The current working validation defaults are:

- EJAM subset file: `scripts/utilities/validation/output/{STATE}/ejam_o3_subset.csv`
- EJAM score column: `ozone`
- pipeline score column: `o3_score`

## Output contract

- Tract-level preprocess output: `preprocessed_input/o3_tract_annual_average.csv`
- Per-state final output: `output/indicators/{postal}/final_bg_scores.csv`
- Final columns: `block_group_geoid`, `o3_score`
- Zero-population block groups must have null `o3_score`