"""
hazardous_waste_preprocess.py

Slice 1 purpose:
  Open the outer hazardous-waste archive and enumerate the HD_HANDLER members
  inside it. This first slice is intentionally narrow so local archive access can
  be tested before any filtering or output logic is introduced.
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


@dataclass
class Config:
	storage_mode: str
	local_root_path: str = "./pipeline/test_data/"
	remote_root_path: str = "s3://pedp-data-preserved/ejscreen-data-processing/hazardous_waste/pipeline/"
	input_relative_path: str = "downloads/HD_HANDLER_20260315.zip"
	output_relative_path: str = "outputs/hazardous_waste_filtered.csv"


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


def inspect_first_member_csv(outer_archive_path: str, member_name: str) -> tuple[int, list[str]]:
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

					preview_fields = header_row[:5]
					return len(header_row), preview_fields
	except FileNotFoundError as exc:
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}") from exc
	except KeyError as exc:
		raise RuntimeError(
			f"Expected CSV member was not found in the archive: {member_name}"
		) from exc


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
		header_count, preview_fields = inspect_first_member_csv(
			outer_archive_path,
			first_member_name,
		)
	except Exception as exc:
		logging.error("Slice 2 failed while inspecting the first CSV member: %s", exc)
		return 1

	logging.info("First CSV member is readable and has %d header columns", header_count)
	logging.info("Header preview: %s", preview_fields)

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
