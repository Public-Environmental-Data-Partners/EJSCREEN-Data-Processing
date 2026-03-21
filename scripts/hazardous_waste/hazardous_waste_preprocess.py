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
import io
import logging
import zipfile


DEFAULT_OUTER_ARCHIVE = (
	"./pipeline/test_data/downloads/HD_HANDLER_20260315.zip"
)


@dataclass
class Config:
	outer_archive_path: str = DEFAULT_OUTER_ARCHIVE


def get_config(argv=None) -> Config:
	parser = argparse.ArgumentParser(
		description="Enumerate HD_HANDLER members inside the outer hazardous-waste archive."
	)
	parser.add_argument(
		"--outer-archive-path",
		dest="outer_archive_path",
		default=Config.outer_archive_path,
		help=f"Path to the outer hazardous-waste ZIP archive (default: {Config.outer_archive_path})",
	)
	args = parser.parse_args(argv)
	return Config(outer_archive_path=args.outer_archive_path)


def enumerate_archive_members(outer_archive_path: str) -> list[str]:
	archive_path = Path(outer_archive_path)
	if not archive_path.exists():
		raise FileNotFoundError(f"Outer archive not found: {outer_archive_path}")
	if not archive_path.is_file():
		raise ValueError(f"Outer archive path is not a file: {outer_archive_path}")

	try:
		with zipfile.ZipFile(archive_path, "r") as archive:
			member_names = [name for name in archive.namelist() if name and not name.endswith("/")]
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
	archive_path = Path(outer_archive_path)
	try:
		with zipfile.ZipFile(archive_path, "r") as archive:
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
	except KeyError as exc:
		raise RuntimeError(
			f"Expected CSV member was not found in the archive: {member_name}"
		) from exc


def main(argv=None) -> int:
	logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
	cfg = get_config(argv)

	logging.info("Opening outer archive: %s", cfg.outer_archive_path)
	try:
		hd_handler_members = enumerate_archive_members(cfg.outer_archive_path)
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
			cfg.outer_archive_path,
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
