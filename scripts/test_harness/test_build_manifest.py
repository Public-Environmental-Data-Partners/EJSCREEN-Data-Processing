"""Dry-run harness for the shared build_manifest module.

Examples (run from the `scripts/` folder):

    python3 test_harness/test_build_manifest.py --storage-mode local --target-type indicator --name o3 --stage preprocess --version 1.2020
    python3 test_harness/test_build_manifest.py --storage-mode remote --target-type indicator --name o3 --stage score --version 1.2020
    python3 test_harness/test_build_manifest.py --storage-mode local --target-type shared --name tiger_bg --stage fetch --version 2020
    python3 test_harness/test_build_manifest.py --storage-mode local --target-type shared --name census_block_weights --stage preprocess --version 1.0

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

import build_manifest  # type: ignore[import-not-found]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run stage manifest build for an indicator or shared asset.")
    parser.add_argument("--storage-mode", dest="storage_mode", required=True, choices=("local","remote"))
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
            environment=args.storage_mode,
        )
    except Exception as exc:
        LOGGER.error("Manifest dry-run failed: %s", exc)
        return 1

    payload = {"status": "DRY_RUN_MANIFEST_BUILT", **manifest}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())