"""verify_census_block_weights.py

Purpose:
    Fail-fast pre-check used by the indicator pipeline runners. Verifies that
    the shared census_block_weights asset required by the requested indicator
    config version is already present at the requested location, before any
    fetch/preprocess/score work starts.

Runtime arguments:
    --indicator: indicator whose config version should be checked (o3 or pm25).
    --version: indicator config version to check (e.g. 1.2022).
    --location: storage location to check (local or remote; defaults to local).

Exit code:
    0 if the resolved census_block_weights file exists at the requested location.
    1 if it is missing, after printing the expected path.

Example (run from the `scripts` folder or repo root):
    python3 runners/verify_census_block_weights.py --indicator o3 --version 1.2022 --location remote
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import sys

import scripts.shared.resolve_path as resolve_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-i', '--indicator',
        required=True,
        choices=('o3', 'pm25'),
        help='indicator config to check (o3 or pm25)',
    )
    parser.add_argument(
        '-v', '--version',
        required=True,
        help='indicator config version, e.g. 1.2022',
    )
    parser.add_argument(
        '-l', '--location',
        choices=('local', 'remote'),
        default='local',
        help='storage location to check (local or remote; default: local)',
    )
    args = parser.parse_args()

    if args.location == 'remote':
        dotenv = importlib.import_module('dotenv')
        dotenv.load_dotenv()

    shared_version = resolve_path.get_dependency_version(
        args.indicator,
        args.version,
        'census_block_weights',
    )
    asset = resolve_path.get_shared_asset_path(
        asset='census_block_weights',
        version=shared_version,
        category='preprocessed_input',
        environment=args.location,
    )
    shared_root = resolve_path.get_shared_root(
        'census_block_weights',
        shared_version,
        args.location,
    )
    if args.location == 'local':
        path = Path(shared_root) / asset['relative']
        exists = path.is_file()
    else:
        fsspec = importlib.import_module('fsspec')
        path = f"{shared_root.rstrip('/')}/{asset['relative'].lstrip('/')}"
        exists = bool(fsspec.open(path).fs.exists(path))

    if not exists:
        print(f'Missing required shared asset: census_block_weights (v{shared_version})')
        print(f'Expected {args.location} path: {path}')
        return 1

    print(f'Found shared asset census_block_weights (v{shared_version}): {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())