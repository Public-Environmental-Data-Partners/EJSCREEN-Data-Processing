"""
fetch_tiger_lines_bg.py

Purpose:
	Download TIGER/Line Census block-group ZIP files for the states configured in
	shared/state_config.json and write them to the configured local or remote
	shared pipeline destination.

Process summary:
	- Load the external config from ./fetch_tiger_lines_bg_config.json.
	- Read the state list and FIPS codes from ./state_config.json.
	- For each state in the config file (or one if specified with --state)
	-- Build the Census source URL and target destination path for each state.
	-- Skip files that already exist at the destination and log the reason.
	-- Download each missing ZIP with requests into a temporary local file.
	-- Validate that the ZIP contains the expected core shapefile members.
	-- Write the validated ZIP to the configured local path or remote S3 path.
	-- Append one final per-file outcome row to fetch_audit.csv at the active
	  destination root for the run.
	- Write narrative logging to the local fetch_tiger_lines_bg.log file beside
	  this script.

Runtime arguments:
	- storage_mode
	  REQUIRED positional argument. Must be either 'local' or 'remote'.
	  Selects whether downloaded files and the audit CSV are written to the
	  configured local root path or remote S3 root path.
	- --state
	  Optional two-letter postal abbreviation. When provided, limits the run to a
	  single configured state for testing.

Credits:
	Designed by Anne Gunn.
	Coded by GitHub Copilot (GPT-5.4 Medium) and Anne Gunn.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import argparse
import importlib
import json
import logging
import shutil
import tempfile
import uuid
import zipfile

import requests
from state_config import STATE_CONFIG_PATH, get_state_config


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_PATH = SCRIPT_DIR / 'fetch_tiger_lines_bg_config.json'
PROCESS_NAME = 'fetch_tiger_lines_bg'
DEFAULT_LOG_FILENAME = 'fetch_tiger_lines_bg.log'
DEFAULT_AUDIT_FILENAME = 'fetch_audit.csv'
PROGRESS_PRINT_EVERY = 5
AUDIT_FIELDNAMES = (
	'process_name',
	'run_id',
	'dl_started_at',
	'dl_ended_at',
	'filename',
	'state',
	'destination_path',
	'storage_mode',
	'status',
	'source_url',
	'bytes_downloaded',
	'message',
)
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


@dataclass(frozen=True, slots=True)
class DownloadResult:
	filename: str
	state: str
	destination_path: str
	storage_mode: str
	status: str
	source_url: str
	bytes_downloaded: int
	message: str


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
	importlib.import_module('s3fs')
	importlib.import_module('fsspec')
	dotenv.load_dotenv()


def load_fsspec_module():
	return importlib.import_module('fsspec')


def configure_logging() -> str:
	log_path = SCRIPT_DIR / DEFAULT_LOG_FILENAME
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


def open_text_output_stream(path: str, mode: str):
	ensure_local_parent_dir(path)
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, mode, encoding='utf-8', newline='')
	return open(path, mode, encoding='utf-8', newline='')


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
	return str(Path(root_path) / Path(relative_path))


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def path_exists(path: str) -> bool:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return bool(fsspec.open(path).fs.exists(path))
	return Path(path).exists()


def build_audit_row(
	*,
	run_id: str,
	dl_started_at: str,
	dl_ended_at: str,
	result: DownloadResult,
) -> dict[str, str | int]:
	return {
		'process_name': PROCESS_NAME,
		'run_id': run_id,
		'dl_started_at': dl_started_at,
		'dl_ended_at': dl_ended_at,
		'filename': result.filename,
		'state': result.state,
		'destination_path': result.destination_path,
		'storage_mode': result.storage_mode,
		'status': result.status,
		'source_url': result.source_url,
		'bytes_downloaded': result.bytes_downloaded,
		'message': result.message,
	}


def append_audit_row(path: str, row: dict[str, str | int]) -> None:
	file_exists = path_exists(path)
	mode = 'a' if file_exists else 'w'
	with open_text_output_stream(path, mode) as output_stream:
		writer = csv.DictWriter(output_stream, fieldnames=list(AUDIT_FIELDNAMES))
		if not file_exists:
			writer.writeheader()
		writer.writerow(row)


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


def get_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), DEFAULT_AUDIT_FILENAME)


def create_run_id() -> str:
	return uuid.uuid4().hex


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
	for state_code in sorted(payload):
		state_config = get_state_config(state_code)
		targets.append(
			StateDownloadTarget(
				postal=state_config.postal,
				fips=state_config.fips,
				name=state_config.name,
			)
		)

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


def build_download_details(cfg: Config, state_target: StateDownloadTarget) -> tuple[str, str, str]:
	source_url = build_source_url(cfg, state_target)
	target_relative_path = build_target_relative_path(cfg, state_target)
	destination_path = join_root_and_relative_path(get_active_root_path(cfg), target_relative_path)
	filename = Path(target_relative_path).name
	return source_url, destination_path, filename


def download_state_tiger_zip(
	session: requests.Session,
	cfg: Config,
	state_target: StateDownloadTarget,
	source_url: str,
	destination_path: str,
	filename: str,
) -> DownloadResult:
	if path_exists(destination_path):
		logging.info('Skipping download for %s because destination already exists: %s', filename, destination_path)
		return DownloadResult(
			filename=filename,
			state=state_target.postal,
			destination_path=destination_path,
			storage_mode=cfg.storage_mode,
			status='skipped',
			source_url=source_url,
			bytes_downloaded=0,
			message='destination already existed',
		)

	logging.info('Downloading %s (%s) from %s', state_target.postal, state_target.fips, source_url)
	temp_path, total_bytes = download_to_temp_file(session, source_url, cfg)
	try:
		validate_tiger_zip(temp_path, filename)
		logging.info('Validation OK for %s', filename)
		write_temp_file_to_destination(temp_path, destination_path, cfg)
	finally:
		temp_path.unlink(missing_ok=True)

	logging.info('Wrote %s bytes to %s', total_bytes, destination_path)
	return DownloadResult(
		filename=filename,
		state=state_target.postal,
		destination_path=destination_path,
		storage_mode=cfg.storage_mode,
		status='uploaded',
		source_url=source_url,
		bytes_downloaded=total_bytes,
		message='validated and uploaded',
	)


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
	log_path = configure_logging()
	cfg = get_config(argv)
	run_id = create_run_id()
	audit_path = get_audit_path(cfg)
	logging.info('Logging to %s', log_path)
	logging.info('Run ID: %s', run_id)
	logging.info('Audit CSV path: %s', audit_path)
	initialize_runtime_dependencies(cfg)
	state_targets = select_state_targets(cfg, load_state_targets())
	log_runtime_context(cfg, state_targets)

	total_downloaded_bytes = 0
	skipped_file_count = 0
	completed_download_count = 0
	total_target_count = len(state_targets)
	with requests.Session() as session:
		for index, state_target in enumerate(state_targets, start=1):
			dl_started_at = datetime.now().astimezone().isoformat(timespec='seconds')
			source_url, destination_path, filename = build_download_details(cfg, state_target)
			try:
				result = download_state_tiger_zip(
					session,
					cfg,
					state_target,
					source_url,
					destination_path,
					filename,
				)
			except Exception as exc:
				dl_ended_at = datetime.now().astimezone().isoformat(timespec='seconds')
				append_audit_row(
					audit_path,
					build_audit_row(
						run_id=run_id,
						dl_started_at=dl_started_at,
						dl_ended_at=dl_ended_at,
						result=DownloadResult(
							filename=filename,
							state=state_target.postal,
							destination_path=destination_path,
							storage_mode=cfg.storage_mode,
							status='failed',
							source_url=source_url,
							bytes_downloaded=0,
							message=str(exc),
						),
					),
				)
				raise

			dl_ended_at = datetime.now().astimezone().isoformat(timespec='seconds')
			append_audit_row(
				audit_path,
				build_audit_row(
					run_id=run_id,
					dl_started_at=dl_started_at,
					dl_ended_at=dl_ended_at,
					result=result,
				),
			)
			if result.status == 'skipped':
				skipped_file_count += 1
			else:
				completed_download_count += 1
			total_downloaded_bytes += result.bytes_downloaded
			if index % PROGRESS_PRINT_EVERY == 0 or index == total_target_count:
				print(
					f'Proof of life: processed {index} of {total_target_count} files '
					f'(uploaded={completed_download_count}, skipped={skipped_file_count})'
				)

	logging.info('Skipped %s files because they already existed.', skipped_file_count)
	logging.info('Completed %s TIGER downloads (%s total bytes).', completed_download_count, total_downloaded_bytes)
	return 0


if __name__ == '__main__':
	raise SystemExit(main())