from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json

SUPERFUND_PATHS_CONFIG_PATH = Path(__file__).with_name('superfund_paths_config.json')
REQUIRED_CONFIG_FIELDS = (
	'local_root_path',
	'remote_root_path',
	'canonical_npl_boundaries_relative_path',
	'state_output_relative_path_template',
)


@dataclass(frozen=True, slots=True)
class SuperfundPathsConfig:
	local_root_path: str
	remote_root_path: str
	canonical_npl_boundaries_relative_path: str
	state_output_relative_path_template: str


def _require_string_field(raw_config: dict[str, object], field_name: str) -> str:
	value = raw_config.get(field_name)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f'{SUPERFUND_PATHS_CONFIG_PATH.name} field {field_name!r} must be a non-empty string')
	return value.strip()


def _validate_relative_path(field_name: str, relative_path: str) -> None:
	if relative_path.startswith('/') or relative_path.startswith('s3://'):
		raise ValueError(
			f'{SUPERFUND_PATHS_CONFIG_PATH.name} field {field_name!r} must be a relative path'
		)


@lru_cache(maxsize=1)
def get_superfund_paths_config() -> SuperfundPathsConfig:
	if not SUPERFUND_PATHS_CONFIG_PATH.exists():
		raise FileNotFoundError(f'Superfund paths config not found: {SUPERFUND_PATHS_CONFIG_PATH}')

	with SUPERFUND_PATHS_CONFIG_PATH.open('r', encoding='utf-8') as handle:
		raw_config = json.load(handle)

	if not isinstance(raw_config, dict):
		raise ValueError(f'{SUPERFUND_PATHS_CONFIG_PATH.name} must contain a JSON object')

	missing_fields = [field_name for field_name in REQUIRED_CONFIG_FIELDS if field_name not in raw_config]
	if missing_fields:
		raise ValueError(
			f'{SUPERFUND_PATHS_CONFIG_PATH.name} missing required fields: {", ".join(missing_fields)}'
		)

	config = SuperfundPathsConfig(
		local_root_path=_require_string_field(raw_config, 'local_root_path'),
		remote_root_path=_require_string_field(raw_config, 'remote_root_path'),
		canonical_npl_boundaries_relative_path=_require_string_field(raw_config, 'canonical_npl_boundaries_relative_path'),
		state_output_relative_path_template=_require_string_field(raw_config, 'state_output_relative_path_template'),
	)

	_validate_relative_path('canonical_npl_boundaries_relative_path', config.canonical_npl_boundaries_relative_path)
	_validate_relative_path('state_output_relative_path_template', config.state_output_relative_path_template)
	return config


def resolve_local_superfund_root_path(superfund_dir: Path) -> str:
	config = get_superfund_paths_config()
	return str((superfund_dir / config.local_root_path).resolve())