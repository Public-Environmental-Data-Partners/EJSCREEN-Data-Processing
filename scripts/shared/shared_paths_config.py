from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json

SHARED_PATHS_CONFIG_PATH = Path(__file__).with_name('shared_paths_config.json')
REQUIRED_CONFIG_FIELDS = (
	'local_root_path',
	'remote_root_path',
	'downloads_subtree_name',
	'preprocessed_input_subtree_name',
	'tiger_bg_relative_path_template',
	'census_block_weights_relative_path_template',
)


@dataclass(frozen=True, slots=True)
class SharedPathsConfig:
	local_root_path: str
	remote_root_path: str
	downloads_subtree_name: str
	preprocessed_input_subtree_name: str
	tiger_bg_relative_path_template: str
	census_block_weights_relative_path_template: str


def _require_string_field(raw_config: dict[str, object], field_name: str) -> str:
	value = raw_config.get(field_name)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f'{SHARED_PATHS_CONFIG_PATH.name} field {field_name!r} must be a non-empty string')
	return value.strip()


def _validate_template_prefix(template_value: str, subtree_name: str, field_name: str) -> None:
	expected_prefix = subtree_name.rstrip('/') + '/'
	if not template_value.startswith(expected_prefix):
		raise ValueError(
			f'{SHARED_PATHS_CONFIG_PATH.name} field {field_name!r} must start with {expected_prefix!r}'
		)


@lru_cache(maxsize=1)
def get_shared_paths_config() -> SharedPathsConfig:
	if not SHARED_PATHS_CONFIG_PATH.exists():
		raise FileNotFoundError(f'Shared paths config not found: {SHARED_PATHS_CONFIG_PATH}')

	with SHARED_PATHS_CONFIG_PATH.open('r', encoding='utf-8') as handle:
		raw_config = json.load(handle)

	if not isinstance(raw_config, dict):
		raise ValueError(f'{SHARED_PATHS_CONFIG_PATH.name} must contain a JSON object')

	missing_fields = [field_name for field_name in REQUIRED_CONFIG_FIELDS if field_name not in raw_config]
	if missing_fields:
		raise ValueError(
			f'{SHARED_PATHS_CONFIG_PATH.name} missing required fields: {", ".join(missing_fields)}'
		)

	config = SharedPathsConfig(
		local_root_path=_require_string_field(raw_config, 'local_root_path'),
		remote_root_path=_require_string_field(raw_config, 'remote_root_path'),
		downloads_subtree_name=_require_string_field(raw_config, 'downloads_subtree_name'),
		preprocessed_input_subtree_name=_require_string_field(raw_config, 'preprocessed_input_subtree_name'),
		tiger_bg_relative_path_template=_require_string_field(raw_config, 'tiger_bg_relative_path_template'),
		census_block_weights_relative_path_template=_require_string_field(
			raw_config,
			'census_block_weights_relative_path_template',
		),
	)

	_validate_template_prefix(
		config.tiger_bg_relative_path_template,
		config.downloads_subtree_name,
		'tiger_bg_relative_path_template',
	)
	_validate_template_prefix(
		config.census_block_weights_relative_path_template,
		config.preprocessed_input_subtree_name,
		'census_block_weights_relative_path_template',
	)
	return config


def resolve_local_shared_root_path(scripts_dir: Path) -> str:
	config = get_shared_paths_config()
	return str((scripts_dir / config.local_root_path).resolve())