"""
hazardous_waste_preprocess.py

Purpose:
	Prove the current hazardous-waste preprocessing access path by enumerating
	the configured source archives and validating the CSV schemas we now believe
	are relevant.

Current slice:
	- Loads archive names and source layout from the config file.
	- Supports local or remote root paths.
	- Accepts only the main `HD.zip` filename as a runtime filename override.
	- Enumerates `HD_REPORTING` from inside `HD.zip`.
	- Enumerates `BR_REPORTING_2023` from its own ZIP file.
	- Reads CSV headers from the matched members, logs key columns needed downstream,
	  and fails fast when the discovered schema does not match the current working design.
	- Streams `HD_REPORTING` rows, applies the revised EJScreen significant-facility mask,
	  and logs condition counts plus the filtered output counts.

Sample command lines:
	- Local storage, all other options default:
	  python3 ./scripts/hazardous_waste/hazardous_waste_preprocess.py local
	- Remote storage, all other options default:
	  python3 ./scripts/hazardous_waste/hazardous_waste_preprocess.py remote
	- Local storage with a different `HD.zip` filename:
	  python3 ./scripts/hazardous_waste/hazardous_waste_preprocess.py local --hd-outer-archive-filename HD.zip

This slice intentionally stops before schema validation, filtering, joins,
or audit outputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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

SOURCE_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
	"hd_reporting": (
		"HANDLER ID",
		"HANDLER NAME",
		"LOCATION CITY",
		"LOCATION STATE",
		"LOCATION ZIP",
		"OPERATING TSDF",
		"FED WASTE GENERATOR",
		"STATE WASTE GENERATOR",
		"IN A UNIVERSE",
		"ACTIVE SITE",
		"LOCATION LATITUDE",
		"LOCATION LONGITUDE",
	),
	"br_reporting": (
		"HANDLER ID",
		"REPORT CYCLE",
	),
}

SOURCE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
	"hd_reporting": (
		"HANDLER ID",
		"HANDLER NAME",
		"LOCATION CITY",
		"LOCATION STATE",
		"LOCATION ZIP",
		"OPERATING TSDF",
		"FED WASTE GENERATOR",
		"STATE WASTE GENERATOR",
		"IN A UNIVERSE",
		"ACTIVE SITE",
		"LOCATION LATITUDE",
		"LOCATION LONGITUDE",
	),
	"br_reporting": (
		"HANDLER ID",
		"REPORT CYCLE",
	),
}


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
	source_key: str
	logical_name: str
	inventory_phase: str
	description: str
	outer_archive_filename: str | None
	inner_zip_member_filename: str | None
	target_csv_globs: tuple[str, ...]
	target_biennial_year: int | None = None


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	local_root_path: str
	remote_root_path: str
	downloads_relative_path: str
	outer_archive_filename: str
	provisional_output_relative_path: str
	canonical_output_relative_path: str
	active_nested_sources: tuple[SourceDescriptor, ...]
	deferred_sources: tuple[SourceDescriptor, ...]


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


def build_source_descriptor(raw_descriptor: dict) -> SourceDescriptor:
	return SourceDescriptor(
		source_key=raw_descriptor["source_key"],
		logical_name=raw_descriptor["logical_name"],
		inventory_phase=raw_descriptor["inventory_phase"],
		description=raw_descriptor["description"],
		outer_archive_filename=raw_descriptor.get("outer_archive_filename"),
		inner_zip_member_filename=raw_descriptor.get("inner_zip_member_filename"),
		target_csv_globs=tuple(raw_descriptor.get("target_csv_globs", [])),
		target_biennial_year=raw_descriptor.get("target_biennial_year"),
	)


def load_config_payload() -> dict:
	if not CONFIG_FILE_PATH.exists():
		raise FileNotFoundError(f"Config file not found: {CONFIG_FILE_PATH}")
	with CONFIG_FILE_PATH.open("r", encoding="utf-8") as config_stream:
		return json.load(config_stream)


def get_config(argv=None) -> Config:
	config_payload = load_config_payload()
	default_outer_archive_filename = config_payload["default_outer_archive_filename"]

	parser = argparse.ArgumentParser(
		description="Enumerate the configured hazardous-waste source archives."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads through the local root path or the remote S3 root path.",
	)
	parser.add_argument(
		"--hd-outer-archive-filename",
		dest="outer_archive_filename",
		default=default_outer_archive_filename,
		help=(
			"Override only the main HD outer archive filename. "
			f"Default from config: {default_outer_archive_filename}"
		),
	)
	args = parser.parse_args(argv)

	return Config(
		storage_mode=args.storage_mode,
		local_root_path=config_payload["local_root_path"],
		remote_root_path=config_payload["remote_root_path"],
		downloads_relative_path=config_payload["downloads_relative_path"],
		outer_archive_filename=args.outer_archive_filename,
		provisional_output_relative_path=config_payload["provisional_output_relative_path"],
		canonical_output_relative_path=config_payload["canonical_output_relative_path"],
		active_nested_sources=tuple(
			build_source_descriptor(raw_descriptor)
			for raw_descriptor in config_payload.get("active_nested_sources", [])
		),
		deferred_sources=tuple(
			build_source_descriptor(raw_descriptor)
			for raw_descriptor in config_payload.get("deferred_sources", [])
		),
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


def get_outer_archive_path(cfg: Config) -> str:
	return join_root_and_relative_path(
		get_active_root_path(cfg),
		str(Path(cfg.downloads_relative_path) / cfg.outer_archive_filename),
	)


def get_source_outer_archive_path(cfg: Config, source: SourceDescriptor) -> str:
	archive_filename = source.outer_archive_filename or cfg.outer_archive_filename
	return join_root_and_relative_path(
		get_active_root_path(cfg),
		str(Path(cfg.downloads_relative_path) / archive_filename),
	)


def get_provisional_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.provisional_output_relative_path)


def get_canonical_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.canonical_output_relative_path)


def open_binary_input_stream(path: str):
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, "rb")
	return open(path, "rb")


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
	try:
		return SOURCE_REQUIRED_COLUMNS[source.source_key]
	except KeyError as exc:
		raise RuntimeError(f"No required-column definition exists for source {source.source_key!r}") from exc


def get_key_columns_for_source(source: SourceDescriptor) -> tuple[str, ...]:
	try:
		return SOURCE_KEY_COLUMNS[source.source_key]
	except KeyError as exc:
		raise RuntimeError(f"No key-column definition exists for source {source.source_key!r}") from exc


def get_required_source(cfg: Config, source_key: str) -> SourceDescriptor:
	matching_sources = [source for source in cfg.active_nested_sources if source.source_key == source_key]
	if not matching_sources:
		raise RuntimeError(f"Configured active source not found: {source_key}")
	if len(matching_sources) > 1:
		raise RuntimeError(f"Expected exactly one active source for {source_key}, found {len(matching_sources)}")
	return matching_sources[0]


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
	source = get_required_source(cfg, "hd_reporting")
	source_outer_archive_path = get_source_outer_archive_path(cfg, source)
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
						validate_required_columns(tuple(reader.fieldnames), SOURCE_REQUIRED_COLUMNS["hd_reporting"], source.logical_name, matched_csv_member)

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


def inventory_configured_sources(cfg: Config) -> None:
	outer_archive_path = get_outer_archive_path(cfg)
	with open_outer_zip_archive(outer_archive_path) as default_outer_archive:
		default_outer_member_names = list_archive_members(default_outer_archive)
		logging.info("HD outer archive member count: %d", len(default_outer_member_names))
		for outer_member_name in default_outer_member_names:
			logging.info("HD outer archive member: %s", outer_member_name)

		for source in cfg.active_nested_sources:
			logging.info("------------------------------------------------------------")
			logging.info("Inventorying source: %s", source.logical_name)
			logging.info("Source description: %s", source.description)
			if source.target_biennial_year is not None:
				logging.info("Configured biennial year for %s: %s", source.logical_name, source.target_biennial_year)

			source_outer_archive_path = get_source_outer_archive_path(cfg, source)
			logging.info("Source outer archive path for %s: %s", source.logical_name, source_outer_archive_path)
			required_columns = get_required_columns_for_source(source)
			logging.info("Required columns for %s: %s", source.logical_name, list(required_columns))

			if source.outer_archive_filename is None:
				source_outer_archive = default_outer_archive
				source_outer_member_names = default_outer_member_names
			else:
				with open_outer_zip_archive(source_outer_archive_path) as source_archive:
					source_outer_member_names = list_archive_members(source_archive)
					logging.info("Outer archive member count for %s: %d", source.logical_name, len(source_outer_member_names))
					for member_name in source_outer_member_names:
						logging.info("Outer archive member for %s: %s", source.logical_name, member_name)
					matched_csv_members = select_matching_members(
						member_names=source_outer_member_names,
						patterns=source.target_csv_globs,
						source_name=source.logical_name,
					)
					schema_summaries = []
					for matched_csv_member in matched_csv_members:
						logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)
						with open_outer_csv_text_stream(source_archive, matched_csv_member) as text_stream:
							column_names = parse_csv_header_line(text_stream, matched_csv_member)
						schema_summary = CsvSchemaSummary(
							csv_member_name=matched_csv_member,
							column_names=column_names,
						)
						validate_required_columns(column_names, required_columns, source.logical_name, matched_csv_member)
						log_source_schema(source, schema_summary)
						schema_summaries.append(schema_summary)
					validate_consistent_member_schema(schema_summaries, source.logical_name)
					continue

			if not source.inner_zip_member_filename:
				raise RuntimeError(
					f"Source inside HD.zip is missing inner_zip_member_filename: {source.logical_name}"
				)

			inner_zip_member_name = require_member_present(
				member_names=source_outer_member_names,
				expected_member_name=source.inner_zip_member_filename,
				archive_label=f"archive {source_outer_archive_path}",
			)
			logging.info("Configured inner ZIP member: %s", inner_zip_member_name)

			with open_inner_zip_archive(source_outer_archive, inner_zip_member_name) as inner_archive:
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
					validate_required_columns(column_names, required_columns, source.logical_name, matched_csv_member)
					log_source_schema(source, schema_summary)
					schema_summaries.append(schema_summary)
				validate_consistent_member_schema(schema_summaries, source.logical_name)

		if cfg.deferred_sources:
			logging.info("------------------------------------------------------------")
			logging.info("Deferred sources for later slices: %d", len(cfg.deferred_sources))
			for source in cfg.deferred_sources:
				logging.info(
					"Deferred source: %s | year=%s | configured CSV patterns=%s",
					source.logical_name,
					source.target_biennial_year,
					list(source.target_csv_globs),
				)


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	try:
		cfg = get_config(argv)
		initialize_runtime_dependencies(cfg)

		logging.info("Config file: %s", CONFIG_FILE_PATH)
		logging.info("Storage mode: %s", cfg.storage_mode)
		logging.info("Active root path: %s", get_active_root_path(cfg))
		logging.info("Downloads relative path: %s", cfg.downloads_relative_path)
		logging.info("HD outer archive filename: %s", cfg.outer_archive_filename)
		logging.info("HD outer archive path: %s", get_outer_archive_path(cfg))
		logging.info("Provisional output path: %s", get_provisional_output_path(cfg))
		logging.info("Canonical output path: %s", get_canonical_output_path(cfg))

		inventory_configured_sources(cfg)
		provisional_rows, extraction_summary = extract_hd_reporting_sites(
			cfg,
			provisional_output_path=get_provisional_output_path(cfg),
		)
		finalization_summary = finalize_hd_reporting_sites(
			provisional_rows=provisional_rows,
			canonical_output_path=get_canonical_output_path(cfg),
		)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing proof slice failed: %s", exc)
		return 1

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
	logging.info("Hazardous waste preprocessing proof slice completed successfully")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
