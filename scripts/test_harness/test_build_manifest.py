"""Dry-run harness for the shared build_manifest module."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


LOGGER = logging.getLogger(__name__)

SHARED_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "shared"
if str(SHARED_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS_DIR))

import build_manifest  # type: ignore[import-not-found]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run stage manifest build for an indicator or shared asset.")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--target-type", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--version", required=True)
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    try:
        manifest = build_manifest.get_stage_manifest(
            target_type=args.target_type,
            name=args.name,
            stage=args.stage,
            version=args.version,
            environment=args.environment,
        )
    except Exception as exc:
        LOGGER.error("Manifest dry-run failed: %s", exc)
        return 1

    payload = {"status": "DRY_RUN_MANIFEST_BUILT", **manifest}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())