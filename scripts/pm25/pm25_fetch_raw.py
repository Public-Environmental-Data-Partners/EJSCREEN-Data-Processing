"""pm25_fetch_raw.py

Purpose:
	Download the national 2020 PM2.5 source .txt.gz file into the configured
	PM2.5 pipeline storage location.

Process summary:
	- Read PM2.5 configuration and choose local or remote storage mode.
	- Stream the configured source URL into a temporary file.
	- Emit heartbeat logging during long downloads.
	- Copy the completed temp file into the configured destination path.
	- Append one fetch-audit row describing the run outcome.

Runtime arguments:
	- storage_mode
		Required. Either local or remote.

Outputs:
	- downloads/2020/2020_pm25_daily_average.txt.gz under the active PM2.5 root.
	- fetch_audit.csv under the active PM2.5 root.
	- pm25_fetch_raw.log in scripts/pm25.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import importlib
import logging
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import requests

from pm25_config import get_pm25_config, resolve_local_pm25_root_path


PM25_DIR = Path(__file__).resolve().parent
PROCESS_NAME = 'pm25_fetch_raw'
DEFAULT_LOG_FILENAME = 'pm25_fetch_raw.log'
DEFAULT_AUDIT_FILENAME = 'fetch_audit.csv'
HEARTBEAT_INTERVAL_SECONDS = 30.0
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


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	local_root_path: str
	remote_root_path: str
	raw_download_relative_path: str
	source_url: str
	request_timeout_seconds: int
	chunk_size_bytes: int


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


def get_config(argv=None) -> Config:
	"""Parse the runtime arguments and merge them with validated PM2.5 config values."""
	raw_config = get_pm25_config()

	parser = argparse.ArgumentParser(description='Download the raw national PM2.5 .txt.gz file to local or remote PM2.5 pipeline storage.')
	parser.add_argument(
		'storage_mode',
		choices=('local', 'remote'),
		help='Select whether the script writes to the configured local root path or remote S3 root path.',
	)
	args = parser.parse_args(argv)

	return Config(
		storage_mode=args.storage_mode,
		local_root_path=resolve_local_pm25_root_path(PM25_DIR),
		remote_root_path=raw_config.remote_root_path,
		raw_download_relative_path=raw_config.raw_download_relative_path,
		source_url=raw_config.source_url,
		request_timeout_seconds=raw_config.request_timeout_seconds,
		chunk_size_bytes=raw_config.chunk_size_bytes,
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
	log_path = PM25_DIR / DEFAULT_LOG_FILENAME
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


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def open_text_output_stream(path: str, mode: str):
	ensure_local_parent_dir(path)
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, mode, encoding='utf-8', newline='')
	return open(path, mode, encoding='utf-8', newline='')


def open_binary_output_stream(path: str):
	ensure_local_parent_dir(path)
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, 'wb')
	return open(path, 'wb')


def path_exists(path: str) -> bool:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return bool(fsspec.open(path).fs.exists(path))
	return Path(path).exists()


def get_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), DEFAULT_AUDIT_FILENAME)


def create_run_id() -> str:
	return uuid.uuid4().hex


def build_audit_row(*, run_id: str, dl_started_at: str, dl_ended_at: str, result: DownloadResult) -> dict[str, str | int]:
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


def download_to_temp_file(session: requests.Session, cfg: Config) -> tuple[Path, int]:
	"""Stream the configured PM2.5 source file into a temporary local file."""
	temp_file = tempfile.NamedTemporaryFile(delete=False)
	temp_path = Path(temp_file.name)
	total_bytes = 0
	started_at = time.monotonic()
	last_heartbeat_at = started_at
	try:
		with temp_file:
			with session.get(cfg.source_url, stream=True, timeout=cfg.request_timeout_seconds) as response:
				response.raise_for_status()
				content_length_header = response.headers.get('Content-Length')
				logging.info(
					'Connected to source URL. HTTP %s. Content-Length=%s',
					response.status_code,
					content_length_header or 'unknown',
				)
				for chunk in response.iter_content(chunk_size=cfg.chunk_size_bytes):
					if not chunk:
						continue
					temp_file.write(chunk)
					total_bytes += len(chunk)
					now = time.monotonic()
					if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
						elapsed_seconds = max(now - started_at, 0.001)
						bytes_per_second = total_bytes / elapsed_seconds
						logging.info(
							'Downloaded %s bytes so far (elapsed=%.1fs, rate=%.1f bytes/s)',
							total_bytes,
							elapsed_seconds,
							bytes_per_second,
						)
						print(f'Bytes downloaded so far: {total_bytes}', flush=True)
						last_heartbeat_at = now
	except Exception:
		if temp_path.exists():
			temp_path.unlink()
		raise
	logging.info('Finished download stream with %s total bytes', total_bytes)
	return temp_path, total_bytes


def write_temp_file_to_destination(temp_path: Path, destination_path: str, cfg: Config) -> None:
	"""Copy a completed temp file into the configured local path or S3 object."""
	logging.info('Starting file copy from temp file %s to %s', temp_path, destination_path)
	print(f'Starting file copy to destination: {destination_path}')
	with temp_path.open('rb') as input_stream:
		with open_binary_output_stream(destination_path) as output_stream:
			shutil.copyfileobj(input_stream, output_stream, length=cfg.chunk_size_bytes)
	logging.info('Finished file copy from temp file %s to %s', temp_path, destination_path)
	print(f'Finished file copy to destination: {destination_path}')


def download_raw_pm25_file(session: requests.Session, cfg: Config, destination_path: str, filename: str) -> DownloadResult:
	"""Download the raw PM2.5 file unless the configured destination already exists."""
	if path_exists(destination_path):
		logging.info('Skipping download for %s because destination already exists: %s', filename, destination_path)
		return DownloadResult(
			filename=filename,
			state='ALL',
			destination_path=destination_path,
			storage_mode=cfg.storage_mode,
			status='skipped',
			source_url=cfg.source_url,
			bytes_downloaded=0,
			message='destination already existed',
		)

	logging.info('Downloading raw PM2.5 file from %s', cfg.source_url)
	total_bytes = 0

	# Optimize local writes: stream directly into a .tmp sibling inside the
	# destination directory and atomically replace the final file. For remote
	# storage we keep the existing temp-file + copy flow.
	if cfg.storage_mode == 'local':
		dest_path = Path(destination_path)
		ensure_local_parent_dir(destination_path)
		temp_dest = dest_path.with_name(dest_path.name + '.tmp')
		try:
			started_at = time.monotonic()
			last_heartbeat_at = started_at
			with session.get(cfg.source_url, stream=True, timeout=cfg.request_timeout_seconds) as response:
				response.raise_for_status()
				logging.info('Connected to source URL. HTTP %s. Content-Length=%s', response.status_code, response.headers.get('Content-Length', 'unknown'))
				with temp_dest.open('wb') as f:
					for chunk in response.iter_content(chunk_size=cfg.chunk_size_bytes):
						if not chunk:
							continue
						f.write(chunk)
						total_bytes += len(chunk)
						now = time.monotonic()
						if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
							elapsed_seconds = max(now - started_at, 0.001)
							bytes_per_second = total_bytes / elapsed_seconds
							logging.info('Downloading %s: %d bytes so far (%.1f B/s)', filename, total_bytes, bytes_per_second)
							last_heartbeat_at = now
			temp_dest.replace(dest_path)
		except Exception:
			# Try to remove a partial temp file, but don't mask the original exception.
			try:
				temp_dest.unlink()
			except Exception:
				pass
			raise

	else:
		temp_path, total_bytes = download_to_temp_file(session, cfg)
		try:
			write_temp_file_to_destination(temp_path, destination_path, cfg)
		finally:
			temp_path.unlink(missing_ok=True)

	logging.info('Wrote %s bytes to %s', total_bytes, destination_path)
	return DownloadResult(
		filename=filename,
		state='ALL',
		destination_path=destination_path,
		storage_mode=cfg.storage_mode,
		status='uploaded',
		source_url=cfg.source_url,
		bytes_downloaded=total_bytes,
		message='downloaded and uploaded',
	)


def main(argv=None) -> int:
	"""Run the PM2.5 raw-download workflow and append one audit record."""
	log_path = configure_logging()
	cfg = get_config(argv)
	run_id = create_run_id()
	audit_path = get_audit_path(cfg)
	destination_path = join_root_and_relative_path(get_active_root_path(cfg), cfg.raw_download_relative_path)
	filename = Path(cfg.raw_download_relative_path).name
	logging.info('Logging to %s', log_path)
	logging.info('Run ID: %s', run_id)
	logging.info('Audit CSV path: %s', audit_path)
	logging.info('Storage mode: %s', cfg.storage_mode)
	logging.info('Active root path: %s', get_active_root_path(cfg))
	logging.info('Destination path: %s', destination_path)
	logging.info('Source URL: %s', cfg.source_url)
	initialize_runtime_dependencies(cfg)

	with requests.Session() as session:
		dl_started_at = datetime.now().astimezone().isoformat(timespec='seconds')
		try:
			result = download_raw_pm25_file(session, cfg, destination_path, filename)
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
						state='ALL',
						destination_path=destination_path,
						storage_mode=cfg.storage_mode,
						status='failed',
						source_url=cfg.source_url,
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

	logging.info('Completed raw PM2.5 fetch with status %s', result.status)
	return 0


if __name__ == '__main__':
	raise SystemExit(main())