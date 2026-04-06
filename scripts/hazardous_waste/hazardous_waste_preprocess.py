"""
hazardous_waste_preprocess.py

Purpose:
	Build the hazardous-waste proximity site universe from RCRA_FACILITIES.csv
	plus BR_REPORTING_2021.zip, emit the canonical site output,
	and write source-aware audit artifacts.
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
DEFAULT_LOG_FILENAME = "hwpre.log"

PARSE_AUDIT_FIELDNAMES = (
	"audit_stage",
	"source_dataset",
	"source_member_filename",
	"source_member_row_number",
	"audit_reason",
	"audit_note",
	"raw_record_excerpt",
)

VALIDATION_AUDIT_FIELDNAMES = (
	"audit_stage",
	"source_dataset",
	"source_member_filename",
	"source_member_row_number",
	"HANDLER ID",
	"HANDLER NAME",
	"LOCATION CITY",
	"LOCATION STATE",
	"LOCATION ZIP",
	"LOCATION LATITUDE",
	"LOCATION LONGITUDE",
	"is_lqg_site",
	"is_tsdf_site",
	"site_class",
	"audit_reason",
	"audit_note",
)

DEDUP_AUDIT_FIELDNAMES = VALIDATION_AUDIT_FIELDNAMES + (
	"group_size",
	"is_canonical_row",
)


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
	source_key: str
	logical_name: str
	description: str
	relative_path: str
	target_csv_globs: tuple[str, ...]
	required_columns: tuple[str, ...]
	key_columns: tuple[str, ...]
	handler_id_column: str
	target_biennial_year: int | None = None


@dataclass(frozen=True, slots=True)
class Config:
	storage_mode: str
	local_root_path: str
	remote_root_path: str
	canonical_output_relative_path: str
	parse_audit_relative_path: str
	validation_audit_relative_path: str
	dedup_audit_relative_path: str
	planned_master_rcra_source: SourceDescriptor
	planned_br_reporting_source: SourceDescriptor


@dataclass(frozen=True, slots=True)
class CsvSchemaSummary:
	csv_member_name: str
	column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BrReportingSummary:
	total_rows: int
	matched_year_rows: int
	wrong_year_rows: int
	missing_handler_id_rows: int
	unique_handler_ids: int
	member_row_counts: tuple[tuple[str, int], ...]
	member_unique_handler_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RcraClassificationSummary:
	total_rows: int
	active_rows: int
	operating_tsdf_rows: int
	lqg_rows: int
	active_operating_tsdf_rows: int
	active_lqg_rows: int
	unique_handler_ids: int


@dataclass(frozen=True, slots=True)
class RcraTsdfSubsetSummary:
	total_rows: int
	qualifying_rows: int
	rejected_rows: int
	validation_failure_count: int
	provisional_row_count: int
	unique_handler_ids: int
	validation_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RcraBrLqgSubsetSummary:
	total_rows: int
	br_reporter_match_rows: int
	lqg_rows: int
	qualifying_rows: int
	rejected_rows: int
	validation_failure_count: int
	provisional_row_count: int
	unique_handler_ids: int
	validation_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CombinedPopulationSummary:
	tsdf_row_count: int
	lqg_row_count: int
	combined_input_row_count: int
	tsdf_only_rows: int
	lqg_only_rows: int
	overlap_rows: int
	canonical_row_count: int
	dedup_removed_count: int


@dataclass(frozen=True, slots=True)
class CombinedPopulationValidationSummary:
	validated_row_count: int
	tsdf_only_rows: int
	lqg_only_rows: int
	both_rows: int


@dataclass(frozen=True, slots=True)
class AuditOutputSummary:
	parse_audit_row_count: int
	validation_audit_row_count: int
	dedup_audit_row_count: int


@dataclass(frozen=True, slots=True)
class CanonicalOutputSummary:
	canonical_row_count: int


def build_source_descriptor(raw_descriptor: dict) -> SourceDescriptor:
	return SourceDescriptor(
		source_key=raw_descriptor["source_key"],
		logical_name=raw_descriptor["logical_name"],
		description=raw_descriptor["description"],
		relative_path=raw_descriptor["relative_path"],
		target_csv_globs=tuple(raw_descriptor.get("target_csv_globs", [])),
		required_columns=tuple(raw_descriptor["required_columns"]),
		key_columns=tuple(raw_descriptor["key_columns"]),
		handler_id_column=raw_descriptor["handler_id_column"],
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

	parser = argparse.ArgumentParser(
		description="Run the hazardous-waste preprocessing pipeline."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads through the local root path or the remote S3 root path.",
	)
	args = parser.parse_args(argv)

	return Config(
		storage_mode=args.storage_mode,
		local_root_path=config_payload["local_root_path"],
		remote_root_path=config_payload["remote_root_path"],
		canonical_output_relative_path=config_payload["canonical_output_relative_path"],
		parse_audit_relative_path=config_payload["parse_audit_relative_path"],
		validation_audit_relative_path=config_payload["validation_audit_relative_path"],
		dedup_audit_relative_path=config_payload["dedup_audit_relative_path"],
		planned_master_rcra_source=build_source_descriptor(planned_sources_payload["master_rcra_source"]),
		planned_br_reporting_source=build_source_descriptor(planned_sources_payload["br_reporting_source"]),
	)


def configure_logging() -> str:
	log_path = Path.cwd() / DEFAULT_LOG_FILENAME
	log_path.parent.mkdir(parents=True, exist_ok=True)
	logging.basicConfig(
		level=logging.INFO,
		format="%(levelname)s: %(message)s",
		handlers=[
			logging.FileHandler(log_path, mode="w", encoding="utf-8"),
		],
		force=True,
	)
	return str(log_path)


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


def get_canonical_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.canonical_output_relative_path)


def get_parse_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.parse_audit_relative_path)


def get_validation_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.validation_audit_relative_path)


def get_dedup_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.dedup_audit_relative_path)


def log_runtime_context(cfg: Config) -> None:
	logging.info("Config file: %s", CONFIG_FILE_PATH)
	logging.info("Storage mode: %s", cfg.storage_mode)
	logging.info("Active root path: %s", get_active_root_path(cfg))
	logging.info("Planned master RCRA source path: %s", get_source_path(cfg, cfg.planned_master_rcra_source))
	logging.info("Planned BR reporting source path: %s", get_source_path(cfg, cfg.planned_br_reporting_source))
	logging.info("Canonical output path: %s", get_canonical_output_path(cfg))
	logging.info("Parse audit path: %s", get_parse_audit_path(cfg))
	logging.info("Validation audit path: %s", get_validation_audit_path(cfg))
	logging.info("Dedup audit path: %s", get_dedup_audit_path(cfg))


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


def write_csv_rows(path: str, fieldnames: tuple[str, ...] | list[str], rows: list[dict[str, str]]) -> None:
	with open_text_output_stream(path) as output_stream:
		writer = csv.DictWriter(output_stream, fieldnames=list(fieldnames))
		writer.writeheader()
		writer.writerows(rows)


def write_population_output_rows(path: str, rows: list[dict[str, str]]) -> None:
	write_csv_rows(path, get_population_output_fieldnames(), rows)


def list_archive_members(archive: zipfile.ZipFile) -> list[str]:
	return [name for name in archive.namelist() if name and not name.endswith("/")]


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


def get_key_columns_for_source(source: SourceDescriptor) -> tuple[str, ...]:
	return source.key_columns


def normalize_cell_text(value: str | None) -> str:
	if value is None:
		return ""
	return value.strip()


def normalize_handler_id(value: str | None) -> str:
	return normalize_cell_text(value).upper()


def normalize_report_cycle(value: str | None) -> str:
	return normalize_cell_text(value)


def has_alpha_code(value: str | None) -> bool:
	normalized_value = normalize_cell_text(value)
	if not normalized_value:
		return False
	return any(character.isalpha() for character in normalized_value)


def rcra_row_is_active(row: dict[str, str]) -> bool:
	return has_alpha_code(row.get("ACTIVE_SITE"))


def rcra_row_has_operating_tsdf(row: dict[str, str]) -> bool:
	return has_alpha_code(row.get("OPERATING_TSDF"))


def rcra_row_is_lqg(row: dict[str, str]) -> bool:
	status_text = normalize_cell_text(row.get("HREPORT_UNIVERSE_RECORD")).upper()
	if not status_text:
		return False
	status_parts = [part.strip() for part in status_text.split(",")]
	return "LQG" in status_parts


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


def rcra_row_has_usable_coordinates(row: dict[str, str]) -> bool:
	latitude = parse_float_or_none(row.get("LATITUDE83"))
	longitude = parse_float_or_none(row.get("LONGITUDE83"))
	if latitude is None or longitude is None:
		return False
	return latitude != 0.0 and longitude != 0.0


def classify_rcra_tsdf_validation_failure(row: dict[str, str], source: SourceDescriptor) -> tuple[str, str] | None:
	validation_errors = []
	if not normalize_handler_id(row.get(source.handler_id_column)):
		validation_errors.append("missing_handler_id")
	if not rcra_row_has_usable_coordinates(row):
		validation_errors.append("invalid_coordinates")
	if not validation_errors:
		return None
	validation_reason = ";".join(validation_errors)
	return validation_reason, f"RCRA TSDF row failed site-level validation: {validation_reason}"


def build_rcra_tsdf_site_row(
	row: dict[str, str],
	source: SourceDescriptor,
	source_member_row_number: int,
) -> dict[str, str]:
	return {
		"HANDLER ID": normalize_handler_id(row.get(source.handler_id_column)),
		"HANDLER NAME": normalize_cell_text(row.get("FACILITY_NAME")),
		"LOCATION CITY": normalize_cell_text(row.get("CITY_NAME")),
		"LOCATION STATE": normalize_cell_text(row.get("STATE_CODE")),
		"LOCATION ZIP": normalize_cell_text(row.get("ZIP_CODE")),
		"LOCATION LATITUDE": normalize_cell_text(row.get("LATITUDE83")),
		"LOCATION LONGITUDE": normalize_cell_text(row.get("LONGITUDE83")),
		"OPERATING TSDF": normalize_cell_text(row.get("OPERATING_TSDF")),
		"FED WASTE GENERATOR": normalize_cell_text(row.get("FED_WASTE_GENERATOR")),
		"IN A UNIVERSE": "",
		"ACTIVE SITE": normalize_cell_text(row.get("ACTIVE_SITE")),
		"is_lqg_site": "N",
		"is_tsdf_site": "Y",
		"site_class": "tsdf",
		"source_dataset": source.logical_name,
		"source_member_filename": Path(source.relative_path).name,
		"source_member_row_number": str(source_member_row_number),
	}


def classify_rcra_lqg_validation_failure(row: dict[str, str], source: SourceDescriptor) -> tuple[str, str] | None:
	validation_errors = []
	if not normalize_handler_id(row.get(source.handler_id_column)):
		validation_errors.append("missing_handler_id")
	if not rcra_row_has_usable_coordinates(row):
		validation_errors.append("invalid_coordinates")
	if not validation_errors:
		return None
	validation_reason = ";".join(validation_errors)
	return validation_reason, f"RCRA BR-reporting LQG row failed site-level validation: {validation_reason}"


def build_rcra_lqg_site_row(
	row: dict[str, str],
	source: SourceDescriptor,
	source_member_row_number: int,
) -> dict[str, str]:
	return {
		"HANDLER ID": normalize_handler_id(row.get(source.handler_id_column)),
		"HANDLER NAME": normalize_cell_text(row.get("FACILITY_NAME")),
		"LOCATION CITY": normalize_cell_text(row.get("CITY_NAME")),
		"LOCATION STATE": normalize_cell_text(row.get("STATE_CODE")),
		"LOCATION ZIP": normalize_cell_text(row.get("ZIP_CODE")),
		"LOCATION LATITUDE": normalize_cell_text(row.get("LATITUDE83")),
		"LOCATION LONGITUDE": normalize_cell_text(row.get("LONGITUDE83")),
		"OPERATING TSDF": normalize_cell_text(row.get("OPERATING_TSDF")),
		"FED WASTE GENERATOR": normalize_cell_text(row.get("FED_WASTE_GENERATOR")),
		"IN A UNIVERSE": "",
		"ACTIVE SITE": normalize_cell_text(row.get("ACTIVE_SITE")),
		"is_lqg_site": "Y",
		"is_tsdf_site": "N",
		"site_class": "lqg",
		"source_dataset": source.logical_name,
		"source_member_filename": Path(source.relative_path).name,
		"source_member_row_number": str(source_member_row_number),
	}


def get_population_output_fieldnames() -> list[str]:
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


def build_validation_audit_row(
	*,
	audit_stage: str,
	source_dataset: str,
	source_member_filename: str,
	source_member_row_number: int | str,
	audit_reason: str,
	audit_note: str,
	handler_id: str = "",
	handler_name: str = "",
	location_city: str = "",
	location_state: str = "",
	location_zip: str = "",
	location_latitude: str = "",
	location_longitude: str = "",
	is_lqg_site: str = "",
	is_tsdf_site: str = "",
	site_class: str = "",
) -> dict[str, str]:
	return {
		"audit_stage": audit_stage,
		"source_dataset": source_dataset,
		"source_member_filename": source_member_filename,
		"source_member_row_number": str(source_member_row_number),
		"HANDLER ID": handler_id,
		"HANDLER NAME": handler_name,
		"LOCATION CITY": location_city,
		"LOCATION STATE": location_state,
		"LOCATION ZIP": location_zip,
		"LOCATION LATITUDE": location_latitude,
		"LOCATION LONGITUDE": location_longitude,
		"is_lqg_site": is_lqg_site,
		"is_tsdf_site": is_tsdf_site,
		"site_class": site_class,
		"audit_reason": audit_reason,
		"audit_note": audit_note,
	}


def build_site_validation_audit_row(
	row: dict[str, str],
	*,
	audit_stage: str,
	audit_reason: str,
	audit_note: str,
) -> dict[str, str]:
	return build_validation_audit_row(
		audit_stage=audit_stage,
		source_dataset=normalize_cell_text(row.get("source_dataset")),
		source_member_filename=normalize_cell_text(row.get("source_member_filename")),
		source_member_row_number=normalize_cell_text(row.get("source_member_row_number")),
		audit_reason=audit_reason,
		audit_note=audit_note,
		handler_id=normalize_handler_id(row.get("HANDLER ID")),
		handler_name=normalize_cell_text(row.get("HANDLER NAME")),
		location_city=normalize_cell_text(row.get("LOCATION CITY")),
		location_state=normalize_cell_text(row.get("LOCATION STATE")),
		location_zip=normalize_cell_text(row.get("LOCATION ZIP")),
		location_latitude=normalize_cell_text(row.get("LOCATION LATITUDE")),
		location_longitude=normalize_cell_text(row.get("LOCATION LONGITUDE")),
		is_lqg_site=normalize_cell_text(row.get("is_lqg_site")),
		is_tsdf_site=normalize_cell_text(row.get("is_tsdf_site")),
		site_class=normalize_cell_text(row.get("site_class")),
	)


def build_site_dedup_audit_row(
	row: dict[str, str],
	*,
	audit_stage: str,
	audit_reason: str,
	audit_note: str,
	group_size: int,
	is_canonical_row: bool,
) -> dict[str, str]:
	audit_row = build_site_validation_audit_row(
		row,
		audit_stage=audit_stage,
		audit_reason=audit_reason,
		audit_note=audit_note,
	)
	audit_row["group_size"] = str(group_size)
	audit_row["is_canonical_row"] = "Y" if is_canonical_row else "N"
	return audit_row


def get_audit_dedup_comparison_key(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
	comparison_fields = [
		field_name
		for field_name in get_population_output_fieldnames()
		if field_name not in {"source_member_filename", "source_member_row_number"}
	]
	return tuple((field_name, normalize_cell_text(row.get(field_name))) for field_name in comparison_fields)


def deduplicate_population_slice_for_audit(
	population_rows: list[dict[str, str]],
	*,
	audit_stage: str,
	audit_note_prefix: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
	if not population_rows:
		return [], []

	grouped_rows: dict[str, list[dict[str, str]]] = {}
	for row in population_rows:
		grouped_rows.setdefault(row["HANDLER ID"], []).append(row)

	canonical_rows: list[dict[str, str]] = []
	dedup_audit_rows: list[dict[str, str]] = []
	for handler_id in sorted(grouped_rows):
		group_rows = sorted(grouped_rows[handler_id], key=get_canonical_sort_key, reverse=True)
		selected_row = group_rows[0]
		canonical_rows.append(selected_row)
		if len(group_rows) == 1:
			continue

		group_size = len(group_rows)
		comparison_keys = {get_audit_dedup_comparison_key(row) for row in group_rows}
		audit_reason = "exact_duplicate" if len(comparison_keys) == 1 else "duplicate_handler_id"
		audit_note = f"{audit_note_prefix}; strongest row retained by canonical sort"
		for row in group_rows:
			dedup_audit_rows.append(
				build_site_dedup_audit_row(
					row,
					audit_stage=audit_stage,
					audit_reason=audit_reason,
					audit_note=audit_note,
					group_size=group_size,
					is_canonical_row=(row is selected_row),
				)
			)

	return canonical_rows, dedup_audit_rows


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


def derive_site_class_from_flags(is_tsdf_site: str, is_lqg_site: str) -> str:
	if is_tsdf_site == "Y" and is_lqg_site == "Y":
		return "both"
	if is_tsdf_site == "Y" and is_lqg_site == "N":
		return "tsdf"
	if is_tsdf_site == "N" and is_lqg_site == "Y":
		return "lqg"
	raise RuntimeError(
		f"Invalid site-flag combination for derived site_class: is_tsdf_site={is_tsdf_site!r}, is_lqg_site={is_lqg_site!r}"
	)


def merge_population_rows(tsdf_row: dict[str, str] | None, lqg_row: dict[str, str] | None) -> dict[str, str]:
	if tsdf_row is None and lqg_row is None:
		raise RuntimeError("Expected at least one population row to merge")
	if tsdf_row is None:
		return dict(lqg_row)
	if lqg_row is None:
		return dict(tsdf_row)

	merged_row = dict(tsdf_row)
	merged_row["is_lqg_site"] = "Y"
	merged_row["is_tsdf_site"] = "Y"
	merged_row["site_class"] = derive_site_class_from_flags(
		merged_row["is_tsdf_site"],
		merged_row["is_lqg_site"],
	)
	return merged_row


def deduplicate_population_rows(population_rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
	if not population_rows:
		return [], 0

	sorted_rows = sorted(population_rows, key=get_canonical_sort_key, reverse=True)
	seen_handler_ids: set[str] = set()
	canonical_rows: list[dict[str, str]] = []
	for row in sorted_rows:
		handler_id = row["HANDLER ID"]
		if handler_id in seen_handler_ids:
			continue
		seen_handler_ids.add(handler_id)
		canonical_rows.append(row)

	canonical_rows.sort(key=lambda row: row["HANDLER ID"])
	return canonical_rows, len(population_rows) - len(canonical_rows)


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


def extract_br_reporting_handler_ids(cfg: Config) -> tuple[set[str], BrReportingSummary, list[dict[str, str]]]:
	source = cfg.planned_br_reporting_source
	if source.target_biennial_year is None:
		raise RuntimeError(f"BR reporting source is missing target_biennial_year: {source.logical_name}")

	target_report_cycle = str(source.target_biennial_year)
	source_path = get_source_path(cfg, source)
	unique_handler_ids: set[str] = set()
	total_rows = 0
	matched_year_rows = 0
	wrong_year_rows = 0
	missing_handler_id_rows = 0
	member_row_counts: list[tuple[str, int]] = []
	member_unique_handler_counts: list[tuple[str, int]] = []
	validation_audit_rows: list[dict[str, str]] = []

	with open_outer_zip_archive(source_path) as outer_archive:
		outer_member_names = list_archive_members(outer_archive)
		matched_csv_members = select_matching_members(
			member_names=outer_member_names,
			patterns=source.target_csv_globs,
			source_name=source.logical_name,
		)

		for matched_csv_member in matched_csv_members:
			member_row_count = 0
			member_handler_ids: set[str] = set()
			logging.info("Extracting BR-reporting handler IDs from %s", matched_csv_member)
			with open_outer_csv_text_stream(outer_archive, matched_csv_member) as text_stream:
				reader = csv.DictReader(text_stream)
				if reader.fieldnames is None:
					raise RuntimeError(f"CSV member is missing a header row: {matched_csv_member}")
				validate_required_columns(tuple(reader.fieldnames), source.required_columns, source.logical_name, matched_csv_member)

				for row in reader:
					total_rows += 1
					member_row_count += 1
					source_member_row_number = member_row_count
					report_cycle = normalize_report_cycle(row.get("REPORT CYCLE"))
					if report_cycle != target_report_cycle:
						wrong_year_rows += 1
						continue

					matched_year_rows += 1
					handler_id = normalize_handler_id(row.get(source.handler_id_column))
					if not handler_id:
						missing_handler_id_rows += 1
						validation_audit_rows.append(
							build_validation_audit_row(
								audit_stage="br_reporting_handler_extraction",
								source_dataset=source.logical_name,
								source_member_filename=matched_csv_member,
								source_member_row_number=source_member_row_number,
								audit_reason="missing_handler_id",
								audit_note="BR reporting row matched the target cycle but could not contribute to the reporter set because HANDLER ID was blank after normalization.",
							)
						)
						continue

					member_handler_ids.add(handler_id)
					unique_handler_ids.add(handler_id)

			member_row_counts.append((matched_csv_member, member_row_count))
			member_unique_handler_counts.append((matched_csv_member, len(member_handler_ids)))

	return unique_handler_ids, BrReportingSummary(
		total_rows=total_rows,
		matched_year_rows=matched_year_rows,
		wrong_year_rows=wrong_year_rows,
		missing_handler_id_rows=missing_handler_id_rows,
		unique_handler_ids=len(unique_handler_ids),
		member_row_counts=tuple(member_row_counts),
		member_unique_handler_counts=tuple(member_unique_handler_counts),
	), validation_audit_rows


def log_br_reporting_summary(summary: BrReportingSummary, target_biennial_year: int) -> None:
	logging.info(
		"BR reporting summary for %s: total_rows=%d matched_year_rows=%d wrong_year_rows=%d missing_handler_id_rows=%d unique_handler_ids=%d",
		target_biennial_year,
		summary.total_rows,
		summary.matched_year_rows,
		summary.wrong_year_rows,
		summary.missing_handler_id_rows,
		summary.unique_handler_ids,
	)
	for member_name, member_row_count in summary.member_row_counts:
		logging.info("BR member rows [%s]: %d", member_name, member_row_count)
	for member_name, unique_handler_count in summary.member_unique_handler_counts:
		logging.info("BR unique handler IDs [%s]: %d", member_name, unique_handler_count)


def summarize_rcra_classification(cfg: Config) -> RcraClassificationSummary:
	source = cfg.planned_master_rcra_source
	source_path = get_source_path(cfg, source)
	total_rows = 0
	active_rows = 0
	operating_tsdf_rows = 0
	lqg_rows = 0
	active_operating_tsdf_rows = 0
	active_lqg_rows = 0
	unique_handler_ids: set[str] = set()

	with open_text_input_stream(source_path) as text_stream:
		reader = csv.DictReader(text_stream)
		if reader.fieldnames is None:
			raise RuntimeError(f"CSV file is missing a header row: {source_path}")
		validate_required_columns(tuple(reader.fieldnames), source.required_columns, source.logical_name, Path(source.relative_path).name)

		for row in reader:
			total_rows += 1
			handler_id = normalize_handler_id(row.get(source.handler_id_column))
			if handler_id:
				unique_handler_ids.add(handler_id)

			is_active = rcra_row_is_active(row)
			has_operating_tsdf = rcra_row_has_operating_tsdf(row)
			is_lqg = rcra_row_is_lqg(row)

			if is_active:
				active_rows += 1
			if has_operating_tsdf:
				operating_tsdf_rows += 1
			if is_lqg:
				lqg_rows += 1
			if is_active and has_operating_tsdf:
				active_operating_tsdf_rows += 1
			if is_active and is_lqg:
				active_lqg_rows += 1

	return RcraClassificationSummary(
		total_rows=total_rows,
		active_rows=active_rows,
		operating_tsdf_rows=operating_tsdf_rows,
		lqg_rows=lqg_rows,
		active_operating_tsdf_rows=active_operating_tsdf_rows,
		active_lqg_rows=active_lqg_rows,
		unique_handler_ids=len(unique_handler_ids),
	)


def log_rcra_classification_summary(summary: RcraClassificationSummary) -> None:
	logging.info(
		"RCRA classification summary: total_rows=%d unique_handler_ids=%d active_rows=%d operating_tsdf_rows=%d lqg_rows=%d active_operating_tsdf_rows=%d active_lqg_rows=%d",
		summary.total_rows,
		summary.unique_handler_ids,
		summary.active_rows,
		summary.operating_tsdf_rows,
		summary.lqg_rows,
		summary.active_operating_tsdf_rows,
		summary.active_lqg_rows,
	)


def build_rcra_tsdf_subset(cfg: Config) -> tuple[list[dict[str, str]], RcraTsdfSubsetSummary, list[dict[str, str]]]:
	source = cfg.planned_master_rcra_source
	source_path = get_source_path(cfg, source)
	provisional_rows: list[dict[str, str]] = []
	validation_reason_counter: Counter[str] = Counter()
	validation_audit_rows: list[dict[str, str]] = []
	total_rows = 0
	qualifying_rows = 0
	validation_failure_count = 0

	with open_text_input_stream(source_path) as text_stream:
		reader = csv.DictReader(text_stream)
		if reader.fieldnames is None:
			raise RuntimeError(f"CSV file is missing a header row: {source_path}")
		validate_required_columns(tuple(reader.fieldnames), source.required_columns, source.logical_name, Path(source.relative_path).name)

		source_member_row_number = 0
		for row in reader:
			source_member_row_number += 1
			total_rows += 1
			if not (rcra_row_is_active(row) and rcra_row_has_operating_tsdf(row)):
				continue

			qualifying_rows += 1
			validation_failure = classify_rcra_tsdf_validation_failure(row, source)
			if validation_failure is not None:
				validation_failure_count += 1
				validation_reason_counter[validation_failure[0]] += 1
				validation_audit_rows.append(
					build_validation_audit_row(
						audit_stage="rcra_tsdf_subset",
						source_dataset=source.logical_name,
						source_member_filename=Path(source.relative_path).name,
						source_member_row_number=source_member_row_number,
						audit_reason=validation_failure[0],
						audit_note=validation_failure[1],
						handler_id=normalize_handler_id(row.get(source.handler_id_column)),
						handler_name=normalize_cell_text(row.get("FACILITY_NAME")),
						location_city=normalize_cell_text(row.get("CITY_NAME")),
						location_state=normalize_cell_text(row.get("STATE_CODE")),
						location_zip=normalize_cell_text(row.get("ZIP_CODE")),
						location_latitude=normalize_cell_text(row.get("LATITUDE83")),
						location_longitude=normalize_cell_text(row.get("LONGITUDE83")),
						is_lqg_site="N",
						is_tsdf_site="Y",
						site_class="tsdf",
					)
				)
				continue

			provisional_rows.append(
				build_rcra_tsdf_site_row(
					row=row,
					source=source,
					source_member_row_number=source_member_row_number,
				)
			)

	unique_handler_ids = len({row["HANDLER ID"] for row in provisional_rows})
	return provisional_rows, RcraTsdfSubsetSummary(
		total_rows=total_rows,
		qualifying_rows=qualifying_rows,
		rejected_rows=total_rows - qualifying_rows,
		validation_failure_count=validation_failure_count,
		provisional_row_count=len(provisional_rows),
		unique_handler_ids=unique_handler_ids,
		validation_reason_counts=tuple(sorted(validation_reason_counter.items())),
	), validation_audit_rows


def log_rcra_tsdf_subset_summary(summary: RcraTsdfSubsetSummary) -> None:
	logging.info(
		"RCRA TSDF subset summary: total_rows=%d qualifying_rows=%d rejected_rows=%d validation_failures=%d provisional_rows=%d unique_handler_ids=%d",
		summary.total_rows,
		summary.qualifying_rows,
		summary.rejected_rows,
		summary.validation_failure_count,
		summary.provisional_row_count,
		summary.unique_handler_ids,
	)
	for validation_reason, reason_count in summary.validation_reason_counts:
		logging.info("RCRA TSDF validation rejects [%s]: %d", validation_reason, reason_count)


def build_rcra_br_reporting_lqg_subset(
	cfg: Config,
	br_reporting_handler_ids: set[str],
) -> tuple[list[dict[str, str]], RcraBrLqgSubsetSummary, list[dict[str, str]]]:
	source = cfg.planned_master_rcra_source
	source_path = get_source_path(cfg, source)
	provisional_rows: list[dict[str, str]] = []
	validation_reason_counter: Counter[str] = Counter()
	validation_audit_rows: list[dict[str, str]] = []
	total_rows = 0
	br_reporter_match_rows = 0
	lqg_rows = 0
	qualifying_rows = 0
	validation_failure_count = 0

	with open_text_input_stream(source_path) as text_stream:
		reader = csv.DictReader(text_stream)
		if reader.fieldnames is None:
			raise RuntimeError(f"CSV file is missing a header row: {source_path}")
		validate_required_columns(tuple(reader.fieldnames), source.required_columns, source.logical_name, Path(source.relative_path).name)

		source_member_row_number = 0
		for row in reader:
			source_member_row_number += 1
			total_rows += 1
			handler_id = normalize_handler_id(row.get(source.handler_id_column))
			is_br_reporter = bool(handler_id) and handler_id in br_reporting_handler_ids
			is_lqg = rcra_row_is_lqg(row)

			if is_br_reporter:
				br_reporter_match_rows += 1
			if is_lqg:
				lqg_rows += 1
			if not (is_br_reporter and is_lqg):
				continue

			qualifying_rows += 1
			validation_failure = classify_rcra_lqg_validation_failure(row, source)
			if validation_failure is not None:
				validation_failure_count += 1
				validation_reason_counter[validation_failure[0]] += 1
				validation_audit_rows.append(
					build_validation_audit_row(
						audit_stage="rcra_br_reporting_lqg_subset",
						source_dataset=source.logical_name,
						source_member_filename=Path(source.relative_path).name,
						source_member_row_number=source_member_row_number,
						audit_reason=validation_failure[0],
						audit_note=validation_failure[1],
						handler_id=normalize_handler_id(row.get(source.handler_id_column)),
						handler_name=normalize_cell_text(row.get("FACILITY_NAME")),
						location_city=normalize_cell_text(row.get("CITY_NAME")),
						location_state=normalize_cell_text(row.get("STATE_CODE")),
						location_zip=normalize_cell_text(row.get("ZIP_CODE")),
						location_latitude=normalize_cell_text(row.get("LATITUDE83")),
						location_longitude=normalize_cell_text(row.get("LONGITUDE83")),
						is_lqg_site="Y",
						is_tsdf_site="N",
						site_class="lqg",
					)
				)
				continue

			provisional_rows.append(
				build_rcra_lqg_site_row(
					row=row,
					source=source,
					source_member_row_number=source_member_row_number,
				)
			)

	unique_handler_ids = len({row["HANDLER ID"] for row in provisional_rows})
	return provisional_rows, RcraBrLqgSubsetSummary(
		total_rows=total_rows,
		br_reporter_match_rows=br_reporter_match_rows,
		lqg_rows=lqg_rows,
		qualifying_rows=qualifying_rows,
		rejected_rows=total_rows - qualifying_rows,
		validation_failure_count=validation_failure_count,
		provisional_row_count=len(provisional_rows),
		unique_handler_ids=unique_handler_ids,
		validation_reason_counts=tuple(sorted(validation_reason_counter.items())),
	), validation_audit_rows


def log_rcra_br_reporting_lqg_subset_summary(summary: RcraBrLqgSubsetSummary) -> None:
	logging.info(
		"RCRA BR-reporting LQG subset summary: total_rows=%d br_reporter_match_rows=%d lqg_rows=%d qualifying_rows=%d rejected_rows=%d validation_failures=%d provisional_rows=%d unique_handler_ids=%d",
		summary.total_rows,
		summary.br_reporter_match_rows,
		summary.lqg_rows,
		summary.qualifying_rows,
		summary.rejected_rows,
		summary.validation_failure_count,
		summary.provisional_row_count,
		summary.unique_handler_ids,
	)
	for validation_reason, reason_count in summary.validation_reason_counts:
		logging.info("RCRA BR-reporting LQG validation rejects [%s]: %d", validation_reason, reason_count)


def combine_rcra_population_slices(
	rcra_tsdf_rows: list[dict[str, str]],
	rcra_br_reporting_lqg_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], CombinedPopulationSummary]:
	tsdf_by_handler_id: dict[str, dict[str, str]] = {}
	lqg_by_handler_id: dict[str, dict[str, str]] = {}

	for row in sorted(rcra_tsdf_rows, key=get_canonical_sort_key, reverse=True):
		tsdf_by_handler_id.setdefault(row["HANDLER ID"], row)
	for row in sorted(rcra_br_reporting_lqg_rows, key=get_canonical_sort_key, reverse=True):
		lqg_by_handler_id.setdefault(row["HANDLER ID"], row)

	all_handler_ids = sorted(set(tsdf_by_handler_id) | set(lqg_by_handler_id))
	combined_rows: list[dict[str, str]] = []
	tsdf_only_rows = 0
	lqg_only_rows = 0
	overlap_rows = 0

	for handler_id in all_handler_ids:
		tsdf_row = tsdf_by_handler_id.get(handler_id)
		lqg_row = lqg_by_handler_id.get(handler_id)
		if tsdf_row is not None and lqg_row is not None:
			overlap_rows += 1
		elif tsdf_row is not None:
			tsdf_only_rows += 1
		else:
			lqg_only_rows += 1
		combined_rows.append(merge_population_rows(tsdf_row, lqg_row))

	canonical_rows, dedup_removed_count = deduplicate_population_rows(combined_rows)
	return canonical_rows, CombinedPopulationSummary(
		tsdf_row_count=len(rcra_tsdf_rows),
		lqg_row_count=len(rcra_br_reporting_lqg_rows),
		combined_input_row_count=len(combined_rows),
		tsdf_only_rows=tsdf_only_rows,
		lqg_only_rows=lqg_only_rows,
		overlap_rows=overlap_rows,
		canonical_row_count=len(canonical_rows),
		dedup_removed_count=dedup_removed_count,
	)


def log_combined_population_summary(summary: CombinedPopulationSummary) -> None:
	logging.info(
		"Combined population summary: tsdf_rows=%d lqg_rows=%d combined_input_rows=%d tsdf_only_rows=%d lqg_only_rows=%d overlap_rows=%d canonical_rows=%d dedup_removed=%d",
		summary.tsdf_row_count,
		summary.lqg_row_count,
		summary.combined_input_row_count,
		summary.tsdf_only_rows,
		summary.lqg_only_rows,
		summary.overlap_rows,
		summary.canonical_row_count,
		summary.dedup_removed_count,
	)


def apply_combined_population_provenance(row: dict[str, str], cfg: Config) -> dict[str, str]:
	provenanced_row = dict(row)
	provenanced_row["site_class"] = derive_site_class_from_flags(
		provenanced_row.get("is_tsdf_site", ""),
		provenanced_row.get("is_lqg_site", ""),
	)
	provenanced_row["source_dataset"] = cfg.planned_master_rcra_source.logical_name
	return provenanced_row


def validate_combined_population_row(row: dict[str, str]) -> None:
	missing_fields = [
		field_name
		for field_name in get_population_output_fieldnames()
		if field_name not in row
	]
	if missing_fields:
		raise RuntimeError(f"Combined population row is missing expected fields: {missing_fields}")

	handler_id = normalize_handler_id(row.get("HANDLER ID"))
	if not handler_id:
		raise RuntimeError("Combined population row is missing HANDLER ID after normalization")
	if not has_usable_coordinates(row):
		raise RuntimeError(f"Combined population row has invalid coordinates for HANDLER ID {handler_id}")
	if not normalize_cell_text(row.get("source_dataset")):
		raise RuntimeError(f"Combined population row is missing source_dataset for HANDLER ID {handler_id}")
	if not normalize_cell_text(row.get("source_member_filename")):
		raise RuntimeError(f"Combined population row is missing source_member_filename for HANDLER ID {handler_id}")
	if not normalize_cell_text(row.get("source_member_row_number")).isdigit():
		raise RuntimeError(f"Combined population row has invalid source_member_row_number for HANDLER ID {handler_id}")

	expected_site_class = derive_site_class_from_flags(
		normalize_cell_text(row.get("is_tsdf_site")).upper(),
		normalize_cell_text(row.get("is_lqg_site")).upper(),
	)
	actual_site_class = normalize_cell_text(row.get("site_class"))
	if actual_site_class != expected_site_class:
		raise RuntimeError(
			f"Combined population row has inconsistent site classification for HANDLER ID {handler_id}: "
			f"expected {expected_site_class!r}, found {actual_site_class!r}"
		)


def validate_and_finalize_combined_population_rows(
	combined_population_rows: list[dict[str, str]],
	cfg: Config,
) -> tuple[list[dict[str, str]], CombinedPopulationValidationSummary]:
	validated_rows: list[dict[str, str]] = []
	tsdf_only_rows = 0
	lqg_only_rows = 0
	both_rows = 0

	for row in combined_population_rows:
		provenanced_row = apply_combined_population_provenance(row, cfg)
		validate_combined_population_row(provenanced_row)
		validated_rows.append(provenanced_row)

		site_class = provenanced_row["site_class"]
		if site_class == "tsdf":
			tsdf_only_rows += 1
		elif site_class == "lqg":
			lqg_only_rows += 1
		elif site_class == "both":
			both_rows += 1
		else:
			raise RuntimeError(f"Unexpected site_class after provenance application: {site_class!r}")

	return validated_rows, CombinedPopulationValidationSummary(
		validated_row_count=len(validated_rows),
		tsdf_only_rows=tsdf_only_rows,
		lqg_only_rows=lqg_only_rows,
		both_rows=both_rows,
	)


def log_combined_population_validation_summary(summary: CombinedPopulationValidationSummary) -> None:
	logging.info(
		"Combined population validation summary: validated_rows=%d tsdf_only_rows=%d lqg_only_rows=%d both_rows=%d",
		summary.validated_row_count,
		summary.tsdf_only_rows,
		summary.lqg_only_rows,
		summary.both_rows,
	)


def write_planned_pipeline_audits(
	cfg: Config,
	*,
	parse_audit_rows: list[dict[str, str]],
	validation_audit_rows: list[dict[str, str]],
	dedup_audit_rows: list[dict[str, str]],
) -> AuditOutputSummary:
	write_csv_rows(get_parse_audit_path(cfg), PARSE_AUDIT_FIELDNAMES, parse_audit_rows)
	write_csv_rows(get_validation_audit_path(cfg), VALIDATION_AUDIT_FIELDNAMES, validation_audit_rows)
	write_csv_rows(get_dedup_audit_path(cfg), DEDUP_AUDIT_FIELDNAMES, dedup_audit_rows)
	return AuditOutputSummary(
		parse_audit_row_count=len(parse_audit_rows),
		validation_audit_row_count=len(validation_audit_rows),
		dedup_audit_row_count=len(dedup_audit_rows),
	)


def log_planned_pipeline_audit_summary(cfg: Config, summary: AuditOutputSummary) -> None:
	logging.info("Parse audit rows written: %d", summary.parse_audit_row_count)
	logging.info("Parse audit path: %s", get_parse_audit_path(cfg))
	logging.info("Validation audit rows written: %d", summary.validation_audit_row_count)
	logging.info("Validation audit path: %s", get_validation_audit_path(cfg))
	logging.info("Dedup audit rows written: %d", summary.dedup_audit_row_count)
	logging.info("Dedup audit path: %s", get_dedup_audit_path(cfg))


def emit_planned_pipeline_outputs(
	cfg: Config,
	*,
	canonical_rows: list[dict[str, str]],
	) -> CanonicalOutputSummary:
	sorted_canonical_rows = sorted(canonical_rows, key=lambda row: row["HANDLER ID"])
	write_population_output_rows(get_canonical_output_path(cfg), sorted_canonical_rows)
	return CanonicalOutputSummary(
		canonical_row_count=len(sorted_canonical_rows),
	)


def log_planned_output_summary(cfg: Config, summary: CanonicalOutputSummary) -> None:
	logging.info("Planned canonical rows written: %d", summary.canonical_row_count)
	logging.info("Canonical output path: %s", get_canonical_output_path(cfg))


def main(argv=None) -> int:
	print("\n", "*"*20, "\nHazardous-waste preprocessing started")
	try:
		log_path = configure_logging()
		logging.info("Logging to %s", log_path)
		print("Starting setup and source inventory")
		cfg = get_config(argv)
		initialize_runtime_dependencies(cfg)
		log_runtime_context(cfg)
		inventory_planned_source_layout(cfg)
		print("Completed setup and source inventory")
		print("Starting source extraction and filtering")
		parse_audit_rows: list[dict[str, str]] = []
		validation_audit_rows: list[dict[str, str]] = []
		dedup_audit_rows: list[dict[str, str]] = []

		br_reporting_handler_ids, br_reporting_summary, br_validation_audit_rows = extract_br_reporting_handler_ids(cfg)
		validation_audit_rows.extend(br_validation_audit_rows)
		rcra_classification_summary = summarize_rcra_classification(cfg)
		rcra_tsdf_rows, rcra_tsdf_subset_summary, rcra_tsdf_validation_audit_rows = build_rcra_tsdf_subset(cfg)
		validation_audit_rows.extend(rcra_tsdf_validation_audit_rows)
		rcra_tsdf_rows, rcra_tsdf_dedup_audit_rows = deduplicate_population_slice_for_audit(
			rcra_tsdf_rows,
			audit_stage="rcra_tsdf_dedup",
			audit_note_prefix="Multiple qualifying RCRA TSDF rows were found for the same HANDLER ID",
		)
		dedup_audit_rows.extend(rcra_tsdf_dedup_audit_rows)
		rcra_br_reporting_lqg_rows, rcra_br_reporting_lqg_subset_summary, rcra_lqg_validation_audit_rows = build_rcra_br_reporting_lqg_subset(
			cfg,
			br_reporting_handler_ids=br_reporting_handler_ids,
		)
		validation_audit_rows.extend(rcra_lqg_validation_audit_rows)
		rcra_br_reporting_lqg_rows, rcra_lqg_dedup_audit_rows = deduplicate_population_slice_for_audit(
			rcra_br_reporting_lqg_rows,
			audit_stage="rcra_br_reporting_lqg_dedup",
			audit_note_prefix="Multiple qualifying BR-reporting LQG rows were found for the same HANDLER ID",
		)
		dedup_audit_rows.extend(rcra_lqg_dedup_audit_rows)
		print("Completed source extraction and filtering")
		print("Starting final validation and output writing")
		combined_population_rows, combined_population_summary = combine_rcra_population_slices(
			rcra_tsdf_rows,
			rcra_br_reporting_lqg_rows,
		)
		validated_population_rows, combined_population_validation_summary = validate_and_finalize_combined_population_rows(
			combined_population_rows,
			cfg,
		)
		audit_output_summary = write_planned_pipeline_audits(
			cfg,
			parse_audit_rows=parse_audit_rows,
			validation_audit_rows=validation_audit_rows,
			dedup_audit_rows=dedup_audit_rows,
		)
		planned_output_summary = emit_planned_pipeline_outputs(
			cfg,
			canonical_rows=validated_population_rows,
		)
		print("Completed final validation and output writing")
	except Exception as exc:
		logging.error("Hazardous waste preprocessing failed: %s", exc)
		return 1

	log_br_reporting_summary(br_reporting_summary, cfg.planned_br_reporting_source.target_biennial_year or -1)
	log_rcra_classification_summary(rcra_classification_summary)
	log_rcra_tsdf_subset_summary(rcra_tsdf_subset_summary)
	log_rcra_br_reporting_lqg_subset_summary(rcra_br_reporting_lqg_subset_summary)
	log_combined_population_summary(combined_population_summary)
	log_combined_population_validation_summary(combined_population_validation_summary)
	log_planned_pipeline_audit_summary(cfg, audit_output_summary)
	log_planned_output_summary(cfg, planned_output_summary)
	logging.info("Hazardous waste preprocessing completed successfully")
	print("Hazardous-waste preprocessing completed successfully")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
