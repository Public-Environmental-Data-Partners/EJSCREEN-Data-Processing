"""Dry-run harness for the shared resolve_path module."""

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

import resolve_path  # type: ignore[import-not-found]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run path resolution for indicator and shared assets.")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--type", required=True, choices=["indicator", "shared"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--key")
    parser.add_argument("--category")
    return parser


def _resolve_from_args(args: argparse.Namespace) -> dict[str, str]:
    if args.type == "indicator":
        if not args.key:
            raise ValueError("--key is required when --type indicator")
        if args.category is not None:
            raise ValueError("--category is not valid when --type indicator")
        return resolve_path.get_download_path(args.name, args.version, args.key, args.environment)

    if args.category is None:
        raise ValueError("--category is required when --type shared")
    if args.category == "downloads" and not args.key:
        raise ValueError("--key is required when resolving shared downloads")
    if args.category == "preprocessed_input" and args.key is not None:
        raise ValueError("--key is not valid for shared preprocessed_input resolution")

    return resolve_path.get_shared_asset_path(
        asset=args.name,
        version=args.version,
        category=args.category,
        asset_key=args.key,
        environment=args.environment,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args()

    try:
        resolved = _resolve_from_args(args)
    except Exception as exc:
        LOGGER.error("Resolver dry-run failed: %s", exc)
        return 1

    payload = {"status": "DRY_RUN_RESOLVED", **resolved}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())