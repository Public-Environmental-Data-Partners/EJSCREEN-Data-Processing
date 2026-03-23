"""
hazardous_waste_preprocess.py

Purpose:
	Read the hazardous-waste handler ZIP archive from local storage or S3,
	validate raw CSV records, separate parse failures from content-validation
	failures, write a provenance-aware provisional filtered CSV, and finalize a
	canonical deduped CSV plus audit outputs.

Module requirements note:
	All modes require pandas and fsspec to be installed.
	Remote-mode reading also requires python-dotenv and s3fs.

Credits:
  - Overall design by Anne Gunn and Gemimi
  - CSV parsing and auditing designed by GPT-5.4 and Anne Gunn
  - Implemented by GitHub Copilot, GPT-5.4, and Anne Gunn
"""

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import argparse
import csv
import io
import logging
import zipfile

import fsspec
import pandas as pd


REQUIRED_SIEVE_COLUMNS = (
	"CURRENT RECORD",
	"INCLUDE IN NATIONAL REPORT",
	"TSD ACTIVITY",
	"FED WASTE GENERATOR",
	"LOCATION LONGITUDE",
	"LOCATION LATITUDE",
)

PROVENANCE_COLUMN_NAMES = (
	"source_member_filename",
	"source_member_row_number",
	"source_member_end_line_number",
)

REQUIRED_FINALIZATION_COLUMNS = (
	"HANDLER ID",
	"LOCATION LATITUDE",
	"LOCATION LONGITUDE",
	"source_member_filename",
	"source_member_row_number",
)

PARSE_AUDIT_COLUMN_NAMES = (
	"source_member_filename",
	"source_member_row_number",
	"source_member_end_line_number",
	"parse_error",
	"expected_column_count",
	"parsed_column_count",
	"raw_field_excerpt",
)

ROW_AUDIT_COLUMN_NAMES = (
	"provisional_file_line_number",
	"audit_reason",
	"audit_note",
	"group_size",
	"is_canonical_row",
)

VALIDATION_AUDIT_FRONT_COLUMNS = PROVENANCE_COLUMN_NAMES + (
	"audit_reason",
	"audit_note",
)


@dataclass
class Config:
	storage_mode: str
	local_root_path: str = "./pipeline/test_data/"
	remote_root_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/hazardous_waste/pipeline/"
	input_relative_path: str = "downloads/HD_HANDLER_20260315.zip"
	provisional_output_relative_path: str = "outputs/hazardous_waste_filtered_pre_dedup.csv"
	canonical_output_relative_path: str = "outputs/hazardous_waste_filtered.csv"
	parse_audit_relative_path: str = "outputs/hazardous_waste_parse_audit.csv"
	validation_audit_relative_path: str = "outputs/hazardous_waste_validation_audit.csv"
	dedup_audit_relative_path: str = "outputs/hazardous_waste_dedup_audit.csv"
	chunk_size: int = 50000


@dataclass(frozen=True)
class ProcessingSummary:
	raw_record_count: int
	parse_failure_count: int
	sieve_match_count: int
	validation_failure_count: int
	provisional_row_count: int


@dataclass(frozen=True)
class FinalizationSummary:
	provisional_row_count: int
	exact_duplicates_removed: int
	duplicate_handler_id_count: int
	coordinate_conflict_count: int
	canonical_row_count: int
	dedup_audit_row_count: int


def get_config(argv=None) -> Config:
	parser = argparse.ArgumentParser(
		description="Validate HD_HANDLER records, then finalize a canonical hazardous-waste site CSV."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads and writes through the local root path or the remote S3 root path.",
	)
	args = parser.parse_args(argv)
	return Config(storage_mode=args.storage_mode)


def initialize_runtime_dependencies(cfg: Config) -> None:
	if cfg.storage_mode != "remote":
		return

	import dotenv
	import s3fs

	dotenv.load_dotenv()
	if s3fs is None:
		raise RuntimeError("s3fs failed to import for remote mode")


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
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.input_relative_path)


def get_provisional_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.provisional_output_relative_path)


def get_canonical_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.canonical_output_relative_path)


def get_parse_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.parse_audit_relative_path)


def get_validation_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.validation_audit_relative_path)


def get_dedup_audit_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.dedup_audit_relative_path)


def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_csv_via_fsspec(path: str, **read_csv_kwargs) -> pd.DataFrame:
	with fsspec.open(path, "r") as input_stream:
		return pd.read_csv(input_stream, low_memory=False, **read_csv_kwargs)


