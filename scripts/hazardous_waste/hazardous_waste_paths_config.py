from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json

HAZARDOUS_WASTE_PATHS_CONFIG_PATH = Path(__file__).with_name('hazardous_waste_paths_config.json')
REQUIRED_CONFIG_FIELDS = (
	'local_root_path',
	'remote_root_path',
	'canonical_output_relative_path',
	'parse_audit_relative_path',
	'validation_audit_relative_path',
	'dedup_audit_relative_path',
)


@dataclass(frozen=True, slots=True)
class HazardousWastePathsConfig:
	local_root_path: str
	remote_root_path: str
	canonical_output_relative_path: str
	parse_audit_relative_path: str
	validation_audit_relative_path: str
	dedup_audit_relative_path: str


def _require_string_field(raw_config: dict[str, object], field_name: str) -> str:
	value = raw_config.get(field_name)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f'{HAZARDOUS_WASTE_PATHS_CONFIG_PATH.name} field {field_name!r} must be a non-empty string')
	return value.strip()


def _validate_relative_path(field_name: str, relative_path: str) -> None:
	if relative_path.startswith('/') or relative_path.startswith('s3://'):
		raise ValueError(
			f'{HAZARDOUS_WASTE_PATHS_CONFIG_PATH.name} field {field_name!r} must be a relative path'
		)


@lru_cache(maxsize=1)
def get_hazardous_waste_paths_config() -> HazardousWastePathsConfig:
	if not HAZARDOUS_WASTE_PATHS_CONFIG_PATH.exists():
		raise FileNotFoundError(f'Hazardous-waste paths config not found: {HAZARDOUS_WASTE_PATHS_CONFIG_PATH}')

	with HAZARDOUS_WASTE_PATHS_CONFIG_PATH.open('r', encoding='utf-8') as handle:
		raw_config = json.load(handle)

	if not isinstance(raw_config, dict):
		raise ValueError(f'{HAZARDOUS_WASTE_PATHS_CONFIG_PATH.name} must contain a JSON object')

	missing_fields = [field_name for field_name in REQUIRED_CONFIG_FIELDS if field_name not in raw_config]
	if missing_fields:
		raise ValueError(
			f'{HAZARDOUS_WASTE_PATHS_CONFIG_PATH.name} missing required fields: {", ".join(missing_fields)}'
		)

	config = HazardousWastePathsConfig(
		local_root_path=_require_string_field(raw_config, 'local_root_path'),
		remote_root_path=_require_string_field(raw_config, 'remote_root_path'),
		canonical_output_relative_path=_require_string_field(raw_config, 'canonical_output_relative_path'),
		parse_audit_relative_path=_require_string_field(raw_config, 'parse_audit_relative_path'),
		validation_audit_relative_path=_require_string_field(raw_config, 'validation_audit_relative_path'),
		dedup_audit_relative_path=_require_string_field(raw_config, 'dedup_audit_relative_path'),
	)

	_validate_relative_path('canonical_output_relative_path', config.canonical_output_relative_path)
	_validate_relative_path('parse_audit_relative_path', config.parse_audit_relative_path)
	_validate_relative_path('validation_audit_relative_path', config.validation_audit_relative_path)
	_validate_relative_path('dedup_audit_relative_path', config.dedup_audit_relative_path)
	return config


def resolve_local_hazardous_waste_root_path(hazardous_waste_dir: Path) -> str:
	config = get_hazardous_waste_paths_config()
	return str((hazardous_waste_dir / config.local_root_path).resolve())