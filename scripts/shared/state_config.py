from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from pyproj import CRS

DEFAULT_METRIC_CRS = 'EPSG:5070'
STATE_CONFIG_PATH = Path(__file__).with_name('state_config.json')
REQUIRED_STATE_FIELDS = ('fips', 'postal', 'name')


@dataclass(frozen=True, slots=True)
class StateConfig:
    fips: str
    postal: str
    name: str
    metric_crs: str = DEFAULT_METRIC_CRS


def validate_metric_target_crs(metric_crs: str, description: str) -> CRS:
    try:
        target_crs = CRS.from_user_input(metric_crs)
    except Exception as exc:
        raise RuntimeError(f'Invalid {description}: {metric_crs}: {exc}') from exc

    if not target_crs.is_projected:
        raise RuntimeError(f'{description} must be a projected CRS in meters, got non-projected CRS: {metric_crs}')

    axis_units = {axis.unit_name.lower() for axis in target_crs.axis_info if axis.unit_name}
    if axis_units and not axis_units.issubset({'metre', 'meter'}):
        raise RuntimeError(
            f'{description} must use meter units, got {", ".join(sorted(axis_units))}: {metric_crs}'
        )

    return target_crs


def get_state_config(state_code: str) -> StateConfig:
    normalized_state_code = _normalize_state_code(state_code)
    raw_state_configs = _load_state_configs()

    if normalized_state_code not in raw_state_configs:
        raise RuntimeError(
            f"State code '{normalized_state_code}' is not present in {STATE_CONFIG_PATH.name}"
        )

    raw_state_config = raw_state_configs[normalized_state_code]
    if not isinstance(raw_state_config, dict):
        raise RuntimeError(
            f"State entry for '{normalized_state_code}' in {STATE_CONFIG_PATH.name} must be a JSON object"
        )

    fips = _require_nonempty_string(raw_state_config, 'fips', normalized_state_code)
    if len(fips) != 2 or not fips.isdigit():
        raise RuntimeError(f"State entry for '{normalized_state_code}' must have a two-digit string fips value")

    postal = _require_nonempty_string(raw_state_config, 'postal', normalized_state_code).upper()
    if postal != normalized_state_code:
        raise RuntimeError(
            f"State entry for '{normalized_state_code}' must have postal value '{normalized_state_code}', got '{postal}'"
        )

    name = _require_nonempty_string(raw_state_config, 'name', normalized_state_code)
    metric_crs = raw_state_config.get('metric_crs') or DEFAULT_METRIC_CRS
    if not isinstance(metric_crs, str) or not metric_crs.strip():
        raise RuntimeError(
            f"State entry for '{normalized_state_code}' must have a non-empty string metric_crs when provided"
        )

    metric_crs = metric_crs.strip()
    validate_metric_target_crs(metric_crs, f'metric_crs for {normalized_state_code}')
    return StateConfig(fips=fips, postal=postal, name=name, metric_crs=metric_crs)


@lru_cache(maxsize=1)
def _load_state_configs() -> dict[str, Any]:
    if not STATE_CONFIG_PATH.exists():
        raise RuntimeError(f'State config file does not exist: {STATE_CONFIG_PATH}')

    try:
        raw_text = STATE_CONFIG_PATH.read_text(encoding='utf-8')
    except OSError as exc:
        raise RuntimeError(f'Failed to read state config file {STATE_CONFIG_PATH}: {exc}') from exc

    try:
        raw_state_configs = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid JSON in state config file {STATE_CONFIG_PATH}: {exc}') from exc

    if not isinstance(raw_state_configs, dict):
        raise RuntimeError(
            f'State config file must contain a JSON object keyed by two-letter state codes: {STATE_CONFIG_PATH}'
        )

    return raw_state_configs


def _normalize_state_code(state_code: str) -> str:
    if not isinstance(state_code, str):
        raise RuntimeError(f'State code must be a string, got {type(state_code).__name__}')

    normalized_state_code = state_code.strip().upper()
    if len(normalized_state_code) != 2 or not normalized_state_code.isalpha():
        raise RuntimeError(f"State code must be a two-letter code, got '{state_code}'")

    return normalized_state_code


def _require_nonempty_string(raw_state_config: dict[str, Any], field_name: str, state_code: str) -> str:
    if field_name not in raw_state_config:
        required_fields = ', '.join(REQUIRED_STATE_FIELDS)
        raise RuntimeError(
            f"State entry for '{state_code}' is missing required field '{field_name}'. Required fields: {required_fields}"
        )

    field_value = raw_state_config[field_name]
    if not isinstance(field_value, str) or not field_value.strip():
        raise RuntimeError(
            f"State entry for '{state_code}' must have a non-empty string value for '{field_name}'"
        )

    return field_value.strip()