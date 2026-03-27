"""
hazardous_waste_preprocess.py

Purpose:
	Prove the new hazardous-waste preprocessing access path by enumerating the
	configured nested ZIP structure inside the outer hazardous-waste archive.

Current slice:
	- Loads all filename conventions from a config file.
	- Accepts only the outermost archive filename as a runtime filename override.
	- Opens configured archive sources through the local filesystem or fsspec.
	- Enumerates nested ZIP members inside `HD.zip` and standalone CSV members inside
	  `BR_REPORTING_2023.zip`.

Sample command lines:
	- Local storage, all other options default:
	  python3 ./scripts/hazardous_waste/hazardous_waste_preprocess.py local
	- Remote storage, all other options default:
	  python3 ./scripts/hazardous_waste/hazardous_waste_preprocess.py remote

This slice intentionally stops before schema validation, filtering, joins,
or audit outputs.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import argparse
import fnmatch
import importlib
import io
import json
import logging
import zipfile


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE_PATH = SCRIPT_DIR / "hazardous_waste_preprocess_config.json"


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
		description="Enumerate the configured nested hazardous-waste ZIP structure."
	)
	parser.add_argument(
		"storage_mode",
		choices=("local", "remote"),
		help="Select whether the script reads through the local root path or the remote S3 root path.",
	)
	parser.add_argument(
		"--outer-archive-filename",
		dest="outer_archive_filename",
		default=default_outer_archive_filename,
		help=(
			"Override only the outermost archive filename. "
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


def inventory_nested_sources(cfg: Config, outer_archive_path: str) -> None:
	with open_outer_zip_archive(outer_archive_path) as default_outer_archive:
		default_outer_member_names = list_archive_members(default_outer_archive)
		logging.info("Default outer archive member count: %d", len(default_outer_member_names))
		for outer_member_name in default_outer_member_names:
			logging.info("Default outer archive member: %s", outer_member_name)

		for source in cfg.active_nested_sources:
			logging.info("------------------------------------------------------------")
			logging.info("Inventorying source: %s", source.logical_name)
			logging.info("Source description: %s", source.description)
			if source.target_biennial_year is not None:
				logging.info("Configured biennial year for %s: %s", source.logical_name, source.target_biennial_year)

			source_outer_archive_path = get_source_outer_archive_path(cfg, source)
			logging.info("Source outer archive path for %s: %s", source.logical_name, source_outer_archive_path)

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
					for matched_csv_member in matched_csv_members:
						logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)
					continue

			if not source.inner_zip_member_filename:
				raise RuntimeError(
					f"Source inside the default outer archive is missing inner_zip_member_filename: {source.logical_name}"
				)

			inner_zip_member_name = require_member_present(
				member_names=source_outer_member_names,
				expected_member_name=source.inner_zip_member_filename,
				archive_label=f"outer archive {source_outer_archive_path}",
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
				for matched_csv_member in matched_csv_members:
					logging.info("Matched CSV member for %s: %s", source.logical_name, matched_csv_member)

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
		outer_archive_path = get_outer_archive_path(cfg)

		logging.info("Config file: %s", CONFIG_FILE_PATH)
		logging.info("Storage mode: %s", cfg.storage_mode)
		logging.info("Active root path: %s", get_active_root_path(cfg))
		logging.info("Downloads relative path: %s", cfg.downloads_relative_path)
		logging.info("Outer archive filename: %s", cfg.outer_archive_filename)
		logging.info("Outer archive path: %s", outer_archive_path)

		inventory_nested_sources(cfg, outer_archive_path)
	except Exception as exc:
		logging.error("Hazardous waste preprocessing proof slice failed: %s", exc)
		return 1

	logging.info("Hazardous waste preprocessing proof slice completed successfully")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
