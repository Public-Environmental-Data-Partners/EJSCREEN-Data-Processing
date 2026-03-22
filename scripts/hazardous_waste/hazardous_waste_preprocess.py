"""
hazardous_waste_preprocess.py

Purpose:
	Read the hazardous-waste handler ZIP archive from local storage or S3,
	filter all HD_HANDLER_*.csv members to the required major facility subset
	(i.e. Treatment, Storage, and Disposal Facilities (TSDFs) and 
	Large Quantity Generators (LQGs) with current records only),
	and write one consolidated filtered CSV to the selected storage root.

Module requirements note:
	All modes require pandas and fsspec to be installed.
	Remote-mode reading also requires python-dotenv and s3fs.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
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


@dataclass
class Config:
	storage_mode: str
	local_root_path: str = "./pipeline/test_data/"
	remote_root_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/hazardous_waste/pipeline/"
	input_relative_path: str = "downloads/HD_HANDLER_20260315.zip"
	output_relative_path: str = "outputs/hazardous_waste_filtered.csv"
	chunk_size: int = 50000


def get_config(argv=None) -> Config:
	parser = argparse.ArgumentParser(
		description="Filter all HD_HANDLER CSV members in the hazardous-waste archive into one consolidated CSV."
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


def get_output_path(cfg: Config) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), cfg.output_relative_path)


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


def create_filter_masks(chunk_df: pd.DataFrame) -> dict[str, pd.Series]:
	return {
		"is_tsdf": chunk_df["TSD ACTIVITY"] == "Y",
		"is_lqg": chunk_df["FED WASTE GENERATOR"] == "1",
		"is_current": chunk_df["CURRENT RECORD"] == "Y",
		"include_in_national_report": chunk_df["INCLUDE IN NATIONAL REPORT"] == "Y",
	}


def apply_hazardous_waste_sieve(chunk_df: pd.DataFrame) -> pd.DataFrame:
	masks = create_filter_masks(chunk_df)
	combined_mask = (
		masks["is_current"]
		& masks["include_in_national_report"]
		& (masks["is_tsdf"] | masks["is_lqg"])
	)
	return chunk_df[combined_mask].copy()


def process_archive_to_output(
	outer_archive_path: str,
	member_names: list[str],
	chunk_size: int,
	output_path: str,
) -> tuple[int, int, int, int]:
	if chunk_size <= 0:
		raise ValueError(f"Chunk size must be positive. Received: {chunk_size}")
	if not member_names:
		raise RuntimeError("No CSV members were provided for processing")

	if not is_s3_uri(output_path):
		Path(output_path).parent.mkdir(parents=True, exist_ok=True)

	total_member_count = 0
	total_chunk_count = 0
	total_input_rows = 0
	total_survivor_rows = 0
	header_written = False
	output_columns = None

	try:
		with fsspec.open(outer_archive_path, "rb") as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				with fsspec.open(output_path, "w") as output_stream:
					for member_name in member_names:
						logging.info("Processing member: %s", member_name)
						member_chunk_count = 0
						with archive.open(member_name, "r") as member_stream:
							text_stream = io.TextIOWrapper(member_stream, encoding="utf-8-sig", newline="")
							chunk_iterator = pd.read_csv(text_stream, chunksize=chunk_size, low_memory=False)
							for chunk_index, chunk_df in enumerate(chunk_iterator, start=1):
								if member_chunk_count == 0:
									member_columns = chunk_df.columns.tolist()
									required_columns_found = validate_required_sieve_columns(member_columns, member_name)
									logging.info(
										"Validated required sieve columns for %s: %s",
										member_name,
										required_columns_found,
									)
									if output_columns is None:
										output_columns = member_columns
									elif member_columns != output_columns:
										raise RuntimeError(
											"CSV member columns differ from the first processed member. "
											f"Member examined: {member_name}"
										)

								filtered_chunk_df = apply_hazardous_waste_sieve(chunk_df)
								input_row_count = len(chunk_df)
								survivor_row_count = len(filtered_chunk_df)
								filtered_chunk_df.to_csv(
									output_stream,
									index=False,
									header=not header_written,
								)
								logging.info(
									"Process chunk member=%s chunk=%d input=%d survivors=%d",
									member_name,
									chunk_index,
									input_row_count,
									survivor_row_count,
								)
								total_input_rows += input_row_count
								total_survivor_rows += survivor_row_count
								total_chunk_count += 1
								member_chunk_count = chunk_index
								header_written = True

						if member_chunk_count == 0:
							raise RuntimeError(f"Chunked CSV read produced no data rows: {member_name}")

						total_member_count += 1
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except KeyError as exc:
		raise RuntimeError("Expected CSV member was not found in the archive during processing") from exc

	if total_member_count == 0:
		raise RuntimeError("No CSV members were processed")

	return total_member_count, total_chunk_count, total_input_rows, total_survivor_rows


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	try:
		cfg = get_config(argv)
		initialize_runtime_dependencies(cfg)
		outer_archive_path = get_outer_archive_path(cfg)
		output_path = get_output_path(cfg)

		logging.info("Storage mode: %s", cfg.storage_mode)
		logging.info("Outer archive path: %s", outer_archive_path)
		logging.info("Output path: %s", output_path)
		logging.info("Chunk size: %d", cfg.chunk_size)

		hd_handler_members = enumerate_archive_members(outer_archive_path)
		logging.info("Found %d HD_HANDLER members in outer archive", len(hd_handler_members))
		for member_name in hd_handler_members:
			logging.info("Archive member: %s", member_name)

		member_count, chunk_count, total_input_rows, total_survivor_rows = process_archive_to_output(
			outer_archive_path,
			hd_handler_members,
			cfg.chunk_size,
			output_path,
		)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing failed: %s", exc)
		return 1

	logging.info("Hazardous waste preprocessing complete")
	logging.info("Members processed: %d", member_count)
	logging.info("Chunks processed: %d", chunk_count)
	logging.info("Total input rows: %d", total_input_rows)
	logging.info("Total survivor rows written: %d", total_survivor_rows)
	logging.info("Consolidated output path: %s", output_path)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
