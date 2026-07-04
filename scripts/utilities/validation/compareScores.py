#!/usr/bin/env python3
"""compareScores.py

Slice 1 implementation: config + CLI merge, validation, --dry-run, and logging.

This script intentionally implements only the configuration handling and validation
for the first slice. The actual comparison and plotting are implemented in later
slices.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional


REQUIRED_FIELDS = (
    'indicator',
    'state',
    'file_a',
    'id_a',
    'score_a',
    'version_a',
    'file_b',
    'id_b',
    'score_b',
    'version_b',
)


def load_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open('r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception as exc:
        raise RuntimeError(f'Failed to load config {path}: {exc}') from exc


def merge_config(file_config: Optional[Dict[str, Any]], cli_args: argparse.Namespace) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if file_config:
        cfg.update(file_config)

    # Map CLI names to config keys
    cli_to_key = {
        'indicator': 'indicator',
        'state': 'state',
        'file_a': 'file_a',
        'id_a': 'id_a',
        'score_a': 'score_a',
        'version_a': 'version_a',
        'file_b': 'file_b',
        'id_b': 'id_b',
        'score_b': 'score_b',
        'version_b': 'version_b',
        'out_dir': 'out_dir',
    }

    for arg_name, key in cli_to_key.items():
        val = getattr(cli_args, arg_name, None)
        if val is not None:
            cfg[key] = val

    # Normalize common path strings to plain strings
    for pkey in ('file_a', 'file_b', 'out_dir'):
        if pkey in cfg and isinstance(cfg[pkey], str):
            cfg[pkey] = cfg[pkey].strip()

    return cfg


def validate_config(cfg: Dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [f for f in REQUIRED_FIELDS if not cfg.get(f)]
    return (len(missing) == 0, missing)


def init_logging(script_dir: Path) -> None:
    log_path = script_dir / 'compareScores.log'
    handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[handler],
    )
    logging.info('=== compareScores log started ===')


def pretty_print_config(cfg: Dict[str, Any]) -> None:
    print(json.dumps(cfg, indent=2, sort_keys=True))


def pretty_print_cli_style_config(cfg: Dict[str, Any]) -> None:
    """Print a user-facing config with underscore keys converted to hyphen keys.

    This is intended for dry-run output so the displayed option names match the
    CLI hyphenated form (e.g. --version-a) while internal logging continues to
    use underscore keys.
    """
    cli_cfg = { (k.replace('_', '-')): v for k, v in cfg.items() }
    print(json.dumps(cli_cfg, indent=2, sort_keys=True))


def default_out_dir(state: str, indicator: str, version_a: str, version_b: str) -> str:
    return str(Path('output') / state / 'compare' / f'{indicator}_{version_a}_vs_{version_b}')


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compare two indicator score CSVs (slice 1: config/dry-run)')
    parser.add_argument('--config', type=str, default=None, help='Path to JSON config file (optional)')

    parser.add_argument('--indicator', type=str, help='Indicator slug (used for labels and filenames)')
    parser.add_argument('--state', type=str, help='Two-letter state postal code (required)')

    parser.add_argument('--file-a', type=str, help='Path to CSV file A')
    parser.add_argument('--id-a', type=str, help='ID column name in file A')
    parser.add_argument('--score-a', type=str, help='Score column name in file A')
    parser.add_argument('--version-a', type=str, help='Version label for file A')

    parser.add_argument('--file-b', type=str, help='Path to CSV file B')
    parser.add_argument('--id-b', type=str, help='ID column name in file B')
    parser.add_argument('--score-b', type=str, help='Score column name in file B')
    parser.add_argument('--version-b', type=str, help='Version label for file B')

    parser.add_argument('--out-dir', type=str, default=None, help='Optional output directory')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print merged config; do not run')

    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    init_logging(script_dir)

    file_cfg = None
    if args.config:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            print(f'ERROR: config file not found: {cfg_path}', file=sys.stderr)
            logging.error('Config file not found: %s', cfg_path)
            return 2
        try:
            file_cfg = load_config(cfg_path)
        except Exception as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            logging.exception('Failed to load config')
            return 3

    merged = merge_config(file_cfg, args)

    # If out_dir unspecified, propose a default (do not create yet)
    if 'out_dir' not in merged or not merged.get('out_dir'):
        if merged.get('state') and merged.get('indicator') and merged.get('version_a') and merged.get('version_b'):
            merged['out_dir'] = default_out_dir(merged['state'], merged['indicator'], merged['version_a'], merged['version_b'])

    valid, missing = validate_config(merged)
    if not valid:
        print('ERROR: missing required configuration values:', file=sys.stderr)
        for m in missing:
            print(f'  - {m}', file=sys.stderr)
        logging.error('Validation failed; missing: %s', missing)
        return 4

    logging.info('Merged run configuration: %s', json.dumps(merged))

    if args.dry_run:
        print('Dry-run: merged configuration (no files will be read or written)')
        pretty_print_cli_style_config(merged)
        logging.info('Dry-run completed')
        return 0

    # For slice 1 we stop after validation and reporting; later slices will perform reading and comparison.
    print('Configuration validated. Ready to run comparator (not implemented in slice 1).')
    pretty_print_config(merged)
    logging.info('Validation-only run completed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
