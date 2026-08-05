compareREADME
==============

Purpose
-------
This short README explains how to run the three validation scripts in sequence so they produce comparable CSV outputs and a simple bias report. The intended order is:

1. `geojson2csv.py` (pipeline -> CSV)
2. `ejam2csv.py` (EJAM API -> CSV)
3. `prototypes/compare_csv_tables.py` (compare the two CSVs)

Why this order
---------------
- `geojson2csv.py` requires a pipeline-produced GeoJSON input. Without that file, no comparison
is possible. So it is highly recommended it be run first to convert the pipeline's GeoJSON export into a compact CSV that `prototypes/compare_csv_tables.py` 
expects.
- `ejam2csv.py` independently fetches and produces the EJAM reference CSV. 
- `prototypes/compare_csv_tables.py` merges and analyzes the two CSVs.

Important runtime note
----------------------
All three scripts now require a `--state` (or `--state-code`) parameter. This is a REQUIRED two-letter state short code (for example `RI`). The scripts will uppercase the value and read/write files inside a per-state subfolder under the configured `--path`, e.g. `{path}/RI/`.

Quick start (local output folder)
-----------------------------
### 1) Convert pipeline GeoJSON to CSV (run on the pipeline developer's GeoJSON input)
python geojson2csv.py --state RI -p ./output/ --dry-run

### 2) Fetch EJAM API and produce EJAM CSV
python ejam2csv.py --state RI --data-type traffic -p ./output/ --dry-run

### 3) Compare EJAM CSV and pipeline CSV
python prototypes/compare_csv_tables.py --state RI -p ./output/ --dry-run

PM2.5 local validation quick start
-----------------------------
Use the PM2.5-specific helpers when you want to compare the tract-expanded PM2.5
pipeline output to an EJAM PM subset for one state:

1. `python ejam2csv.py --state RI --data-type pm25 -p ./output/`
2. `bash ../pm25/run_state_validation.sh RI local`

The current PM2.5 wrapper defaults to the EJAM score column `pm` and compares it
to the pipeline score column `pm25_score`.

Notes on options and defaults
----------------------------
- All three scripts accept `-p/--path` (S3 prefix or local folder). If the path starts with `s3://` the scripts will read/write to S3 (boto3 + credentials required).
- `geojson2csv.py` defaults: input `bg_summary.geojson`, output `bg_summary.csv`; both are expected under `{path}/{STATE}/` unless overridden.
- `ejam2csv.py` accepts `--data-type/--type` with values `traffic`, `superfund`, `hazardous_waste`, or `pm25`.
- `ejam2csv.py` defaults: `--data-type traffic`, output `ejam_{data_type}_subset.csv`, and writes a small JSON sample `ejam_response.json` in the same `{path}/{STATE}/` folder for inspection.
- `prototypes/compare_csv_tables.py` defaults: EJAM input `ejam_traffic_subset.csv`, pipeline input `bg_summary.csv` (looked up under `{path}/{STATE}/`). Default join keys: `ejam_uniq_id` (EJAM) and `block_group_geoid` (pipeline). Default mapping (new_pipe -> ejam): `{"blk_grp_score":"traffic.score", "total_pop":"pop"}`.

Outputs from `prototypes/compare_csv_tables.py`
-------------------------------------
When run (not dry-run) it writes under `{path}/{STATE}/{output_prefix}/` (default `comparison_results`):
- `merged_reduced.csv` — reduced merged CSV containing the join key plus the compared columns.
- `summary.json` — numeric summaries and orphan counts.
- `simpleReport.txt` — readable top/bottom absolute-difference tables for population and score.

S3 and credentials
------------------
- To read/write S3 set AWS credentials in your environment or in a `.env` file (scripts call `load_dotenv()` so `.env` is supported). Install `boto3` if interacting with S3.

Troubleshooting tips
--------------------
- If the join fails, verify the column names; you can override them with `--join-key-ejam` and `--join-key-pipe`.
- To change which fields are compared, pass a JSON string to `--mapping` (e.g. `--mapping '{"blk_grp_score":"traffic.score"}'`).
- Use `--dry-run` to inspect what would be written without uploading/writing files.

Dependencies
------------
- Python 3.8+
- pandas
- requests (for `ejam2csv.py`)
- boto3 (if using S3)
- python-dotenv (optional)

Contact
-------
If outputs look wrong, check the pipeline GeoJSON and EJAM API sample JSON first and verify expected column names are present.
