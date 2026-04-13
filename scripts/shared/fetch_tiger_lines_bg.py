from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import argparse
import importlib
import json
import logging
import shutil
import tempfile
import zipfile

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_PATH = SCRIPT_DIR / 'fetch_tiger_lines_bg_config.json'
STATE_CONFIG_PATH = SCRIPT_DIR / 'state_config.json'
# what data do we need from our state config file?
REQUIRED_STATE_FIELDS = ('fips', 'postal', 'name')
# what's a minimal set of files we expect in each ZIP (shapefile) archive?
REQUIRED_ZIP_MEMBER_SUFFIXES = ('.dbf', '.prj', '.shp', '.shx')


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	state: str | None
	local_root_path: str
	remote_root_path: str
	target_year: int
	source_url_template: str
	target_relative_dir_template: str
	filename_template: str
	request_timeout_seconds: int
	chunk_size_bytes: int


@dataclass(frozen=True, slots=True)
class StateDownloadTarget:
	postal: str
	fips: str
	name: str


def load_config_payload() -> dict:
	if not CONFIG_FILE_PATH.exists():
		raise FileNotFoundError(f'Config file not found: {CONFIG_FILE_PATH}')
	with CONFIG_FILE_PATH.open('r', encoding='utf-8') as config_stream:
		return json.load(config_stream)


def get_config(argv=None) -> Config:
	config_payload = load_config_payload()

	parser = argparse.ArgumentParser(
		description='Download shared TIGER/Line block-group ZIPs to local or remote shared pipeline storage.'
	)
	parser.add_argument(
		'storage_mode',
		choices=('local', 'remote'),
		help='Select whether the script writes to the configured local root path or remote S3 root path.',
	)
	parser.add_argument(
		'--state',
		dest='state',
		help='Optional two-letter postal abbreviation for a single-state test download.',
	)
	args = parser.parse_args(argv)

	return Config(
		storage_mode=args.storage_mode,
		state=normalize_state_code(args.state) if args.state else None,
		local_root_path=config_payload['local_root_path'],
		remote_root_path=config_payload['remote_root_path'],
		target_year=int(config_payload['target_year']),
		source_url_template=config_payload['source_url_template'],
		target_relative_dir_template=config_payload['target_relative_dir_template'],
		filename_template=config_payload['filename_template'],
		request_timeout_seconds=int(config_payload['request_timeout_seconds']),
		chunk_size_bytes=int(config_payload['chunk_size_bytes']),
	)


def initialize_runtime_dependencies(cfg: Config) -> None:
	if cfg.storage_mode != 'remote':
		return

	dotenv = importlib.import_module('dotenv')
	s3fs = importlib.import_module('s3fs')
	load_fsspec_module()

	dotenv.load_dotenv()
	if s3fs is None:
		raise RuntimeError('s3fs failed to import for remote mode')


def load_fsspec_module():
	return importlib.import_module('fsspec')


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
	return str(Path(root_path) / Path(relative_path))


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def open_binary_output_stream(path: str):
	ensure_local_parent_dir(path)
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, 'wb')
	return open(path, 'wb')


def get_active_root_path(cfg: Config) -> str:
	if cfg.storage_mode == 'local':
		return cfg.local_root_path
	if cfg.storage_mode == 'remote':
		return cfg.remote_root_path
	raise ValueError(f'Unsupported storage mode: {cfg.storage_mode}')


def normalize_state_code(state_code: str) -> str:
	normalized_state_code = state_code.strip().upper()
	if len(normalized_state_code) != 2 or not normalized_state_code.isalpha():
		raise RuntimeError(f"State code must be a two-letter postal abbreviation, got '{state_code}'")
	return normalized_state_code


def load_state_targets() -> list[StateDownloadTarget]:
	if not STATE_CONFIG_PATH.exists():
		raise FileNotFoundError(f'State config file not found: {STATE_CONFIG_PATH}')

	with STATE_CONFIG_PATH.open('r', encoding='utf-8') as state_stream:
		payload = json.load(state_stream)

	if not isinstance(payload, dict):
		raise RuntimeError(f'State config file must contain a JSON object: {STATE_CONFIG_PATH}')

	targets: list[StateDownloadTarget] = []
	for state_code, raw_state in sorted(payload.items()):
		if not isinstance(raw_state, dict):
			raise RuntimeError(f"State entry for '{state_code}' must be a JSON object")
		missing_fields = [field_name for field_name in REQUIRED_STATE_FIELDS if field_name not in raw_state]
		if missing_fields:
			missing_fields_text = ', '.join(missing_fields)
			raise RuntimeError(f"State entry for '{state_code}' is missing required fields: {missing_fields_text}")

		postal = str(raw_state['postal']).strip().upper()
		fips = str(raw_state['fips']).strip()
		name = str(raw_state['name']).strip()
		if postal != state_code:
			raise RuntimeError(f"State entry for '{state_code}' must have postal value '{state_code}', got '{postal}'")
		if len(fips) != 2 or not fips.isdigit():
			raise RuntimeError(f"State entry for '{state_code}' must have a two-digit string fips value")
		if not name:
			raise RuntimeError(f"State entry for '{state_code}' must have a non-empty name")

		targets.append(StateDownloadTarget(postal=postal, fips=fips, name=name))

	return targets


