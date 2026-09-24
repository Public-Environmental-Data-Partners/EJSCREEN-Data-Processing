"""Centralized configuration-backed path resolver for indicators and shared assets.

Public (flat) module-level functions:
- get_download_path(indicator: str, version: str, asset_key: str, environment: str = "local") -> dict
- get_dependency_version(indicator: str, version: str, dependency: str) -> str
- get_shared_asset_path(asset: str, version: str, category: str, asset_key: str | None = None, environment: str = "local") -> dict
- get_indicator_root(indicator: str, version: str, environment: str = "local") -> str
- get_shared_root(asset: str, version: str, environment: str = "local") -> str
- get_shared_version_block(config: dict, asset_name: str, version: str) -> dict
- substitute_version_placeholder(template: str, version: str) -> str
- get_pipeline_root(environment: str = "local") -> str

Return contract:
- Functions that resolve locations return a dictionary containing at least the keys
    "root" and "relative".
- When `environment == 'local'`, `root` is returned as an absolute path resolved
    against the project root. When `environment == 'remote'`, `root` is the raw remote
    root string (for example, an S3 URI).
- `environment` accepts only the values 'local' or 'remote' and will raise on
    invalid input.

Notes:
- The module exposes a flat functional interface suitable for use from R via
    `reticulate` and from other callers. Internally a singleton `_PathResolver`
    caches parsed JSON configs to minimize I/O.
- TODO: We've accumulated several naming inconsistencies as the tested code and the configurations have evolved.
  For example, "--environment" here corresponds to the  
  '-l/--location' argument in the cli for most of the other current modules and to --storage-mode in the test harnesses. 
  I'm not fixing any of that 
  today but it should be cleaned up soon to avoid confusion as more modules start to use this code's services.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

# For any local paths that we need to resolve, we want them to
# consistently be resolved relative to the project root, no matter
# where this code is executed from. This file always lives at
# <repo_root>/scripts/shared/resolve_path.py under an editable install or
# git checkout, so the repo root is a fixed number of parents away -- no
# need to search for a `.git` anchor.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
PIPELINE_ROOT = REPO_ROOT / "pipeline"
if not SCRIPTS_ROOT.is_dir() or not PIPELINE_ROOT.is_dir():
    raise RuntimeError(
        f"Architectural Error: expected sibling 'scripts' and 'pipeline' directories under "
        f"{REPO_ROOT}, but at least one is missing. This module assumes the source tree is "
        f"present on disk (e.g. an editable install or git checkout); a non-editable wheel "
        f"install would not satisfy this layout."
    )

LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

SHARED_CONFIG_PATH = Path(__file__).with_name("shared_config.json")
VALID_ENVIRONMENTS = {"local", "remote"}
VALID_SHARED_CATEGORIES = {"downloads", "preprocessed_input"}


def substitute_version_placeholder(template: str, version: str) -> str:
    """Replace a literal `{version}` placeholder with the resolved version string.

    This lets config authors write version-scoped relative paths (e.g.
    `v{version}/downloads/...`) that automatically track the `versions`/`assets.*`
    dict key instead of requiring a manually maintained duplicate field. Other
    unresolved placeholders (e.g. `{postal}`, `{fips}`) are left intact for
    later substitution by the caller.
    """
    return template.replace("{version}", version)


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
        relative = substitute_version_placeholder(relative, version)
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
            relative = substitute_version_placeholder(relative, version)
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
            relative = substitute_version_placeholder(relative, version)

        root = self._get_root(config, environment, "shared")
        return {"root": root, "relative": relative}

    def _load_indicator_config(self, indicator: str) -> dict[str, object]:
        logging.info("Loading indicator config for %s, SCRIPTS_ROOT: %s", indicator, SCRIPTS_ROOT)
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

    def _get_shared_version_block(
        self,
        config: dict[str, object],
        asset_name: str,
        version: str,
    ) -> dict[str, object]:
        assets = self._require_mapping(config.get("assets"), "shared.assets")
        asset_versions = self._require_mapping(assets.get(asset_name), f"shared.assets.{asset_name}")
        version_block = asset_versions.get(version)
        if version_block is None:
            self._fail(f"Version {version!r} not found for shared asset {asset_name}")
        return self._require_mapping(version_block, f"shared.assets.{asset_name}.{version}")

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


def get_indicator_root(indicator: str, version: str, environment: str = "local") -> str:
    """Return the configured root path for an indicator.

    For `environment=='local'` this returns an absolute path resolved
    against the project root. For `environment=='remote'` it returns
    the raw remote root string from the config (e.g. an S3 URI).
    """
    resolver = _get_resolver()
    config = resolver._load_indicator_config(indicator)
    # validate version exists
    _ = resolver._get_indicator_version_block(config, indicator, version)
    root = resolver._get_root(config, environment, f"indicator {indicator}")
    if environment == "local":
        resolved_root = str((REPO_ROOT / root).resolve())
    else:
        resolved_root = root

    logging.info("Resolved indicator root: %s", resolved_root)
    return resolved_root


def get_shared_root(asset: str, version: str, environment: str = "local") -> str:
    """Return the configured root path for a shared asset.

    For `environment=='local'` this returns an absolute path resolved
    against the project root. For `environment=='remote'` it returns
    the raw remote root string from the shared config.
    """
    resolver = _get_resolver()
    config = resolver._load_shared_config()
    # validate asset/version exist
    _ = resolver._get_shared_version_block(config, asset, version)
    root = resolver._get_root(config, environment, "shared")
    if environment == "local":
        resolved_root = str((REPO_ROOT / root).resolve())
    else:
        resolved_root = root

    logging.info("Resolved shared root: %s", resolved_root)
    return resolved_root


def get_shared_version_block(config: dict[str, object], asset_name: str, version: str) -> dict[str, object]:
    """Public helper to validate and return a shared asset's version block.

    This delegates to the internal resolver implementation so callers can
    rely on a single authoritative implementation for shared-version
    validation and error messages.
    """
    return _get_resolver()._get_shared_version_block(config, asset_name, version)


def get_pipeline_root(environment: str = "local") -> str:
    """Return the pipeline root (the parent of the shared assets root).

    This is a generic anchor for callers (e.g. the compare/validation
    scripts) that need to resolve arbitrary, free-form relative paths that
    aren't tied to a specific indicator or shared-asset schema entry. It
    reuses the same `local_root_path`/`remote_root_path` fields from
    `shared_config.json` that `get_shared_root` relies on, so it stays in
    sync automatically if that root ever moves.

    For `environment=='local'` this returns an absolute path resolved
    against the project root. For `environment=='remote'` it returns the
    raw remote root string (e.g. an S3 URI).
    """
    resolver = _get_resolver()
    resolver._validate_environment(environment)
    config = resolver._load_shared_config()
    shared_root = resolver._get_root(config, environment, "shared")
    logging.info("Resolved shared root for pipeline: %s", shared_root)

    if environment == "local":
        pipeline_root = str((REPO_ROOT / shared_root).resolve().parent)
    else:
        stripped = shared_root.rstrip("/")
        parent, _, _ = stripped.rpartition("/")
        pipeline_root = parent + "/" if parent else stripped + "/"

    logging.info("Resolved pipeline root: %s", pipeline_root)
    return pipeline_root