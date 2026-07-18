"""o3_score.py

Purpose:
	Read tract-level Ozone averages, expand them to block groups with the shared
	census block weights inputs, apply the zero-population null rule, and write
	per-state final_bg_scores.csv outputs.

Notes:
	- The final block-group output filename is fixed to ``final_bg_scores.csv``
	  and is not configurable via the CLI.

Process summary:
	- Resolve the Ozone and shared roots for local or remote mode.
	- Read the tract-level preprocess output.
	- Read each state's census block weights file from the shared pipeline inputs.
	- Derive tract GEOIDs from block-group GEOIDs and join tract scores.
	- Generate scores for positive-population block groups.
	- Set o3_score to null for zero-population block groups.
	- Write per-state final_bg_scores.csv files and state summary logs.

Runtime arguments (current defaults shown):
		- -l/--location: required one of `local` or `remote`.
		- -s/--state: required two-letter state code (e.g. `WY`) or `all` (case-insensitive) to process the full
			configured set. Use `--state all` to iterate across all configured states (the code maps this to
			an internal `None` which `load_state_targets()` interprets as "all").
		- --output-dir: optional explicit output directory (local path or S3 URI). Default: none (uses indicator output template).
		- -v/--version: optional config version to use. Default: `1.0`.
		- --dry-run: long-only flag. When present the script validates manifests and prints resolved paths
			without performing any I/O.

Outputs:
		- output/indicators/{postal}/final_bg_scores.csv under the active Ozone root (filename is fixed to
			`final_bg_scores.csv`).
		- o3_score.log in scripts/o3.

Examples (run from the `scripts` folder):
		- Dry-run for Wyoming (local):
			python3 o3/o3_score.py --location local --state WY --dry-run

		- Full run for all configured states (local):
			python3 o3/o3_score.py --location local --state all

		- Full run for a specific version (remote):
			python3 o3/o3_score.py --location remote -v 1.0 --state CA

"""
 
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import importlib
import logging
from pathlib import Path
import sys

import pandas as pd

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
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import shared.build_manifest as build_manifest
import shared.resolve_path as resolve_path


O3_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_FILENAME = 'o3_score.log'
DEFAULT_FINAL_BG_SCORES_FILENAME = 'final_bg_scores.csv'
TRACT_GEOID_COLUMN = 'tract_geoid'
ANNUAL_AVERAGE_COLUMN = 'annual_average_ten_highest_MDA8'
FINAL_SCORE_COLUMN = 'o3_score'  #Edit this to match EJAM/EJSCREEN e.g. o3

# Column name defaults (lower_snake_case variables). Default to V1 names.
# These are selected per-version at read time and then mapped to the canonical
# names used throughout the downstream code.
# Note that the 2020, 2021, and 2022 raw o3 data files all use
# the 2020 block_group_geoid values, not the block_group_geoid_2022 values
# (which are only different for CT anyway). 
block_group_geoid_col = 'block_group_geoid'
# But, from version 1.0 onward, the population column that we use
# for assigning nulls to zero-population block groups is the ACS 2022 population column.
block_group_pop_col = 'acs_2022_bg_pop'
state_abb_col = 'state_abb'

# Canonical column names used downstream after normalization
canonical_block_group_geoid = 'block_group_geoid'
canonical_block_group_pop = 'block_group_pop'
O3_STORAGE_MODES = ('local', 'remote')


def parse_version_decimal(version_str: str) -> Decimal:
	try:
		return Decimal(str(version_str))
	except (InvalidOperation, TypeError) as exc:
		raise RuntimeError(f'Invalid version string: {version_str}') from exc

try:
	from shared.state_config import StateConfig, get_state_config, get_state_config_list
