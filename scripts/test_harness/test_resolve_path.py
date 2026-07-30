"""Dry-run harness for the shared resolve_path module.

Examples (run from the `scripts/` folder):

    python3 test_harness/test_resolve_path.py --storage-mode local --type indicator --name o3 version 1.2020 --key raw_o3
    python3 test_harness/test_resolve_path.py --storage-mode remote --type indicator --name o3 version 1.2021 --key raw_o3
    python3 test_harness/test_resolve_path.py --storage-mode local --type shared --name tiger_bg --version 2020 --category downloads --key tiger_bg_2020
    python3 test_harness/test_resolve_path.py --storage-mode remote --type shared --name census_block_weights version 1.2022 --category preprocessed_input

Notes:
- See the config files for what names, stages, and versions are valid for any particular indicator or shared asset. 
  Error messages here are not particularly diagnostic, so if you get a "not found" error, check the config files to 
  make sure you are using a valid combination.
- TODO: We've accumulated several naming inconsistencies as the tested code and the configurations have evolved.
  For example, "--storage-mode" here is called "--environment" in the underlying code but is corresponds to the 
  '-l/--location' argument in the cli for most of the other current modules. I'm not fixing any of that 
  today but it should be cleaned up soon to avoid confusion as more modules start to use this code's services.
"""

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
    parser.add_argument("--storage-mode", dest="storage_mode", required=True, choices=("local","remote"))
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
        return resolve_path.get_download_path(args.name, args.version, args.key, args.storage_mode)

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
        environment=args.storage_mode,
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