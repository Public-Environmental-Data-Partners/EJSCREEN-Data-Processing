"""
hazardous_waste_preprocess.py

Purpose:
	Provide the hazardous-waste preprocessing entrypoint while keeping runtime
	infrastructure separate from source-specific filtering logic.

Current transitional slice:
	- Keeps the current local-or-remote runtime pattern and ZIP access helpers.
	- Preserves the existing config loading, path resolution, and fail-fast schema checks.
	- Isolates the current HD_REPORTING-specific extraction path behind a single pipeline call.
	- Writes the same provisional and canonical outputs as the current implementation.

This is a transitional structure change for the planned RCRA_FACILITIES plus
BR_REPORTING rewrite. The current HD_REPORTING-specific filtering logic remains
in place temporarily so it can be replaced cleanly in the next steps.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import argparse
import csv
import fnmatch
import importlib
import io
import json
import logging
import zipfile
from collections import Counter


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_PATH = SCRIPT_DIR / "hazardous_waste_preprocess_config.json"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
	source_key: str
	logical_name: str
	description: str
	relative_path: str
	inner_zip_member_filename: str | None
	target_csv_globs: tuple[str, ...]
	required_columns: tuple[str, ...]
	key_columns: tuple[str, ...]
	handler_id_column: str
	id_normalization: str
	target_biennial_year: int | None = None


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	local_root_path: str
	remote_root_path: str
	provisional_output_relative_path: str
	canonical_output_relative_path: str
	planned_master_rcra_source: SourceDescriptor
	planned_br_reporting_source: SourceDescriptor
	current_hd_reporting_source: SourceDescriptor


@dataclass(frozen=True, slots=True)
class CsvSchemaSummary:
	csv_member_name: str
	column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HdReportingUniverseSummary:
	total_rows: int
	operating_tsdf_rows: int
	federal_lqg_rows: int
	state_lqg_rows: int
	significant_facility_rows: int
	active_mask_rows: int
	qualifying_rows: int
	rejected_rows: int
	unique_handler_ids: int


@dataclass(frozen=True, slots=True)
class SiteExtractionSummary:
	total_rows: int
	operating_tsdf_rows: int
	federal_lqg_rows: int
	state_lqg_rows: int
	significant_facility_rows: int
	active_mask_rows: int
	qualifying_rows: int
	rejected_rows: int
	validation_failure_count: int
	provisional_row_count: int
	unique_handler_ids: int
	handler_dedup_removed_count: int
	validation_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class FinalizationSummary:
	provisional_row_count: int
	handler_dedup_removed_count: int
	canonical_row_count: int


@dataclass(frozen=True, slots=True)
class CurrentSliceResult:
	extraction_summary: SiteExtractionSummary
	finalization_summary: FinalizationSummary


def build_source_descriptor(raw_descriptor: dict) -> SourceDescriptor:
	return SourceDescriptor(
		source_key=raw_descriptor["source_key"],
		logical_name=raw_descriptor["logical_name"],
		description=raw_descriptor["description"],
		relative_path=raw_descriptor["relative_path"],
		inner_zip_member_filename=raw_descriptor.get("inner_zip_member_filename"),
		target_csv_globs=tuple(raw_descriptor.get("target_csv_globs", [])),
		required_columns=tuple(raw_descriptor["required_columns"]),
		key_columns=tuple(raw_descriptor["key_columns"]),
		handler_id_column=raw_descriptor["handler_id_column"],
		id_normalization=raw_descriptor["id_normalization"],
		target_biennial_year=raw_descriptor.get("target_biennial_year"),
	)


def load_config_payload() -> dict:
	if not CONFIG_FILE_PATH.exists():
		raise FileNotFoundError(f"Config file not found: {CONFIG_FILE_PATH}")
	with CONFIG_FILE_PATH.open("r", encoding="utf-8") as config_stream:
		return json.load(config_stream)


def get_config(argv=None) -> Config:
	config_payload = load_config_payload()
	planned_sources_payload = config_payload["planned_sources"]
	current_slice_payload = config_payload["current_slice"]
	default_hd_archive_filename = Path(current_slice_payload["hd_reporting_source"]["relative_path"]).name

	parser = argparse.ArgumentParser(
		description="Run the hazardous-waste preprocessing transition script."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads through the local root path or the remote S3 root path.",
	)
	parser.add_argument(
		"--hd-outer-archive-filename",
		dest="hd_outer_archive_filename",
		default=default_hd_archive_filename,
		help=(
			"Override only the transitional HD outer archive filename used by the current slice. "
			f"Default from config: {default_hd_archive_filename}"
		),
	)
	args = parser.parse_args(argv)

	current_hd_source = build_source_descriptor(current_slice_payload["hd_reporting_source"])
	current_hd_source = replace(
		current_hd_source,
		relative_path=str(Path(current_hd_source.relative_path).with_name(args.hd_outer_archive_filename)),
	)

	return Config(
		storage_mode=args.storage_mode,
		local_root_path=config_payload["local_root_path"],
		remote_root_path=config_payload["remote_root_path"],
		provisional_output_relative_path=config_payload["provisional_output_relative_path"],
		canonical_output_relative_path=config_payload["canonical_output_relative_path"],
		planned_master_rcra_source=build_source_descriptor(planned_sources_payload["master_rcra_source"]),
		planned_br_reporting_source=build_source_descriptor(planned_sources_payload["br_reporting_source"]),
		current_hd_reporting_source=current_hd_source,
	)


def initialize_runtime_dependencies(cfg: Config) -> None:
	if cfg.storage_mode != "remote":
		return

	dotenv = importlib.import_module("dotenv")
	s3fs = importlib.import_module("s3fs")
	load_fsspec_module()

	dotenv.load_dotenv()
	if s3fs is None:
		raise RuntimeError("s3fs failed to import for remote mode")


def load_fsspec_module():
	return importlib.import_module("fsspec")


def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith("s3://")


def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip("/") + "/" + relative_path.lstrip("/")
	return str(Path(root_path) / relative_path)


def get_active_root_path(cfg: Config) -> str:
	if cfg.storage_mode == "local":
		return cfg.local_root_path
	if cfg.storage_mode == "remote":
		return cfg.remote_root_path
	raise ValueError(f"Unsupported storage mode: {cfg.storage_mode}")


def get_source_path(cfg: Config, source: SourceDescriptor) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), source.relative_path)


def get_current_hd_outer_archive_path(cfg: Config) -> str:
	return get_source_path(cfg, cfg.current_hd_reporting_source)


def get_provisional_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.provisional_output_relative_path)


def get_canonical_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.canonical_output_relative_path)


def log_runtime_context(cfg: Config) -> None:
	logging.info("Config file: %s", CONFIG_FILE_PATH)
	logging.info("Storage mode: %s", cfg.storage_mode)
	logging.info("Active root path: %s", get_active_root_path(cfg))
	logging.info("Current-slice HD outer archive path: %s", get_current_hd_outer_archive_path(cfg))
	logging.info("Planned master RCRA source path: %s", get_source_path(cfg, cfg.planned_master_rcra_source))
	logging.info("Planned BR reporting source path: %s", get_source_path(cfg, cfg.planned_br_reporting_source))
	logging.info("Provisional output path: %s", get_provisional_output_path(cfg))
	logging.info("Canonical output path: %s", get_canonical_output_path(cfg))


def open_binary_input_stream(path: str):
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, "rb")
	return open(path, "rb")


def open_text_input_stream(path: str):
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, "r", encoding="utf-8-sig", newline="")
	return open(path, "r", encoding="utf-8-sig", newline="")


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def open_text_output_stream(path: str):
	ensure_local_parent_dir(path)
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, "w", newline="")
	return open(path, "w", encoding="utf-8", newline="")


def list_archive_members(archive: zipfile.ZipFile) -> list[str]:
	return [name for name in archive.namelist() if name and not name.endswith("/")]


def require_member_present(member_names: list[str], expected_member_name: str, archive_label: str) -> str:
	matching_members = [member_name for member_name in member_names if Path(member_name).name == expected_member_name]
	if not matching_members:
		raise RuntimeError(
			f"Expected member {expected_member_name!r} was not found in {archive_label}."
		)
	if len(matching_members) > 1:
		raise RuntimeError(
			f"Expected exactly one member named {expected_member_name!r} in {archive_label}, "
			f"but found {len(matching_members)} matches: {matching_members}"
		)
	return matching_members[0]


def select_matching_members(member_names: list[str], patterns: tuple[str, ...], source_name: str) -> list[str]:
	matched_members = []
	for member_name in member_names:
		leaf_name = Path(member_name).name
		if any(fnmatch.fnmatch(leaf_name, pattern) for pattern in patterns):
			matched_members.append(member_name)
	if not matched_members:
		raise RuntimeError(
			f"No CSV members matched the configured patterns for {source_name}: {patterns}"
		)
	return matched_members


def get_required_columns_for_source(source: SourceDescriptor) -> tuple[str, ...]:
	return source.required_columns


def get_key_columns_for_source(source: SourceDescriptor) -> tuple[str, ...]:
	return source.key_columns


def normalize_cell_text(value: str | None) -> str:
	if value is None:
		return ""
	return value.strip()


def normalize_handler_id(value: str | None) -> str:
	return normalize_cell_text(value).upper()


def parse_float_or_none(value: str | None) -> float | None:
	normalized_value = normalize_cell_text(value)
	if not normalized_value:
		return None
	try:
		return float(normalized_value)
	except ValueError:
		return None


def has_usable_coordinates(row: dict[str, str]) -> bool:
	latitude = parse_float_or_none(row.get("LOCATION LATITUDE"))
	longitude = parse_float_or_none(row.get("LOCATION LONGITUDE"))
	if latitude is None or longitude is None:
		return False
	return latitude != 0.0 and longitude != 0.0


def is_yes_flag(value: str | None) -> bool:
	return normalize_cell_text(value).upper() == "Y"


def is_one_flag(value: str | None) -> bool:
	return normalize_cell_text(value) == "1"


def has_operating_tsdf_status(value: str | None) -> bool:
	return bool(value) and value != "------"


def is_active_site(row: dict[str, str]) -> bool:
	return is_yes_flag(row.get("IN A UNIVERSE")) or normalize_cell_text(row.get("ACTIVE SITE")).upper() == "H"


def classify_hd_reporting_row(row: dict[str, str]) -> tuple[bool, bool, bool, bool]:
	is_operating_tsdf = has_operating_tsdf_status(row.get("OPERATING TSDF"))
	is_federal_lqg = is_one_flag(row.get("FED WASTE GENERATOR"))
	is_state_lqg = is_one_flag(row.get("STATE WASTE GENERATOR"))
	is_significant_facility = is_operating_tsdf or is_federal_lqg
	return is_operating_tsdf, is_federal_lqg, is_state_lqg, is_significant_facility


def classify_hd_reporting_validation_failure(row: dict[str, str]) -> tuple[str, str] | None:
	validation_errors = []
	if not normalize_handler_id(row.get("HANDLER ID")):
		validation_errors.append("missing_handler_id")
	if not has_usable_coordinates(row):
		validation_errors.append("invalid_coordinates")
	if not validation_errors:
		return None
	validation_reason = ";".join(validation_errors)
	return validation_reason, f"HD_REPORTING row failed site-level validation: {validation_reason}"


def build_hd_reporting_site_row(
	row: dict[str, str],
	csv_member_name: str,
	source_member_row_number: int,
	is_operating_tsdf: bool,
	is_federal_lqg: bool,
	is_state_lqg: bool,
) -> dict[str, str]:
	is_lqg_site = is_federal_lqg
	site_class = "both" if is_operating_tsdf and is_lqg_site else "tsdf" if is_operating_tsdf else "lqg"
	return {
		"HANDLER ID": normalize_handler_id(row.get("HANDLER ID")),
		"HANDLER NAME": normalize_cell_text(row.get("HANDLER NAME")),
		"LOCATION CITY": normalize_cell_text(row.get("LOCATION CITY")),
		"LOCATION STATE": normalize_cell_text(row.get("LOCATION STATE")),
		"LOCATION ZIP": normalize_cell_text(row.get("LOCATION ZIP")),
		"LOCATION LATITUDE": normalize_cell_text(row.get("LOCATION LATITUDE")),
		"LOCATION LONGITUDE": normalize_cell_text(row.get("LOCATION LONGITUDE")),
		"OPERATING TSDF": normalize_cell_text(row.get("OPERATING TSDF")),
		"FED WASTE GENERATOR": normalize_cell_text(row.get("FED WASTE GENERATOR")),
		"IN A UNIVERSE": normalize_cell_text(row.get("IN A UNIVERSE")),
		"ACTIVE SITE": normalize_cell_text(row.get("ACTIVE SITE")),
		"is_lqg_site": "Y" if is_lqg_site else "N",
		"is_tsdf_site": "Y" if is_operating_tsdf else "N",
		"site_class": site_class,
		"source_dataset": "HD_REPORTING",
		"source_member_filename": csv_member_name,
		"source_member_row_number": str(source_member_row_number),
	}


def get_provisional_output_fieldnames() -> list[str]:
	return [
		"HANDLER ID",
		"HANDLER NAME",
		"LOCATION CITY",
		"LOCATION STATE",
		"LOCATION ZIP",
		"LOCATION LATITUDE",
		"LOCATION LONGITUDE",
		"OPERATING TSDF",
		"FED WASTE GENERATOR",
		"IN A UNIVERSE",
		"ACTIVE SITE",
		"is_lqg_site",
		"is_tsdf_site",
		"site_class",
		"source_dataset",
		"source_member_filename",
		"source_member_row_number",
	]


def get_canonical_sort_key(row: dict[str, str]) -> tuple[int, int, str, int]:
	site_class_priority = {
		"both": 3,
		"tsdf": 2,
		"lqg": 1,
	}.get(row.get("site_class", ""), 0)
	return (
		site_class_priority,
		1 if row.get("HANDLER NAME", "") else 0,
		row.get("source_member_filename", ""),
		-int(row.get("source_member_row_number", "0")),
	)


def extract_hd_reporting_sites(cfg: Config, provisional_output_path: str) -> tuple[list[dict[str, str]], SiteExtractionSummary]:
	source = cfg.current_hd_reporting_source
	source_outer_archive_path = get_source_path(cfg, source)
	provisional_rows: list[dict[str, str]] = []
	validation_reason_counter: Counter[str] = Counter()
	total_rows = 0
	total_operating_tsdf_rows = 0
	total_federal_lqg_rows = 0
	total_state_lqg_rows = 0
	total_significant_facility_rows = 0
	total_active_mask_rows = 0
	total_qualifying_rows = 0
	validation_failure_count = 0
	provisional_fieldnames = get_provisional_output_fieldnames()

	with open_text_output_stream(provisional_output_path) as output_stream:
		writer = csv.DictWriter(output_stream, fieldnames=provisional_fieldnames)
		writer.writeheader()

		with open_outer_zip_archive(source_outer_archive_path) as outer_archive:
			outer_member_names = list_archive_members(outer_archive)
			inner_zip_member_name = require_member_present(
				member_names=outer_member_names,
				expected_member_name=source.inner_zip_member_filename,
				archive_label=f"archive {source_outer_archive_path}",
			)

			with open_inner_zip_archive(outer_archive, inner_zip_member_name) as inner_archive:
				inner_member_names = list_archive_members(inner_archive)
				matched_csv_members = select_matching_members(
					member_names=inner_member_names,
					patterns=source.target_csv_globs,
					source_name=source.logical_name,
				)

				for matched_csv_member in matched_csv_members:
					logging.info("Streaming HD_REPORTING site rows from %s", matched_csv_member)
					with open_inner_csv_text_stream(inner_archive, matched_csv_member) as text_stream:
						reader = csv.DictReader(text_stream)
						if reader.fieldnames is None:
							raise RuntimeError(f"CSV member is missing a header row: {matched_csv_member}")
						validate_required_columns(tuple(reader.fieldnames), source.required_columns, source.logical_name, matched_csv_member)

						source_member_row_number = 0
						for row in reader:
							source_member_row_number += 1
							total_rows += 1
							is_operating_tsdf, is_federal_lqg, is_state_lqg, is_significant_facility = classify_hd_reporting_row(row)
							passes_active_mask = is_active_site(row)
							if is_operating_tsdf:
								total_operating_tsdf_rows += 1
							if is_federal_lqg:
								total_federal_lqg_rows += 1
							if is_state_lqg:
								total_state_lqg_rows += 1
							if is_significant_facility:
								total_significant_facility_rows += 1
							if passes_active_mask:
								total_active_mask_rows += 1
							if not (is_significant_facility and passes_active_mask):
								continue

							total_qualifying_rows += 1
							validation_failure = classify_hd_reporting_validation_failure(row)
							if validation_failure is not None:
								validation_failure_count += 1
								validation_reason_counter[validation_failure[0]] += 1
								continue

							provisional_row = build_hd_reporting_site_row(
								row=row,
								csv_member_name=matched_csv_member,
								source_member_row_number=source_member_row_number,
								is_operating_tsdf=is_operating_tsdf,
								is_federal_lqg=is_federal_lqg,
								is_state_lqg=is_state_lqg,
							)
							writer.writerow(provisional_row)
							provisional_rows.append(provisional_row)

	handler_dedup_removed_count = len(provisional_rows) - len({row["HANDLER ID"] for row in provisional_rows})
	unique_handler_ids = len({row["HANDLER ID"] for row in provisional_rows})
	return provisional_rows, SiteExtractionSummary(
		total_rows=total_rows,
		operating_tsdf_rows=total_operating_tsdf_rows,
		federal_lqg_rows=total_federal_lqg_rows,
		state_lqg_rows=total_state_lqg_rows,
		significant_facility_rows=total_significant_facility_rows,
		active_mask_rows=total_active_mask_rows,
		qualifying_rows=total_qualifying_rows,
		rejected_rows=total_rows - total_qualifying_rows,
		validation_failure_count=validation_failure_count,
		provisional_row_count=len(provisional_rows),
		unique_handler_ids=unique_handler_ids,
		handler_dedup_removed_count=handler_dedup_removed_count,
		validation_reason_counts=tuple(sorted(validation_reason_counter.items())),
	)


def finalize_hd_reporting_sites(
	provisional_rows: list[dict[str, str]],
	canonical_output_path: str,
) -> FinalizationSummary:
	provisional_row_count = len(provisional_rows)
	fieldnames = get_provisional_output_fieldnames()
	if not provisional_rows:
		with open_text_output_stream(canonical_output_path) as output_stream:
			writer = csv.DictWriter(output_stream, fieldnames=fieldnames)
			writer.writeheader()
		return FinalizationSummary(
			provisional_row_count=0,
			handler_dedup_removed_count=0,
			canonical_row_count=0,
		)

	sorted_rows = sorted(provisional_rows, key=get_canonical_sort_key, reverse=True)
	seen_handler_ids: set[str] = set()
	canonical_rows: list[dict[str, str]] = []
	for row in sorted_rows:
		handler_id = row["HANDLER ID"]
		if handler_id in seen_handler_ids:
			continue
		seen_handler_ids.add(handler_id)
		canonical_rows.append(row)
	canonical_rows.sort(key=lambda row: row["HANDLER ID"])
	handler_dedup_removed_count = provisional_row_count - len(canonical_rows)

	with open_text_output_stream(canonical_output_path) as output_stream:
		writer = csv.DictWriter(output_stream, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(canonical_rows)

	return FinalizationSummary(
		provisional_row_count=provisional_row_count,
		handler_dedup_removed_count=handler_dedup_removed_count,
		canonical_row_count=len(canonical_rows),
	)


def parse_csv_header_line(text_stream: io.TextIOWrapper, csv_member_name: str) -> tuple[str, ...]:
	reader = csv.reader(text_stream)
	try:
		header_row = next(reader)
	except StopIteration as exc:
		raise RuntimeError(f"CSV member is empty: {csv_member_name}") from exc
	return tuple(header_row)


def validate_required_columns(column_names: tuple[str, ...], required_columns: tuple[str, ...], source_name: str, csv_member_name: str) -> None:
	column_name_set = set(column_names)
	missing_columns = [column_name for column_name in required_columns if column_name not in column_name_set]
	if missing_columns:
		raise RuntimeError(
			f"Source {source_name} is missing required columns {missing_columns} in CSV member {csv_member_name}."
		)


def validate_consistent_member_schema(schema_summaries: list[CsvSchemaSummary], source_name: str) -> None:
	if not schema_summaries:
		raise RuntimeError(f"No schema summaries were collected for source {source_name}")
	first_summary = schema_summaries[0]
	for schema_summary in schema_summaries[1:]:
		if schema_summary.column_names != first_summary.column_names:
			raise RuntimeError(
				f"Source {source_name} has inconsistent CSV schemas between {first_summary.csv_member_name} "
				f"and {schema_summary.csv_member_name}."
			)


def log_source_schema(source: SourceDescriptor, schema_summary: CsvSchemaSummary) -> None:
	key_columns = get_key_columns_for_source(source)
	present_key_columns = [column_name for column_name in key_columns if column_name in schema_summary.column_names]
	logging.info("Schema column count for %s / %s: %d", source.logical_name, schema_summary.csv_member_name, len(schema_summary.column_names))
	logging.info("Key columns present for %s / %s: %s", source.logical_name, schema_summary.csv_member_name, present_key_columns)


@contextmanager
def open_outer_zip_archive(outer_archive_path: str):
	try:
		with open_binary_input_stream(outer_archive_path) as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				yield archive
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except zipfile.BadZipFile as exc:
		raise RuntimeError(f"Outer archive is not a valid ZIP file: {outer_archive_path}") from exc


@contextmanager
def open_inner_zip_archive(outer_archive: zipfile.ZipFile, inner_zip_member_name: str):
	try:
		with outer_archive.open(inner_zip_member_name, "r") as inner_zip_stream:
			inner_zip_bytes = io.BytesIO(inner_zip_stream.read())
	except KeyError as exc:
		raise RuntimeError(
			f"Inner ZIP member not found in outer archive: {inner_zip_member_name}"
		) from exc

	try:
		with zipfile.ZipFile(inner_zip_bytes, "r") as inner_archive:
			yield inner_archive
	except zipfile.BadZipFile as exc:
		raise RuntimeError(
			f"Configured inner member is not a valid ZIP archive: {inner_zip_member_name}"
		) from exc


@contextmanager
def open_inner_csv_text_stream(inner_archive: zipfile.ZipFile, csv_member_name: str):
	try:
		with inner_archive.open(csv_member_name, "r") as csv_binary_stream:
			text_stream = io.TextIOWrapper(csv_binary_stream, encoding="utf-8-sig", newline="")
			try:
				yield text_stream
			finally:
				text_stream.detach()
	except KeyError as exc:
		raise RuntimeError(f"CSV member not found in inner archive: {csv_member_name}") from exc


@contextmanager
def open_outer_csv_text_stream(outer_archive: zipfile.ZipFile, csv_member_name: str):
	try:
		with outer_archive.open(csv_member_name, "r") as csv_binary_stream:
			text_stream = io.TextIOWrapper(csv_binary_stream, encoding="utf-8-sig", newline="")
			try:
				yield text_stream
			finally:
				text_stream.detach()
	except KeyError as exc:
		raise RuntimeError(f"CSV member not found in outer archive: {csv_member_name}") from exc


def inventory_direct_csv_source(cfg: Config, source: SourceDescriptor) -> None:
	source_path = get_source_path(cfg, source)
	logging.info("------------------------------------------------------------")
	logging.info("Inventorying planned source: %s", source.logical_name)
	logging.info("Source description: %s", source.description)
	logging.info("Source path: %s", source_path)
	logging.info("Required columns for %s: %s", source.logical_name, list(source.required_columns))
	with open_text_input_stream(source_path) as text_stream:
		column_names = parse_csv_header_line(text_stream, Path(source.relative_path).name)
	schema_summary = CsvSchemaSummary(
		csv_member_name=Path(source.relative_path).name,
		column_names=column_names,
	)
	validate_required_columns(column_names, source.required_columns, source.logical_name, schema_summary.csv_member_name)
	log_source_schema(source, schema_summary)


def inventory_outer_zip_csv_source(cfg: Config, source: SourceDescriptor) -> None:
	source_path = get_source_path(cfg, source)
	logging.info("------------------------------------------------------------")
	logging.info("Inventorying planned source: %s", source.logical_name)
	logging.info("Source description: %s", source.description)
	logging.info("Source path: %s", source_path)
	if source.target_biennial_year is not None:
		logging.info("Configured biennial year for %s: %s", source.logical_name, source.target_biennial_year)
	logging.info("Required columns for %s: %s", source.logical_name, list(source.required_columns))
	with open_outer_zip_archive(source_path) as outer_archive:
		outer_member_names = list_archive_members(outer_archive)
		logging.info("Outer archive member count for %s: %d", source.logical_name, len(outer_member_names))
		for member_name in outer_member_names:
			logging.info("Outer archive member for %s: %s", source.logical_name, member_name)

		if source.inner_zip_member_filename:
			inner_zip_member_name = require_member_present(
				member_names=outer_member_names,
				expected_member_name=source.inner_zip_member_filename,
				archive_label=f"archive {source_path}",
			)
			logging.info("Configured inner ZIP member for %s: %s", source.logical_name, inner_zip_member_name)
			with open_inner_zip_archive(outer_archive, inner_zip_member_name) as inner_archive:
				inner_member_names = list_archive_members(inner_archive)
				logging.info("Inner archive member count for %s: %d", source.logical_name, len(inner_member_names))
				for inner_member_name in inner_member_names:
					logging.info("Inner archive member for %s: %s", source.logical_name, inner_member_name)
				matched_csv_members = select_matching_members(
					member_names=inner_member_names,
					patterns=source.target_csv_globs,
					source_name=source.logical_name,
				)
				schema_summaries = []
				for matched_csv_member in matched_csv_members:
					logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)
					with open_inner_csv_text_stream(inner_archive, matched_csv_member) as text_stream:
						column_names = parse_csv_header_line(text_stream, matched_csv_member)
					schema_summary = CsvSchemaSummary(
						csv_member_name=matched_csv_member,
						column_names=column_names,
					)
					validate_required_columns(column_names, source.required_columns, source.logical_name, matched_csv_member)
					log_source_schema(source, schema_summary)
					schema_summaries.append(schema_summary)
				validate_consistent_member_schema(schema_summaries, source.logical_name)
				return

		matched_csv_members = select_matching_members(
			member_names=outer_member_names,
			patterns=source.target_csv_globs,
			source_name=source.logical_name,
		)
		schema_summaries = []
		for matched_csv_member in matched_csv_members:
			logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)
			with open_outer_csv_text_stream(outer_archive, matched_csv_member) as text_stream:
				column_names = parse_csv_header_line(text_stream, matched_csv_member)
			schema_summary = CsvSchemaSummary(
				csv_member_name=matched_csv_member,
				column_names=column_names,
			)
			validate_required_columns(column_names, source.required_columns, source.logical_name, matched_csv_member)
			log_source_schema(source, schema_summary)
			schema_summaries.append(schema_summary)
		validate_consistent_member_schema(schema_summaries, source.logical_name)


def inventory_planned_source_layout(cfg: Config) -> None:
	inventory_direct_csv_source(cfg, cfg.planned_master_rcra_source)
	inventory_outer_zip_csv_source(cfg, cfg.planned_br_reporting_source)


def summarize_hd_reporting_member(inner_archive: zipfile.ZipFile, csv_member_name: str) -> HdReportingUniverseSummary:
	total_rows = 0
	operating_tsdf_rows = 0
	federal_lqg_rows = 0
	state_lqg_rows = 0
	significant_facility_rows = 0
	active_mask_rows = 0
	qualifying_rows = 0
	unique_handler_ids: set[str] = set()

	with open_inner_csv_text_stream(inner_archive, csv_member_name) as text_stream:
		reader = csv.DictReader(text_stream)
		if reader.fieldnames is None:
			raise RuntimeError(f"CSV member is missing a header row: {csv_member_name}")

		for row in reader:
			total_rows += 1
			is_operating_tsdf, is_federal_lqg, is_state_lqg, is_significant_facility = classify_hd_reporting_row(row)
			passes_active_mask = is_active_site(row)
			if is_operating_tsdf:
				operating_tsdf_rows += 1
			if is_federal_lqg:
				federal_lqg_rows += 1
			if is_state_lqg:
				state_lqg_rows += 1
			if is_significant_facility:
				significant_facility_rows += 1
			if passes_active_mask:
				active_mask_rows += 1
			if is_significant_facility and passes_active_mask:
				qualifying_rows += 1
				handler_id = normalize_handler_id(row.get("HANDLER ID"))
				if handler_id:
					unique_handler_ids.add(handler_id)

	return HdReportingUniverseSummary(
		total_rows=total_rows,
		operating_tsdf_rows=operating_tsdf_rows,
		federal_lqg_rows=federal_lqg_rows,
		state_lqg_rows=state_lqg_rows,
		significant_facility_rows=significant_facility_rows,
		active_mask_rows=active_mask_rows,
		qualifying_rows=qualifying_rows,
		rejected_rows=total_rows - qualifying_rows,
		unique_handler_ids=len(unique_handler_ids),
	)


def inventory_current_source_layout(cfg: Config) -> None:
	source = cfg.current_hd_reporting_source
	source_outer_archive_path = get_source_path(cfg, source)
	with open_outer_zip_archive(source_outer_archive_path) as outer_archive:
		outer_member_names = list_archive_members(outer_archive)
		logging.info("------------------------------------------------------------")
		logging.info("Inventorying current-slice source: %s", source.logical_name)
		logging.info("Source description: %s", source.description)
		logging.info("Source outer archive path: %s", source_outer_archive_path)
		logging.info("Required columns for %s: %s", source.logical_name, list(source.required_columns))
		logging.info("Outer archive member count: %d", len(outer_member_names))
		for outer_member_name in outer_member_names:
			logging.info("Outer archive member: %s", outer_member_name)

		if not source.inner_zip_member_filename:
			raise RuntimeError(
				f"Current-slice source is missing inner_zip_member_filename: {source.logical_name}"
			)

		inner_zip_member_name = require_member_present(
			member_names=outer_member_names,
			expected_member_name=source.inner_zip_member_filename,
			archive_label=f"archive {source_outer_archive_path}",
		)
		logging.info("Configured inner ZIP member: %s", inner_zip_member_name)

		with open_inner_zip_archive(outer_archive, inner_zip_member_name) as inner_archive:
			inner_member_names = list_archive_members(inner_archive)
			logging.info("Inner archive member count for %s: %d", source.logical_name, len(inner_member_names))
			for inner_member_name in inner_member_names:
				logging.info("Inner archive member for %s: %s", source.logical_name, inner_member_name)

			matched_csv_members = select_matching_members(
				member_names=inner_member_names,
				patterns=source.target_csv_globs,
				source_name=source.logical_name,
			)
			schema_summaries = []
			for matched_csv_member in matched_csv_members:
				logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)
				with open_inner_csv_text_stream(inner_archive, matched_csv_member) as text_stream:
					column_names = parse_csv_header_line(text_stream, matched_csv_member)
				schema_summary = CsvSchemaSummary(
					csv_member_name=matched_csv_member,
					column_names=column_names,
				)
				validate_required_columns(column_names, source.required_columns, source.logical_name, matched_csv_member)
				log_source_schema(source, schema_summary)
				schema_summaries.append(schema_summary)
			validate_consistent_member_schema(schema_summaries, source.logical_name)


def run_current_hd_reporting_pipeline(cfg: Config) -> CurrentSliceResult:
	inventory_current_source_layout(cfg)
	provisional_rows, extraction_summary = extract_hd_reporting_sites(
		cfg,
		provisional_output_path=get_provisional_output_path(cfg),
	)
	finalization_summary = finalize_hd_reporting_sites(
		provisional_rows=provisional_rows,
		canonical_output_path=get_canonical_output_path(cfg),
	)
	return CurrentSliceResult(
		extraction_summary=extraction_summary,
		finalization_summary=finalization_summary,
	)


def log_current_hd_reporting_result(result: CurrentSliceResult) -> None:
	extraction_summary = result.extraction_summary
	finalization_summary = result.finalization_summary
	logging.info(
		"HD_REPORTING site extraction summary: total=%d operating_tsdf=%d federal_lqg=%d state_lqg=%d significant_facility=%d active_mask=%d qualifying=%d rejected=%d validation_failures=%d provisional_rows=%d unique_handlers=%d",
		extraction_summary.total_rows,
		extraction_summary.operating_tsdf_rows,
		extraction_summary.federal_lqg_rows,
		extraction_summary.state_lqg_rows,
		extraction_summary.significant_facility_rows,
		extraction_summary.active_mask_rows,
		extraction_summary.qualifying_rows,
		extraction_summary.rejected_rows,
		extraction_summary.validation_failure_count,
		extraction_summary.provisional_row_count,
		extraction_summary.unique_handler_ids,
	)
	for validation_reason, reason_count in extraction_summary.validation_reason_counts:
		logging.info("HD_REPORTING validation rejects [%s]: %d", validation_reason, reason_count)
	logging.info("Provisional rows removed by HANDLER ID dedup: %d", finalization_summary.handler_dedup_removed_count)
	logging.info("Canonical site rows written: %d", finalization_summary.canonical_row_count)


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	try:
		cfg = get_config(argv)
		initialize_runtime_dependencies(cfg)
		log_runtime_context(cfg)
		inventory_planned_source_layout(cfg)
		current_slice_result = run_current_hd_reporting_pipeline(cfg)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing proof slice failed: %s", exc)
		return 1

	log_current_hd_reporting_result(current_slice_result)
	logging.info("Hazardous waste preprocessing proof slice completed successfully")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
