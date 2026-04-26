"""shared_config.py

Loader and validator for the shared indicator configuration.

This file merges the information previously present in
`fetch_tiger_lines_bg_config.json` and `shared_paths_config.json` and
provides typed accessors used by the central runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json


SHARED_CONFIG_PATH = Path(__file__).with_name('shared_config.json')
REQUIRED_CONFIG_FIELDS = (
    'local_root_path',
    'remote_root_path',
    'downloads_subtree_name',
    'preprocessed_input_subtree_name',
    'tiger_bg_relative_path_template',
    'downloads',
)


@dataclass(frozen=True, slots=True)
class SharedConfig:
    local_root_path: str
    remote_root_path: str
    downloads_subtree_name: str
    preprocessed_input_subtree_name: str
    tiger_bg_relative_path_template: str
    census_block_weights_relative_path_template: str | None
    request_timeout_seconds: int
    chunk_size_bytes: int
    downloads_entries: dict


def _require_string_field(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{SHARED_CONFIG_PATH.name} field {field!r} must be a non-empty string')
    return value.strip()


def _require_int_field(raw: dict[str, object], field: str) -> int:
    value = raw.get(field)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f'{SHARED_CONFIG_PATH.name} field {field!r} must be a positive integer')
    return value


def _require_dict_field(raw: dict[str, object], field: str) -> dict[str, object]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise ValueError(f'{SHARED_CONFIG_PATH.name} field {field!r} must be a JSON object')
    return value


def _validate_relative_path(field_name: str, relative_path: str) -> None:
    if relative_path.startswith('/') or relative_path.startswith('s3://'):
        raise ValueError(f'{SHARED_CONFIG_PATH.name} field {field_name!r} must be a relative path')


@lru_cache(maxsize=1)
def get_shared_config() -> SharedConfig:
    """Return validated SharedConfig loaded from shared_config.json."""
    if not SHARED_CONFIG_PATH.exists():
        raise FileNotFoundError(f'Shared config not found: {SHARED_CONFIG_PATH}')

    with SHARED_CONFIG_PATH.open('r', encoding='utf-8') as fh:
        raw = json.load(fh)

    if not isinstance(raw, dict):
        raise ValueError(f'{SHARED_CONFIG_PATH.name} must contain a JSON object')

    missing = [f for f in REQUIRED_CONFIG_FIELDS if f not in raw]
    if missing:
        raise ValueError(f'{SHARED_CONFIG_PATH.name} missing required fields: {", ".join(missing)}')

    downloads_obj = _require_dict_field(raw, 'downloads')
    # downloads may follow the 'entries' nesting or a flat entries map
    if 'entries' in downloads_obj and isinstance(downloads_obj['entries'], dict):
        entries = downloads_obj['entries']
    else:
        # treat other keys except known settings as entries
        entries = {}
        for k, v in downloads_obj.items():
            if k in ('request_timeout_seconds', 'chunk_size_bytes'):
                continue
            entries[k] = v

    if not isinstance(entries, dict) or not entries:
        raise ValueError(f'{SHARED_CONFIG_PATH.name} downloads.entries must be a non-empty object')

    # Validate entries
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            raise ValueError(f'{SHARED_CONFIG_PATH.name} downloads entry {key!r} must be an object')
        scope = entry.get('scope')
        if scope not in ('single', 'state'):
            raise ValueError(f'{SHARED_CONFIG_PATH.name} downloads.{key}.scope must be "single" or "state"')
        if scope == 'state':
            if 'source_url_template' not in entry or 'relative_path_template' not in entry:
                raise ValueError(f'{SHARED_CONFIG_PATH.name} downloads.{key} with scope=state must include templates')
            # Basic checks
            src = entry['source_url_template']
            if not isinstance(src, str) or not src.lower().startswith(('http://', 'https://')):
                raise ValueError(f'{SHARED_CONFIG_PATH.name} downloads.{key}.source_url_template must be an http(s) URL')

    request_timeout_seconds = _require_int_field(downloads_obj, 'request_timeout_seconds') if 'request_timeout_seconds' in downloads_obj else 120
    chunk_size_bytes = _require_int_field(downloads_obj, 'chunk_size_bytes') if 'chunk_size_bytes' in downloads_obj else 1048576

    config = SharedConfig(
        local_root_path=_require_string_field(raw, 'local_root_path'),
        remote_root_path=_require_string_field(raw, 'remote_root_path'),
        downloads_subtree_name=_require_string_field(raw, 'downloads_subtree_name'),
        preprocessed_input_subtree_name=_require_string_field(raw, 'preprocessed_input_subtree_name'),
        tiger_bg_relative_path_template=_require_string_field(raw, 'tiger_bg_relative_path_template'),
        census_block_weights_relative_path_template=raw.get('census_block_weights_relative_path_template'),
        request_timeout_seconds=request_timeout_seconds,
        chunk_size_bytes=chunk_size_bytes,
        downloads_entries=entries,
    )

    # Validate relative path templates
    _validate_relative_path('tiger_bg_relative_path_template', config.tiger_bg_relative_path_template)
    if config.census_block_weights_relative_path_template:
        _validate_relative_path('census_block_weights_relative_path_template', config.census_block_weights_relative_path_template)

    return config


def resolve_local_shared_root_path(shared_dir: Path) -> str:
    """Resolve the configured local shared pipeline root from the shared script directory."""
    cfg = get_shared_config()
    return str((shared_dir / cfg.local_root_path).resolve())
