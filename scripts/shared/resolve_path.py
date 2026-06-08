"""Strict configuration-backed path resolver for indicator and shared assets."""

from __future__ import annotations

import json
import logging
from pathlib import Path


LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
SHARED_CONFIG_PATH = Path(__file__).with_name("shared_config.json")
VALID_ENVIRONMENTS = {"local", "remote"}
VALID_SHARED_CATEGORIES = {"downloads", "preprocessed_input"}


class _PathResolver:
    def __init__(self) -> None:
        self._indicator_config_cache: dict[str, dict[str, object]] = {}
        self._shared_config_cache: dict[str, object] | None = None

    def get_download_path(
        self,
        indicator: str,
        version: str,
        asset_key: str,
        environment: str = "local",
    ) -> dict[str, str]:
        self._validate_environment(environment)
        config = self._load_indicator_config(indicator)
        version_block = self._get_indicator_version_block(config, indicator, version)
        fetch_outputs = self._get_stage_outputs(
            version_block,
            f"{indicator}.versions.{version}",
            "fetch",
        )
        asset = self._require_mapping(
            fetch_outputs.get(asset_key),
            f"{indicator}.versions.{version}.stages.fetch.outputs.{asset_key}",
        )
        relative = self._require_non_empty_string(
            asset.get("relative_path"),
            f"{indicator}.versions.{version}.stages.fetch.outputs.{asset_key}.relative_path",
        )
        root = self._get_root(config, environment, f"indicator {indicator}")
        return {"root": root, "relative": relative}

    def get_dependency_version(self, indicator: str, version: str, dependency: str) -> str:
        config = self._load_indicator_config(indicator)
        version_block = self._get_indicator_version_block(config, indicator, version)
        required_shared_assets = self._require_mapping(
            version_block.get("required_shared_assets"),
            f"{indicator}.versions.{version}.required_shared_assets",
        )
        return self._require_non_empty_string(
            required_shared_assets.get(dependency),
            f"{indicator}.versions.{version}.required_shared_assets.{dependency}",
        )

    def get_shared_asset_path(
        self,
        asset: str,
        version: str,
        category: str,
        asset_key: str | None = None,
        environment: str = "local",
    ) -> dict[str, str]:
        self._validate_environment(environment)
        if category not in VALID_SHARED_CATEGORIES:
            self._fail(
                f"Invalid shared asset category {category!r}; expected one of {sorted(VALID_SHARED_CATEGORIES)}"
            )

        config = self._load_shared_config()
        assets = self._require_mapping(config.get("assets"), "shared.assets")
        asset_versions = self._require_mapping(assets.get(asset), f"shared.assets.{asset}")
        version_block = self._require_mapping(asset_versions.get(version), f"shared.assets.{asset}.{version}")

        if category == "preprocessed_input":
            preprocess_outputs = self._get_stage_outputs(
                version_block,
                f"shared.assets.{asset}.{version}",
                "preprocess",
            )
            preprocess_asset = self._require_mapping(
                preprocess_outputs.get(asset),
                f"shared.assets.{asset}.{version}.stages.preprocess.outputs.{asset}",
            )
            relative = self._require_non_empty_string(
                preprocess_asset.get("relative_path_template"),
                f"shared.assets.{asset}.{version}.stages.preprocess.outputs.{asset}.relative_path_template",
            )
        else:
            if not asset_key:
                self._fail(
                    "asset_key is required when resolving shared downloads"
                )
            fetch_outputs = self._get_stage_outputs(
                version_block,
                f"shared.assets.{asset}.{version}",
                "fetch",
            )
            download_asset = self._require_mapping(
                fetch_outputs.get(asset_key),
                f"shared.assets.{asset}.{version}.stages.fetch.outputs.{asset_key}",
            )
            relative = self._require_non_empty_string(
                download_asset.get("relative_path_template"),
                f"shared.assets.{asset}.{version}.stages.fetch.outputs.{asset_key}.relative_path_template",
            )

        root = self._get_root(config, environment, "shared")
        return {"root": root, "relative": relative}

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
        return self._require_mapping(versions.get(version), f"{indicator}.versions.{version}")

    def _get_stage_outputs(
        self,
        version_block: dict[str, object],
        field_prefix: str,
        stage_name: str,
    ) -> dict[str, object]:
        stages = self._require_mapping(version_block.get("stages"), f"{field_prefix}.stages")
        stage_block = self._require_mapping(stages.get(stage_name), f"{field_prefix}.stages.{stage_name}")
        return self._require_mapping(stage_block.get("outputs"), f"{field_prefix}.stages.{stage_name}.outputs")

    def _get_root(self, config: dict[str, object], environment: str, label: str) -> str:
        field_name = "local_root_path" if environment == "local" else "remote_root_path"
        return self._require_non_empty_string(config.get(field_name), f"{label}.{field_name}")

    def _validate_environment(self, environment: str) -> None:
        if environment not in VALID_ENVIRONMENTS:
            self._fail(f"Invalid environment {environment!r}; expected one of {sorted(VALID_ENVIRONMENTS)}")

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


_RESOLVER: _PathResolver | None = None


def _get_resolver() -> _PathResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = _PathResolver()
    return _RESOLVER


def get_download_path(
    indicator: str,
    version: str,
    asset_key: str,
    environment: str = "local",
) -> dict[str, str]:
    return _get_resolver().get_download_path(indicator, version, asset_key, environment)


def get_dependency_version(indicator: str, version: str, dependency: str) -> str:
    return _get_resolver().get_dependency_version(indicator, version, dependency)


def get_shared_asset_path(
    asset: str,
    version: str,
    category: str,
    asset_key: str | None = None,
    environment: str = "local",
) -> dict[str, str]:
    return _get_resolver().get_shared_asset_path(asset, version, category, asset_key, environment)