except Exception as exc:
	raise RuntimeError(
		'Failed to import shared.state_config. Ensure scripts/shared/state_config.py is present and '
		'that the repository root (containing the scripts directory) is on PYTHONPATH.'
	) from exc


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	state: str | None
	version: str = '1.0'
	version_decimal: Decimal | None = None
	output_dir: str | None = None
	dry_run: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedPaths:
	state_config: StateConfig
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
		'-l', '--location',
		dest='storage_mode',
		choices=O3_STORAGE_MODES,
		required=True,
		help='Select whether the script reads and writes through the local root path or remote S3 root path.',
	)
	parser.add_argument(
		'-s', '--state',
		dest='state',
		required=True,
		help="Specify a two-letter state code (e.g. 'WY') or 'all' to process the full configured set.",
	)
	parser.add_argument(
		'--output-dir',
		dest='output_dir',
		default=defaults.output_dir,
		help='Optional explicit local path or S3 URI for the state output directory.',
	)

	parser.add_argument(
		'-v', '--version',
		dest='version',
		default=defaults.version,
		help='Optional: config version to base processing on (current default: 1.0)'
	)
	# Long-only dry-run flag (no short alias)
	parser.add_argument(
		'--dry-run',
		dest='dry_run',
		action='store_true',
		help='Dry run: validate manifest and paths but do not read or write any files.',
	)
	args = parser.parse_args(argv)

	# Accept explicit 'all' (case-insensitive) to indicate processing the full set.
	state = None
	if args.state:
		if isinstance(args.state, str) and args.state.strip().lower() == 'all':
			state = None  # This value gets interpreted by load_state_targets as "load all states"
		else:
			state = normalize_state_code(args.state)

	return Config(
		storage_mode=args.storage_mode,
		state=state,
		version=args.version,
		output_dir=args.output_dir,
		dry_run=bool(args.dry_run),
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


# We can load one state or 'all' (as indicated by no argument). 
# But note that each indicator needs to be aware of what its own 'all'
# entails. In the case of Ozone, values are only available for the continental US,
# so all is the 48 lower states plus DC.
def load_state_targets(selected_state: str | None) -> list[StateConfig]:
	logging.info('Loading state config(s) for selection: %s', selected_state or 'all')
	state_list = []

	if selected_state:
		state_config = get_state_config(selected_state)
		state_list.append(state_config)
	else:
		state_list = get_state_config_list('conus')

	logging.info('Loaded %d state configs for processing', len(state_list))
	return state_list


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




def resolve_paths(cfg: Config, state_config: StateConfig, manifest: dict) -> ResolvedPaths:
	"""Resolve the tract input, shared block-weight input, and state output paths.

	The manifest must be provided by the caller to avoid duplicate manifest
	lookups; callers should request the stage manifest once and pass it through.
	"""
	inputs = manifest.get('inputs', {})
	outputs = manifest.get('outputs', {})

	# preprocessed_tracts should be a plain file input (relative to indicator root)
	tract_entry = inputs.get('preprocessed_tracts')
	if not tract_entry:
		raise RuntimeError('Score manifest missing required input: preprocessed_tracts')
	indicator_root = resolve_path.get_indicator_root('o3', cfg.version, cfg.storage_mode)
	tract_scores_path = join_root_and_relative_path(indicator_root, tract_entry['relative'])

	# weights is a shared asset; resolve shared version and root via resolver
	weights_entry = inputs.get('weights')
	if not weights_entry:
		raise RuntimeError('Score manifest missing required input: weights')
	shared_version = resolve_path.get_dependency_version('o3', cfg.version, 'census_block_weights')
	shared_root = resolve_path.get_shared_root('census_block_weights', shared_version, cfg.storage_mode)
	census_block_weights_path = join_root_and_relative_path(
		shared_root,
		weights_entry['relative'].format(postal=state_config.postal),
	)

	# output directory/template (indicator outputs are relative to indicator root)
	output_dir_entry = outputs.get('indicator_output_template')
	if not output_dir_entry:
		raise RuntimeError('Score manifest missing required output: indicator_output_template')
	output_dir = cfg.output_dir or join_root_and_relative_path(
		indicator_root,
		output_dir_entry['relative'].format(postal=state_config.postal),
	)

	return ResolvedPaths(
		state_config=state_config,
		o3_root_path=indicator_root,
		shared_root_path=shared_root,
		tract_scores_path=tract_scores_path,
		census_block_weights_path=census_block_weights_path,
		output_dir=output_dir,
		final_bg_scores_path=join_path_and_file(output_dir, DEFAULT_FINAL_BG_SCORES_FILENAME),
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
	"""Validate and reduce the shared block-weight input to one population row per block group.

	Expects the DataFrame to contain the canonical columns
	`canonical_block_group_geoid` and `canonical_block_group_pop` (and
	`state_abb` where present). Returns a DataFrame with one row per block
	group and a derived `tract_geoid` column.
	"""
	require_columns(df, (canonical_block_group_geoid, canonical_block_group_pop), 'Census block weights CSV')
	prepared = df[[canonical_block_group_geoid, canonical_block_group_pop]].copy()
	prepared[canonical_block_group_geoid] = prepared[canonical_block_group_geoid].astype('string').str.strip()

	# Drop rows with missing or placeholder GEOIDs (e.g., NA) before validation
	geoid_upper = prepared[canonical_block_group_geoid].fillna('').str.upper()
	missing_geoid_mask = geoid_upper.isin(['', 'NA', '<NA>'])
	if missing_geoid_mask.any():
		logging.info('Dropping %d rows with missing block-group GEOID values', int(missing_geoid_mask.sum()))
		prepared = prepared.loc[~missing_geoid_mask].copy()

	invalid_bg_mask = prepared[canonical_block_group_geoid].isna() | ~prepared[canonical_block_group_geoid].str.fullmatch(r'\d{12}')
	if invalid_bg_mask.any():
		invalid_samples = prepared.loc[invalid_bg_mask, canonical_block_group_geoid].drop_duplicates().astype(str).head(5).tolist()
		raise RuntimeError(f'Census block weights CSV contains invalid block group GEOIDs. Sample invalid values: {invalid_samples}')

	# Coerce population to numeric; treat missing (NA) as zero to tolerate
	# upstream files that encode zero fractions as NA.
	prepared[canonical_block_group_pop] = pd.to_numeric(prepared[canonical_block_group_pop], errors='coerce')
	prepared[canonical_block_group_pop] = prepared[canonical_block_group_pop].fillna(0)
	if (prepared[canonical_block_group_pop] < 0).any():
		raise RuntimeError('Census block weights CSV contains negative block_group_pop values')

	population_variants = prepared.groupby(canonical_block_group_geoid, dropna=False)[canonical_block_group_pop].nunique(dropna=False)
	inconsistent_geoids = population_variants[population_variants > 1].index.tolist()
	if inconsistent_geoids:
		raise RuntimeError(
			'Census block weights CSV contains inconsistent block_group_pop values for some block groups. '
			f'Sample GEOIDs: {inconsistent_geoids[:5]}'
		)

	group_population = prepared.drop_duplicates(subset=[canonical_block_group_geoid]).copy()
	group_population[TRACT_GEOID_COLUMN] = group_population[canonical_block_group_geoid].str.slice(0, 11)
	return group_population.sort_values(canonical_block_group_geoid).reset_index(drop=True)


def build_final_scores(tract_scores: pd.DataFrame, block_group_population: pd.DataFrame) -> pd.DataFrame:
	"""Join tract scores to block groups and apply the zero-population null rule.

	Assumes `block_group_population` contains canonical columns produced by
	`prepare_block_group_population()`.
	"""
	merged = block_group_population.merge(tract_scores, on=TRACT_GEOID_COLUMN, how='left')
	positive_population_mask = merged[canonical_block_group_pop] > 0
	missing_positive_mask = positive_population_mask & merged[ANNUAL_AVERAGE_COLUMN].isna()
	if missing_positive_mask.any():
		missing_samples = merged.loc[missing_positive_mask, canonical_block_group_geoid].astype(str).head(5).tolist()
		raise RuntimeError(
			'Positive-population block groups are missing tract-level Ozone scores. '
			f'Sample block groups: {missing_samples}'
		)

	merged[FINAL_SCORE_COLUMN] = merged[ANNUAL_AVERAGE_COLUMN].astype('Float64')
	merged.loc[~positive_population_mask, FINAL_SCORE_COLUMN] = pd.NA
	return merged[[canonical_block_group_geoid, FINAL_SCORE_COLUMN]].copy()


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
	state_config: StateConfig,
	block_group_population: pd.DataFrame,
	final_scores: pd.DataFrame,
) -> None:
	total_tract_count = int(block_group_population[TRACT_GEOID_COLUMN].nunique(dropna=True))
	total_block_group_count = int(len(block_group_population))
	non_zero_block_group_count = int(block_group_population[canonical_block_group_pop].gt(0).sum())
	zero_population_block_group_count = int(block_group_population[canonical_block_group_pop].eq(0).sum())
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


def process_state(cfg: Config, state_config: StateConfig, tract_scores: pd.DataFrame, manifest: dict) -> None:
	"""Build and write one state's final Ozone block-group score file."""
	paths = resolve_paths(cfg, state_config, manifest)
	log_resolved_paths(paths, cfg)
	
	# Note that some code below was changed for processing our v0.6 configuration, where
	# we use only the whole-nation census block weight csv. But, because
	# the column names did not change, this code works equally well for the v0.5 configuration,
	# where the input file is one state at a time.
	# Choose per-version input column names (defaults are V1 names set above).
	bg_geoid_col = block_group_geoid_col
	bg_pop_col = block_group_pop_col
	if cfg.version_decimal is not None and cfg.version_decimal < Decimal('1.0'):
		# Before version 1.0, we used the block_group_pop column for 
		# determining null scores.
		#bg_geoid_col = 'block_group_geoid'
		bg_pop_col = 'block_group_pop'

	usecols = [state_abb_col, bg_geoid_col, bg_pop_col]

	block_weights_df = read_csv_s3_or_local(
		paths.census_block_weights_path,
		usecols=usecols,
		dtype={bg_geoid_col: 'string'},
	)

	# Require the explicit state column to exist
	if 'state_abb' not in block_weights_df.columns:
		raise RuntimeError(
			f"Census block weights CSV missing required column 'state_abb': {paths.census_block_weights_path}"
		)

	# Filter to rows for this state and fail if none found
	state_block_weights = block_weights_df.loc[block_weights_df[state_abb_col] == state_config.postal]
	if state_block_weights.empty:
		raise RuntimeError(
			f'No census block weights found for state {state_config.postal} in {paths.census_block_weights_path}'
		)

	# Normalize column names to canonical names used downstream
	state_block_weights = state_block_weights.rename(columns={bg_geoid_col: canonical_block_group_geoid, bg_pop_col: canonical_block_group_pop})

	# Prepare block-group population (single validator for all versions)
	block_group_population = prepare_block_group_population(state_block_weights)

	final_scores = build_final_scores(tract_scores, block_group_population)
	zero_population_count = int(block_group_population[canonical_block_group_pop].eq(0).sum())
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

	# Parse and validate version early; fail fast for versions we don't support yet.
	version_decimal = parse_version_decimal(cfg.version)
	cfg = replace(cfg, version_decimal=version_decimal)
	# Note that the following line should be updated every time the major version
	# number is incremented. The design plan is that minor version increments (e.g. 1.0 -> 1.1)
	# should happen when we have new data files but not new columns. 
	manifest_version_unsupported = Decimal('2.0')
	if cfg.version_decimal is not None and cfg.version_decimal >= manifest_version_unsupported:
		raise RuntimeError('Configured manifest version >= %s is not yet supported by this module' % manifest_version_unsupported)
	
	logging.info('Runtime config: version=%s storage_mode=%s state=%s output_dir=%s',
			 cfg.version_decimal, cfg.storage_mode, cfg.state or 'all', cfg.output_dir or 'None')
	initialize_runtime_dependencies(cfg)
	state_targets = load_state_targets(cfg.state)
	# Load the score-stage manifest once and reuse it for all path resolution
	manifest = build_manifest.get_stage_manifest(
		target_type='indicator',
		name='o3',
		stage='score',
		version=cfg.version,
		environment=cfg.storage_mode,
	)
	tract_entry = manifest.get('inputs', {}).get('preprocessed_tracts')
	if not tract_entry:
		raise RuntimeError('Score manifest missing required input: preprocessed_tracts')
	# Use the resolver to get an absolute indicator root (local) or raw remote root
	indicator_root = resolve_path.get_indicator_root('o3', cfg.version, cfg.storage_mode)
	tract_scores_path = join_root_and_relative_path(indicator_root, tract_entry['relative'])
	logging.info('Logging to %s', log_path)
	logging.info('Selected state filter: %s', cfg.state or 'all configured states')
	logging.info('Using tract scores path: %s', tract_scores_path)

	# If dry-run was requested, print resolved paths for the tract scores and
	# for each state's weights and outputs, then exit without reading/writing.
	if cfg.dry_run:
		print('DRY RUN: manifest and path resolution')
		print(f'tract_scores_path={tract_scores_path}')
		logging.info('DRY RUN tract_scores_path=%s', tract_scores_path)
		for state_config in state_targets:
			paths = resolve_paths(cfg, state_config, manifest)
			print(f'state={state_config.postal}')
			print(f'  census_block_weights_path={paths.census_block_weights_path}')
			print(f'  final_bg_scores_path={paths.final_bg_scores_path}')
			logging.info('DRY RUN state=%s', state_config.postal)
			logging.info('DRY RUN census_block_weights_path=%s', paths.census_block_weights_path)
			logging.info('DRY RUN final_bg_scores_path=%s', paths.final_bg_scores_path)
		return 0
	tract_scores_df = read_csv_s3_or_local(tract_scores_path, dtype={TRACT_GEOID_COLUMN: 'string'})
	prepared_tract_scores = prepare_tract_scores(tract_scores_df)
	logging.info('Prepared %d tract-level Ozone scores', len(prepared_tract_scores))

	for state_config in state_targets:
		# pass manifest into process_state via resolve_paths to avoid duplicate lookups
		process_state(cfg, state_config, prepared_tract_scores, manifest)

	state_count = len(state_targets)
	msg = f"Completed Ozone block-group indicator generation"
	if state_count == 1:
		msg += f" for {state_count} state: {state_targets[0].postal}"
	else:
		msg += f" for {state_count} states"
	logging.info(msg)
	print(msg)

	return 0


if __name__ == '__main__':
	raise SystemExit(main())