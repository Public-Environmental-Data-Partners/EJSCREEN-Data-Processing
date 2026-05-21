"""superfund_preprocess.py

Purpose:
	Read the raw Superfund NPL boundaries ZIP archive directly, extract the single
	geodatabase it contains, and write one canonical local .gdb directory used by
	the indicator step.

Process summary:
	- Resolve the raw ZIP input from local or remote Superfund storage.
	- Copy the archive locally when the raw input lives in remote storage.
	- Inspect the ZIP contents and locate exactly one .gdb directory.
	- Extract that .gdb directory into the canonical local preprocess path.
	- Replace any previous extracted .gdb directory so the canonical input stays stable.

Runtime arguments:
	- storage_mode
		Required. Either local or remote.

Outputs:
	- preprocessed_input/npl_boundaries/NPL_Boundaries.gdb under the local Superfund root,
		regardless of storage_mode.
	- superfund_preprocess.log in scripts/superfund.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import importlib
import logging
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

from superfund_config import get_superfund_config, resolve_local_superfund_root_path


SUPERFUND_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_FILENAME = 'superfund_preprocess.log'


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	local_root_path: str
	remote_root_path: str
	raw_download_relative_path: str
	preprocessed_npl_boundaries_relative_path: str


def get_config(argv=None) -> Config:
	"""Parse runtime arguments and merge them with validated Superfund config values."""
	raw_config = get_superfund_config()

	parser = argparse.ArgumentParser(
		description='Read the raw Superfund NPL ZIP archive, extract the geodatabase, and write the canonical local preprocess input.'
	)
	parser.add_argument(
		'storage_mode',
		choices=('local', 'remote'),
		help='Select whether the script reads the raw ZIP from the configured local root path or remote S3 root path.',
	)
	args = parser.parse_args(argv)

	return Config(
		storage_mode=args.storage_mode,
		local_root_path=resolve_local_superfund_root_path(SUPERFUND_DIR),
		remote_root_path=raw_config.remote_root_path,
		raw_download_relative_path=raw_config.raw_download_relative_path,
		preprocessed_npl_boundaries_relative_path=raw_config.preprocessed_npl_boundaries_relative_path,
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
	log_path = SUPERFUND_DIR / DEFAULT_LOG_FILENAME
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
	return join_root_and_relative_path(cfg.local_root_path, cfg.preprocessed_npl_boundaries_relative_path)


def path_exists(path: str) -> bool:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return bool(fsspec.open(path).fs.exists(path))
	return Path(path).exists()


def stage_remote_raw_zip_to_local(path: str, staging_dir: Path) -> Path:
	fsspec = load_fsspec_module()
	local_zip_path = staging_dir / PurePosixPath(path[5:]).name
	with fsspec.open(path, 'rb') as input_stream:
		with local_zip_path.open('wb') as output_stream:
			shutil.copyfileobj(input_stream, output_stream)
	return local_zip_path


def locate_single_gdb_prefix(zip_path: Path) -> str:
	with zipfile.ZipFile(zip_path) as archive:
		gdb_prefixes = set()
		for member_name in archive.namelist():
			member_path = PurePosixPath(member_name)
			for index, part in enumerate(member_path.parts):
				if part.lower().endswith('.gdb'):
					gdb_prefix = '/'.join(member_path.parts[: index + 1]).rstrip('/') + '/'
					gdb_prefixes.add(gdb_prefix)
					break

	if not gdb_prefixes:
		raise RuntimeError(f'ZIP archive does not contain a .gdb directory: {zip_path}')
	if len(gdb_prefixes) != 1:
		raise RuntimeError(
			'ZIP archive must contain exactly one .gdb directory. '
			f'Found {len(gdb_prefixes)} candidates: {sorted(gdb_prefixes)}'
		)
	return sorted(gdb_prefixes)[0]


def extract_gdb_from_zip(zip_path: Path, output_gdb_path: Path) -> None:
	gdb_prefix = locate_single_gdb_prefix(zip_path)
	logging.info('Discovered .gdb prefix in ZIP: %s', gdb_prefix)

	if output_gdb_path.exists():
		logging.info('Removing existing canonical .gdb directory at %s', output_gdb_path)
		shutil.rmtree(output_gdb_path)
	output_gdb_path.parent.mkdir(parents=True, exist_ok=True)

	extracted_file_count = 0
	with zipfile.ZipFile(zip_path) as archive:
		for member_name in archive.namelist():
			if not member_name.startswith(gdb_prefix) or member_name.endswith('/'):
				continue

			relative_member_path = member_name[len(gdb_prefix):].lstrip('/')
			if not relative_member_path:
				continue

			output_path = output_gdb_path / Path(relative_member_path)
			output_path.parent.mkdir(parents=True, exist_ok=True)
			with archive.open(member_name, 'r') as input_stream:
				with output_path.open('wb') as output_stream:
					shutil.copyfileobj(input_stream, output_stream)
			extracted_file_count += 1

	if extracted_file_count == 0:
		raise RuntimeError(f'ZIP archive did not produce any extracted .gdb files at {output_gdb_path}')
	logging.info('Extracted %d files into canonical .gdb directory %s', extracted_file_count, output_gdb_path)


def main(argv=None) -> int:
	"""Run the Superfund preprocess step and write the canonical local .gdb directory."""
	log_path = configure_logging()
	cfg = get_config(argv)
	initialize_runtime_dependencies(cfg)
	raw_input_path = get_raw_input_path(cfg)
	output_path = get_preprocessed_output_path(cfg)
	logging.info('Logging to %s', log_path)
	logging.info('Storage mode: %s', cfg.storage_mode)
	logging.info('Raw input path: %s', raw_input_path)
	logging.info('Canonical local preprocess output path: %s', output_path)

	if not path_exists(raw_input_path):
		raise FileNotFoundError(f'Raw Superfund ZIP input not found at {raw_input_path}')

	with tempfile.TemporaryDirectory(prefix='superfund_preprocess_') as temp_dir_name:
		staging_dir = Path(temp_dir_name)
		if is_s3_uri(raw_input_path):
			logging.info('Copying remote raw ZIP to local staging directory')
			local_zip_path = stage_remote_raw_zip_to_local(raw_input_path, staging_dir)
		else:
			local_zip_path = Path(raw_input_path)

		logging.info('Extracting canonical .gdb from ZIP %s', local_zip_path)
		extract_gdb_from_zip(local_zip_path, Path(output_path))

	logging.info('Superfund preprocess completed successfully')
	print(f'Canonical Superfund .gdb path: {output_path}')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())