"""
hazardous_waste_proximity.py

Purpose:
	Run the hazardous-waste proximity pipeline for one state using the canonical
	hazardous-waste site CSV produced by hazardous_waste_preprocess.py and emit
	three CSV artifacts:
	- targeted_block_groups.csv
	- block_site_distances.csv
	- final_bg_scores.csv

Sample commandline:
	python scripts/hazardous_waste/hazardous_waste_proximity.py --state MT --input-path ./pipeline/test_data --output-path ./pipeline/test_data

Behavior summary:
	- Uses externalized state metadata shared with the Superfund pipeline.
	- Supports local paths or S3 URIs for inputs and outputs.
	- Defaults to the canonical hazardous-waste site file at
	outputs/hazardous_waste_filtered.csv under the configured input root.
	- Stages S3-hosted geospatial assets to a temporary local directory before
	reading them with GeoPandas.
	- Preserves the Superfund file structure where practical while adapting the
	workflow to hazardous-waste point sites.

Credits:
	- Superfund proximity reference pipeline by Anne Gunn, Gemini, and GitHub Copilot.
	- Hazardous-waste adaptation by GitHub Copilot, GPT-5.4, and Anne Gunn.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import argparse
import importlib
import io
import logging
import sys
import tempfile
from typing import Any

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv

try:
	boto3 = importlib.import_module('boto3')
except Exception:
	boto3 = None

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SHARED_STATE_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'state_config.py'


def _load_shared_state_config_symbols():
	if not SHARED_STATE_CONFIG_MODULE_PATH.exists():
		raise ImportError(f'Shared state_config.py not found: {SHARED_STATE_CONFIG_MODULE_PATH}')

	module_spec = importlib.util.spec_from_file_location(
		'shared_state_config',
		SHARED_STATE_CONFIG_MODULE_PATH,
	)
	if module_spec is None or module_spec.loader is None:
		raise ImportError(f'Unable to load module spec from {SHARED_STATE_CONFIG_MODULE_PATH}')

	module = importlib.util.module_from_spec(module_spec)
	sys.modules[module_spec.name] = module
	module_spec.loader.exec_module(module)
	return module.StateConfig, module.get_state_config, module.validate_metric_target_crs

try:
	from ..shared.state_config import StateConfig, get_state_config, validate_metric_target_crs
except ImportError:
	try:
		from shared.state_config import StateConfig, get_state_config, validate_metric_target_crs
	except ImportError:
		StateConfig, get_state_config, validate_metric_target_crs = _load_shared_state_config_symbols()

DEFAULT_LOCAL_PIPELINE_PATH = './pipeline/'
DEFAULT_S3_PIPELINE_PATH = 's3://pedp-data-preserved/ejscreen-data-processing/hazardous_waste/pipeline/'
DEFAULT_SHARED_LOCAL_PIPELINE_PATH = '../shared/pipeline/'
DEFAULT_SHARED_S3_PIPELINE_PATH = 's3://pedp-data-preserved/ejscreen-data-processing/shared/pipeline/'
DEFAULT_HAZARDOUS_WASTE_SITES_FILENAME = 'outputs/hazardous_waste_filtered.csv'
DEFAULT_BLOCK_GROUPS_FILENAME_TEMPLATE = 'downloads/tiger_lines/2020/bg/tl_2020_{fips}_bg.zip'
DEFAULT_CENSUS_BLOCKS_FILENAME_TEMPLATE = 'downloads/census_block_weights_2020/census_block_weights_2020_{postal}.csv'
DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME = 'targeted_block_groups.csv'
DEFAULT_BLOCK_SITE_DISTANCES_FILENAME = 'block_site_distances.csv'
DEFAULT_FINAL_BG_SCORES_FILENAME = 'final_bg_scores.csv'
DEFAULT_LOG_FILENAME = 'hwprox.log'
DEFAULT_BUFFER_METERS = 10000.0

HAZARDOUS_WASTE_HANDLER_ID_COLUMN = 'HANDLER ID'
HAZARDOUS_WASTE_STATE_COLUMN = 'LOCATION STATE'
HAZARDOUS_WASTE_LATITUDE_COLUMN = 'LOCATION LATITUDE'
HAZARDOUS_WASTE_LONGITUDE_COLUMN = 'LOCATION LONGITUDE'
BG_GEOID_COLUMN = 'GEOID'
BLOCKS_CSV_REQUIRED_COLUMNS = (
	'GEOID20',
	'INTPTLAT20',
	'INTPTLON20',
	'POP20',
	'block_group_geoid',
	'block_group_pop',
	'fraction_of_total',
)


@dataclass(slots=True)
class Config:
	state: str = 'VT'
	input_path: str = DEFAULT_LOCAL_PIPELINE_PATH
	output_path: str = DEFAULT_LOCAL_PIPELINE_PATH
	hazardous_waste_sites_path: str | None = None
	block_groups_path: str | None = None
	census_blocks_path: str | None = None
	output_dir: str | None = None
	targeted_block_groups_filename: str = DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME
	block_site_distances_filename: str = DEFAULT_BLOCK_SITE_DISTANCES_FILENAME
	final_bg_scores_filename: str = DEFAULT_FINAL_BG_SCORES_FILENAME
	buffer_meters: float = DEFAULT_BUFFER_METERS


@dataclass(frozen=True, slots=True)
class ResolvedPaths:
	state_config: Any
	hazardous_waste_sites_path: str
	block_groups_path: str
	census_blocks_path: str
	output_dir: str
	targeted_block_groups_path: str
	block_site_distances_path: str
	final_bg_scores_path: str


def get_config(argv=None) -> Config:
	load_dotenv()

	defaults = Config()
	parser = argparse.ArgumentParser(description='Run the hazardous-waste proximity pipeline for one state')
	parser.add_argument(
		'--state',
		dest='state',
		default=defaults.state,
		help=f'Two-letter state code to process (default: {defaults.state})',
	)
	parser.add_argument(
		'--input-path',
		dest='input_path',
		default=defaults.input_path,
		help=(
			'Base input folder or S3 prefix. '
			f'Default local: {defaults.input_path} | Example S3: {DEFAULT_S3_PIPELINE_PATH}'
		),
	)
	parser.add_argument(
		'--output-path',
		dest='output_path',
		default=defaults.output_path,
		help=(
			'Base output folder or S3 prefix. '
			f'Default local: {defaults.output_path} | Example S3: {DEFAULT_S3_PIPELINE_PATH}'
		),
	)
	parser.add_argument(
		'--hazardous-waste-sites-path',
		dest='hazardous_waste_sites_path',
		default=defaults.hazardous_waste_sites_path,
		help='Explicit local path or S3 URI for the canonical hazardous-waste CSV; overrides the derived default',
	)
	parser.add_argument(
		'--block-groups-path',
		dest='block_groups_path',
		default=defaults.block_groups_path,
		help='Explicit local path or S3 URI for the TIGER block-group dataset; overrides the derived default',
	)
	parser.add_argument(
		'--census-blocks-path',
		dest='census_blocks_path',
		default=defaults.census_blocks_path,
		help='Explicit local path or S3 URI for the census block weights CSV; overrides the derived default',
	)
	parser.add_argument(
		'--output-dir',
		dest='output_dir',
		default=defaults.output_dir,
		help='Explicit local path or S3 URI for the state output directory; overrides output-path + state postal code',
	)
	parser.add_argument(
		'--targeted-block-groups-filename',
		dest='targeted_block_groups_filename',
		default=defaults.targeted_block_groups_filename,
		help=f'Output filename for Step 1 results (default: {defaults.targeted_block_groups_filename})',
	)
	parser.add_argument(
		'--block-site-distances-filename',
		dest='block_site_distances_filename',
		default=defaults.block_site_distances_filename,
		help=f'Output filename for Step 2 results (default: {defaults.block_site_distances_filename})',
	)
	parser.add_argument(
		'--final-bg-scores-filename',
		dest='final_bg_scores_filename',
		default=defaults.final_bg_scores_filename,
		help=f'Output filename for Step 4 results (default: {defaults.final_bg_scores_filename})',
	)

	args = parser.parse_args(argv)
	return Config(
		state=args.state,
		input_path=args.input_path,
		output_path=args.output_path,
		hazardous_waste_sites_path=args.hazardous_waste_sites_path,
		block_groups_path=args.block_groups_path,
		census_blocks_path=args.census_blocks_path,
		output_dir=args.output_dir,
		targeted_block_groups_filename=args.targeted_block_groups_filename,
		block_site_distances_filename=args.block_site_distances_filename,
		final_bg_scores_filename=args.final_bg_scores_filename,
	)


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')


def join_path_and_file(path: str, filename: str) -> str:
	if is_s3_uri(path):
		return path.rstrip('/') + '/' + filename.lstrip('/')
	return str(Path(path) / filename)


def parse_s3_uri(path: str) -> tuple[str, str]:
	if not is_s3_uri(path):
		raise ValueError(f'Expected S3 URI, got: {path}')

	tail = path[5:]
	parts = tail.split('/', 1)
	if len(parts) != 2 or not parts[0] or not parts[1]:
		raise ValueError(f'Invalid S3 URI: {path}')
	return parts[0], parts[1]


def get_s3_client():
	if boto3 is None:
		raise RuntimeError('boto3 not available; cannot access S3 paths')
	return boto3.client('s3')


def read_csv_s3_or_local(path: str, **read_csv_kwargs) -> pd.DataFrame:
	if is_s3_uri(path):
		bucket, key = parse_s3_uri(path)
		try:
			s3 = get_s3_client()
			obj = s3.get_object(Bucket=bucket, Key=key)
			return pd.read_csv(io.BytesIO(obj['Body'].read()), **read_csv_kwargs)
		except Exception as exc:
			raise RuntimeError(f'Failed to read S3 CSV {path}: {exc}') from exc

	local_path = Path(path)
	if not local_path.exists():
		raise FileNotFoundError(f'Local CSV not found: {path}')
	return pd.read_csv(local_path, **read_csv_kwargs)


def write_df_s3_or_local(df: pd.DataFrame, out_path: str) -> None:
	if is_s3_uri(out_path):
		bucket, key = parse_s3_uri(out_path)
		try:
			s3 = get_s3_client()
			csv_text = df.to_csv(index=False)
			s3.put_object(Bucket=bucket, Key=key, Body=csv_text.encode('utf-8'))
			return
		except Exception as exc:
			raise RuntimeError(f'Failed to write CSV to S3 {out_path}: {exc}') from exc

	local_path = Path(out_path)
	local_path.parent.mkdir(parents=True, exist_ok=True)
	df.to_csv(local_path, index=False)


def _require_existing_local_path(path_value: str, path_name: str) -> Path:
	if not path_value:
		raise RuntimeError(f'{path_name} is not configured')

	path = Path(path_value).expanduser()
	if not path.exists():
		raise RuntimeError(f'{path_name} does not exist: {path}')
	return path


def _download_s3_object(bucket: str, key: str, destination: Path) -> Path:
	destination.parent.mkdir(parents=True, exist_ok=True)
	s3 = get_s3_client()
	try:
		s3.download_file(bucket, key, str(destination))
	except Exception as exc:
		raise RuntimeError(f'Failed to download s3://{bucket}/{key} to {destination}: {exc}') from exc
	return destination


def _download_s3_prefix(bucket: str, prefix: str, destination_dir: Path) -> Path:
	paginator = get_s3_client().get_paginator('list_objects_v2')
	found_any = False

	for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
		for obj in page.get('Contents', []):
			object_key = obj.get('Key')
			if not object_key or object_key.endswith('/'):
				continue

			relative_key = object_key[len(prefix):]
			if not relative_key:
				continue

			local_path = destination_dir.joinpath(*PurePosixPath(relative_key).parts)
			local_path.parent.mkdir(parents=True, exist_ok=True)
			try:
				get_s3_client().download_file(bucket, object_key, str(local_path))
			except Exception as exc:
				raise RuntimeError(
					f'Failed to download s3://{bucket}/{object_key} to {local_path}: {exc}'
				) from exc
			found_any = True

	if not found_any:
		raise FileNotFoundError(f'No S3 objects found under prefix s3://{bucket}/{prefix}')
	return destination_dir


def stage_geospatial_input(source_path: str, staging_root: Path, path_name: str) -> Path:
	if not is_s3_uri(source_path):
		return _require_existing_local_path(source_path, path_name)

	bucket, key = parse_s3_uri(source_path)
	normalized_key = key.rstrip('/')
	key_name = PurePosixPath(normalized_key).name

	destination = staging_root / key_name
	logging.info('Staging S3 geospatial file %s to %s', source_path, destination)
	return _download_s3_object(bucket, key, destination)


def resolve_pipeline_paths(cfg: Config) -> ResolvedPaths:
	state_config = get_state_config(cfg.state)

	hazardous_waste_sites_path = cfg.hazardous_waste_sites_path or join_path_and_file(
		cfg.input_path,
		DEFAULT_HAZARDOUS_WASTE_SITES_FILENAME,
	)
	shared_downloads_input_path = (
		DEFAULT_SHARED_S3_PIPELINE_PATH if is_s3_uri(cfg.input_path) else DEFAULT_SHARED_LOCAL_PIPELINE_PATH
	)
	block_groups_path = cfg.block_groups_path or join_path_and_file(
		shared_downloads_input_path,
		DEFAULT_BLOCK_GROUPS_FILENAME_TEMPLATE.format(
			fips=state_config.fips,
			postal=state_config.postal,
			name=state_config.name,
		),
	)
	census_blocks_path = cfg.census_blocks_path or join_path_and_file(
		shared_downloads_input_path,
		DEFAULT_CENSUS_BLOCKS_FILENAME_TEMPLATE.format(
			fips=state_config.fips,
			postal=state_config.postal,
			name=state_config.name,
		),
	)
	output_dir = cfg.output_dir or join_path_and_file(cfg.output_path, state_config.postal)

	return ResolvedPaths(
		state_config=state_config,
		hazardous_waste_sites_path=hazardous_waste_sites_path,
		block_groups_path=block_groups_path,
		census_blocks_path=census_blocks_path,
		output_dir=output_dir,
		targeted_block_groups_path=join_path_and_file(output_dir, cfg.targeted_block_groups_filename),
		block_site_distances_path=join_path_and_file(output_dir, cfg.block_site_distances_filename),
		final_bg_scores_path=join_path_and_file(output_dir, cfg.final_bg_scores_filename),
	)


def _require_columns(df: pd.DataFrame, required_columns: tuple[str, ...], description: str) -> None:
	missing_columns = [column for column in required_columns if column not in df.columns]
	if missing_columns:
		raise RuntimeError(f'{description} is missing required columns: {", ".join(missing_columns)}')


def _prepare_block_population_columns(blocks_df: pd.DataFrame) -> pd.DataFrame:
	prepared = blocks_df.copy()
	prepared['block_group_geoid'] = prepared['block_group_geoid'].astype('string').str.strip()
	prepared['block_geoid'] = prepared['block_geoid'].astype('string').str.strip()
	prepared['block_group_pop'] = pd.to_numeric(prepared['block_group_pop'], errors='raise')
	prepared['fraction_of_total'] = pd.to_numeric(prepared['fraction_of_total'], errors='raise')
	return prepared


def _build_block_group_population_table(blocks_df: pd.DataFrame) -> pd.DataFrame:
	group_population = blocks_df[['block_group_geoid', 'block_group_pop']].copy()
	group_population = group_population[
		group_population['block_group_geoid'].notna() & group_population['block_group_geoid'].ne('')
	]

	population_variants = group_population.groupby('block_group_geoid', dropna=True)['block_group_pop'].nunique()
	inconsistent_groups = population_variants[population_variants > 1]
	if not inconsistent_groups.empty:
		sample_groups = ', '.join(inconsistent_groups.index.astype(str).tolist()[:5])
		raise RuntimeError(
			'Blocks GeoDataFrame has inconsistent block_group_pop values within block groups: '
			f'{sample_groups}'
		)

	return group_population.drop_duplicates(subset=['block_group_geoid']).reset_index(drop=True)


def apply_metric_projection(
	description: str,
	input_proj: gpd.GeoDataFrame,
	metric_crs: str,
	state_fips: str,
) -> gpd.GeoDataFrame:
	target_crs = validate_metric_target_crs(metric_crs, f'target CRS for {description}')

	if input_proj.crs is None:
		raise RuntimeError(f'{description} has no source CRS defined')

	original_crs = input_proj.crs.name or str(input_proj.crs)
	original_epsg = input_proj.crs.to_epsg()
	logging.info(
		'PROJECTION LOG: %s | State FIPS %s | Source CRS: %s (EPSG:%s) -> Target CRS: %s',
		description,
		state_fips,
		original_crs,
		original_epsg if original_epsg is not None else 'N/A',
		target_crs,
	)
	try:
		return input_proj.to_crs(target_crs)
	except Exception as exc:
		raise RuntimeError(f'Failed to reproject {description} to {target_crs}: {exc}') from exc


def configure_logging() -> str:
	log_path = Path.cwd() / DEFAULT_LOG_FILENAME
	log_path.parent.mkdir(parents=True, exist_ok=True)
	logging.basicConfig(
		level=logging.INFO,
		format='%(levelname)s: %(message)s',
		handlers=[
			logging.FileHandler(log_path, mode='a', encoding='utf-8'),
		],
		force=True,
	)
	logging.info('========== Log session started %s ==========', datetime.now().astimezone().isoformat(timespec='seconds'))
	return str(log_path)


def _read_block_groups_geodataframe(bg_path: Path) -> gpd.GeoDataFrame:
	candidates = [str(bg_path)]
	if bg_path.suffix.lower() == '.zip':
		candidates.append(f'zip://{bg_path.as_posix()}')

	last_error = None
	for candidate in dict.fromkeys(candidates):
		try:
			return gpd.read_file(candidate)
		except Exception as exc:
			last_error = exc

	raise RuntimeError(f'Failed to read block-group data from {bg_path}: {last_error}')


def step0_prepare_inputs(
	paths: ResolvedPaths,
	staging_root: Path,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
	state_config = paths.state_config

	logging.info('##############################################################')
	logging.info('#### Starting Step0: preparing projected input datasets')

	bg_path = stage_geospatial_input(paths.block_groups_path, staging_root / 'block_groups', 'block_groups_path')

	logging.info('Reading canonical hazardous-waste sites CSV from %s', paths.hazardous_waste_sites_path)
	try:
		hazardous_waste_df = read_csv_s3_or_local(paths.hazardous_waste_sites_path, dtype=str)
	except Exception as exc:
		raise RuntimeError(
			f'Failed to read hazardous-waste sites CSV from {paths.hazardous_waste_sites_path}: {exc}'
		) from exc
	_require_columns(
		hazardous_waste_df,
		(
			HAZARDOUS_WASTE_HANDLER_ID_COLUMN,
			HAZARDOUS_WASTE_LATITUDE_COLUMN,
			HAZARDOUS_WASTE_LONGITUDE_COLUMN,
		),
		'hazardous-waste sites CSV',
	)

	hazardous_waste_df = hazardous_waste_df.copy()
	hazardous_waste_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN] = (
		hazardous_waste_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip()
	)
	if HAZARDOUS_WASTE_STATE_COLUMN in hazardous_waste_df.columns:
		hazardous_waste_df[HAZARDOUS_WASTE_STATE_COLUMN] = (
			hazardous_waste_df[HAZARDOUS_WASTE_STATE_COLUMN].astype(str).str.strip().str.upper()
		)
	else:
		logging.info(
			'Hazardous-waste sites CSV does not include %s; continuing because the current proximity flow does not require it',
			HAZARDOUS_WASTE_STATE_COLUMN,
		)
		hazardous_waste_df[HAZARDOUS_WASTE_STATE_COLUMN] = ''
	logging.info(
		'Canonical hazardous-waste sites available nationwide for state %s processing: %d',
		state_config.postal,
		len(hazardous_waste_df),
	)

	if hazardous_waste_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].eq('').any():
		raise RuntimeError('Hazardous-waste sites CSV contains blank HANDLER ID values')

	try:
		hazardous_waste_df['_site_latitude'] = pd.to_numeric(
			hazardous_waste_df[HAZARDOUS_WASTE_LATITUDE_COLUMN],
			errors='raise',
		)
		hazardous_waste_df['_site_longitude'] = pd.to_numeric(
			hazardous_waste_df[HAZARDOUS_WASTE_LONGITUDE_COLUMN],
			errors='raise',
		)
	except Exception as exc:
		raise RuntimeError(f'Hazardous-waste sites CSV contains invalid numeric coordinates: {exc}') from exc

	hazardous_waste_gdf = gpd.GeoDataFrame(
		hazardous_waste_df,
		geometry=gpd.points_from_xy(
			hazardous_waste_df['_site_longitude'],
			hazardous_waste_df['_site_latitude'],
		),
		crs='EPSG:4269',
	)
	hazardous_waste_gdf = apply_metric_projection(
		'hazardous-waste sites dataset',
		hazardous_waste_gdf,
		state_config.metric_crs,
		state_config.fips,
	)

	logging.info('Reading block-group dataset from %s', paths.block_groups_path)
	bg_gdf = _read_block_groups_geodataframe(bg_path)
	_require_columns(bg_gdf, (BG_GEOID_COLUMN, 'geometry'), 'block-group data')
	bg_gdf = apply_metric_projection('block-group dataset', bg_gdf, state_config.metric_crs, state_config.fips)

	logging.info('Reading blocks CSV from %s', paths.census_blocks_path)
	try:
		blocks_df = read_csv_s3_or_local(paths.census_blocks_path, dtype=str)
	except Exception as exc:
		raise RuntimeError(f'Failed to read blocks CSV from {paths.census_blocks_path}: {exc}') from exc
	_require_columns(blocks_df, BLOCKS_CSV_REQUIRED_COLUMNS, 'blocks CSV')

	blocks_df = blocks_df.copy()
	blocks_df['block_geoid'] = blocks_df['GEOID20'].astype(str).str.strip()
	blocks_df['block_group_geoid'] = blocks_df['block_group_geoid'].astype(str).str.strip()
	if blocks_df['block_geoid'].eq('').any():
		raise RuntimeError('Blocks CSV contains blank GEOID20 values')
	if blocks_df['block_group_geoid'].eq('').any():
		raise RuntimeError('Blocks CSV contains blank block_group_geoid values')

	try:
		blocks_df['block_lat'] = pd.to_numeric(blocks_df['INTPTLAT20'], errors='raise')
		blocks_df['block_lon'] = pd.to_numeric(blocks_df['INTPTLON20'], errors='raise')
		blocks_df['block_pop'] = pd.to_numeric(blocks_df['POP20'], errors='raise')
		blocks_df['block_group_pop'] = pd.to_numeric(blocks_df['block_group_pop'], errors='raise')
		blocks_df['fraction_of_total'] = pd.to_numeric(blocks_df['fraction_of_total'], errors='raise')
	except Exception as exc:
		raise RuntimeError(f'Blocks CSV contains invalid numeric values: {exc}') from exc

	blocks_gdf = gpd.GeoDataFrame(
		blocks_df,
		geometry=gpd.points_from_xy(blocks_df['block_lon'], blocks_df['block_lat']),
		crs='EPSG:4269',
	)
	blocks_gdf = apply_metric_projection('blocks dataset', blocks_gdf, state_config.metric_crs, state_config.fips)

	return hazardous_waste_gdf, bg_gdf, blocks_gdf


def step1_buffer_and_targeted_bgs(
	hazardous_waste_gdf: gpd.GeoDataFrame,
	bg_gdf: gpd.GeoDataFrame,
	buffer_meters: float = DEFAULT_BUFFER_METERS,
	output_path: str | None = None,
) -> pd.DataFrame:
	if hazardous_waste_gdf is None:
		raise RuntimeError('hazardous_waste_gdf is required')
	if bg_gdf is None:
		raise RuntimeError('bg_gdf is required')

	_require_columns(
		hazardous_waste_gdf,
		(HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'geometry'),
		'Hazardous-waste GeoDataFrame',
	)
	_require_columns(bg_gdf, (BG_GEOID_COLUMN, 'geometry'), 'Block-group GeoDataFrame')

	logging.info('##############################################################')
	logging.info('#### Starting Step1: buffering projected hazardous-waste sites and targeting block groups')
	logging.info('Hazardous-waste columns: %d, rows: %d', len(hazardous_waste_gdf.columns), len(hazardous_waste_gdf))
	logging.info('Block Group columns: %d, rows: %d', len(bg_gdf.columns), len(bg_gdf))

	if hazardous_waste_gdf.empty:
		logging.info('No hazardous-waste sites found for this state; Step 1 will return no targeted block groups')
		targeted = pd.DataFrame(columns=['GEOID_BG', HAZARDOUS_WASTE_HANDLER_ID_COLUMN])
	else:
		logging.info('Buffering hazardous-waste point sites by %s meters', buffer_meters)
		hazardous_waste_gdf = hazardous_waste_gdf.copy()
		hazardous_waste_gdf['geometry_buffer'] = hazardous_waste_gdf.geometry.buffer(buffer_meters)
		hazardous_waste_buffer = hazardous_waste_gdf.set_geometry('geometry_buffer')

		logging.info('Performing spatial join to find targeted block groups')
		joined = gpd.sjoin(bg_gdf, hazardous_waste_buffer, how='inner', predicate='intersects')
		if joined.empty:
			logging.info('No block groups found within buffer distance')
			targeted = pd.DataFrame(columns=['GEOID_BG', HAZARDOUS_WASTE_HANDLER_ID_COLUMN])
		else:
			all_handler_ids = set(hazardous_waste_gdf[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip())
			joined_handler_ids = set(joined[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip())
			joined_bg_count = joined[BG_GEOID_COLUMN].astype(str).nunique()
			handler_bg_counts = (
				joined[[HAZARDOUS_WASTE_HANDLER_ID_COLUMN, BG_GEOID_COLUMN]]
				.drop_duplicates()
				.groupby(HAZARDOUS_WASTE_HANDLER_ID_COLUMN)
				.size()
				.sort_values(ascending=False)
			)
			logging.info(
				'Step1 join summary: joined_rows=%d unique_block_groups=%d unique_handlers=%d handlers_with_zero_matches=%d final_pairs_pending_dedup=%d',
				len(joined),
				joined_bg_count,
				len(joined_handler_ids),
				len(all_handler_ids - joined_handler_ids),
				len(joined[[BG_GEOID_COLUMN, HAZARDOUS_WASTE_HANDLER_ID_COLUMN]].drop_duplicates()),
			)
			logging.info(
				'Step1 top handler match counts: %s',
				handler_bg_counts.head(5).to_dict(),
			)
			targeted = joined[[BG_GEOID_COLUMN, HAZARDOUS_WASTE_HANDLER_ID_COLUMN]].drop_duplicates().copy()
			targeted = targeted.rename(columns={BG_GEOID_COLUMN: 'GEOID_BG'})
			targeted[HAZARDOUS_WASTE_HANDLER_ID_COLUMN] = (
				targeted[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip()
			)

	if output_path:
		logging.info('Writing targeted block groups to %s', output_path)
		write_df_s3_or_local(targeted[['GEOID_BG', HAZARDOUS_WASTE_HANDLER_ID_COLUMN]], output_path)

	return targeted


def step2_block_site_distances(
	targeted_df: pd.DataFrame,
	blocks_gdf: gpd.GeoDataFrame,
	hazardous_waste_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
	if targeted_df is None:
		raise RuntimeError('targeted_df is required')
	if blocks_gdf is None:
		raise RuntimeError('blocks_gdf is required')
	if hazardous_waste_gdf is None:
		raise RuntimeError('hazardous_waste_gdf is required')

	_require_columns(targeted_df, ('GEOID_BG', HAZARDOUS_WASTE_HANDLER_ID_COLUMN), 'targeted_df')
	_require_columns(blocks_gdf, ('block_geoid', 'block_group_geoid', 'geometry'), 'Blocks GeoDataFrame')
	_require_columns(hazardous_waste_gdf, (HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'geometry'), 'Hazardous-waste GeoDataFrame')
	if blocks_gdf.crs is None or hazardous_waste_gdf.crs is None:
		raise RuntimeError('Projected inputs must have CRS defined before distance calculations')
	if blocks_gdf.crs != hazardous_waste_gdf.crs:
		raise RuntimeError(
			'Blocks and hazardous-waste inputs must share the same projected CRS: '
			f'blocks={blocks_gdf.crs}, hazardous_waste={hazardous_waste_gdf.crs}'
		)

	logging.info('##############################################################')
	logging.info('#### Starting Step2: computing block-site distances')

	if targeted_df.empty:
		logging.info('No targeted block groups were identified; Step 2 will return no distance records')
		return pd.DataFrame(columns=['GEOID_BLOCK', HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'distance_m'])

	targeted_df = targeted_df.copy()
	targeted_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN] = (
		targeted_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip()
	)
	targeted_df['GEOID_BG'] = targeted_df['GEOID_BG'].astype(str).str.strip()
	blocks_df = pd.DataFrame(blocks_gdf.drop(columns='geometry', errors='ignore')).copy()
	_require_columns(
		blocks_df,
		('block_geoid', 'block_group_geoid', 'block_group_pop', 'fraction_of_total'),
		'Blocks GeoDataFrame',
	)
	blocks_df = _prepare_block_population_columns(blocks_df)
	block_group_population = _build_block_group_population_table(blocks_df)
	zero_population_bgs = set(
		block_group_population.loc[
			block_group_population['block_group_pop'] <= 0,
			'block_group_geoid',
		].astype(str)
	)
	zero_population_pairs = int(targeted_df['GEOID_BG'].isin(zero_population_bgs).sum())
	if zero_population_pairs:
		logging.info(
			'Step2 skipping %d targeted block-group/site pairs because block_group_pop is zero',
			zero_population_pairs,
		)
		targeted_df = targeted_df[~targeted_df['GEOID_BG'].isin(zero_population_bgs)].copy()

	if targeted_df.empty:
		logging.info('All targeted block groups have zero population; Step 2 will return no distance records')
		return pd.DataFrame(columns=['GEOID_BLOCK', HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'distance_m'])

	targeted_pair_count = len(targeted_df)
	unique_targeted_handlers = targeted_df[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).nunique()
	unique_targeted_bgs = targeted_df['GEOID_BG'].astype(str).nunique()
	logging.info(
		'Step2 targeted input summary: targeted_pairs=%d unique_block_groups=%d unique_handlers=%d',
		targeted_pair_count,
		unique_targeted_bgs,
		unique_targeted_handlers,
	)
	target_bgs = set(targeted_df['GEOID_BG'].astype(str).unique())
	gdf_blocks_sub = blocks_gdf[blocks_gdf['block_group_geoid'].astype(str).isin(target_bgs)].copy()
	logging.info('Blocks in targeted block groups: %d (of %d)', len(gdf_blocks_sub), len(blocks_gdf))

	if gdf_blocks_sub.empty:
		logging.info('No blocks found in targeted block groups; nothing to do for Step 2')
		return pd.DataFrame(columns=['GEOID_BLOCK', HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'distance_m'])

	hazardous_waste_lookup = hazardous_waste_gdf[[HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'geometry']].copy()
	hazardous_waste_lookup[HAZARDOUS_WASTE_HANDLER_ID_COLUMN] = (
		hazardous_waste_lookup[HAZARDOUS_WASTE_HANDLER_ID_COLUMN].astype(str).str.strip()
	)
	hazardous_waste_map = {
		row[HAZARDOUS_WASTE_HANDLER_ID_COLUMN]: row.geometry
		for _, row in hazardous_waste_lookup.iterrows()
	}
	logging.info('Hazardous-waste handler keys: %d', len(hazardous_waste_map))

	records = []
	blocks_by_bg = {bg: df for bg, df in gdf_blocks_sub.groupby('block_group_geoid')}

	rows_processed = 0
	ids_found = 0
	ids_not_found = 0
	pairs_missing_blocks = 0

	for _, row in targeted_df.iterrows():
		rows_processed += 1
		bg = str(row['GEOID_BG'])
		handler_id = str(row[HAZARDOUS_WASTE_HANDLER_ID_COLUMN])
		if bg not in blocks_by_bg:
			pairs_missing_blocks += 1
			continue

		blocks = blocks_by_bg[bg]
		handler_key = handler_id.strip()
		site_geometry = hazardous_waste_map.get(handler_key)
		if site_geometry is None:
			if ids_not_found < 5:
				logging.warning('HANDLER ID %s not found in hazardous-waste sites; skipping', handler_id)
			ids_not_found += 1
			continue

		ids_found += 1
		for _, block_row in blocks.iterrows():
			try:
				distance_m = block_row.geometry.distance(site_geometry)
			except Exception as exc:
				logging.error(
					'Distance computation failed for block %s to HANDLER ID %s: %s',
					block_row.get('block_geoid'),
					handler_id,
					exc,
				)
				distance_m = float('nan')

			records.append(
				{
					'GEOID_BLOCK': block_row['block_geoid'],
					HAZARDOUS_WASTE_HANDLER_ID_COLUMN: handler_id,
					'distance_m': distance_m if pd.notna(distance_m) else None,
				}
			)

	logging.info(
		'Step2 summary: rows_processed=%d, ids_found=%d, ids_not_found=%d, pairs_missing_blocks=%d',
		rows_processed,
		ids_found,
		ids_not_found,
		pairs_missing_blocks,
	)

	distances_df = pd.DataFrame.from_records(
		records,
		columns=['GEOID_BLOCK', HAZARDOUS_WASTE_HANDLER_ID_COLUMN, 'distance_m'],
	)
	unique_block_handler_pairs = len(
		distances_df[['GEOID_BLOCK', HAZARDOUS_WASTE_HANDLER_ID_COLUMN]].drop_duplicates()
	)
	logging.info(
		'Step2 output summary: distance_records=%d unique_block_handler_pairs=%d',
		len(distances_df),
		unique_block_handler_pairs,
	)
	logging.info('Step2 produced %d distance records (distance in meters)', len(distances_df))
	return distances_df


def step3_inverse_distance_scoring(
	distances_df: pd.DataFrame,
	output_path: str | None = None,
) -> pd.DataFrame:
	if distances_df is None:
		raise RuntimeError('distances_df is required')
	if 'distance_m' not in distances_df.columns:
		raise RuntimeError("Expected 'distance_m' column in distances DataFrame")

	distances_df = distances_df.copy()

	def calculate_proximity_score(distance_m):
		try:
			if pd.isna(distance_m):
				return None
			distance_m = float(distance_m)
		except Exception:
			return None

		distance_km = distance_m / 1000.0
		if distance_km < 0.1:
			return 10.0
		return float(1.0 / distance_km)

	distances_df['proximity_score'] = distances_df['distance_m'].apply(calculate_proximity_score)

	if output_path:
		cols = [column for column in distances_df.columns if column != 'proximity_score'] + ['proximity_score']
		logging.info('Writing block-site distances with proximity_score to %s (rows=%d)', output_path, len(distances_df))
		write_df_s3_or_local(distances_df[cols], output_path)

	return distances_df


def step4_population_weighting_aggregation(
	distances_with_scores_df: pd.DataFrame,
	blocks_gdf: gpd.GeoDataFrame,
	output_path: str | None = None,
) -> pd.DataFrame:
	if distances_with_scores_df is None:
		raise RuntimeError('distances_with_scores_df is required')
	if blocks_gdf is None:
		raise RuntimeError('blocks_gdf is required')
	if 'GEOID_BLOCK' not in distances_with_scores_df.columns:
		raise RuntimeError("Expected 'GEOID_BLOCK' column in distances DataFrame")
	if 'proximity_score' not in distances_with_scores_df.columns:
		raise RuntimeError("Expected 'proximity_score' column in distances DataFrame")

	blocks_df = pd.DataFrame(blocks_gdf.drop(columns='geometry', errors='ignore')).copy()
	_require_columns(
		blocks_df,
		('block_geoid', 'block_group_geoid', 'block_group_pop', 'fraction_of_total'),
		'Blocks GeoDataFrame',
	)

	distances_with_scores_df = distances_with_scores_df.copy()
	distances_with_scores_df['proximity_score'] = pd.to_numeric(
		distances_with_scores_df['proximity_score'],
		errors='coerce',
	)

	blocks_df = _prepare_block_population_columns(blocks_df)
	block_group_population = _build_block_group_population_table(blocks_df)

	all_block_groups = block_group_population.copy()
	logging.info('Prepared %d block groups from blocks source for final output universe', len(all_block_groups))

	merged = distances_with_scores_df.merge(
		blocks_df[['block_geoid', 'block_group_geoid', 'fraction_of_total']],
		left_on='GEOID_BLOCK',
		right_on='block_geoid',
		how='left',
	)
	merged = merged[merged['proximity_score'].notna()].copy()
	merged['fraction_of_total'] = pd.to_numeric(merged['fraction_of_total'], errors='coerce').fillna(0.0)
	merged['weighted_score'] = merged['proximity_score'].astype(float) * merged['fraction_of_total'].astype(float)

	agg_targeted = merged.groupby('block_group_geoid', dropna=True)['weighted_score'].sum().reset_index()
	agg_targeted['weighted_score'] = agg_targeted['weighted_score'].round(4)

	agg = all_block_groups.merge(agg_targeted, on='block_group_geoid', how='left')
	positive_population_mask = agg['block_group_pop'] > 0
	agg.loc[positive_population_mask, 'weighted_score'] = (
		agg.loc[positive_population_mask, 'weighted_score'].fillna(0.0)
	)

	if output_path:
		logging.info(
			'Writing final block-group scores to %s (rows=%d, targeted_groups=%d)',
			output_path,
			len(agg),
			len(agg_targeted),
		)
		write_df_s3_or_local(agg[['block_group_geoid', 'weighted_score']], output_path)

	return agg


def log_resolved_paths(paths: ResolvedPaths, cfg: Config) -> None:
	logging.info(
		'State: %s (%s) | FIPS: %s | Metric CRS: %s',
		paths.state_config.name,
		paths.state_config.postal,
		paths.state_config.fips,
		paths.state_config.metric_crs,
	)
	logging.info('Input base path: %s', cfg.input_path)
	logging.info('Output base path: %s', cfg.output_path)
	logging.info('Resolved hazardous-waste sites path: %s', paths.hazardous_waste_sites_path)
	logging.info('Resolved block-groups path: %s', paths.block_groups_path)
	logging.info('Resolved census blocks path: %s', paths.census_blocks_path)
	logging.info('Resolved output dir: %s', paths.output_dir)
	logging.info('Step 1 output: %s', paths.targeted_block_groups_path)
	logging.info('Step 3 output: %s', paths.block_site_distances_path)
	logging.info('Step 4 output: %s', paths.final_bg_scores_path)


def main(argv=None) -> int:
	
	logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
	
	print("\n", "*"*20, "\nHazardous-waste proximity pipeline processing started")
	logging.info('Hazardous-waste proximity pipeline processing started')


	try:
		cfg = get_config(argv)
		paths = resolve_pipeline_paths(cfg)
		log_path = configure_logging()
		logging.info('Logging to %s', log_path)
		log_resolved_paths(paths, cfg)
	except Exception as exc:
		logging.error('Failed to resolve pipeline configuration: %s', exc)
		return 1

	try:
		with tempfile.TemporaryDirectory(prefix=f'hazardous_waste_{paths.state_config.postal.lower()}_') as temp_dir_name:
			staging_root = Path(temp_dir_name)
			print('Starting Step0: prepare inputs')
			hazardous_waste_gdf, bg_gdf, blocks_gdf = step0_prepare_inputs(paths, staging_root)
			print('Completed Step0: prepare inputs')
			print('Starting Step1: target block groups')
			targeted_df = step1_buffer_and_targeted_bgs(
				hazardous_waste_gdf=hazardous_waste_gdf,
				bg_gdf=bg_gdf,
				buffer_meters=cfg.buffer_meters,
				output_path=paths.targeted_block_groups_path,
			)
			print('Completed Step1: target block groups')
			print('Starting Step2: compute block-site distances')
			distances_df = step2_block_site_distances(
				targeted_df=targeted_df,
				blocks_gdf=blocks_gdf,
				hazardous_waste_gdf=hazardous_waste_gdf,
			)
			print('Completed Step2: compute block-site distances')
			print('Starting Step3: inverse distance scoring')
			distances_with_scores_df = step3_inverse_distance_scoring(
				distances_df,
				output_path=paths.block_site_distances_path,
			)
			print('Completed Step3: inverse distance scoring')
			print('Starting Step4: population weighting aggregation')
			step4_population_weighting_aggregation(
				distances_with_scores_df,
				blocks_gdf=blocks_gdf,
				output_path=paths.final_bg_scores_path,
			)
			print('Completed Step4: population weighting aggregation')
	except Exception as exc:
		logging.exception('Hazardous-waste proximity pipeline failed: %s', exc)
		return 1

	logging.info('Hazardous-waste proximity pipeline completed successfully')
	print('Hazardous-waste proximity pipeline completed successfully')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
