"""Strict stage manifest builder for indicator workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import resolve_path


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
VALID_ENVIRONMENTS = {"local", "remote"}


class _ManifestBuilder:
    def __init__(self) -> None:
        self._indicator_config_cache: dict[str, dict[str, object]] = {}

    def get_stage_manifest(
        self,
        indicator: str,
        stage: str,
        version: str,
        environment: str = "local",
    ) -> dict[str, dict[str, dict[str, str]]]:
        self._validate_environment(environment)
        config = self._load_indicator_config(indicator)
        version_block = self._get_indicator_version_block(config, indicator, version)
        stage_block = self._get_stage_block(version_block, indicator, version, stage)

        manifest = {
            "inputs": self._compile_inputs(config, stage_block, indicator, version, environment),
            "outputs": self._compile_outputs(config, stage_block, indicator, version, stage, environment),
        }
        return manifest

    def _compile_inputs(
        self,
        config: dict[str, object],
        stage_block: dict[str, object],
        indicator: str,
        version: str,
        environment: str,
    ) -> dict[str, dict[str, str]]:
        inputs = self._optional_mapping(stage_block.get("inputs"), "inputs")
        compiled: dict[str, dict[str, str]] = {}

        for input_name, entry in inputs.items():
            field_prefix = f"{indicator}.versions.{version}.stages.*.inputs.{input_name}"
            entry_mapping = self._require_mapping(entry, field_prefix)
            entry_type = self._require_non_empty_string(entry_mapping.get("type"), f"{field_prefix}.type")

            if entry_type == "indicator_download":
                asset_key = self._require_non_empty_string(entry_mapping.get("key"), f"{field_prefix}.key")
                compiled[input_name] = resolve_path.get_download_path(indicator, version, asset_key, environment)
                continue

            if entry_type == "shared_asset":
                asset_name = self._require_non_empty_string(entry_mapping.get("key"), f"{field_prefix}.key")
                category = self._require_non_empty_string(entry_mapping.get("category"), f"{field_prefix}.category")
                shared_version = resolve_path.get_dependency_version(indicator, version, asset_name)
                shared_asset_key = None
                if category == "downloads":
                    shared_asset_key = self._require_non_empty_string(
                        entry_mapping.get("asset_key"),
                        f"{field_prefix}.asset_key",
                    )
                compiled[input_name] = resolve_path.get_shared_asset_path(
                    asset=asset_name,
                    version=shared_version,
                    category=category,
                    asset_key=shared_asset_key,
                    environment=environment,
                )
                continue

            if entry_type in {"file", "directory", "template"}:
                compiled[input_name] = self._compile_indicator_relative_entry(
                    config,
                    entry_mapping,
                    field_prefix,
                    environment,
                )
                continue

            self._fail(f"Unsupported input type {entry_type!r} at {field_prefix}.type")

        return compiled

    def _compile_outputs(
        self,
        config: dict[str, object],
        stage_block: dict[str, object],
        indicator: str,
        version: str,
        stage: str,
        environment: str,
    ) -> dict[str, dict[str, str]]:
        outputs = self._optional_mapping(stage_block.get("outputs"), "outputs")
        compiled: dict[str, dict[str, str]] = {}

        for output_name, entry in outputs.items():
            field_prefix = f"{indicator}.versions.{version}.stages.{stage}.outputs.{output_name}"
            entry_mapping = self._require_mapping(entry, field_prefix)
            compiled[output_name] = self._compile_indicator_relative_entry(
                config,
                entry_mapping,
                field_prefix,
                environment,
            )

        return compiled

    def _compile_indicator_relative_entry(
        self,
        config: dict[str, object],
        entry_mapping: dict[str, object],
        field_prefix: str,
        environment: str,
    ) -> dict[str, str]:
        root = self._get_root(config, environment, "indicator")
        relative = self._require_non_empty_string(entry_mapping.get("relative_path"), f"{field_prefix}.relative_path")
        return {"root": root, "relative": relative}

    def _load_indicator_config(self, indicator: str) -> dict[str, object]:
        if indicator in self._indicator_config_cache:
            return self._indicator_config_cache[indicator]

        config_path = SCRIPTS_ROOT / indicator / f"{indicator}_config.json"
        config = self._load_json_object(config_path, f"indicator {indicator}")
        self._indicator_config_cache[indicator] = config
        return config

    def _load_json_object(self, config_path: Path, label: str) -> dict[str, object]:
        if not config_path.exists():
            self._fail(f"Missing {label} config file: {config_path}")

        LOGGER.info("Loading %s config from %s", label, config_path)
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            self._fail(f"Expected {label} config to be a JSON object: {config_path}")
        return payload

    def _get_indicator_version_block(
        self,
        config: dict[str, object],
        indicator: str,
        version: str,
    ) -> dict[str, object]:
        versions = self._require_mapping(config.get("versions"), f"{indicator}.versions")
        return self._require_mapping(versions.get(version), f"{indicator}.versions.{version}")

    def _get_stage_block(
        self,
        version_block: dict[str, object],
        indicator: str,
        version: str,
        stage: str,
    ) -> dict[str, object]:
        stages = self._require_mapping(version_block.get("stages"), f"{indicator}.versions.{version}.stages")
        return self._require_mapping(stages.get(stage), f"{indicator}.versions.{version}.stages.{stage}")

    def _get_root(self, config: dict[str, object], environment: str, label: str) -> str:
        field_name = "local_root_path" if environment == "local" else "remote_root_path"
        return self._require_non_empty_string(config.get(field_name), f"{label}.{field_name}")

    def _validate_environment(self, environment: str) -> None:
        if environment not in VALID_ENVIRONMENTS:
            self._fail(f"Invalid environment {environment!r}; expected one of {sorted(VALID_ENVIRONMENTS)}")

    def _optional_mapping(self, value: object, field_name: str) -> dict[str, object]:
        if value is None:
            return {}
        return self._require_mapping(value, field_name)

    def _require_mapping(self, value: object, field_name: str) -> dict[str, object]:
        if not isinstance(value, dict):
            self._fail(f"Expected {field_name} to be a JSON object")
        return value

    def _require_non_empty_string(self, value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value:
            self._fail(f"Expected {field_name} to be a non-empty string")
        return value

    def _fail(self, message: str) -> None:
        LOGGER.error(message)
        raise ValueError(message)


_BUILDER: _ManifestBuilder | None = None


def _get_builder() -> _ManifestBuilder:
    global _BUILDER
    if _BUILDER is None:
        _BUILDER = _ManifestBuilder()
    return _BUILDER


def get_stage_manifest(
    indicator: str,
    stage: str,
    version: str,
    environment: str = "local",
) -> dict[str, dict[str, dict[str, str]]]:
    return _get_builder().get_stage_manifest(indicator, stage, version, environment)