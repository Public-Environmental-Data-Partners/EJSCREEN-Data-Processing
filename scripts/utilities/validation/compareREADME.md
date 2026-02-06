compareREADME
==============

Purpose
-------
This short README explains how to run the three validation scripts in sequence so they produce comparable CSV outputs and a simple bias report. The intended order is:

1. `geojson2csv.py` (pipeline -> CSV)
2. `ejam2csv.py` (EJAM API -> CSV)
3. `compareEJAM2pipeline.py` (compare the two CSVs)

Why this order
---------------
- `geojson2csv.py` requires a pipeline-produced GeoJSON input. Without that file, no comparison
is possible. So it is highly recommended it be run first to convert the pipeline's GeoJSON export into a compact CSV that `compareEJAM2pipeline.py` 
expects.
- `ejam2csv.py` independently fetches and produces the EJAM reference CSV. 
- `compareEJAM2pipeline.py` merges and analyzes the two CSVs.

Quick start (local test files)
-----------------------------
### 1) Convert pipeline GeoJSON to CSV (run on the pipeline developer's GeoJSON input)
python geojson2csv.py -p ./test_files/ --dry-run

### 2) Fetch EJAM API and produce EJAM CSV
python ejam2csv.py -p ./test_files/ --dry-run

### 3) Compare EJAM CSV and pipeline CSV
python compareEJAM2pipeline.py -p ./test_files/ --dry-run

Notes on options and defaults
----------------------------
- All three scripts accept `-p/--path` (S3 prefix or local folder). If the path starts with `s3://` the scripts will read/write to S3 (boto3 + credentials required).
- `geojson2csv.py` defaults: input `ri_bg_summary.geojson`, output `ri_bg_summary.csv`.
- `ejam2csv.py` defaults: output `ri_ejam_traffic_subset.csv` and writes a small JSON sample (by default to `./test_files/ejam_response.json`) for inspection.
- `compareEJAM2pipeline.py` defaults: EJAM input `ri_ejam_traffic_subset.csv`, pipeline input `ri_bg_summary.csv`. Default join keys: `ejam_uniq_id` (EJAM) and `block_group_geoid` (pipeline). Default mapping (new_pipe -> ejam): `{"blk_grp_score":"traffic.score", "total_pop":"pop"}`.

Outputs from `compareEJAM2pipeline.py`
-------------------------------------
When run (not dry-run) it writes under `{path}/{output_prefix}/` (default `comparison_results`):
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
