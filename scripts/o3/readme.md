# Ozone (O3) Scripts

Ozone indicator pipeline: fetch → preprocess → score. 
Configuration (versions, shared assets, input and output file paths)
comes from `o3_config.json` via the shared `build_manifest.py`/`resolve_path.py`
modules. Note that the default version and the supported geographies,
(e.g. 'conus', see load_state_targets()) are still hard-coded in the o3_score.py 
script. Those values could and probably should be moved to the configuration
file to be consistent with the goal of keeping the code as transparent
as possible.

All modules should give you up-to-date documentation on runtime options
if run with the --help option.
All IO is either 'local' (from/to the pipeline folder within your local git repo)
or 'remote' (from/to our AWS S3 bucket pipeline). Examples below all show '-l local'.
To write/read S3 simply substitute '-l remote'.
Note that the first time you want to run this code, you will need to activate
the team virtual environment. 

Also, to use 'remote' io, you have to make your AWS credentials available to the code.  
(Recco: Google something like: "How to Store Your AWS Credentials in a .env File"

## Workflow

0. Be sure you have read and followed the readme in the scripts folder to get your development 
environment set up correctly. 

1. Fetch the raw annual ozone file via the central fetch runner (run from the `scripts` folder),
will skip the download if the file already exists:

   ```bash
   python shared/fetch_raw.py --indicator o3 -v 1.2020 -l local
   ```

2. `o3_preprocess.py` reads the zipped raw annual ozone file source directly 
and writes one annual average of the ten highest daily 8-hour maxima per tract:

   ```bash
   python o3/o3_preprocess.py -v 1.2020 -l local
   ```

3. `o3_score.py` expands tract scores to block groups, applies the zero-population null rule, and writes per-state final scores:

   ```bash
   python o3/o3_score.py -v 1.2020 -s WY  -l local 
   # or use  `-s all` to process every configured state
   ```

## Key files

- `o3_config.json`: versioned fetch/preprocess/score manifest config (local + remote roots, raw-download settings, per-version stage inputs/outputs).
- `o3_preprocess.py`: tract-level annual averaging step.
- `o3_score.py`: tract-to-block-group expansion and final per-state output step.
- `o3_preprocess.log`, `o3_score.log`: log files with more runtime info than you will see on the console


## Validation / Results Comparison

1. Pull an EJAM ozone subset for a state (writes to folder `pipeline/compare/ejamOG/{STATE}/`):

   ```bash
   python utilities/validation/ejam2csv.py --state WY --indicator o3 --location local 
   ```

2. Compare the pipeline output to the EJAM subset with `utilities/validation/compareScores.py`
   (see that script's docstring for the full argument list). Saved configs for this
   pattern are checked in as `utilities/validation/compare_o3_*.json` and can be
   reused with `--config`. All config file options can be overwritten at runtime via
   cli arguments.

## Output contract

- Tract-level preprocess output: `v{version}/preprocessed_input/o3_tract_annual_average.csv`
- Per-state final output: `v{version}/score_output/final_bg_scores_{postal}.csv`
- Final columns include `block_group_geoid` and `o3_score`; zero-population block groups must have a null `o3_score`.
