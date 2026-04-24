"""o3_config.py

Purpose:
	Load and validate the O3 pipeline configuration shared by the fetch,
	preprocess, and indicator scripts.

Configuration summary:
	- Reads scripts/o3/o3_config.json.
	- Validates the local and remote root paths.
	- Validates the tract-level preprocess output path and per-state indicator
		output template.
	- Validates the nested raw-download entry for the 2020 national O3
		source file.

Public helpers:
	- get_o3_config(): returns one validated o3Config instance and caches it.
	- resolve_local_o3_root_path(): resolves the configured local pipeline root
		relative to the scripts/o3 folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json


O3_CONFIG_PATH = Path(__file__).with_name('o3_config.json')
RAW_O3_2020_DOWNLOAD_KEY = 'raw_o3_2020'
REQUIRED_CONFIG_FIELDS = (
	'local_root_path',
	'remote_root_path',
	'preprocessed_tract_output_relative_path',
	'indicator_output_relative_path_template',
	'downloads',
)

REQUIRED_DOWNLOAD_FIELDS = (
	'relative_path',
	'source_url',
)


@dataclass(frozen=True, slots=True)
class O3Config:
	local_root_path: str
	remote_root_path: str
	preprocessed_tract_output_relative_path: str
	indicator_output_relative_path_template: str
	raw_download_relative_path: str
	source_url: str
	request_timeout_seconds: int
	chunk_size_bytes: int


def _require_string_field(raw_config: dict[str, object], field_name: str) -> str:
	value = raw_config.get(field_name)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f'{O3_CONFIG_PATH.name} field {field_name!r} must be a non-empty string')
	return value.strip()


def _require_int_field(raw_config: dict[str, object], field_name: str) -> int:
	value = raw_config.get(field_name)
	if not isinstance(value, int) or value <= 0:
		raise ValueError(f'{O3_CONFIG_PATH.name} field {field_name!r} must be a positive integer')
	return value


def _validate_relative_path(field_name: str, relative_path: str) -> None:
	if relative_path.startswith('/') or relative_path.startswith('s3://'):
		raise ValueError(f'{O3_CONFIG_PATH.name} field {field_name!r} must be a relative path')


def _require_dict_field(raw_config: dict[str, object], field_name: str) -> dict[str, object]:
	value = raw_config.get(field_name)
	if not isinstance(value, dict):
		raise ValueError(f'{O3_CONFIG_PATH.name} field {field_name!r} must be a JSON object')
	return value


@lru_cache(maxsize=1)
def get_o3_config() -> O3Config:
	"""Return the validated O3 configuration from o3_config.json."""
	if not O3_CONFIG_PATH.exists():
		raise FileNotFoundError(f'O3 config not found: {O3_CONFIG_PATH}')

	with O3_CONFIG_PATH.open('r', encoding='utf-8') as handle:
		raw_config = json.load(handle)

	if not isinstance(raw_config, dict):
		raise ValueError(f'{O3_CONFIG_PATH.name} must contain a JSON object')

	missing_fields = [field_name for field_name in REQUIRED_CONFIG_FIELDS if field_name not in raw_config]
	if missing_fields:
		raise ValueError(f'{O3_CONFIG_PATH.name} missing required fields: {", ".join(missing_fields)}')

	downloads_config = _require_dict_field(raw_config, 'downloads')
	download_settings_missing = [
		field_name
		for field_name in ('request_timeout_seconds', 'chunk_size_bytes', RAW_O3_2020_DOWNLOAD_KEY)
		if field_name not in downloads_config
	]
	if download_settings_missing:
		raise ValueError(
			f'{O3_CONFIG_PATH.name} downloads missing required fields: {", ".join(download_settings_missing)}'
		)

	raw_o3_download_config = _require_dict_field(downloads_config, RAW_O3_2020_DOWNLOAD_KEY)
	raw_download_missing_fields = [
		field_name for field_name in REQUIRED_DOWNLOAD_FIELDS if field_name not in raw_o3_download_config
	]
	if raw_download_missing_fields:
		raise ValueError(
			f'{O3_CONFIG_PATH.name} downloads.{RAW_O3_2020_DOWNLOAD_KEY} missing required fields: '
			f'{", ".join(raw_download_missing_fields)}'
		)

	config = O3Config(
		local_root_path=_require_string_field(raw_config, 'local_root_path'),
		remote_root_path=_require_string_field(raw_config, 'remote_root_path'),
		preprocessed_tract_output_relative_path=_require_string_field(raw_config, 'preprocessed_tract_output_relative_path'),
		indicator_output_relative_path_template=_require_string_field(raw_config, 'indicator_output_relative_path_template'),
		raw_download_relative_path=_require_string_field(raw_o3_download_config, 'relative_path'),
		source_url=_require_string_field(raw_o3_download_config, 'source_url'),
		request_timeout_seconds=_require_int_field(downloads_config, 'request_timeout_seconds'),
		chunk_size_bytes=_require_int_field(downloads_config, 'chunk_size_bytes'),
	)

	_validate_relative_path('raw_download_relative_path', config.raw_download_relative_path)
	_validate_relative_path('preprocessed_tract_output_relative_path', config.preprocessed_tract_output_relative_path)
	_validate_relative_path('indicator_output_relative_path_template', config.indicator_output_relative_path_template)
	if not config.source_url.lower().startswith(('http://', 'https://')):
		raise ValueError(f'{O3_CONFIG_PATH.name} field \"source_url\" must be an http(s) URL')
	return config


def resolve_local_o3_root_path(o3_dir: Path) -> str:
	"""Resolve the configured local O3 pipeline root from the o3 script directory."""
	config = get_o3_config()
	return str((o3_dir / config.local_root_path).resolve())