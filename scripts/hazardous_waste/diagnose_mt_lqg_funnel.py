"""
diagnose_mt_lqg_funnel.py

Purpose:
	Diagnose how Montana 2021 BR reporting handlers move through the current
	hazardous-waste LQG filtering rule by comparing Montana BR handler IDs to the
	full RCRA facilities file.

What it does:
	- reads Montana BR_REPORTING_2021 handler IDs
	- looks up each handler in RCRA_FACILITIES.csv
	- flags whether the matching RCRA row is active, marked as LQG, has an
	  operating TSDF flag, and has usable coordinates
	- assigns a drop/survival reason for the current LQG rule
	- writes three CSV outputs under pipeline/outputs/mt_lqg_diagnostic/
	- prints summary counts and frequency tables to stdout for quick inspection

Runtime arguments:
	None. This script is currently hard-coded to Montana input paths and output
	locations and is intended as a one-off diagnostic, not a general-purpose CLI.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BR_MT_PATH = SCRIPT_DIR / 'pipeline' / 'downloads' / 'BR_REPORTING_2021' / 'BR_REPORTING_2021_MT.csv'
RCRA_PATH = SCRIPT_DIR / 'pipeline' / 'downloads' / 'RCRA_FACILITIES.csv'
OUTPUT_DIR = SCRIPT_DIR / 'pipeline' / 'outputs' / 'mt_lqg_diagnostic'


def normalize_cell_text(value: str | None) -> str:
	if value is None:
		return ''
	return value.strip()


def normalize_handler_id(value: str | None) -> str:
	return normalize_cell_text(value).upper()


def has_alpha_code(value: str | None) -> bool:
	normalized_value = normalize_cell_text(value)
	if not normalized_value:
		return False
	return any(character.isalpha() for character in normalized_value)


def rcra_row_is_active(row: dict[str, str]) -> bool:
	return has_alpha_code(row.get('ACTIVE_SITE'))


def active_site_starts_with_h(value: str | None) -> bool:
	return normalize_cell_text(value).upper().startswith('H')


def rcra_row_has_operating_tsdf(row: dict[str, str]) -> bool:
	return has_alpha_code(row.get('OPERATING_TSDF'))


def rcra_row_is_lqg(row: dict[str, str]) -> bool:
	status_text = normalize_cell_text(row.get('HREPORT_UNIVERSE_RECORD')).upper()
	if not status_text:
		return False
	status_parts = [part.strip() for part in status_text.split(',')]
	return 'LQG' in status_parts


def rcra_row_is_lqg_or_sqg(row: dict[str, str]) -> bool:
	status_text = normalize_cell_text(row.get('HREPORT_UNIVERSE_RECORD')).upper()
	if not status_text:
		return False
	status_parts = [part.strip() for part in status_text.split(',')]
	return 'LQG' in status_parts or 'SQG' in status_parts


def parse_float_or_none(value: str | None) -> float | None:
	normalized_value = normalize_cell_text(value)
	if not normalized_value:
		return None
	try:
		return float(normalized_value)
	except ValueError:
		return None


def rcra_row_has_usable_coordinates(row: dict[str, str]) -> bool:
	latitude = parse_float_or_none(row.get('LATITUDE83'))
	longitude = parse_float_or_none(row.get('LONGITUDE83'))
	if latitude is None or longitude is None:
		return False
	return latitude != 0.0 and longitude != 0.0


def determine_drop_reason(rcra_row: dict[str, str] | None) -> str:
	if rcra_row is None:
		return 'missing_in_rcra'
	if not rcra_row_is_lqg(rcra_row):
		return 'not_lqg_by_current_rule'
	if not rcra_row_has_usable_coordinates(rcra_row):
		return 'invalid_coordinates'
	return 'survives_current_lqg_rule'


def read_mt_br_handlers() -> tuple[list[dict[str, str]], dict[str, int]]:
	rows: list[dict[str, str]] = []
	handler_counts: Counter[str] = Counter()
	with BR_MT_PATH.open('r', encoding='utf-8-sig', newline='') as stream:
		reader = csv.DictReader(stream)
		if reader.fieldnames is None or 'HANDLER ID' not in reader.fieldnames:
			raise RuntimeError(f'Expected HANDLER ID column in {BR_MT_PATH}')
		for row in reader:
			handler_id = normalize_handler_id(row.get('HANDLER ID'))
			if not handler_id:
				continue
			handler_counts[handler_id] += 1
			rows.append(row)
	return rows, dict(handler_counts)


def read_rcra_by_handler() -> dict[str, dict[str, str]]:
	rcra_by_handler: dict[str, dict[str, str]] = {}
	duplicate_handlers = 0
	with RCRA_PATH.open('r', encoding='utf-8-sig', newline='') as stream:
		reader = csv.DictReader(stream)
		if reader.fieldnames is None or 'ID_NUMBER' not in reader.fieldnames:
			raise RuntimeError(f'Expected ID_NUMBER column in {RCRA_PATH}')
		for row in reader:
			handler_id = normalize_handler_id(row.get('ID_NUMBER'))
			if not handler_id:
				continue
			if handler_id in rcra_by_handler:
				duplicate_handlers += 1
				continue
			rcra_by_handler[handler_id] = row
	print(f'RCRA duplicate handler rows ignored: {duplicate_handlers}')
	return rcra_by_handler


def build_diagnostic_rows(mt_handler_counts: dict[str, int], rcra_by_handler: dict[str, dict[str, str]]) -> list[dict[str, str]]:
	rows: list[dict[str, str]] = []
	for handler_id in sorted(mt_handler_counts):
		rcra_row = rcra_by_handler.get(handler_id)
		drop_reason = determine_drop_reason(rcra_row)
		rows.append(
			{
				'HANDLER ID': handler_id,
				'br_row_count': str(mt_handler_counts[handler_id]),
				'in_rcra': 'Y' if rcra_row is not None else 'N',
				'is_active': 'Y' if rcra_row is not None and rcra_row_is_active(rcra_row) else 'N',
				'has_operating_tsdf': 'Y' if rcra_row is not None and rcra_row_has_operating_tsdf(rcra_row) else 'N',
				'is_lqg_by_current_rule': 'Y' if rcra_row is not None and rcra_row_is_lqg(rcra_row) else 'N',
				'has_valid_coordinates': 'Y' if rcra_row is not None and rcra_row_has_usable_coordinates(rcra_row) else 'N',
				'drop_reason': drop_reason,
				'FACILITY_NAME': normalize_cell_text(rcra_row.get('FACILITY_NAME')) if rcra_row else '',
				'CITY_NAME': normalize_cell_text(rcra_row.get('CITY_NAME')) if rcra_row else '',
				'STATE_CODE': normalize_cell_text(rcra_row.get('STATE_CODE')) if rcra_row else '',
				'ZIP_CODE': normalize_cell_text(rcra_row.get('ZIP_CODE')) if rcra_row else '',
				'LATITUDE83': normalize_cell_text(rcra_row.get('LATITUDE83')) if rcra_row else '',
				'LONGITUDE83': normalize_cell_text(rcra_row.get('LONGITUDE83')) if rcra_row else '',
				'HREPORT_UNIVERSE_RECORD': normalize_cell_text(rcra_row.get('HREPORT_UNIVERSE_RECORD')) if rcra_row else '',
				'ACTIVE_SITE': normalize_cell_text(rcra_row.get('ACTIVE_SITE')) if rcra_row else '',
				'FED_WASTE_GENERATOR': normalize_cell_text(rcra_row.get('FED_WASTE_GENERATOR')) if rcra_row else '',
				'OPERATING_TSDF': normalize_cell_text(rcra_row.get('OPERATING_TSDF')) if rcra_row else '',
			}
		)
	return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	fieldnames = list(rows[0].keys()) if rows else []
	with path.open('w', encoding='utf-8', newline='') as stream:
		writer = csv.DictWriter(stream, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(rows)


def print_counter(title: str, counter: Counter[str], limit: int = 20) -> None:
	print(title)
	for key, count in counter.most_common(limit):
		label = key if key else '<blank>'
		print(f'  {label}: {count}')


def main() -> int:
	print('Starting MT LQG diagnostic')
	print(f'BR input: {BR_MT_PATH}')
	print(f'RCRA input: {RCRA_PATH}')

	if not BR_MT_PATH.exists():
		raise FileNotFoundError(f'MT BR file not found: {BR_MT_PATH}')
	if not RCRA_PATH.exists():
		raise FileNotFoundError(f'RCRA file not found: {RCRA_PATH}')

	_, mt_handler_counts = read_mt_br_handlers()
	rcra_by_handler = read_rcra_by_handler()
	diagnostic_rows = build_diagnostic_rows(mt_handler_counts, rcra_by_handler)

	all_rows_path = OUTPUT_DIR / 'mt_br_handlers_with_rcra_flags.csv'
	survivors_path = OUTPUT_DIR / 'mt_br_handlers_surviving_current_lqg_rule.csv'
	dropped_path = OUTPUT_DIR / 'mt_br_handlers_dropped_with_reason.csv'

	write_csv(all_rows_path, diagnostic_rows)
	write_csv(
		survivors_path,
		[row for row in diagnostic_rows if row['drop_reason'] == 'survives_current_lqg_rule'],
	)
	write_csv(
		dropped_path,
		[row for row in diagnostic_rows if row['drop_reason'] != 'survives_current_lqg_rule'],
	)

	dropped_lqg_rows = [row for row in diagnostic_rows if row['drop_reason'] == 'not_lqg_by_current_rule']
	drop_reason_counts = Counter(row['drop_reason'] for row in diagnostic_rows)
	in_rcra_count = sum(1 for row in diagnostic_rows if row['in_rcra'] == 'Y')
	lqg_count = sum(1 for row in diagnostic_rows if row['is_lqg_by_current_rule'] == 'Y')
	valid_coord_count = sum(1 for row in diagnostic_rows if row['has_valid_coordinates'] == 'Y')
	survivor_count = drop_reason_counts['survives_current_lqg_rule']
	lqg_or_sqg_and_active_h_count = 0
	lqg_or_sqg_and_active_h_with_valid_coords_count = 0
	hreport_counter = Counter(row['HREPORT_UNIVERSE_RECORD'] for row in dropped_lqg_rows)
	fed_generator_counter = Counter(row['FED_WASTE_GENERATOR'] for row in dropped_lqg_rows)
	combined_counter = Counter(
		f"{row['HREPORT_UNIVERSE_RECORD']} | {row['FED_WASTE_GENERATOR']}"
		for row in dropped_lqg_rows
	)
	active_site_h_counter = Counter(
		'Y' if active_site_starts_with_h(row['ACTIVE_SITE']) else 'N'
		for row in dropped_lqg_rows
	)
	survivor_active_site_h_counter = Counter(
		'Y' if active_site_starts_with_h(row['ACTIVE_SITE']) else 'N'
		for row in diagnostic_rows if row['drop_reason'] == 'survives_current_lqg_rule'
	)
	for row in diagnostic_rows:
		if row['in_rcra'] != 'Y':
			continue
		if not active_site_starts_with_h(row['ACTIVE_SITE']):
			continue
		rule_row = {
			'HREPORT_UNIVERSE_RECORD': row['HREPORT_UNIVERSE_RECORD'],
		}
		if not rcra_row_is_lqg_or_sqg(rule_row):
			continue
		lqg_or_sqg_and_active_h_count += 1
		if row['has_valid_coordinates'] == 'Y':
			lqg_or_sqg_and_active_h_with_valid_coords_count += 1

	print(f'MT BR unique handlers: {len(mt_handler_counts)}')
	print(f'MT BR handlers found in RCRA: {in_rcra_count}')
	print(f'MT BR handlers marked LQG by current rule: {lqg_count}')
	print(f'MT BR handlers with valid coordinates: {valid_coord_count}')
	print(f'MT BR handlers surviving current LQG rule: {survivor_count}')
	print(
		'MT BR handlers meeting (LQG or SQG) and ACTIVE_SITE starts with H: '
		f'{lqg_or_sqg_and_active_h_count}'
	)
	print(
		'MT BR handlers meeting (LQG or SQG) and ACTIVE_SITE starts with H with valid coordinates: '
		f'{lqg_or_sqg_and_active_h_with_valid_coords_count}'
	)
	print(f'Drop reasons: {dict(sorted(drop_reason_counts.items()))}')
	print_counter('Dropped not_lqg_by_current_rule: HREPORT_UNIVERSE_RECORD frequency', hreport_counter)
	print_counter('Dropped not_lqg_by_current_rule: FED_WASTE_GENERATOR frequency', fed_generator_counter)
	print_counter('Dropped not_lqg_by_current_rule: HREPORT_UNIVERSE_RECORD | FED_WASTE_GENERATOR frequency', combined_counter)
	print_counter('Dropped not_lqg_by_current_rule: ACTIVE_SITE starts with H', active_site_h_counter)
	print_counter('Survivors: ACTIVE_SITE starts with H', survivor_active_site_h_counter)
	print(f'Wrote: {all_rows_path}')
	print(f'Wrote: {survivors_path}')
	print(f'Wrote: {dropped_path}')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())