def write_df_to_csv(df: pd.DataFrame, path: str) -> None:
	ensure_local_parent_dir(path)
	with fsspec.open(path, "w") as output_stream:
		df.to_csv(output_stream, index=False)


def require_columns(df: pd.DataFrame, required_columns: tuple[str, ...], dataset_name: str) -> None:
	missing_columns = [column_name for column_name in required_columns if column_name not in df.columns]
	if missing_columns:
		raise RuntimeError(f"{dataset_name} is missing required columns: {missing_columns}")


def enumerate_archive_members(outer_archive_path: str) -> list[str]:
	try:
		with fsspec.open(outer_archive_path, "rb") as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				member_names = [name for name in archive.namelist() if name and not name.endswith("/")]
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except zipfile.BadZipFile as exc:
		raise RuntimeError(f"Outer archive is not a valid ZIP file: {outer_archive_path}") from exc

	hd_handler_members = [
		name
		for name in member_names
		if Path(name).name.startswith("HD_HANDLER_") and Path(name).suffix.lower() == ".csv"
	]
	if not hd_handler_members:
		raise RuntimeError(
			"No HD_HANDLER CSV members were found in the outer archive. "
			f"Archive examined: {outer_archive_path}"
		)

	return sorted(hd_handler_members)


def validate_required_sieve_columns(column_names: list[str], member_name: str) -> list[str]:
	column_name_set = set(column_names)
	missing_columns = [column_name for column_name in REQUIRED_SIEVE_COLUMNS if column_name not in column_name_set]
	if missing_columns:
		raise RuntimeError(
			"CSV member is missing required sieve columns: "
			f"{missing_columns}. Member examined: {member_name}"
		)
	return [column_name for column_name in REQUIRED_SIEVE_COLUMNS if column_name in column_name_set]


def normalize_handler_id_value(value) -> str:
	if pd.isna(value):
		return ""
	return str(value).strip()


def value_has_parseable_float(value) -> bool:
	try:
		float(value)
		return True
	except (TypeError, ValueError):
		return False


def escape_control_characters(value: str) -> str:
	return value.replace("\r", "\\r").replace("\n", "\\n")


def build_raw_field_excerpt(row_values: list[str], max_length: int = 500) -> str:
	joined = " | ".join(escape_control_characters(str(value)) for value in row_values[:8])
	if len(joined) > max_length:
		return joined[: max_length - 3] + "..."
	return joined


def row_passes_hazardous_waste_sieve(row_dict: dict[str, str]) -> bool:
	is_tsdf = row_dict.get("TSD ACTIVITY") == "Y"
	is_lqg = row_dict.get("FED WASTE GENERATOR") == "1"
	is_current = row_dict.get("CURRENT RECORD") == "Y"
	include_in_national_report = row_dict.get("INCLUDE IN NATIONAL REPORT") == "Y"
	return is_current and include_in_national_report and (is_tsdf or is_lqg)


def build_provenanced_row(
	row_dict: dict[str, str],
	member_name: str,
	start_line_number: int,
	end_line_number: int,
) -> dict[str, str | int]:
	provenanced_row = dict(row_dict)
	provenanced_row["source_member_filename"] = member_name
	provenanced_row["source_member_row_number"] = start_line_number
	provenanced_row["source_member_end_line_number"] = end_line_number
	return provenanced_row


def classify_validation_failure(row_dict: dict[str, str | int]) -> tuple[str, str] | None:
	error_codes = []
	if normalize_handler_id_value(row_dict.get("HANDLER ID")) == "":
		error_codes.append("missing_handler_id")
	if not value_has_parseable_float(row_dict.get("LOCATION LATITUDE")) or not value_has_parseable_float(row_dict.get("LOCATION LONGITUDE")):
		error_codes.append("invalid_coordinates")
	if not error_codes:
		return None
	note = "; ".join(error_codes)
	return note, f"Row failed site-level validation: {note}"


def build_parse_audit_record(
	member_name: str,
	start_line_number: int,
	end_line_number: int,
	parse_error: str,
	expected_column_count: int,
	row_values: list[str],
) -> dict[str, str | int]:
	return {
		"source_member_filename": member_name,
		"source_member_row_number": start_line_number,
		"source_member_end_line_number": end_line_number,
		"parse_error": parse_error,
		"expected_column_count": expected_column_count,
		"parsed_column_count": len(row_values),
		"raw_field_excerpt": build_raw_field_excerpt(row_values),
	}


