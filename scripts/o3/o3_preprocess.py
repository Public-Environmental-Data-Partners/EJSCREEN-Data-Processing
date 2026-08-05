"""o3_preprocess.py

Purpose:
	Read the raw national Ozone .txt.gz file directly, validate the tract-keyed
	input columns, compute one annual average of the ten highest daily 8-hour
	maximum concentrations per tract, and write the tract-level intermediate CSV
	used by the indicator step.

Process summary:
	- Resolve local or remote Ozone storage from the manifest + resolver.
	- Validate the compressed file header before performing the full read.
	- Read only the required columns from the raw source.
	- Validate tract GEOIDs, dates, and numeric Ozone values.
	- Aggregate to the average of the ten highest daily 8-hour maximum values per tract.
	- Write the tract-level CSV and log the output range.

Runtime arguments (current defaults shown):
		- -l/--location: required one of `local` or `remote` (assigned to internal `storage_mode`).
		- -v/--version: optional config version to use. Default: `1.2020`.
		- --dry-run: long-only flag. When present the script validates the preprocess manifest
			and source file headers, then exits without reading or writing full outputs.

Outputs:
		- vn.m/preprocessed_input/o3_tract_annual_average.csv, version controlled by manifest.
		- o3_preprocess.log in scripts/o3.

	Examples (run from the `scripts` folder):
		- Dry-run (local):
			python3 o3/o3_preprocess.py -l local --dry-run

		- Full run (local, explicit version):
			python3 o3/o3_preprocess.py -l local -v 1.2020

		- Full run (remote):
			python3 o3/o3_preprocess.py -l remote -v 1.2020

"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import importlib
import logging
from pathlib import Path

import pandas as pd
import sys

# All of our project-specific imports must be relative to the 
# `scripts` folder which we assume is at the first level of the
# repository. 
# NB: ***If `scripts` moves, this code will have to change.***
# Walk up our current working directory tree until you find the
# repository root, then add the scripts directory to sys.path
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".git").exists()), None)
if REPO_ROOT is None:
	# This is a running-from-docker or other non-git environment cry for help.
    # Undone: Handle non-git environments more gracefully when needed.
    raise RuntimeError("Architectural Error: Repository root anchor (.git) could not be found!")
SCRIPTS_ROOT = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import shared.build_manifest as build_manifest
import shared.resolve_path as resolve_path

O3_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_FILENAME = 'o3_preprocess.log'
RAW_FIPS_COLUMN = 'FIPS'
RAW_DATE_COLUMN = 'Date'
RAW_O3_COLUMN = 'ozone_daily_8hour_maximum(ppb)'
TRACT_GEOID_COLUMN = 'tract_geoid'
ANNUAL_AVERAGE_COLUMN = 'annual_average_ten_highest_MDA8'

# Pre-compile or centralize frequently used regexes/patterns as module
# constants to avoid recreating them on each function call.
FIPS_PATTERN = r'\d{11}'


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	version: str
	local_root_path: str
	remote_root_path: str
	raw_download_relative_path: str
	preprocessed_tract_output_relative_path: str
	dry_run: bool = False


def get_config(argv=None) -> Config:
	"""Parse runtime arguments and derive canonical paths from the manifest/resolver.

	This function uses `build_manifest.get_stage_manifest()` (preprocess stage)
	and `resolve_path.get_indicator_root()` to populate the runtime config.
	No compatibility fallback to the old flat JSON format is provided.
	"""

	parser = argparse.ArgumentParser(
		description='Read the raw national Ozone .txt.gz file, compute tract-level annual average concentration, and write the preprocessed tract CSV.'
	)
	parser.add_argument(
		'-l', '--location',
		dest='storage_mode',
		choices=('local', 'remote'),
		required=True,
		help='Select whether the script reads and writes through the configured local root path or remote S3 root path.',
	)
	parser.add_argument(
		'-v', '--version',
		dest='version',
		default='1.2020',
		help='Optional: config version to base processing on (current default: 1.2020)'
	)
	# Long-only dry-run flag (no short alias)
	parser.add_argument(
		'--dry-run',
		dest='dry_run',
		action='store_true',
		help='Dry run: validate manifest and headers but do not process or write outputs.',
	)
	args = parser.parse_args(argv)

	# Build the preprocess manifest for this indicator/version. We expect the
	# preprocess stage to define an input `primary_o3` (indicator download) and
	# an output `main_tract_averages` (the tract CSV).
	manifest = build_manifest.get_stage_manifest(
		target_type='indicator',
		name='o3',
		stage='preprocess',
		version=args.version,
		environment=args.storage_mode,
	)

	inputs = manifest.get('inputs', {})
	outputs = manifest.get('outputs', {})

	if 'primary_o3' not in inputs:
		raise RuntimeError('Preprocess manifest missing required input: primary_o3')
	if 'main_tract_averages' not in outputs:
		raise RuntimeError('Preprocess manifest missing required output: main_tract_averages')

	# Manifest entries may already include a compiled "root" and "relative".
	raw_entry = inputs['primary_o3']
	preproc_entry = outputs['main_tract_averages']

	raw_rel = raw_entry.get('relative')
	preproc_rel = preproc_entry.get('relative')

	if not raw_rel or not isinstance(raw_rel, str):
		raise RuntimeError('Invalid relative path for primary_o3 in preprocess manifest')
	if not preproc_rel or not isinstance(preproc_rel, str):
		raise RuntimeError('Invalid relative path for main_tract_averages in preprocess manifest')

	local_root = resolve_path.get_indicator_root('o3', args.version, 'local')
	remote_root = resolve_path.get_indicator_root('o3', args.version, 'remote')

	return Config(
		storage_mode=args.storage_mode,
		version=args.version,
		local_root_path=local_root,
		remote_root_path=remote_root,
		raw_download_relative_path=raw_rel,
		preprocessed_tract_output_relative_path=preproc_rel,
		dry_run=args.dry_run,
	)


def initialize_runtime_dependencies(cfg: Config) -> None:
	if cfg.storage_mode != 'remote':
		return
	dotenv = importlib.import_module('dotenv')
	importlib.import_module('s3fs')
	importlib.import_module('fsspec')
	dotenv.load_dotenv()


def load_fsspec_module():
	return importlib.import_module('fsspec')


def configure_logging() -> str:
	log_path = O3_DIR / DEFAULT_LOG_FILENAME
	log_path.parent.mkdir(parents=True, exist_ok=True)
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s %(levelname)s: %(message)s',
		datefmt='%Y-%m-%d %H:%M:%S',
		handlers=[logging.FileHandler(log_path, mode='a', encoding='utf-8')],
		force=True,
	)
	logging.info('========== Log session started %s ==========', datetime.now().astimezone().isoformat(timespec='seconds'))
	return str(log_path)


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')


def get_active_root_path(cfg: Config) -> str:
	if cfg.storage_mode == 'local':
		return cfg.local_root_path
	if cfg.storage_mode == 'remote':
		return cfg.remote_root_path
	raise ValueError(f'Unsupported storage mode: {cfg.storage_mode}')


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
	return str(Path(root_path) / Path(relative_path))


def get_raw_input_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.raw_download_relative_path)


def get_preprocessed_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.preprocessed_tract_output_relative_path)


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def path_exists(path: str) -> bool:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return bool(fsspec.open(path).fs.exists(path))
	return Path(path).exists()


def read_raw_o3_header(path: str) -> pd.DataFrame:
	"""Read only the compressed Ozone header so missing columns fail fast."""
	read_csv_kwargs = {
		'compression': 'gzip',
		'nrows': 0,
	}
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		with fsspec.open(path, 'rb') as input_stream:
			return pd.read_csv(input_stream, **read_csv_kwargs)

	local_path = Path(path)
	if not local_path.exists():
		raise FileNotFoundError(f'Raw Ozone file not found: {path}')
	return pd.read_csv(local_path, **read_csv_kwargs)


def read_raw_o3_table(path: str) -> pd.DataFrame:
	"""Read the required Ozone raw columns from a local file or S3 object."""
	read_csv_kwargs = {
		'dtype': {RAW_FIPS_COLUMN: 'string', RAW_DATE_COLUMN: 'string'},
		'compression': 'gzip',
		'usecols': [RAW_FIPS_COLUMN, RAW_DATE_COLUMN, RAW_O3_COLUMN],
	}
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		with fsspec.open(path, 'rb') as input_stream:
			return pd.read_csv(input_stream, **read_csv_kwargs)

	local_path = Path(path)
	if not local_path.exists():
		raise FileNotFoundError(f'Raw Ozone file not found: {path}')
	return pd.read_csv(local_path, **read_csv_kwargs)


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
	ensure_local_parent_dir(out_path)
	if is_s3_uri(out_path):
		fsspec = load_fsspec_module()
		with fsspec.open(out_path, 'w', encoding='utf-8', newline='') as output_stream:
			df.to_csv(output_stream, index=False)
		return
	df.to_csv(out_path, index=False)


def require_columns(df: pd.DataFrame, required_columns: tuple[str, ...]) -> None:
	missing_columns = [column for column in required_columns if column not in df.columns]
	if missing_columns:
		raise RuntimeError(f'Raw Ozone input missing required columns: {", ".join(missing_columns)}')


def validate_and_prepare_raw_table(df: pd.DataFrame) -> pd.DataFrame:
	"""Validate raw Ozone rows and return the cleaned tract-level input fields."""
	require_columns(df, (RAW_FIPS_COLUMN, RAW_DATE_COLUMN, RAW_O3_COLUMN))
	prepared = df[[RAW_FIPS_COLUMN, RAW_DATE_COLUMN, RAW_O3_COLUMN]].copy()
	prepared[RAW_FIPS_COLUMN] = prepared[RAW_FIPS_COLUMN].astype('string').str.strip()
	invalid_fips_mask = prepared[RAW_FIPS_COLUMN].isna() | ~prepared[RAW_FIPS_COLUMN].str.fullmatch(FIPS_PATTERN)
	if invalid_fips_mask.any():
		invalid_samples = prepared.loc[invalid_fips_mask, RAW_FIPS_COLUMN].drop_duplicates().astype(str).head(5).tolist()
		raise RuntimeError(
			'Raw Ozone input contains invalid tract FIPS values. '
			f'Expected 11-digit tract GEOIDs. Sample invalid values: {invalid_samples}'
		)

	prepared[RAW_DATE_COLUMN] = prepared[RAW_DATE_COLUMN].astype('string').str.strip()
	if prepared[RAW_DATE_COLUMN].isna().any() or prepared[RAW_DATE_COLUMN].eq('').any():
		raise RuntimeError('Raw Ozone input contains blank Date values')

	try:
		prepared[RAW_O3_COLUMN] = pd.to_numeric(prepared[RAW_O3_COLUMN], errors='raise')
	except Exception as exc:
		raise RuntimeError(f'Raw Ozone input contains non-numeric {RAW_O3_COLUMN} values: {exc}') from exc

	if prepared[RAW_O3_COLUMN].isna().any():
		raise RuntimeError(f'Raw Ozone input contains null {RAW_O3_COLUMN} values')

	return prepared


def build_tract_annual_average_table(prepared: pd.DataFrame) -> pd.DataFrame:
	"""Aggregate validated daily Ozone rows to average of ten highest concentrations for each FIPS."""
	grouped = (
		prepared.groupby(RAW_FIPS_COLUMN, dropna=False, as_index=False)[RAW_O3_COLUMN]
		.agg(lambda grp: grp.nlargest(10).mean())
		.rename(
			columns={
				RAW_FIPS_COLUMN: TRACT_GEOID_COLUMN,
				RAW_O3_COLUMN: ANNUAL_AVERAGE_COLUMN,
			}
		)
		.sort_values(TRACT_GEOID_COLUMN)
		.reset_index(drop=True)
	)
	if grouped.empty:
		raise RuntimeError('Tract-level Ozone aggregation produced no rows')
	return grouped


def log_summary(prepared: pd.DataFrame, grouped: pd.DataFrame, output_path: str) -> None:
	unique_day_count = prepared[RAW_DATE_COLUMN].nunique(dropna=True)
	minimum_value = float(grouped[ANNUAL_AVERAGE_COLUMN].min())
	maximum_value = float(grouped[ANNUAL_AVERAGE_COLUMN].max())
	logging.info('Unique day count in raw Ozone input: %s', unique_day_count)
	logging.info('Tract-level annual average output rows: %s', len(grouped))
	logging.info('Annual average concentration min=%s max=%s', minimum_value, maximum_value)
	logging.info('Preprocessed tract output path: %s', output_path)
	print(f'Annual average concentration min={minimum_value} max={maximum_value}')


def main(argv=None) -> int:
	"""Run the Ozone preprocess step and write the tract-level intermediate CSV."""
	log_path = configure_logging()
	cfg = get_config(argv)
	initialize_runtime_dependencies(cfg)
	raw_input_path = get_raw_input_path(cfg)
	output_path = get_preprocessed_output_path(cfg)
	logging.info('Logging to %s', log_path)
	logging.info('Storage mode: %s', cfg.storage_mode)
	logging.info('Active root path: %s', get_active_root_path(cfg))
	logging.info('Raw input path: %s', raw_input_path)
	logging.info('Preprocessed tract output path: %s', output_path)

	if not path_exists(raw_input_path):
		raise FileNotFoundError(f'Raw Ozone input not found at {raw_input_path}')

	logging.info('Validating raw Ozone header before full read')
	header_df = read_raw_o3_header(raw_input_path)
	require_columns(header_df, (RAW_FIPS_COLUMN, RAW_DATE_COLUMN, RAW_O3_COLUMN))
	logging.info('Raw Ozone header validation passed')
	# If dry-run was requested, we've validated manifest resolution and the
	# raw file header; exit early without performing the full read/aggregation
	# or writing outputs.
	if cfg.dry_run:
		logging.info('Dry run enabled; skipping full read/processing and write.')
		logging.info('DRY RUN: manifest and header validated.')
		logging.info('raw_input_path=%s', raw_input_path)
		logging.info('output_path=%s', output_path)
		print('DRY RUN: manifest and header validated.')
		print(f'raw_input_path={raw_input_path}')
		print(f'output_path={output_path}')
		return 0

	logging.info('Reading raw Ozone input table')
	raw_df = read_raw_o3_table(raw_input_path)
	logging.info('Raw Ozone input rows: %s', len(raw_df))
	prepared = validate_and_prepare_raw_table(raw_df)
	grouped = build_tract_annual_average_table(prepared)
	write_df_s3_or_local(grouped, output_path)
	log_summary(prepared, grouped, output_path)
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
