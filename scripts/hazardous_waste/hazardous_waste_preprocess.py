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
	- Streams `HD_REPORTING` rows and logs the provisional LQG-or-TSDF universe counts.

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


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_PATH = SCRIPT_DIR / "hazardous_waste_preprocess_config.json"

SOURCE_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
	"hd_reporting": (
		"HANDLER ID",
		"HANDLER NAME",
		"GENSTATUS",
		"OPERATING TSDF",
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
		"GENSTATUS",
		"OPERATING TSDF",
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
	active_nested_sources: tuple[SourceDescriptor, ...]
	deferred_sources: tuple[SourceDescriptor, ...]


@dataclass(frozen=True, slots=True)
class CsvSchemaSummary:
	csv_member_name: str
	column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HdReportingUniverseSummary:
	total_rows: int
	lqg_rows: int
	tsdf_rows: int
	both_rows: int
	qualifying_rows: int
	neither_rows: int
	unique_handler_ids: int


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


def open_binary_input_stream(path: str):
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return fsspec.open(path, "rb")
	return open(path, "rb")


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


def is_lqg_status(value: str | None) -> bool:
	return normalize_cell_text(value).upper() == "LQG"


def has_meaningful_tsdf_status(value: str | None) -> bool:
	normalized_value = normalize_cell_text(value).upper()
	if not normalized_value:
		return False
	return any(character.isalnum() for character in normalized_value if character != "-")


def classify_hd_reporting_row(row: dict[str, str]) -> tuple[bool, bool]:
	is_lqg = is_lqg_status(row.get("GENSTATUS"))
	has_tsdf = has_meaningful_tsdf_status(row.get("OPERATING TSDF"))
	return is_lqg, has_tsdf


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
	lqg_rows = 0
	tsdf_rows = 0
	both_rows = 0
	qualifying_rows = 0
	unique_handler_ids: set[str] = set()

	with open_inner_csv_text_stream(inner_archive, csv_member_name) as text_stream:
		reader = csv.DictReader(text_stream)
		if reader.fieldnames is None:
			raise RuntimeError(f"CSV member is missing a header row: {csv_member_name}")

		for row in reader:
			total_rows += 1
			is_lqg, has_tsdf = classify_hd_reporting_row(row)
			if is_lqg:
				lqg_rows += 1
			if has_tsdf:
				tsdf_rows += 1
			if is_lqg and has_tsdf:
				both_rows += 1
			if is_lqg or has_tsdf:
				qualifying_rows += 1
				handler_id = normalize_handler_id(row.get("HANDLER ID"))
				if handler_id:
					unique_handler_ids.add(handler_id)

	return HdReportingUniverseSummary(
		total_rows=total_rows,
		lqg_rows=lqg_rows,
		tsdf_rows=tsdf_rows,
		both_rows=both_rows,
		qualifying_rows=qualifying_rows,
		neither_rows=total_rows - qualifying_rows,
		unique_handler_ids=len(unique_handler_ids),
	)


def extract_hd_reporting_universe(cfg: Config) -> set[str]:
	source = get_required_source(cfg, "hd_reporting")
	source_outer_archive_path = get_source_outer_archive_path(cfg, source)
	qualifying_handler_ids: set[str] = set()
	total_rows = 0
	total_lqg_rows = 0
	total_tsdf_rows = 0
	total_both_rows = 0
	total_qualifying_rows = 0

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
				member_summary = summarize_hd_reporting_member(inner_archive, matched_csv_member)
				logging.info(
					"HD_REPORTING member summary for %s: total=%d lqg=%d tsdf=%d both=%d qualifying=%d neither=%d unique_handlers=%d",
					matched_csv_member,
					member_summary.total_rows,
					member_summary.lqg_rows,
					member_summary.tsdf_rows,
					member_summary.both_rows,
					member_summary.qualifying_rows,
					member_summary.neither_rows,
					member_summary.unique_handler_ids,
				)

				with open_inner_csv_text_stream(inner_archive, matched_csv_member) as text_stream:
					reader = csv.DictReader(text_stream)
					for row in reader:
						total_rows += 1
						is_lqg, has_tsdf = classify_hd_reporting_row(row)
						if is_lqg:
							total_lqg_rows += 1
						if has_tsdf:
							total_tsdf_rows += 1
						if is_lqg and has_tsdf:
							total_both_rows += 1
						if not (is_lqg or has_tsdf):
							continue
						total_qualifying_rows += 1
						handler_id = normalize_handler_id(row.get("HANDLER ID"))
						if handler_id:
							qualifying_handler_ids.add(handler_id)

	logging.info(
		"HD_REPORTING universe summary: total=%d lqg=%d tsdf=%d both=%d qualifying=%d neither=%d unique_handlers=%d",
		total_rows,
		total_lqg_rows,
		total_tsdf_rows,
		total_both_rows,
		total_qualifying_rows,
		total_rows - total_qualifying_rows,
		len(qualifying_handler_ids),
	)
	return qualifying_handler_ids


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

		inventory_configured_sources(cfg)
		extract_hd_reporting_universe(cfg)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing proof slice failed: %s", exc)
		return 1

	logging.info("Hazardous waste preprocessing proof slice completed successfully")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
