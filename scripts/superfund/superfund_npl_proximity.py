"""
superfund_npl_proximity.py

Purpose:
  Run the Superfund NPL proximity pipeline for one state and emit three
  CSV artifacts:
    - targeted_block_groups.csv (intermediate output from Step 1)
        - block_site_distances.csv (distance and proximity-score output written in Step 3)
    - final_bg_scores.csv (final output from Step 4)

Behavior summary:
    - Uses externalized state metadata, shared path configuration, and
        Superfund path configuration to resolve state-specific inputs and outputs.
    - Supports local paths or S3 URIs for the NPL input, shared inputs, and
        output directory.
  - Stages S3-hosted geospatial assets to a temporary local directory before
    reading them with Fiona or GeoPandas.
    - Computes targeted block groups, block-to-site distances, inverse-distance
        proximity scores, and final population-weighted block-group scores for one
        state at a time.

Sample commands:
    - Local output:
        python ./superfund_npl_proximity.py local --state MT
    - S3 output:
        python ./superfund_npl_proximity.py remote --state MT

Credits:
  - Prototype scoring logic by Anne Gunn, Gemini, and GitHub Copilot.
  - Refactor into the formal pipeline shape by GitHub Copilot, GPT-5.4 and Anne Gunn.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import argparse
import importlib
import io
import logging
import sys
import tempfile
from typing import Any

import fiona
import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv

try:
    boto3 = importlib.import_module('boto3')
except Exception:
    boto3 = None


def _resolve_scripts_dir() -> Path:
    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        if parent.name == 'scripts':
            return parent
    raise RuntimeError(f'Unable to locate scripts directory from {current_path}')


SCRIPTS_DIR = _resolve_scripts_dir()
SUPERFUND_DIR = Path(__file__).resolve().parent
SHARED_STATE_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'state_config.py'
SHARED_PATHS_CONFIG_MODULE_PATH = SCRIPTS_DIR / 'shared' / 'shared_paths_config.py'
SUPERFUND_PATHS_CONFIG_MODULE_PATH = SUPERFUND_DIR / 'superfund_paths_config.py'


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


def _load_shared_paths_config_symbols():
    if not SHARED_PATHS_CONFIG_MODULE_PATH.exists():
        raise ImportError(f'Shared shared_paths_config.py not found: {SHARED_PATHS_CONFIG_MODULE_PATH}')

    module_spec = importlib.util.spec_from_file_location(
        'shared_paths_config_superfund',
        SHARED_PATHS_CONFIG_MODULE_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f'Unable to load module spec from {SHARED_PATHS_CONFIG_MODULE_PATH}')

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module.get_shared_paths_config, module.resolve_local_shared_root_path


def _load_superfund_paths_config_symbols():
    if not SUPERFUND_PATHS_CONFIG_MODULE_PATH.exists():
        raise ImportError(f'Superfund superfund_paths_config.py not found: {SUPERFUND_PATHS_CONFIG_MODULE_PATH}')

    module_spec = importlib.util.spec_from_file_location(
        'superfund_paths_config',
        SUPERFUND_PATHS_CONFIG_MODULE_PATH,
    )
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f'Unable to load module spec from {SUPERFUND_PATHS_CONFIG_MODULE_PATH}')

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module.get_superfund_paths_config, module.resolve_local_superfund_root_path

try:
    from ..shared.state_config import StateConfig, get_state_config, validate_metric_target_crs
except ImportError:
    try:
        from ...shared.state_config import StateConfig, get_state_config, validate_metric_target_crs
    except ImportError:
        try:
            from shared.state_config import StateConfig, get_state_config, validate_metric_target_crs
        except ImportError:
            StateConfig, get_state_config, validate_metric_target_crs = _load_shared_state_config_symbols()

try:
    from ..shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
except ImportError:
    try:
        from shared.shared_paths_config import get_shared_paths_config, resolve_local_shared_root_path
    except ImportError:
        get_shared_paths_config, resolve_local_shared_root_path = _load_shared_paths_config_symbols()

try:
    from .superfund_paths_config import get_superfund_paths_config, resolve_local_superfund_root_path
except ImportError:
    try:
        from superfund_paths_config import get_superfund_paths_config, resolve_local_superfund_root_path
    except ImportError:
        get_superfund_paths_config, resolve_local_superfund_root_path = _load_superfund_paths_config_symbols()
DEFAULT_TARGETED_BLOCK_GROUPS_FILENAME = 'targeted_block_groups.csv'
DEFAULT_BLOCK_SITE_DISTANCES_FILENAME = 'block_site_distances.csv'
DEFAULT_FINAL_BG_SCORES_FILENAME = 'final_bg_scores.csv'
DEFAULT_BUFFER_METERS = 10000.0
DEFAULT_FINAL_SCORE_OUTPUT_COLUMN = 'superfund_score'
SUPERFUND_STORAGE_MODES = ('local', 'remote')

NPL_LAYER_NAME = 'SITE_BOUNDARIES_SF'
NPL_STATUS_COLUMN = 'NPL_STATUS_CODE'
NPL_EPA_ID_COLUMN = 'EPA_ID'
BG_GEOID_COLUMN = 'GEOID'
ACTIVE_NPL_STATUS_CODES = ('F', 'P')
BLOCKS_CSV_REQUIRED_COLUMNS = (
    'GEOID20',
    'INTPTLAT20',
    'INTPTLON20',
    'POP20',
    'block_group_geoid',
    'block_group_pop',
    'fraction_of_total',
)


def get_active_superfund_root_path(storage_mode: str) -> str:
    if storage_mode == 'local':
        return resolve_local_superfund_root_path(SUPERFUND_DIR)
    if storage_mode == 'remote':
        return get_superfund_paths_config().remote_root_path
    raise ValueError(f'Unsupported storage mode: {storage_mode}')


@dataclass(slots=True)
class Config:
    storage_mode: str
    state: str = 'VT'
    npl_boundaries_path: str | None = None
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
    input_root_path: str
    output_root_path: str
    npl_boundaries_path: str
    block_groups_path: str
    census_blocks_path: str
    output_dir: str
    targeted_block_groups_path: str
    block_site_distances_path: str
    final_bg_scores_path: str


def get_config(argv=None) -> Config:
    load_dotenv()

    defaults = Config(storage_mode='local')
    parser = argparse.ArgumentParser(description='Run the Superfund NPL proximity pipeline for one state')
    parser.add_argument(
        'storage_mode',
        choices=SUPERFUND_STORAGE_MODES,
        help='Select whether the script reads and writes through the local root path or the remote S3 root path.',
    )
    parser.add_argument(
        '--state',
        dest='state',
        default=defaults.state,
        help=f'Two-letter state code to process (default: {defaults.state})',
    )
    parser.add_argument(
        '--npl-boundaries-path',
        dest='npl_boundaries_path',
        default=defaults.npl_boundaries_path,
        help='Explicit local path or S3 URI for the NPL geodatabase; overrides the derived default',
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
        help=f'Output filename for Step 3 results (default: {defaults.block_site_distances_filename})',
    )
    parser.add_argument(
        '--final-bg-scores-filename',
        dest='final_bg_scores_filename',
        default=defaults.final_bg_scores_filename,
        help=f'Output filename for Step 4 results (default: {defaults.final_bg_scores_filename})',
    )

    args = parser.parse_args(argv)
    return Config(
        storage_mode=args.storage_mode,
        state=args.state,
        npl_boundaries_path=args.npl_boundaries_path,
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

    if normalized_key.lower().endswith('.gdb'):
        prefix = normalized_key + '/'
        destination_dir = staging_root / key_name
        logging.info('Staging S3 geodatabase %s to %s', source_path, destination_dir)
        return _download_s3_prefix(bucket, prefix, destination_dir)

    destination = staging_root / key_name
    logging.info('Staging S3 geospatial file %s to %s', source_path, destination)
    return _download_s3_object(bucket, key, destination)


def resolve_pipeline_paths(cfg: Config) -> ResolvedPaths:
    state_config = get_state_config(cfg.state)
    shared_paths_config = get_shared_paths_config()
    superfund_paths_config = get_superfund_paths_config()

    input_root_path = get_active_superfund_root_path(cfg.storage_mode)
    output_root_path = input_root_path

    npl_boundaries_path = cfg.npl_boundaries_path or join_path_and_file(
        input_root_path,
        superfund_paths_config.canonical_npl_boundaries_relative_path,
    )
    shared_input_path = (
        shared_paths_config.remote_root_path
        if is_s3_uri(input_root_path)
        else resolve_local_shared_root_path(SCRIPTS_DIR)
    )
    block_groups_path = cfg.block_groups_path or join_path_and_file(
        shared_input_path,
        shared_paths_config.tiger_bg_relative_path_template.format(
            fips=state_config.fips,
            postal=state_config.postal,
            name=state_config.name,
        ),
    )
    census_blocks_path = cfg.census_blocks_path or join_path_and_file(
        shared_input_path,
        shared_paths_config.census_block_weights_relative_path_template.format(
            fips=state_config.fips,
            postal=state_config.postal,
            name=state_config.name,
        ),
    )
    output_dir = cfg.output_dir or join_path_and_file(
        output_root_path,
        superfund_paths_config.state_output_relative_path_template.format(
            postal=state_config.postal,
            fips=state_config.fips,
            name=state_config.name,
        ),
    )

    return ResolvedPaths(
        state_config=state_config,
        input_root_path=input_root_path,
        output_root_path=output_root_path,
        npl_boundaries_path=npl_boundaries_path,
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

    npl_path = stage_geospatial_input(paths.npl_boundaries_path, staging_root / 'npl_boundaries', 'npl_boundaries_path')
    bg_path = stage_geospatial_input(paths.block_groups_path, staging_root / 'block_groups', 'block_groups_path')

    try:
        layers = fiona.listlayers(str(npl_path))
    except Exception as exc:
        raise RuntimeError(f'Failed to inspect NPL geodatabase at {npl_path}: {exc}') from exc
    if NPL_LAYER_NAME not in layers:
        raise RuntimeError(f"NPL layer '{NPL_LAYER_NAME}' not found in {npl_path}")

    logging.info('Reading NPL layer %s from %s', NPL_LAYER_NAME, paths.npl_boundaries_path)
    try:
        npl_gdf = gpd.read_file(str(npl_path), layer=NPL_LAYER_NAME)
    except Exception as exc:
        raise RuntimeError(f"Failed to read NPL layer '{NPL_LAYER_NAME}' from {npl_path}: {exc}") from exc
    _require_columns(npl_gdf, (NPL_STATUS_COLUMN, NPL_EPA_ID_COLUMN, 'geometry'), 'NPL layer')
    npl_gdf = apply_metric_projection('npl dataset', npl_gdf, state_config.metric_crs, state_config.fips)

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
        crs='EPSG:4326',
    )
    blocks_gdf = apply_metric_projection('blocks dataset', blocks_gdf, state_config.metric_crs, state_config.fips)

    return npl_gdf, bg_gdf, blocks_gdf


def step1_buffer_and_targeted_bgs(
    npl_gdf: gpd.GeoDataFrame,
    bg_gdf: gpd.GeoDataFrame,
    buffer_meters: float = DEFAULT_BUFFER_METERS,
    output_path: str | None = None,
) -> pd.DataFrame:
    if npl_gdf is None:
        raise RuntimeError('npl_gdf is required')
    if bg_gdf is None:
        raise RuntimeError('bg_gdf is required')

    _require_columns(npl_gdf, (NPL_STATUS_COLUMN, NPL_EPA_ID_COLUMN, 'geometry'), 'NPL GeoDataFrame')
    _require_columns(bg_gdf, (BG_GEOID_COLUMN, 'geometry'), 'Block-group GeoDataFrame')

    logging.info('##############################################################')
    logging.info('#### Starting Step1: buffering projected NPL polygons and targeting block groups')
    logging.info('NPL columns: %d, rows: %d', len(npl_gdf.columns), len(npl_gdf))
    logging.info('Block Group columns: %d, rows: %d', len(bg_gdf.columns), len(bg_gdf))

    npl_active_gdf = npl_gdf[npl_gdf[NPL_STATUS_COLUMN].isin(ACTIVE_NPL_STATUS_CODES)].copy()
    logging.info('After filtering for active-ish NPL rows (%s): %d', ', '.join(ACTIVE_NPL_STATUS_CODES), len(npl_active_gdf))

    logging.info('Buffering NPL polygons by %s meters', buffer_meters)
    npl_active_gdf['geometry_buffer'] = npl_active_gdf.geometry.buffer(buffer_meters)
    npl_buffer = npl_active_gdf.set_geometry('geometry_buffer')

    logging.info('Performing spatial join to find targeted block groups')
    joined = gpd.sjoin(bg_gdf, npl_buffer, how='inner', predicate='intersects')
    if joined.empty:
        logging.info('No block groups found within buffer distance')
        targeted = pd.DataFrame(columns=['GEOID_BG', 'EPA_ID'])
    else:
        targeted = joined[[BG_GEOID_COLUMN, NPL_EPA_ID_COLUMN]].drop_duplicates().copy()
        targeted = targeted.rename(columns={BG_GEOID_COLUMN: 'GEOID_BG', NPL_EPA_ID_COLUMN: 'EPA_ID'})
        targeted['EPA_ID'] = targeted['EPA_ID'].astype(str).str.strip()

    if output_path:
        logging.info('Writing targeted block groups to %s', output_path)
        write_df_s3_or_local(targeted[['GEOID_BG', 'EPA_ID']], output_path)

    return targeted


def step2_block_site_distances(
    targeted_df: pd.DataFrame,
    blocks_gdf: gpd.GeoDataFrame,
    npl_gdf: gpd.GeoDataFrame,
) -> pd.DataFrame:
    if targeted_df is None:
        raise RuntimeError('targeted_df is required')
    if blocks_gdf is None:
        raise RuntimeError('blocks_gdf is required')
    if npl_gdf is None:
        raise RuntimeError('npl_gdf is required')

    _require_columns(targeted_df, ('GEOID_BG', 'EPA_ID'), 'targeted_df')
    _require_columns(blocks_gdf, ('block_geoid', 'block_group_geoid', 'geometry'), 'Blocks GeoDataFrame')
    _require_columns(npl_gdf, (NPL_EPA_ID_COLUMN, 'geometry'), 'NPL GeoDataFrame')
    if blocks_gdf.crs is None or npl_gdf.crs is None:
        raise RuntimeError('Projected inputs must have CRS defined before distance calculations')
    if blocks_gdf.crs != npl_gdf.crs:
        raise RuntimeError(f'Blocks and NPL inputs must share the same projected CRS: blocks={blocks_gdf.crs}, npl={npl_gdf.crs}')

    logging.info('##############################################################')
    logging.info('#### Starting Step2: computing block-site distances')

    if targeted_df.empty:
        logging.info('No targeted block groups were identified; Step 2 will return no distance records')
        return pd.DataFrame(columns=['GEOID_BLOCK', 'EPA_ID', 'distance_m'])

    targeted_df = targeted_df.copy()
    targeted_df['EPA_ID'] = targeted_df['EPA_ID'].astype(str).str.strip()
    target_bgs = set(targeted_df['GEOID_BG'].astype(str).unique())
    gdf_blocks_sub = blocks_gdf[blocks_gdf['block_group_geoid'].astype(str).isin(target_bgs)].copy()
    logging.info('Blocks in targeted block groups: %d (of %d)', len(gdf_blocks_sub), len(blocks_gdf))

    if gdf_blocks_sub.empty:
        logging.info('No blocks found in targeted block groups; nothing to do for Step 2')
        return pd.DataFrame(columns=['GEOID_BLOCK', 'EPA_ID', 'distance_m'])

    npl_lookup = npl_gdf[[NPL_EPA_ID_COLUMN, 'geometry']].copy()
    npl_lookup[NPL_EPA_ID_COLUMN] = npl_lookup[NPL_EPA_ID_COLUMN].astype(str).str.strip()
    npl_map = {row[NPL_EPA_ID_COLUMN]: row.geometry for _, row in npl_lookup.iterrows()}
    logging.info('NPL EPA keys: %d', len(npl_map))

    records = []
    blocks_by_bg = {bg: df for bg, df in gdf_blocks_sub.groupby('block_group_geoid')}

    rows_processed = 0
    ids_found = 0
    ids_not_found = 0

    for _, row in targeted_df.iterrows():
        rows_processed += 1
        bg = str(row['GEOID_BG'])
        epa = str(row['EPA_ID'])
        if bg not in blocks_by_bg:
            continue

        blocks = blocks_by_bg[bg]
        epa_key = epa.strip()
        poly = npl_map.get(epa_key)
        if poly is None:
            if ids_not_found < 5:
                logging.warning('EPA ID %s not found in NPL polygons; skipping', epa)
            ids_not_found += 1
            continue

        ids_found += 1
        for _, block_row in blocks.iterrows():
            try:
                distance_m = block_row.geometry.distance(poly)
            except Exception as exc:
                logging.error(
                    'Distance computation failed for block %s to EPA %s: %s',
                    block_row.get('block_geoid'),
                    epa,
                    exc,
                )
                distance_m = float('nan')

            records.append(
                {
                    'GEOID_BLOCK': block_row['block_geoid'],
                    'EPA_ID': epa,
                    'distance_m': distance_m if pd.notna(distance_m) else None,
                }
            )

    logging.info('Step2 summary: rows_processed=%d, ids_found=%d, ids_not_found=%d', rows_processed, ids_found, ids_not_found)

    distances_df = pd.DataFrame.from_records(records, columns=['GEOID_BLOCK', 'EPA_ID', 'distance_m'])
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
            return 11.0
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
    _require_columns(blocks_df, ('block_geoid', 'block_group_geoid', 'fraction_of_total'), 'Blocks GeoDataFrame')

    distances_with_scores_df = distances_with_scores_df.copy()
    distances_with_scores_df['proximity_score'] = pd.to_numeric(
        distances_with_scores_df['proximity_score'],
        errors='coerce',
    )

    blocks_df['fraction_of_total'] = pd.to_numeric(blocks_df['fraction_of_total'], errors='raise')
    blocks_df['block_geoid'] = blocks_df['block_geoid'].astype('string').str.strip()
    blocks_df['block_group_geoid'] = blocks_df['block_group_geoid'].astype('string').str.strip()

    all_block_groups = blocks_df[['block_group_geoid']].copy()
    all_block_groups['block_group_geoid'] = all_block_groups['block_group_geoid'].astype('string').str.strip()
    all_block_groups = all_block_groups[
        all_block_groups['block_group_geoid'].notna() & all_block_groups['block_group_geoid'].ne('')
    ].drop_duplicates().reset_index(drop=True)
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
    agg['weighted_score'] = agg['weighted_score'].fillna(0.0)

    if output_path:
        output_df = agg[['block_group_geoid', 'weighted_score']].rename(
            columns={'weighted_score': DEFAULT_FINAL_SCORE_OUTPUT_COLUMN}
        )
        logging.info(
            'Writing final block-group scores to %s with output column %s (rows=%d, targeted_groups=%d)',
            output_path,
            DEFAULT_FINAL_SCORE_OUTPUT_COLUMN,
            len(agg),
            len(agg_targeted),
        )
        write_df_s3_or_local(output_df, output_path)

    return agg


def log_resolved_paths(paths: ResolvedPaths, cfg: Config) -> None:
    logging.info(
        'State: %s (%s) | FIPS: %s | Metric CRS: %s',
        paths.state_config.name,
        paths.state_config.postal,
        paths.state_config.fips,
        paths.state_config.metric_crs,
    )
    logging.info('Storage mode: %s', cfg.storage_mode)
    logging.info('Input root path: %s', paths.input_root_path)
    logging.info('Output root path: %s', paths.output_root_path)
    logging.info('Resolved NPL boundaries path: %s', paths.npl_boundaries_path)
    logging.info('Resolved block-groups path: %s', paths.block_groups_path)
    logging.info('Resolved census blocks path: %s', paths.census_blocks_path)
    logging.info('Resolved output dir: %s', paths.output_dir)
    logging.info('Step 1 output: %s', paths.targeted_block_groups_path)
    logging.info('Step 3 output: %s', paths.block_site_distances_path)
    logging.info('Step 4 output: %s', paths.final_bg_scores_path)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        cfg = get_config(argv)
        paths = resolve_pipeline_paths(cfg)
        log_resolved_paths(paths, cfg)
    except Exception as exc:
        logging.error('Failed to resolve pipeline configuration: %s', exc)
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix=f'superfund_npl_{paths.state_config.postal.lower()}_') as temp_dir_name:
            staging_root = Path(temp_dir_name)
            npl_gdf, bg_gdf, blocks_gdf = step0_prepare_inputs(paths, staging_root)
            targeted_df = step1_buffer_and_targeted_bgs(
                npl_gdf=npl_gdf,
                bg_gdf=bg_gdf,
                buffer_meters=cfg.buffer_meters,
                output_path=paths.targeted_block_groups_path,
            )
            distances_df = step2_block_site_distances(
                targeted_df=targeted_df,
                blocks_gdf=blocks_gdf,
                npl_gdf=npl_gdf,
            )
            distances_with_scores_df = step3_inverse_distance_scoring(
                distances_df,
                output_path=paths.block_site_distances_path,
            )
            step4_population_weighting_aggregation(
                distances_with_scores_df,
                blocks_gdf=blocks_gdf,
                output_path=paths.final_bg_scores_path,
            )
    except Exception as exc:
        logging.exception('Superfund NPL proximity pipeline failed: %s', exc)
        return 1

    logging.info('Superfund NPL proximity pipeline completed successfully')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