def order_columns_with_front(
	all_columns: list[str],
	front_columns: tuple[str, ...],
) -> list[str]:
	ordered_front_columns = [column_name for column_name in front_columns if column_name in all_columns]
	remaining_columns = [column_name for column_name in all_columns if column_name not in front_columns]
	return ordered_front_columns + remaining_columns


def open_csv_writer(path: str, fieldnames: list[str]):
	ensure_local_parent_dir(path)
	return fsspec.open(path, "w", newline=""), fieldnames


def process_archive_to_output(
	outer_archive_path: str,
	member_names: list[str],
	chunk_size: int,
	provisional_output_path: str,
	parse_audit_path: str,
	validation_audit_path: str,
) -> tuple[int, ProcessingSummary]:
	if chunk_size <= 0:
		raise ValueError(f"Chunk size must be positive. Received: {chunk_size}")
	if not member_names:
		raise RuntimeError("No CSV members were provided for processing")

	raw_record_count = 0
	parse_failure_count = 0
	sieve_match_count = 0
	validation_failure_count = 0
	provisional_row_count = 0
	total_member_count = 0
	expected_columns = None
	provisional_writer = None
	validation_writer = None

	with ExitStack() as stack:
		parse_audit_handle = stack.enter_context(fsspec.open(parse_audit_path, "w", newline=""))
		validation_audit_handle = stack.enter_context(fsspec.open(validation_audit_path, "w", newline=""))
		provisional_handle = stack.enter_context(fsspec.open(provisional_output_path, "w", newline=""))

		parse_audit_writer = csv.DictWriter(parse_audit_handle, fieldnames=list(PARSE_AUDIT_COLUMN_NAMES))
		parse_audit_writer.writeheader()

		with fsspec.open(outer_archive_path, "rb") as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				for member_name in member_names:
					logging.info("Processing member: %s", member_name)
					with archive.open(member_name, "r") as member_stream:
						text_stream = io.TextIOWrapper(member_stream, encoding="utf-8-sig", newline="")
						reader = csv.reader(text_stream)
						try:
							member_columns = next(reader)
						except StopIteration as exc:
							raise RuntimeError(f"CSV member is empty: {member_name}") from exc

						required_columns_found = validate_required_sieve_columns(member_columns, member_name)
						logging.info(
							"Validated required sieve columns for %s: %s",
							member_name,
							required_columns_found,
						)
						if expected_columns is None:
							expected_columns = member_columns
							provisional_fieldnames = expected_columns + list(PROVENANCE_COLUMN_NAMES)
							validation_fieldnames = order_columns_with_front(
								all_columns=provisional_fieldnames + ["audit_reason", "audit_note"],
								front_columns=VALIDATION_AUDIT_FRONT_COLUMNS,
							)
							provisional_writer = csv.DictWriter(provisional_handle, fieldnames=provisional_fieldnames)
							validation_writer = csv.DictWriter(validation_audit_handle, fieldnames=validation_fieldnames)
							provisional_writer.writeheader()
							validation_writer.writeheader()
						elif member_columns != expected_columns:
							raise RuntimeError(
								"CSV member columns differ from the first processed member. "
								f"Member examined: {member_name}"
							)

						previous_line_number = reader.line_num
						try:
							for row_values in reader:
								end_line_number = reader.line_num
								start_line_number = previous_line_number + 1
								previous_line_number = end_line_number
								raw_record_count += 1

								if len(row_values) != len(expected_columns):
									parse_failure_count += 1
									parse_audit_writer.writerow(
										build_parse_audit_record(
											member_name=member_name,
											start_line_number=start_line_number,
											end_line_number=end_line_number,
											parse_error="unexpected_column_count",
											expected_column_count=len(expected_columns),
											row_values=row_values,
										)
									)
									continue

								row_dict = dict(zip(expected_columns, row_values))
								if not row_passes_hazardous_waste_sieve(row_dict):
									continue

								sieve_match_count += 1
								provenanced_row = build_provenanced_row(
									row_dict=row_dict,
									member_name=member_name,
									start_line_number=start_line_number,
									end_line_number=end_line_number,
								)
								validation_failure = classify_validation_failure(provenanced_row)
								if validation_failure is not None:
									validation_failure_count += 1
									audit_reason, audit_note = validation_failure
									validation_row = dict(provenanced_row)
									validation_row["audit_reason"] = audit_reason
									validation_row["audit_note"] = audit_note
									validation_writer.writerow(validation_row)
									continue

								provisional_writer.writerow(provenanced_row)
								provisional_row_count += 1
						except csv.Error as exc:
							parse_failure_count += 1
							parse_audit_writer.writerow(
								{
									"source_member_filename": member_name,
									"source_member_row_number": previous_line_number + 1,
									"source_member_end_line_number": reader.line_num,
									"parse_error": f"csv_reader_error: {exc}",
									"expected_column_count": len(expected_columns),
									"parsed_column_count": 0,
									"raw_field_excerpt": "",
								}
							)

					total_member_count += 1

	return total_member_count, ProcessingSummary(
		raw_record_count=raw_record_count,
		parse_failure_count=parse_failure_count,
		sieve_match_count=sieve_match_count,
		validation_failure_count=validation_failure_count,
		provisional_row_count=provisional_row_count,
	)


