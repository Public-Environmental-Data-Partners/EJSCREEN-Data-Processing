"""External source provenance report for indicator releases.

PURPOSE:
    Documents the real-world (external) files used by the fetch and preprocess
    stages of one or more indicators, plus the shared assets those indicators
    require. Interim outputs passed from one pipeline stage to the next are not
    documented. Everything is resolved through `scripts.shared.build_manifest`.

USAGE:
    python scripts/utilities/external_sources.py --indicators [names, comma-delimited] --version [version]

EXAMPLE:
    python scripts/utilities/external_sources.py --indicators o3,pm25 --version 1.2022

OUTPUT FILES (written next to this script):
    external_sources.json - the report (overwritten on each run; written only
                            after every indicator and shared asset resolves).
    external_sources.log  - run log (appended).

OUTPUT SHAPE:
    {
      "indicators": {"o3": {"version": ..., "external_sources": [{name, file_name, source_url}]}},
      "shared": {"census_block_weights": {"version": ..., "required_by": [...],
                                           "external_sources": [{name, file_name, [source_url], [scope]}]}}
    }
    - Indicator sources are the fetch downloads that the preprocess stage consumes.
    - Shared assets use their fetch downloads when they have a fetch stage; otherwise
      their preprocess outputs are listed by stored file name only (no source_url).
    - TEMPORARY: every tiger_* shared asset that has a version "2022" is always added
      (hardcoded, logged), with an empty "required_by" if no indicator requires it.
    - Stored file names are reported as configured; {postal}-style placeholders are
      left unresolved. Notes fields are never reported.

Any problem (bad indicator or version, missing source_url, unmatched input, etc.)
raises immediately, exits non-zero, and leaves no partial output.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import scripts.shared.build_manifest as build_manifest
import scripts.shared.resolve_path as resolve_path

DEFAULT_OUTPUT_FILENAME = "external_sources.json"
DEFAULT_LOG_FILENAME = "external_sources.log"
HARDCODED_TIGER_VERSION = "2022"
SCRIPT_DIR = Path(__file__).resolve().parent

LOGGER = logging.getLogger(__name__)


class ExternalSourcesError(ValueError):
    pass


def _fail(message: str) -> None:
    LOGGER.error(message)
    raise ExternalSourcesError(message)


def _file_name(relative: str) -> str:
    return relative.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        _fail(f"Missing {label} config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        _fail(f"Expected {label} config to be a JSON object: {path}")
    return payload


def _source_entry(name: str, entry: dict[str, str], label: str, require_url: bool) -> dict[str, str]:
    source = {"name": name, "file_name": _file_name(entry["relative"])}
    url = entry.get("source_url") or entry.get("source_url_template")
    if url:
        source["source_url"] = url
    elif require_url:
        _fail(f"{label} defines no source_url")
    if entry.get("scope"):
        source["scope"] = entry["scope"]
    return source


def _indicator_sources(indicator: str, version: str) -> list[dict[str, str]]:
    fetch = build_manifest.get_stage_manifest("indicator", indicator, "fetch", version)["outputs"]
    preprocess = build_manifest.get_stage_manifest("indicator", indicator, "preprocess", version)["inputs"]

    # The manifest resolves indicator_download inputs to {root, relative}, so match on `relative`.
    fetch_by_relative = {entry["relative"]: (name, entry) for name, entry in fetch.items()}
    sources = []
    for entry in preprocess.values():
        match = fetch_by_relative.get(entry["relative"])
        if match is None:
            continue
        name, fetch_entry = match
        sources.append(
            _source_entry(name, fetch_entry, f"{indicator} {version} fetch output {name!r}", require_url=True)
        )

    if not sources:
        _fail(f"Indicator {indicator} {version} has no external sources consumed by its preprocess stage")
    return sources


def _shared_sources(asset: str, version: str, shared_config: dict) -> list[dict[str, str]]:
    version_block = resolve_path.get_shared_version_block(shared_config, asset, version)
    stages = version_block.get("stages")
    if not isinstance(stages, dict):
        _fail(f"Expected shared.assets.{asset}.{version}.stages to be a JSON object")

    label = f"shared asset {asset} {version}"
    if "fetch" in stages:
        outputs = build_manifest.get_stage_manifest("shared", asset, "fetch", version)["outputs"]
        sources = [_source_entry(n, e, f"{label} fetch output {n!r}", require_url=True) for n, e in outputs.items()]
    elif "preprocess" in stages:
        outputs = build_manifest.get_stage_manifest("shared", asset, "preprocess", version)["outputs"]
        sources = [_source_entry(n, e, label, require_url=False) for n, e in outputs.items()]
    else:
        _fail(f"{label} has neither a fetch nor a preprocess stage")

    if not sources:
        _fail(f"{label} has no files to report")
    return sources


def build_report(indicators: list[str], version: str) -> dict:
    shared_config = _load_json(resolve_path.SCRIPTS_ROOT / "shared" / "shared_config.json", "shared")
    report: dict = {"indicators": {}, "shared": {}}

    for indicator in indicators:
        config = _load_json(
            resolve_path.SCRIPTS_ROOT / indicator / f"{indicator}_config.json", f"indicator {indicator}"
        )
        versions = config.get("versions")
        if not isinstance(versions, dict) or version not in versions:
            valid = sorted(versions) if isinstance(versions, dict) else []
            _fail(f"Version {version!r} not found for indicator {indicator}; valid versions: {valid}")

        report["indicators"][indicator] = {
            "version": version,
            "external_sources": _indicator_sources(indicator, version),
        }

        required = versions[version].get("required_shared_assets")
        if not isinstance(required, dict) or not required:
            _fail(f"Indicator {indicator} {version} defines no required_shared_assets")

        for asset in required:
            shared_version = resolve_path.get_dependency_version(indicator, version, asset)
            entry = report["shared"].get(asset)
            if entry is None:
                report["shared"][asset] = {
                    "version": shared_version,
                    "required_by": [indicator],
                    "external_sources": _shared_sources(asset, shared_version, shared_config),
                }
            elif entry["version"] != shared_version:
                _fail(
                    f"Shared asset {asset} is required at conflicting versions: "
                    f"{entry['version']} and {shared_version} (by {indicator})"
                )
            else:
                entry["required_by"].append(indicator)

    _add_hardcoded_tiger_assets(report, shared_config)
    return report


def _add_hardcoded_tiger_assets(report: dict, shared_config: dict) -> None:
    # Temporary: other code that doesn't use the config files yet depends on the
    # TIGER 2022 files, so they are always reported whether or not an indicator requires them.
    LOGGER.info(
        "Hardcoding TIGER line shared assets to version %s; they are not required by the requested indicators",
        HARDCODED_TIGER_VERSION,
    )
    assets = shared_config.get("assets")
    if not isinstance(assets, dict):
        _fail("Expected shared.assets to be a JSON object")

    added = 0
    for asset, versions in assets.items():
        if not asset.startswith("tiger_") or not isinstance(versions, dict) or HARDCODED_TIGER_VERSION not in versions:
            continue
        existing = report["shared"].get(asset)
        if existing is not None and existing["version"] != HARDCODED_TIGER_VERSION:
            _fail(
                f"Shared asset {asset} is required at version {existing['version']}, "
                f"conflicting with hardcoded version {HARDCODED_TIGER_VERSION}"
            )
        if existing is None:
            report["shared"][asset] = {
                "version": HARDCODED_TIGER_VERSION,
                "required_by": [],
                "external_sources": _shared_sources(asset, HARDCODED_TIGER_VERSION, shared_config),
            }
        added += 1

    if not added:
        _fail(f"No TIGER shared assets found at hardcoded version {HARDCODED_TIGER_VERSION}")


def parse_indicators(raw: str) -> list[str]:
    indicators = [item.strip() for item in raw.split(",")]
    if not all(indicators):
        _fail(f"Empty indicator name in --indicators {raw!r}")
    if len(set(indicators)) != len(indicators):
        _fail(f"Duplicate indicator in --indicators {raw!r}")
    return indicators


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Documents the external sources used by indicator releases")
    parser.add_argument("-i", "--indicators", type=str, required=True,
                        help="Comma-separated list of indicator(s), e.g. o3,pm25")
    parser.add_argument("-v", "--version", type=str, required=True,
                        help="The version of the indicator(s), e.g. 1.2022")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(SCRIPT_DIR / DEFAULT_LOG_FILENAME, mode="a", encoding="utf-8")],
    )

    try:
        report = build_report(parse_indicators(args.indicators), args.version)
    except Exception as exc:
        LOGGER.exception("External source report failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # write the output to a folder where it will be ignored by git
    output_dir = SCRIPT_DIR / "output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / DEFAULT_OUTPUT_FILENAME
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    LOGGER.info("Wrote %s", output_path)
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
