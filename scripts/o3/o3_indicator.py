"""o3_indicator.py

Purpose:
	Read tract-level Ozone averages, expand them to block groups with the shared
	census block weights inputs, apply the zero-population null rule, and write
	per-state final_bg_scores.csv outputs.

Process summary:
	- Resolve the Ozone and shared roots for local or remote mode.
	- Read the tract-level preprocess output.
	- Read each state's census block weights file from the shared pipeline inputs.
	- Derive tract GEOIDs from block-group GEOIDs and join tract scores.
	- Require scores for positive-population block groups.
	- Set o3_score to null for zero-population block groups.
	- Write per-state final_bg_scores.csv files and state summary logs.

Runtime arguments:
	- storage_mode
		Required. Either local or remote.
	- --state
		Optional two-letter state filter. If omitted, process all configured states.

Outputs:
	- output/indicators/{postal}/final_bg_scores.csv under the active Ozone root.
	- o3_indicator.log in scripts/o3.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import importlib
import importlib.util
import json
import logging
from pathlib import Path
import sys
from typing import Any

import pandas as pd

from o3_config import get_o3_config, resolve_local_o3_root_path


O3_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_FILENAME = 'o3_indicator.log'
DEFAULT_FINAL_BG_SCORES_FILENAME = 'final_bg_scores.csv'
TRACT_GEOID_COLUMN = 'tract_geoid'
ANNUAL_AVERAGE_COLUMN = 'annual_average_ten_highest_MDA8'
FINAL_SCORE_COLUMN = 'o3_score'  #Edit this to match EJAM/EJSCREEN e.g. o3
BLOCK_GROUP_GEOID_COLUMN = 'block_group_geoid'
BLOCK_GROUP_POP_COLUMN = 'block_group_pop'
O3_STORAGE_MODES = ('local', 'remote')


def _resolve_scripts_dir() -> Path:
	current_path = Path(__file__).resolve()
	for parent in current_path.parents:
		if parent.name == 'scripts':
			return parent
	raise RuntimeError(f'Unable to locate scripts directory from {current_path}')


SCRIPTS_DIR = _resolve_scripts_dir()
SHARED_STATE_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'state_config.py'
SHARED_PATHS_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'shared_paths_config.py'


def _load_shared_state_config_symbols():
	if not SHARED_STATE_CONFIG_MODULE_PATH.exists():
		raise ImportError(f'Shared state_config.py not found: {SHARED_STATE_CONFIG_MODULE_PATH}')

	module_spec = importlib.util.spec_from_file_location('shared_state_config_o3', SHARED_STATE_CONFIG_MODULE_PATH)
	if module_spec is None or module_spec.loader is None:
		raise ImportError(f'Unable to load module spec from {SHARED_STATE_CONFIG_MODULE_PATH}')

	module = importlib.util.module_from_spec(module_spec)
	sys.modules[module_spec.name] = module
	module_spec.loader.exec_module(module)
	return module.StateConfig, module.STATE_CONFIG_PATH, module.get_state_config


def _load_shared_paths_config_symbols():
	if not SHARED_PATHS_CONFIG_MODULE_PATH.exists():
		raise ImportError(f'Shared shared_paths_config.py not found: {SHARED_PATHS_CONFIG_MODULE_PATH}')

	module_spec = importlib.util.spec_from_file_location('shared_paths_config_o3', SHARED_PATHS_CONFIG_MODULE_PATH)
	if module_spec is None or module_spec.loader is None:
		raise ImportError(f'Unable to load module spec from {SHARED_PATHS_CONFIG_MODULE_PATH}')

	module = importlib.util.module_from_spec(module_spec)
	sys.modules[module_spec.name] = module
	module_spec.loader.exec_module(module)
	return module.get_shared_paths_config, module.resolve_local_shared_root_path


try:
	from ..shared.state_config import StateConfig, STATE_CONFIG_PATH, get_state_config
except ImportError:
	try:
		from shared.state_config import StateConfig, STATE_CONFIG_PATH, get_state_config
	except ImportError:
		StateConfig, STATE_CONFIG_PATH, get_state_config = _load_shared_state_config_symbols()

try:
	from ..shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
except ImportError:
	try:
		from shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
	except ImportError:
		get_shared_paths_config, resolve_local_shared_root_path = _load_shared_paths_config_symbols()


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	state: str | None
	tract_scores_path: str | None = None
	census_block_weights_path: str | None = None
	output_dir: str | None = None
	final_bg_scores_filename: str = DEFAULT_FINAL_BG_SCORES_FILENAME


@dataclass(frozen=True, slots=True)
class ResolvedPaths:
	state_config: Any
	o3_root_path: str
	shared_root_path: str
	tract_scores_path: str
	census_block_weights_path: str
	output_dir: str
	final_bg_scores_path: str


def get_config(argv=None) -> Config:
	"""Parse runtime arguments for the Ozone indicator step."""
	defaults = Config(storage_mode='local', state=None)
	parser = argparse.ArgumentParser(
		description='Read tract-level Ozone scores and produce per-state block-group final scores.'
	)
	parser.add_argument(
		'storage_mode',
		choices=O3_STORAGE_MODES,
		help='Select whether the script reads and writes through the local root path or remote S3 root path.',
	)
	parser.add_argument(
		'--state',
		dest='state',
		default=defaults.state,
		help='Optional two-letter state code to process a single state.',
	)
	parser.add_argument(
		'--tract-scores-path',
		dest='tract_scores_path',
		default=defaults.tract_scores_path,
		help='Optional explicit local path or S3 URI for the tract-level Ozone preprocessed CSV.',
	)
	parser.add_argument(
		'--census-block-weights-path',
		dest='census_block_weights_path',
		default=defaults.census_block_weights_path,
		help='Optional explicit local path or S3 URI for a state census block weights CSV.',
	)
	parser.add_argument(
		'--output-dir',
		dest='output_dir',
		default=defaults.output_dir,
		help='Optional explicit local path or S3 URI for the state output directory.',
	)
	parser.add_argument(
		'--final-bg-scores-filename',
		dest='final_bg_scores_filename',
		default=defaults.final_bg_scores_filename,
		help=f'Output filename for final state scores (default: {defaults.final_bg_scores_filename})',
	)
	args = parser.parse_args(argv)

	state = None
	if args.state:
		state = normalize_state_code(args.state)

	return Config(
		storage_mode=args.storage_mode,
		state=state,
		tract_scores_path=args.tract_scores_path,
		census_block_weights_path=args.census_block_weights_path,
		output_dir=args.output_dir,
		final_bg_scores_filename=args.final_bg_scores_filename,
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


def normalize_state_code(state_code: str) -> str:
	normalized_state_code = state_code.strip().upper()
	if len(normalized_state_code) != 2 or not normalized_state_code.isalpha():
		raise RuntimeError(f"State code must be a two-letter postal abbreviation, got '{state_code}'")
	return normalized_state_code


def load_state_targets(selected_state: str | None) -> list[Any]:
	"""Return the configured state targets, optionally filtered to one postal code."""
	if not STATE_CONFIG_PATH.exists():
		raise FileNotFoundError(f'State config file not found: {STATE_CONFIG_PATH}')

	with STATE_CONFIG_PATH.open('r', encoding='utf-8') as state_stream:
		payload = json.load(state_stream)

	if not isinstance(payload, dict):
		raise RuntimeError(f'State config file must contain a JSON object: {STATE_CONFIG_PATH}')

	state_codes = sorted(payload)
	if selected_state is not None:
		if selected_state not in state_codes:
			raise RuntimeError(f"Configured state '{selected_state}' was not found in {STATE_CONFIG_PATH.name}")
		state_codes = [selected_state]

	return [get_state_config(state_code) for state_code in state_codes]


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
	return str(Path(root_path) / Path(relative_path))


def join_path_and_file(path: str, filename: str) -> str:
	if is_s3_uri(path):
		return path.rstrip('/') + '/' + filename.lstrip('/')
	return str(Path(path) / filename)


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_csv_s3_or_local(path: str, **read_csv_kwargs) -> pd.DataFrame:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		with fsspec.open(path, 'rb') as input_stream:
			return pd.read_csv(input_stream, **read_csv_kwargs)

	local_path = Path(path)
	if not local_path.exists():
		raise FileNotFoundError(f'Local CSV not found: {path}')
	return pd.read_csv(local_path, **read_csv_kwargs)


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
	ensure_local_parent_dir(out_path)
	if is_s3_uri(out_path):
		fsspec = load_fsspec_module()
		with fsspec.open(out_path, 'w', encoding='utf-8', newline='') as output_stream:
			df.to_csv(output_stream, index=False)
		return
	df.to_csv(out_path, index=False)


def get_active_o3_root_path(storage_mode: str) -> str:
	if storage_mode == 'local':
		return resolve_local_o3_root_path(O3_DIR)
	if storage_mode == 'remote':
		return get_o3_config().remote_root_path
	raise ValueError(f'Unsupported storage mode: {storage_mode}')


def get_active_shared_root_path(storage_mode: str) -> str:
	if storage_mode == 'local':
		return resolve_local_shared_root_path(SCRIPTS_DIR)
	if storage_mode == 'remote':
		return get_shared_paths_config().remote_root_path
	raise ValueError(f'Unsupported storage mode: {storage_mode}')


def resolve_paths(cfg: Config, state_config: Any) -> ResolvedPaths:
	"""Resolve the tract input, shared block-weight input, and state output paths."""
	o3_config = get_o3_config()
	shared_paths_config = get_shared_paths_config()
	o3_root_path = get_active_o3_root_path(cfg.storage_mode)
	shared_root_path = get_active_shared_root_path(cfg.storage_mode)

	tract_scores_path = cfg.tract_scores_path or join_root_and_relative_path(
		o3_root_path,
		o3_config.preprocessed_tract_output_relative_path,
	)
	census_block_weights_relative_path = shared_paths_config.census_block_weights_relative_path_template.format(
		postal=state_config.postal,
	)
	census_block_weights_path = cfg.census_block_weights_path or join_root_and_relative_path(
		shared_root_path,
		census_block_weights_relative_path,
	)
	output_dir = cfg.output_dir or join_root_and_relative_path(
		o3_root_path,
		o3_config.indicator_output_relative_path_template.format(postal=state_config.postal),
	)
	return ResolvedPaths(
		state_config=state_config,
		o3_root_path=o3_root_path,
		shared_root_path=shared_root_path,
		tract_scores_path=tract_scores_path,
		census_block_weights_path=census_block_weights_path,
		output_dir=output_dir,
		final_bg_scores_path=join_path_and_file(output_dir, cfg.final_bg_scores_filename),
	)


def require_columns(df: pd.DataFrame, required_columns: tuple[str, ...], description: str) -> None:
	missing_columns = [column for column in required_columns if column not in df.columns]
	if missing_columns:
		raise RuntimeError(f'{description} missing required columns: {", ".join(missing_columns)}')


def prepare_tract_scores(df: pd.DataFrame) -> pd.DataFrame:
	"""Validate and normalize the tract-level Ozone preprocess output."""
	require_columns(df, (TRACT_GEOID_COLUMN, ANNUAL_AVERAGE_COLUMN), 'Tract scores CSV')
	prepared = df[[TRACT_GEOID_COLUMN, ANNUAL_AVERAGE_COLUMN]].copy()
	prepared[TRACT_GEOID_COLUMN] = prepared[TRACT_GEOID_COLUMN].astype('string').str.strip()
	invalid_tract_mask = prepared[TRACT_GEOID_COLUMN].isna() | ~prepared[TRACT_GEOID_COLUMN].str.fullmatch(r'\d{11}')
	if invalid_tract_mask.any():
		invalid_samples = prepared.loc[invalid_tract_mask, TRACT_GEOID_COLUMN].drop_duplicates().astype(str).head(5).tolist()
		raise RuntimeError(f'Tract scores CSV contains invalid tract GEOIDs. Sample invalid values: {invalid_samples}')

	duplicate_mask = prepared[TRACT_GEOID_COLUMN].duplicated(keep=False)
	if duplicate_mask.any():
		duplicate_samples = prepared.loc[duplicate_mask, TRACT_GEOID_COLUMN].drop_duplicates().astype(str).head(5).tolist()
		raise RuntimeError(f'Tract scores CSV contains duplicate tract GEOIDs. Sample duplicates: {duplicate_samples}')

	prepared[ANNUAL_AVERAGE_COLUMN] = pd.to_numeric(prepared[ANNUAL_AVERAGE_COLUMN], errors='raise')
	if prepared[ANNUAL_AVERAGE_COLUMN].isna().any():
		raise RuntimeError(f'Tract scores CSV contains null {ANNUAL_AVERAGE_COLUMN} values')
	return prepared.sort_values(TRACT_GEOID_COLUMN).reset_index(drop=True)


def prepare_block_group_population(df: pd.DataFrame) -> pd.DataFrame:
	"""Reduce the shared block-weight input to one population row per block group."""
	require_columns(df, (BLOCK_GROUP_GEOID_COLUMN, BLOCK_GROUP_POP_COLUMN), 'Census block weights CSV')
	prepared = df[[BLOCK_GROUP_GEOID_COLUMN, BLOCK_GROUP_POP_COLUMN]].copy()
	prepared[BLOCK_GROUP_GEOID_COLUMN] = prepared[BLOCK_GROUP_GEOID_COLUMN].astype('string').str.strip()
	invalid_bg_mask = prepared[BLOCK_GROUP_GEOID_COLUMN].isna() | ~prepared[BLOCK_GROUP_GEOID_COLUMN].str.fullmatch(r'\d{12}')
	if invalid_bg_mask.any():
		invalid_samples = prepared.loc[invalid_bg_mask, BLOCK_GROUP_GEOID_COLUMN].drop_duplicates().astype(str).head(5).tolist()
		raise RuntimeError(f'Census block weights CSV contains invalid block group GEOIDs. Sample invalid values: {invalid_samples}')

	prepared[BLOCK_GROUP_POP_COLUMN] = pd.to_numeric(prepared[BLOCK_GROUP_POP_COLUMN], errors='raise')
	if prepared[BLOCK_GROUP_POP_COLUMN].isna().any():
		raise RuntimeError('Census block weights CSV contains null block_group_pop values')
	if (prepared[BLOCK_GROUP_POP_COLUMN] < 0).any():
		raise RuntimeError('Census block weights CSV contains negative block_group_pop values')

	population_variants = prepared.groupby(BLOCK_GROUP_GEOID_COLUMN, dropna=False)[BLOCK_GROUP_POP_COLUMN].nunique(dropna=False)
	inconsistent_geoids = population_variants[population_variants > 1].index.tolist()
	if inconsistent_geoids:
		raise RuntimeError(
			'Census block weights CSV contains inconsistent block_group_pop values for some block groups. '
			f'Sample GEOIDs: {inconsistent_geoids[:5]}'
		)

	group_population = prepared.drop_duplicates(subset=[BLOCK_GROUP_GEOID_COLUMN]).copy()
	group_population[TRACT_GEOID_COLUMN] = group_population[BLOCK_GROUP_GEOID_COLUMN].str.slice(0, 11)
	return group_population.sort_values(BLOCK_GROUP_GEOID_COLUMN).reset_index(drop=True)


def build_final_scores(tract_scores: pd.DataFrame, block_group_population: pd.DataFrame) -> pd.DataFrame:
	"""Join tract scores to block groups and apply the zero-population null rule."""
	merged = block_group_population.merge(tract_scores, on=TRACT_GEOID_COLUMN, how='left')
	positive_population_mask = merged[BLOCK_GROUP_POP_COLUMN] > 0
	missing_positive_mask = positive_population_mask & merged[ANNUAL_AVERAGE_COLUMN].isna()
	if missing_positive_mask.any():
		missing_samples = merged.loc[missing_positive_mask, BLOCK_GROUP_GEOID_COLUMN].astype(str).head(5).tolist()
		raise RuntimeError(
			'Positive-population block groups are missing tract-level Ozone scores. '
			f'Sample block groups: {missing_samples}'
		)

	merged[FINAL_SCORE_COLUMN] = merged[ANNUAL_AVERAGE_COLUMN].astype('Float64')
	merged.loc[~positive_population_mask, FINAL_SCORE_COLUMN] = pd.NA
	return merged[[BLOCK_GROUP_GEOID_COLUMN, FINAL_SCORE_COLUMN]].copy()


def log_resolved_paths(paths: ResolvedPaths, cfg: Config) -> None:
	logging.info('State: %s (%s) | FIPS: %s', paths.state_config.name, paths.state_config.postal, paths.state_config.fips)
	logging.info('Storage mode: %s', cfg.storage_mode)
	logging.info('Ozone root path: %s', paths.o3_root_path)
	logging.info('Shared root path: %s', paths.shared_root_path)
	logging.info('Tract scores path: %s', paths.tract_scores_path)
	logging.info('Census block weights path: %s', paths.census_block_weights_path)
	logging.info('Final output path: %s', paths.final_bg_scores_path)


def log_state_summary(
	*,
	state_config: Any,
	block_group_population: pd.DataFrame,
	final_scores: pd.DataFrame,
) -> None:
	total_tract_count = int(block_group_population[TRACT_GEOID_COLUMN].nunique(dropna=True))
	total_block_group_count = int(len(block_group_population))
	non_zero_block_group_count = int(block_group_population[BLOCK_GROUP_POP_COLUMN].gt(0).sum())
	zero_population_block_group_count = int(block_group_population[BLOCK_GROUP_POP_COLUMN].eq(0).sum())
	non_null_scores = final_scores[FINAL_SCORE_COLUMN].dropna()
	minimum_score = None if non_null_scores.empty else float(non_null_scores.min())
	maximum_score = None if non_null_scores.empty else float(non_null_scores.max())
	logging.info(
		'State summary for %s (%s):',
		state_config.name,
		state_config.postal,
	)
	logging.info(
		'     tracts_processed=%d total_block_groups=%d non_zero_block_groups=%d zero_population_block_groups=%d',
		total_tract_count,
		total_block_group_count,
		non_zero_block_group_count,
		zero_population_block_group_count,
	)	
	logging.info(
		'     min_o3_score=%s max_o3_score=%s',
		minimum_score,
		maximum_score,
	)


def process_state(cfg: Config, state_config: Any, tract_scores: pd.DataFrame) -> None:
	"""Build and write one state's final Ozone block-group score file."""
	paths = resolve_paths(cfg, state_config)
	log_resolved_paths(paths, cfg)
	block_weights_df = read_csv_s3_or_local(paths.census_block_weights_path, dtype={BLOCK_GROUP_GEOID_COLUMN: 'string'})
	block_group_population = prepare_block_group_population(block_weights_df)
	final_scores = build_final_scores(tract_scores, block_group_population)
	zero_population_count = int(block_group_population[BLOCK_GROUP_POP_COLUMN].eq(0).sum())
	logging.info(
		'Writing final Ozone scores to %s (rows=%d, zero_population_groups=%d)',
		paths.final_bg_scores_path,
		len(final_scores),
		zero_population_count,
	)
	write_df_s3_or_local(final_scores, paths.final_bg_scores_path)
	log_state_summary(
		state_config=state_config,
		block_group_population=block_group_population,
		final_scores=final_scores,
	)


def main(argv=None) -> int:
	"""Run the Ozone tract-to-block-group indicator workflow."""
	log_path = configure_logging()
	cfg = get_config(argv)
	initialize_runtime_dependencies(cfg)
	state_targets = load_state_targets(cfg.state)
	tract_scores_path = cfg.tract_scores_path or join_root_and_relative_path(
		get_active_o3_root_path(cfg.storage_mode),
		get_o3_config().preprocessed_tract_output_relative_path,
	)
	logging.info('Logging to %s', log_path)
	logging.info('Selected state filter: %s', cfg.state or 'all configured states')
	logging.info('Using tract scores path: %s', tract_scores_path)
	tract_scores_df = read_csv_s3_or_local(tract_scores_path, dtype={TRACT_GEOID_COLUMN: 'string'})
	prepared_tract_scores = prepare_tract_scores(tract_scores_df)
	logging.info('Prepared %d tract-level Ozone scores', len(prepared_tract_scores))

	for state_config in state_targets:
		process_state(cfg, state_config, prepared_tract_scores)

	logging.info('Completed Ozone block-group indicator generation for %d states', len(state_targets))
	return 0


if __name__ == '__main__':
	raise SystemExit(main())