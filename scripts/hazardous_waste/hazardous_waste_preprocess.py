"""
hazardous_waste_preprocess.py

Slice 1 purpose:
  Open the outer hazardous-waste archive and enumerate the HD_HANDLER members
  inside it. This first slice is intentionally narrow so local archive access can
  be tested before any filtering or output logic is introduced.

Module requirements note:
	Remote-mode reading requires fsspec and s3fs to be installed.
	Local-mode reading does not require those modules.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
import csv
import importlib
import io
import logging
import zipfile

try:
	fsspec = importlib.import_module("fsspec")
except Exception as _e:  # pragma: no cover - environment dependent
	fsspec = None

try:
	pd = importlib.import_module("pandas")
except Exception as _e:  # pragma: no cover - environment dependent
	pd = None


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
	try:
		dotenv = importlib.import_module("dotenv")
		dotenv.load_dotenv()
	except Exception:
		pass

	parser = argparse.ArgumentParser(
		description="Enumerate and inspect HD_HANDLER CSV members inside the hazardous-waste archive."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads from the local root path or the remote S3 root path.",
	)
	args = parser.parse_args(argv)
	return Config(storage_mode=args.storage_mode)


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


def enumerate_archive_members(outer_archive_path: str) -> list[str]:
	if fsspec is None:
		raise RuntimeError("fsspec is not available; cannot open local or remote archive paths")

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


def read_member_csv_header(outer_archive_path: str, member_name: str) -> list[str]:
	if fsspec is None:
		raise RuntimeError("fsspec is not available; cannot open local or remote archive paths")

	try:
		with fsspec.open(outer_archive_path, "rb") as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				with archive.open(member_name, "r") as member_stream:
					text_stream = io.TextIOWrapper(member_stream, encoding="utf-8-sig", newline="")
					reader = csv.reader(text_stream)
					try:
						header_row = next(reader)
					except StopIteration as exc:
						raise RuntimeError(f"CSV member is empty: {member_name}") from exc

					if not header_row:
						raise RuntimeError(f"CSV member has an empty header row: {member_name}")

					return header_row
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except KeyError as exc:
		raise RuntimeError(
			f"Expected CSV member was not found in the archive: {member_name}"
		) from exc


def validate_required_sieve_columns(header_row: list[str], member_name: str) -> list[str]:
	header_set = set(header_row)
	missing_columns = [column_name for column_name in REQUIRED_SIEVE_COLUMNS if column_name not in header_set]
	if missing_columns:
		raise RuntimeError(
			"CSV member is missing required sieve columns: "
			f"{missing_columns}. Member examined: {member_name}"
		)
	return [column_name for column_name in REQUIRED_SIEVE_COLUMNS if column_name in header_set]


def chunk_read_member_csv(outer_archive_path: str, member_name: str, chunk_size: int) -> tuple[int, int]:
	if fsspec is None:
		raise RuntimeError("fsspec is not available; cannot open local or remote archive paths")
	if pd is None:
		raise RuntimeError("pandas is not available; cannot chunk-read CSV members")
	if chunk_size <= 0:
		raise ValueError(f"Chunk size must be positive. Received: {chunk_size}")

	total_rows = 0
	chunk_count = 0

	try:
		with fsspec.open(outer_archive_path, "rb") as archive_stream:
			with zipfile.ZipFile(archive_stream, "r") as archive:
				with archive.open(member_name, "r") as member_stream:
					text_stream = io.TextIOWrapper(member_stream, encoding="utf-8-sig", newline="")
					chunk_iterator = pd.read_csv(text_stream, chunksize=chunk_size, low_memory=False)
					for chunk_index, chunk_df in enumerate(chunk_iterator, start=1):
						chunk_row_count = len(chunk_df)
						logging.info("Chunk %d rows: %d", chunk_index, chunk_row_count)
						total_rows += chunk_row_count
						chunk_count = chunk_index
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except KeyError as exc:
		raise RuntimeError(
			f"Expected CSV member was not found in the archive: {member_name}"
		) from exc

	if chunk_count == 0:
		raise RuntimeError(f"Chunked CSV read produced no data rows: {member_name}")

	return chunk_count, total_rows


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	cfg = get_config(argv)
	outer_archive_path = get_outer_archive_path(cfg)

	logging.info("Storage mode: %s", cfg.storage_mode)
	logging.info("Opening outer archive: %s", outer_archive_path)
	try:
		hd_handler_members = enumerate_archive_members(outer_archive_path)
	except Exception as exc:
		logging.error("Slice 1 failed while enumerating archive members: %s", exc)
		return 1

	logging.info("Found %d HD_HANDLER members in outer archive", len(hd_handler_members))
	for member_name in hd_handler_members:
		logging.info("Archive member: %s", member_name)

	first_member_name = hd_handler_members[0]
	logging.info("Inspecting first CSV member: %s", first_member_name)
	try:
		header_row = read_member_csv_header(
			outer_archive_path,
			first_member_name,
		)
		required_columns_found = validate_required_sieve_columns(header_row, first_member_name)
	except Exception as exc:
		logging.error("Slice 3 failed while reading and validating the first CSV header: %s", exc)
		return 1

	logging.info("First CSV member is readable and has %d header columns", len(header_row))
	logging.info("Header preview: %s", header_row[:5])
	logging.info("Required sieve columns found: %s", required_columns_found)

	logging.info("Chunk-reading first CSV member with chunk size: %d", cfg.chunk_size)
	try:
		chunk_count, total_rows = chunk_read_member_csv(
			outer_archive_path,
			first_member_name,
			cfg.chunk_size,
		)
	except Exception as exc:
		logging.error("Slice 4 failed while chunk-reading the first CSV member: %s", exc)
		return 1

	logging.info("Chunked read complete. Chunks processed: %d", chunk_count)
	logging.info("Chunked read complete. Total data rows read: %d", total_rows)

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