def normalize_handler_id_series(series: pd.Series) -> pd.Series:
	return series.astype("string").fillna("").str.strip()


def build_coordinate_key(latitudes: pd.Series, longitudes: pd.Series) -> pd.Series:
	valid_mask = latitudes.notna() & longitudes.notna()
	coordinate_key = pd.Series("", index=latitudes.index, dtype="string")
	coordinate_key.loc[valid_mask] = (
		latitudes.loc[valid_mask].round(6).map(lambda value: f"{value:.6f}")
		+ "|"
		+ longitudes.loc[valid_mask].round(6).map(lambda value: f"{value:.6f}")
	)
	return coordinate_key


def build_audit_frame(df: pd.DataFrame) -> pd.DataFrame:
	base_columns = [column_name for column_name in df.columns if not column_name.startswith("_")]
	if "_source_row_number" in df.columns and "provisional_file_line_number" not in base_columns:
		df = df.copy()
		df["provisional_file_line_number"] = df["_source_row_number"] + 2
		base_columns = [column_name for column_name in df.columns if not column_name.startswith("_")]
	for audit_column_name in ROW_AUDIT_COLUMN_NAMES:
		if audit_column_name not in base_columns:
			base_columns.append(audit_column_name)
	ordered_columns = order_columns_with_front(base_columns, ROW_AUDIT_COLUMN_NAMES)
	return df[ordered_columns].copy()