def select_state_targets(cfg: Config, state_targets: list[StateDownloadTarget]) -> list[StateDownloadTarget]:
	if cfg.state is None:
		return state_targets

	selected_targets = [state_target for state_target in state_targets if state_target.postal == cfg.state]
	if not selected_targets:
		raise RuntimeError(
			f"Configured state '{cfg.state}' was not found in {STATE_CONFIG_PATH.name}"
		)
	return selected_targets


def build_target_relative_path(cfg: Config, state_target: StateDownloadTarget) -> str:
	target_dir = cfg.target_relative_dir_template.format(year=cfg.target_year)
	filename = cfg.filename_template.format(year=cfg.target_year, fips=state_target.fips)
	return str(PurePosixPath(target_dir) / filename)


def build_source_url(cfg: Config, state_target: StateDownloadTarget) -> str:
	return cfg.source_url_template.format(year=cfg.target_year, fips=state_target.fips)


def download_to_temp_file(session: requests.Session, url: str, cfg: Config) -> tuple[Path, int]:
	temp_file = tempfile.NamedTemporaryFile(delete=False)
	temp_path = Path(temp_file.name)
	total_bytes = 0

	try:
		with temp_file:
			with session.get(url, stream=True, timeout=cfg.request_timeout_seconds) as response:
				response.raise_for_status()
				for chunk in response.iter_content(chunk_size=cfg.chunk_size_bytes):
					if not chunk:
						continue
					temp_file.write(chunk)
					total_bytes += len(chunk)
	except Exception:
		if temp_path.exists():
			temp_path.unlink()
		raise

	return temp_path, total_bytes


def validate_tiger_zip(path: Path, expected_filename: str) -> None:
	try:
		with zipfile.ZipFile(path) as archive:
			member_leaf_names = [Path(member_name).name for member_name in archive.namelist() if member_name and not member_name.endswith('/')]
	except zipfile.BadZipFile as exc:
		raise RuntimeError(f'Downloaded file is not a valid ZIP archive for {expected_filename}: {exc}') from exc

	missing_suffixes = [
		suffix
		for suffix in REQUIRED_ZIP_MEMBER_SUFFIXES
		if not any(member_name.lower().endswith(suffix) for member_name in member_leaf_names)
	]
	if missing_suffixes:
		missing_suffixes_text = ', '.join(missing_suffixes)
		raise RuntimeError(
			f'Downloaded TIGER ZIP for {expected_filename} is missing expected shapefile members: {missing_suffixes_text}'
		)


def write_temp_file_to_destination(temp_path: Path, destination_path: str, cfg: Config) -> None:
	with temp_path.open('rb') as input_stream:
		with open_binary_output_stream(destination_path) as output_stream:
			shutil.copyfileobj(input_stream, output_stream, length=cfg.chunk_size_bytes)


def download_state_tiger_zip(session: requests.Session, cfg: Config, state_target: StateDownloadTarget) -> tuple[str, int]:
	source_url = build_source_url(cfg, state_target)
	target_relative_path = build_target_relative_path(cfg, state_target)
	destination_path = join_root_and_relative_path(get_active_root_path(cfg), target_relative_path)
	filename = Path(target_relative_path).name

	logging.info('Downloading %s (%s) from %s', state_target.postal, state_target.fips, source_url)
	temp_path, total_bytes = download_to_temp_file(session, source_url, cfg)
	try:
		validate_tiger_zip(temp_path, filename)
		write_temp_file_to_destination(temp_path, destination_path, cfg)
	finally:
		temp_path.unlink(missing_ok=True)

	logging.info('Wrote %s bytes to %s', total_bytes, destination_path)
	return destination_path, total_bytes


def log_runtime_context(cfg: Config, state_targets: list[StateDownloadTarget]) -> None:
	logging.info('Config file: %s', CONFIG_FILE_PATH)
	logging.info('State config file: %s', STATE_CONFIG_PATH)
	logging.info('Storage mode: %s', cfg.storage_mode)
	logging.info('Selected state filter: %s', cfg.state or 'all configured states')
	logging.info('Active root path: %s', get_active_root_path(cfg))
	logging.info('Target year: %s', cfg.target_year)
	logging.info('Source URL template: %s', cfg.source_url_template)
	logging.info('Target relative dir template: %s', cfg.target_relative_dir_template)
	logging.info('Configured state count: %s', len(state_targets))


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
	cfg = get_config(argv)
	initialize_runtime_dependencies(cfg)
	state_targets = select_state_targets(cfg, load_state_targets())
	log_runtime_context(cfg, state_targets)

	total_downloaded_bytes = 0
	with requests.Session() as session:
		for state_target in state_targets:
			_, downloaded_bytes = download_state_tiger_zip(session, cfg, state_target)
			total_downloaded_bytes += downloaded_bytes

	logging.info('Completed %s TIGER downloads (%s total bytes).', len(state_targets), total_downloaded_bytes)
	return 0


if __name__ == '__main__':
	raise SystemExit(main())