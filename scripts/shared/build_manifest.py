"""Strict stage manifest builder for indicator and shared workflows.

Public API:
- get_stage_manifest(target_type: str, name: str, stage: str, version: str, environment: str = "local") -> dict

Behavior:
- Compiles a stage-level manifest for an `indicator` or `shared` target and
    returns a dictionary with `inputs` and `outputs`. Each entry is compiled to
    include at minimum `root` and `relative` values; fetch entries may include
    additional metadata such as `source_url`, `scope`, or `source_url_template`.
- When `environment == 'local'` compiled `root` values are resolved against the
    project root. `environment` accepts only 'local' or 'remote'.

Notes:
- The builder delegates shared-version validation to `resolve_path.get_shared_version_block`
    to ensure one authoritative implementation for shared-asset lookups and error messages.
- Exposes a flat function `get_stage_manifest` suitable for use from R via `reticulate`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

# All of our project-specific imports must be relative to the 
# `scripts` folder which we assume is at the first level of the
# repository. 
# NB: ***If `scripts` moves, this code will have to change.***
# Walk up our current working directory tree until you find the
# repository root, then add the scripts directory to sys.path
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".git").exists()), None)
if REPO_ROOT is None:
	# This is a running-from-docker or other non-git environment cry for help.
    # Undone: Handle non-git environments more gracefully when needed.
    raise RuntimeError("Architectural Error: Repository root anchor (.git) could not be found!")
SCRIPTS_ROOT = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import shared.resolve_path as resolve_path

LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

VALID_ENVIRONMENTS = {"local", "remote"}
VALID_TARGET_TYPES = {"indicator", "shared"}
SHARED_CONFIG_PATH = Path(__file__).with_name("shared_config.json")

class _ManifestBuilder:
    def __init__(self) -> None:
        self._indicator_config_cache: dict[str, dict[str, object]] = {}
        self._shared_config_cache: dict[str, object] | None = None

    def get_stage_manifest(
        self,
        target_type: str,
        name: str,
        stage: str,
        version: str,
        environment: str = "local",
    ) -> dict[str, dict[str, dict[str, str]]]:
        self._validate_target_type(target_type)
        self._validate_environment(environment)

        if target_type == "indicator":
            config = self._load_indicator_config(name)
            version_block = self._get_indicator_version_block(config, name, version)
            stage_block = self._get_indicator_stage_block(version_block, name, version, stage)
            return {
                "inputs": self._compile_indicator_inputs(config, stage_block, name, version, environment),
                "outputs": self._compile_target_outputs(
                    config,
                    stage_block,
                    f"{name}.versions.{version}.stages.{stage}",
                    environment,
                ),
            }

        config = self._load_shared_config()
        version_block = self._get_shared_version_block(config, name, version)
        stage_block = self._get_shared_stage_block(version_block, name, version, stage)
        manifest = {
            "inputs": self._compile_shared_inputs(stage_block, name, version),
            "outputs": self._compile_target_outputs(
                config,
                stage_block,
                f"shared.assets.{name}.{version}.stages.{stage}",
                environment,
            ),
        }
        return manifest

    def _compile_indicator_inputs(
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

    def _compile_indicator_relative_entry(
        self,
        config: dict[str, object],
        entry_mapping: dict[str, object],
        field_prefix: str,
        environment: str,
    ) -> dict[str, str]:
        # Indicator inputs that reference local files/directories/templates
        # reuse the same relative entry compilation used for outputs.
        return self._compile_relative_entry(config, entry_mapping, field_prefix, environment)

    def _compile_shared_inputs(
        self,
        stage_block: dict[str, object],
        asset_name: str,
        version: str,
    ) -> dict[str, dict[str, str]]:
        inputs = self._optional_mapping(stage_block.get("inputs"), "inputs")
        if not inputs:
            return {}

        for input_name in inputs:
            self._fail(
                f"Shared target inputs are not yet defined for strict manifest compilation: "
                f"shared.assets.{asset_name}.{version}.stages.*.inputs.{input_name}"
            )

        return {}

    def _compile_target_outputs(
        self,
        config: dict[str, object],
        stage_block: dict[str, object],
        field_prefix: str,
        environment: str,
    ) -> dict[str, dict[str, str]]:
        outputs = self._optional_mapping(stage_block.get("outputs"), "outputs")
        compiled: dict[str, dict[str, str]] = {}

        for output_name, entry in outputs.items():
            output_prefix = f"{field_prefix}.outputs.{output_name}"
            entry_mapping = self._require_mapping(entry, field_prefix)
            compiled[output_name] = self._compile_relative_entry(
                config,
                entry_mapping,
                output_prefix,
                environment,
            )

        return compiled

    def _compile_relative_entry(
        self,
        config: dict[str, object],
        entry_mapping: dict[str, object],
        field_prefix: str,
        environment: str,
    ) -> dict[str, str]:
        root = self._get_root(config, environment, "target")
        relative = self._extract_relative_value(entry_mapping, field_prefix)
        compiled: dict[str, str] = {"root": root, "relative": relative}

        # Optionally copy fetch/source metadata when present (fetch outputs
        # may define `source_url`, `source_url_template`, and `scope`).
        source_url = entry_mapping.get("source_url")
        if isinstance(source_url, str) and source_url:
            compiled["source_url"] = source_url
        source_template = entry_mapping.get("source_url_template")
        if isinstance(source_template, str) and source_template:
            compiled["source_url_template"] = source_template
        scope = entry_mapping.get("scope")
        if isinstance(scope, str) and scope:
            compiled["scope"] = scope

        return compiled

    def _extract_relative_value(self, entry_mapping: dict[str, object], field_prefix: str) -> str:
        relative_path = entry_mapping.get("relative_path")
        relative_template = entry_mapping.get("relative_path_template")

        if isinstance(relative_path, str) and relative_path:
            return relative_path
        if isinstance(relative_template, str) and relative_template:
            return relative_template

        self._fail(
            f"Expected {field_prefix} to define a non-empty relative_path or relative_path_template"
        )

    def _load_indicator_config(self, indicator: str) -> dict[str, object]:
        if indicator in self._indicator_config_cache:
            return self._indicator_config_cache[indicator]

        config_path = SCRIPTS_ROOT / indicator / f"{indicator}_config.json"
        config = self._load_json_object(config_path, f"indicator {indicator}")
        self._indicator_config_cache[indicator] = config
        return config

    def _load_shared_config(self) -> dict[str, object]:
        if self._shared_config_cache is not None:
            return self._shared_config_cache

        self._shared_config_cache = self._load_json_object(SHARED_CONFIG_PATH, "shared")
        return self._shared_config_cache

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
        version_block = versions.get(version)
        if version_block is None:
            self._fail(f"Version {version!r} not found for indicator {indicator}")
        return self._require_mapping(version_block, f"{indicator}.versions.{version}")

    def _get_indicator_stage_block(
        self,
        version_block: dict[str, object],
        indicator: str,
        version: str,
        stage: str,
    ) -> dict[str, object]:
        stages = self._require_mapping(version_block.get("stages"), f"{indicator}.versions.{version}.stages")
        return self._require_mapping(stages.get(stage), f"{indicator}.versions.{version}.stages.{stage}")

    def _get_shared_version_block(
        self,
        config: dict[str, object],
        asset_name: str,
        version: str,
    ) -> dict[str, object]:
        # Delegate shared-version validation to the centralized resolver
        # to avoid duplicating lookup logic and error messages.
        return resolve_path.get_shared_version_block(config, asset_name, version)

    def _get_shared_stage_block(
        self,
        version_block: dict[str, object],
        asset_name: str,
        version: str,
        stage: str,
    ) -> dict[str, object]:
        stages = self._require_mapping(version_block.get("stages"), f"shared.assets.{asset_name}.{version}.stages")
        return self._require_mapping(stages.get(stage), f"shared.assets.{asset_name}.{version}.stages.{stage}")

    def _get_root(self, config: dict[str, object], environment: str, label: str) -> str:
        field_name = "local_root_path" if environment == "local" else "remote_root_path"
        return self._require_non_empty_string(config.get(field_name), f"{label}.{field_name}")

    def _validate_environment(self, environment: str) -> None:
        if environment not in VALID_ENVIRONMENTS:
            self._fail(f"Invalid environment {environment!r}; expected one of {sorted(VALID_ENVIRONMENTS)}")

    def _validate_target_type(self, target_type: str) -> None:
        if target_type not in VALID_TARGET_TYPES:
            self._fail(f"Invalid target_type {target_type!r}; expected one of {sorted(VALID_TARGET_TYPES)}")

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
    target_type: str,
    name: str,
    stage: str,
    version: str,
    environment: str = "local",
) -> dict[str, dict[str, dict[str, str]]]:
    return _get_builder().get_stage_manifest(target_type, name, stage, version, environment)