def finalize_provisional_output(
	provisional_output_path: str,
	canonical_output_path: str,
	dedup_audit_path: str,
) -> FinalizationSummary:
	provisional_df = read_csv_via_fsspec(provisional_output_path)
	require_columns(provisional_df, REQUIRED_FINALIZATION_COLUMNS, "provisional hazardous-waste output")

	provisional_row_count = len(provisional_df)
	provisional_df = provisional_df.copy()
	provisional_df["_source_row_number"] = range(len(provisional_df))
	provisional_df["_handler_id_norm"] = normalize_handler_id_series(provisional_df["HANDLER ID"])
	provisional_df["_lat_num"] = pd.to_numeric(provisional_df["LOCATION LATITUDE"], errors="coerce")
	provisional_df["_lon_num"] = pd.to_numeric(provisional_df["LOCATION LONGITUDE"], errors="coerce")
	provisional_df["_has_valid_coords"] = provisional_df["_lat_num"].notna() & provisional_df["_lon_num"].notna()
	provisional_df["_coord_key"] = build_coordinate_key(provisional_df["_lat_num"], provisional_df["_lon_num"])

	if provisional_df["_handler_id_norm"].eq("").any():
		raise RuntimeError("Provisional hazardous-waste output still contains blank HANDLER ID values after validation")
	if (~provisional_df["_has_valid_coords"]).any():
		raise RuntimeError("Provisional hazardous-waste output still contains invalid coordinates after validation")

	if "RECEIVE DATE" in provisional_df.columns:
		provisional_df["_receive_date_rank"] = pd.to_datetime(
			provisional_df["RECEIVE DATE"],
			errors="coerce",
		).astype("int64")
	else:
		provisional_df["_receive_date_rank"] = -1

	if "REPORT CYCLE" in provisional_df.columns:
		provisional_df["_report_cycle_rank"] = pd.to_numeric(
			provisional_df["REPORT CYCLE"],
			errors="coerce",
		).fillna(float("-inf"))
	else:
		provisional_df["_report_cycle_rank"] = float("-inf")

	if "SEQ NUMBER" in provisional_df.columns:
		provisional_df["_seq_number_rank"] = pd.to_numeric(
			provisional_df["SEQ NUMBER"],
			errors="coerce",
		).fillna(float("-inf"))
	else:
		provisional_df["_seq_number_rank"] = float("-inf")

	audit_frames = []
	business_columns = [column_name for column_name in provisional_df.columns if column_name not in PROVENANCE_COLUMN_NAMES]
	exact_duplicate_mask = provisional_df.duplicated(subset=business_columns, keep=False)
	exact_duplicates_removed = 0
	if exact_duplicate_mask.any():
		exact_duplicate_df = provisional_df[exact_duplicate_mask].copy()
		exact_duplicate_group_count = len(exact_duplicate_df.drop_duplicates(subset=business_columns))
		exact_duplicates_removed = len(exact_duplicate_df) - exact_duplicate_group_count
		for _, group_df in exact_duplicate_df.groupby(business_columns, dropna=False, sort=False):
			selected_row = group_df.iloc[0]
			group_audit_df = group_df.copy()
			group_audit_df["audit_reason"] = "exact_duplicate"
			group_audit_df["audit_note"] = "Rows are identical across non-provenance columns; first source row retained"
			group_audit_df["group_size"] = len(group_df)
			group_audit_df["is_canonical_row"] = group_audit_df.index == selected_row.name
			audit_frames.append(build_audit_frame(group_audit_df))

	candidate_df = provisional_df.drop_duplicates(subset=business_columns, keep="first").copy()
	if candidate_df.empty:
		canonical_df = candidate_df[[column_name for column_name in candidate_df.columns if not column_name.startswith("_")]]
		audit_df = pd.concat(audit_frames, ignore_index=True) if audit_frames else pd.DataFrame(columns=list(canonical_df.columns) + list(ROW_AUDIT_COLUMN_NAMES))
		write_df_to_csv(canonical_df, canonical_output_path)
		write_df_to_csv(audit_df, dedup_audit_path)
		return FinalizationSummary(
			provisional_row_count=provisional_row_count,
			exact_duplicates_removed=exact_duplicates_removed,
			duplicate_handler_id_count=0,
			coordinate_conflict_count=0,
			canonical_row_count=0,
			dedup_audit_row_count=len(audit_df),
		)

	sorted_df = candidate_df.sort_values(
		by=[
			"_handler_id_norm",
			"_has_valid_coords",
			"_receive_date_rank",
			"_report_cycle_rank",
			"_seq_number_rank",
			"_source_row_number",
		],
		ascending=[True, False, False, False, False, True],
		kind="mergesort",
	)

	group_sizes = sorted_df.groupby("_handler_id_norm").size()
	duplicate_handler_id_count = int((group_sizes > 1).sum())
	canonical_index = sorted_df.groupby("_handler_id_norm", sort=False).head(1).index
	canonical_df = sorted_df.loc[canonical_index].copy()
	canonical_df["HANDLER ID"] = canonical_df["_handler_id_norm"]

	coordinate_conflict_count = 0
	for _, group_df in sorted_df.groupby("_handler_id_norm", sort=False):
		if len(group_df) <= 1:
			continue

		selected_row = group_df.iloc[0]
		valid_coordinate_keys = [coordinate_key for coordinate_key in group_df["_coord_key"].tolist() if coordinate_key]
		coordinate_conflict = len(set(valid_coordinate_keys)) > 1
		if coordinate_conflict:
			coordinate_conflict_count += 1

		tie_mask = (
			(group_df["_has_valid_coords"] == selected_row["_has_valid_coords"])
			& (group_df["_receive_date_rank"] == selected_row["_receive_date_rank"])
			& (group_df["_report_cycle_rank"] == selected_row["_report_cycle_rank"])
			& (group_df["_seq_number_rank"] == selected_row["_seq_number_rank"])
		)
		unresolved_tie = int(tie_mask.sum()) > 1

		reason = "coordinate_conflict" if coordinate_conflict else "duplicate_handler_id"
		note_parts = []
		if unresolved_tie:
			note_parts.append("Deterministic first-row fallback applied after tie-break fields remained tied")
		if not coordinate_conflict:
			note_parts.append("Rows share the same HANDLER ID without a coordinate conflict")
		group_audit_df = group_df.copy()
		group_audit_df["audit_reason"] = reason
		group_audit_df["audit_note"] = " ".join(note_parts).strip()
		group_audit_df["group_size"] = len(group_df)
		group_audit_df["is_canonical_row"] = group_audit_df.index == selected_row.name
		audit_frames.append(build_audit_frame(group_audit_df))

	canonical_df = canonical_df.sort_values(by=["HANDLER ID"], kind="mergesort")
	canonical_df = canonical_df[[column_name for column_name in canonical_df.columns if not column_name.startswith("_")]].reset_index(drop=True)
	if canonical_df["HANDLER ID"].duplicated().any():
		raise RuntimeError("Canonical hazardous-waste output still contains duplicate HANDLER ID values after finalization")

	if audit_frames:
		audit_df = pd.concat(audit_frames, ignore_index=True)
	else:
		audit_df = pd.DataFrame(columns=list(canonical_df.columns) + list(ROW_AUDIT_COLUMN_NAMES))

	write_df_to_csv(canonical_df, canonical_output_path)
	write_df_to_csv(audit_df, dedup_audit_path)

	return FinalizationSummary(
		provisional_row_count=provisional_row_count,
		exact_duplicates_removed=exact_duplicates_removed,
		duplicate_handler_id_count=duplicate_handler_id_count,
		coordinate_conflict_count=coordinate_conflict_count,
		canonical_row_count=len(canonical_df),
		dedup_audit_row_count=len(audit_df),
	)


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	try:
		cfg = get_config(argv)
		initialize_runtime_dependencies(cfg)
		outer_archive_path = get_outer_archive_path(cfg)
		provisional_output_path = get_provisional_output_path(cfg)
		canonical_output_path = get_canonical_output_path(cfg)
		parse_audit_path = get_parse_audit_path(cfg)
		validation_audit_path = get_validation_audit_path(cfg)
		dedup_audit_path = get_dedup_audit_path(cfg)

		logging.info("Storage mode: %s", cfg.storage_mode)
		logging.info("Outer archive path: %s", outer_archive_path)
		logging.info("Provisional output path: %s", provisional_output_path)
		logging.info("Canonical output path: %s", canonical_output_path)
		logging.info("Parse audit path: %s", parse_audit_path)
		logging.info("Validation audit path: %s", validation_audit_path)
		logging.info("Dedup audit path: %s", dedup_audit_path)
		logging.info("Chunk size: %d", cfg.chunk_size)

		hd_handler_members = enumerate_archive_members(outer_archive_path)
		logging.info("Found %d HD_HANDLER members in outer archive", len(hd_handler_members))
		for member_name in hd_handler_members:
			logging.info("Archive member: %s", member_name)

		member_count, processing_summary = process_archive_to_output(
			outer_archive_path=outer_archive_path,
			member_names=hd_handler_members,
			chunk_size=cfg.chunk_size,
			provisional_output_path=provisional_output_path,
			parse_audit_path=parse_audit_path,
			validation_audit_path=validation_audit_path,
		)
		finalization_summary = finalize_provisional_output(
			provisional_output_path=provisional_output_path,
			canonical_output_path=canonical_output_path,
			dedup_audit_path=dedup_audit_path,
		)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing failed: %s", exc)
		return 1

	logging.info("Hazardous waste preprocessing complete")
	logging.info("Zip file csv members processed: %d", member_count)
	logging.info("Raw records examined: %d", processing_summary.raw_record_count)
	logging.info("Parse failures written: %d", processing_summary.parse_failure_count)
	logging.info("Parse audit path: %s", parse_audit_path)
	logging.info("Rows matching hazardous-waste sieve: %d", processing_summary.sieve_match_count)
	logging.info("Validation failures written: %d", processing_summary.validation_failure_count)
	logging.info("Validation audit path: %s", validation_audit_path)
	logging.info("Rows written to provisional output: %d", processing_summary.provisional_row_count)
	logging.info("Exact duplicate rows removed: %d", finalization_summary.exact_duplicates_removed)
	logging.info("Duplicate HANDLER ID groups encountered: %d", finalization_summary.duplicate_handler_id_count)
	logging.info("Coordinate conflict groups: %d", finalization_summary.coordinate_conflict_count)
	logging.info("Canonical site rows written: %d", finalization_summary.canonical_row_count)
	logging.info("Dedup audit rows written: %d", finalization_summary.dedup_audit_row_count)
	logging.info("Dedup audit path: %s", dedup_audit_path)
	logging.info("Provisional output path: %s", provisional_output_path)
	logging.info("Canonical output path: %s", canonical_output_path)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
