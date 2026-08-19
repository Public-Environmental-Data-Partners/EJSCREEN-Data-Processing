#!/usr/bin/env python3
"""
Preprocess EPA RSEI Water Geographic Microdata.

The workflow streams the all-years onsite and offsite CSV files directly
from their ZIP archives, filters to a target reporting year, removes invalid
NHDPlus COMIDs, aggregates modeled concentration metrics by COMID, combines
onsite and offsite results, and writes a compact Parquet dataset.

Primary output:
    pipeline/wastewater/<version>/preprocessed_input/
    water_microdata_<year>_by_comid.parquet

QA output:
    pipeline/wastewater/<version>/preprocessed_input/
    water_microdata_<year>_qa.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any
import subprocess
from dataclasses import dataclass
import pandas as pd
import importlib
from datetime import datetime

# All of our project-specific imports must be relative to the 
# `scripts` folder which we assume is at the first level of the
# repository. 
# NB: ***If `scripts` moves, this code will have to change.***
# Walk up our current working directory tree until you find the
# repository root, then add the scripts directory to sys.path
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".git").exists()), None)
if REPO_ROOT is None:
	# This is a running-from-docker or other non-git environment cry for help.
    # Undone: Handle non-git environments more gracefully when needed.
    raise RuntimeError("Architectural Error: Repository root anchor (.git) could not be found!")
SCRIPTS_ROOT = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
import shared.build_manifest as build_manifest
import shared.resolve_path as resolve_path

WASTEWATER_DIR = Path(__file__).resolve().parent

REQUIRED_COLUMNS = (
    "ReleaseNumber",
    "ComID",
    "ToxConc",
    "NCTW",
    "CTW",
    "Year",
)

METRIC_COLUMNS = (
    "ToxConc",
    "NCTW",
    "CTW",
)

DEFAULT_CHUNK_SIZE = 500_000

DEFAULT_LOG_FILENAME =  "wastewater_microdata_preprocess.log"

def configure_logging() -> str:
	log_path = WASTEWATER_DIR / DEFAULT_LOG_FILENAME
	log_path.parent.mkdir(parents=True, exist_ok=True)
	logging.basicConfig(
		level=logging.INFO,
		format='%(asctime)s %(levelname)s: %(message)s',
		datefmt='%Y-%m-%d %H:%M:%S',
		handlers=[logging.FileHandler(log_path, mode='a', encoding='utf-8')],
		force=True,
	)
	logging.info('========== Log session started %s ==========', datetime.now().astimezone().isoformat(timespec='seconds'))
	return str(log_path)

class _UnzipMemberStream:
    """Stream one archive member through the system unzip utility."""

    def __init__(
        self,
        zip_path: Path,
        member_name: str,
    ) -> None:
        self.zip_path = zip_path
        self.member_name = member_name
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self):
        self.process = subprocess.Popen(
            [
                "unzip",
                "-p",
                str(self.zip_path),
                self.member_name,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if self.process.stdout is None:
            raise RuntimeError(
                f"Could not open ZIP output stream: {self.zip_path}"
            )

        return self.process.stdout

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> bool:
        if self.process is None:
            return False

        if self.process.stdout is not None:
            self.process.stdout.close()

        stderr_text = ""

        if self.process.stderr is not None:
            stderr_text = self.process.stderr.read().decode(
                "utf-8",
                errors="replace",
            )
            self.process.stderr.close()

        return_code = self.process.wait()

        # A consumer may intentionally stop reading before the end of the
        # archive member, such as when pandas uses nrows for a sample.
        # In that case unzip receives SIGPIPE and exits with return code -13.
        expected_sigpipe = return_code == -13

        if (
            exc_type is None
            and return_code != 0
            and not expected_sigpipe
        ):
            raise RuntimeError(
                "System unzip failed while reading "
                f"{self.member_name} from {self.zip_path}. "
                f"Exit code: {return_code}. "
                f"Details: {stderr_text.strip()}"
            )

        return False


class ZipFile:
    """
    Compatibility wrapper using the system unzip command.

    The EPA archives use a compression method unsupported by Python's
    built-in zipfile decompressor, while the installed unzip utility can
    successfully read them.
    """

    def __init__(self, zip_path: Path) -> None:
        self.zip_path = Path(zip_path)

    def __enter__(self) -> "ZipFile":
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> bool:
        return False

    def namelist(self) -> list[str]:
        result = subprocess.run(
            [
                "unzip",
                "-Z1",
                str(self.zip_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Could not inspect ZIP archive {self.zip_path}. "
                f"Details: {result.stderr.strip()}"
            )

        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def open(
        self,
        member_name: str,
    ) -> _UnzipMemberStream:
        return _UnzipMemberStream(
            zip_path=self.zip_path,
            member_name=member_name,
        )

def validate_archive(
    zip_path: Path,
    member_name: str,
) -> None:
    """Confirm that the ZIP archive and expected CSV member exist."""
    if not zip_path.exists():
        raise FileNotFoundError(
            f"ZIP archive not found: {zip_path}"
        )

    with ZipFile(zip_path) as archive:
        members = archive.namelist()

    if member_name not in members:
        raise FileNotFoundError(
            f"{member_name} was not found inside {zip_path}. "
            f"Archive members: {members}"
        )


def add_grouped_chunk(
    accumulator: pd.DataFrame | None,
    grouped: pd.DataFrame,
) -> pd.DataFrame:
    """
    Add one grouped chunk to the cumulative COMID-level results.

    Both frames use COMID as their index. DataFrame.add aligns COMIDs and
    avoids retaining all original microdata rows in memory.
    """
    if accumulator is None:
        return grouped.copy()

    return accumulator.add(
        grouped,
        fill_value=0,
    )


def process_archive(
    zip_path: Path,
    member_name: str,
    source_name: str,
    target_year: int,
    chunk_size: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Stream, filter, and aggregate one Water Geographic Microdata archive.
    """
    validate_archive(
        zip_path=zip_path,
        member_name=member_name,
    )

    logging.info(
        "Processing %s archive: %s",
        source_name,
        zip_path,
    )
    logging.info(
        "%s target year: %d",
        source_name,
        target_year,
    )
    logging.info(
        "%s chunk size: %d",
        source_name,
        chunk_size,
    )

    stats: dict[str, Any] = {
        "source": source_name,
        "zip_path": str(zip_path),
        "member_name": member_name,
        "target_year": target_year,
        "rows_scanned": 0,
        "target_year_rows": 0,
        "valid_target_year_rows": 0,
        "invalid_comid_rows": 0,
        "missing_year_rows": 0,
        "missing_toxconc_rows": 0,
        "missing_nctw_rows": 0,
        "missing_ctw_rows": 0,
        "chunks_processed": 0,
    }

    accumulator: pd.DataFrame | None = None

    with ZipFile(zip_path) as archive:
        with archive.open(member_name) as stream:
            reader = pd.read_csv(
                stream,
                usecols=list(REQUIRED_COLUMNS),
                dtype={
                    "ReleaseNumber": "string",
                    "ComID": "string",
                    "Year": "string",
                },
                chunksize=chunk_size,
                low_memory=False,
            )

            for chunk_number, chunk in enumerate(
                reader,
                start=1,
            ):
                stats["chunks_processed"] = chunk_number
                stats["rows_scanned"] += len(chunk)
                year_numeric = pd.to_numeric(
                    chunk["Year"],
                    errors="coerce",
                )

                comid_numeric = pd.to_numeric(
                    chunk["ComID"],
                    errors="coerce",
                )

                stats["missing_year_rows"] += int(
                    year_numeric.isna().sum()
                )

                target_year_mask = year_numeric.eq(
                    target_year
                )

                stats["target_year_rows"] += int(
                    target_year_mask.sum()
                )

                invalid_comid_mask = (
                    target_year_mask
                    & (
                        comid_numeric.isna()
                        | comid_numeric.le(0)
                    )
                )

                stats["invalid_comid_rows"] += int(
                    invalid_comid_mask.sum()
                )

                valid_mask = (
                    target_year_mask
                    & comid_numeric.notna()
                    & comid_numeric.gt(0)
                )

                selected = chunk.loc[
                    valid_mask,
                    [
                        "ComID",
                        "ToxConc",
                        "NCTW",
                        "CTW",
                    ],
                ].copy()

                stats["valid_target_year_rows"] += len(
                    selected
                )

                if selected.empty:
                    if (
                        chunk_number == 1
                        or chunk_number % 10 == 0
                    ):
                        logging.info(
                            "%s progress: chunks=%d, "
                            "rows_scanned=%d, valid_rows=%d",
                            source_name,
                            chunk_number,
                            stats["rows_scanned"],
                            stats["valid_target_year_rows"],
                        )
                    continue

                selected["comid"] = (
                    comid_numeric.loc[valid_mask]
                    .astype("int64")
                    .to_numpy()
                )

                for metric in METRIC_COLUMNS:
                    metric_numeric = pd.to_numeric(
                        selected[metric],
                        errors="coerce",
                    )

                    stats[
                        f"missing_{metric.lower()}_rows"
                    ] += int(
                        metric_numeric.isna().sum()
                    )

                    selected[metric] = (
                        metric_numeric.fillna(0.0)
                    )

                selected["record_count"] = 1

                grouped = (
                    selected.groupby(
                        "comid",
                        sort=False,
                    )[
                        [
                            "ToxConc",
                            "NCTW",
                            "CTW",
                            "record_count",
                        ]
                    ]
                    .sum()
                )

                accumulator = add_grouped_chunk(
                    accumulator=accumulator,
                    grouped=grouped,
                )

                if (
                    chunk_number == 1
                    or chunk_number % 10 == 0
                ):
                    unique_comids = (
                        len(accumulator)
                        if accumulator is not None
                        else 0
                    )

                    logging.info(
                        "%s progress: chunks=%d, "
                        "rows_scanned=%d, "
                        "target_year_rows=%d, "
                        "valid_rows=%d, "
                        "unique_comids=%d",
                        source_name,
                        chunk_number,
                        stats["rows_scanned"],
                        stats["target_year_rows"],
                        stats["valid_target_year_rows"],
                        unique_comids,
                    )

    if accumulator is None:
        result = pd.DataFrame(
            columns=[
                "comid",
                f"{source_name}_toxconc",
                f"{source_name}_nctw",
                f"{source_name}_ctw",
                f"{source_name}_record_count",
            ]
        )
    else:
        result = (
            accumulator.reset_index()
            .rename(
                columns={
                    "ToxConc": (
                        f"{source_name}_toxconc"
                    ),
                    "NCTW": f"{source_name}_nctw",
                    "CTW": f"{source_name}_ctw",
                    "record_count": (
                        f"{source_name}_record_count"
                    ),
                }
            )
        )

        result["comid"] = result["comid"].astype(
            "int64"
        )

        result[
            f"{source_name}_record_count"
        ] = (
            result[
                f"{source_name}_record_count"
            ]
            .round()
            .astype("int64")
        )

    stats["unique_valid_comids"] = len(result)

    for metric in (
        "toxconc",
        "nctw",
        "ctw",
    ):
        column = f"{source_name}_{metric}"

        stats[f"total_{metric}"] = (
            float(result[column].sum())
            if column in result.columns
            else 0.0
        )

    logging.info(
        "Finished %s: rows_scanned=%d, "
        "target_year_rows=%d, valid_rows=%d, "
        "invalid_comid_rows=%d, unique_comids=%d",
        source_name,
        stats["rows_scanned"],
        stats["target_year_rows"],
        stats["valid_target_year_rows"],
        stats["invalid_comid_rows"],
        stats["unique_valid_comids"],
    )

    return result, stats


def combine_sources(
    onsite: pd.DataFrame,
    offsite: pd.DataFrame,
    target_year: int,
) -> pd.DataFrame:
    """Combine onsite and offsite COMID-level aggregates."""
    combined = onsite.merge(
        offsite,
        on="comid",
        how="outer",
        validate="one_to_one",
    )

    numeric_columns = [
        "onsite_toxconc",
        "onsite_nctw",
        "onsite_ctw",
        "onsite_record_count",
        "offsite_toxconc",
        "offsite_nctw",
        "offsite_ctw",
        "offsite_record_count",
    ]

    for column in numeric_columns:
        if column not in combined.columns:
            combined[column] = 0

        combined[column] = pd.to_numeric(
            combined[column],
            errors="coerce",
        ).fillna(0)

    for column in (
        "onsite_record_count",
        "offsite_record_count",
    ):
        combined[column] = (
            combined[column]
            .round()
            .astype("int64")
        )

    combined["total_toxconc"] = (
        combined["onsite_toxconc"]
        + combined["offsite_toxconc"]
    )

    combined["total_nctw"] = (
        combined["onsite_nctw"]
        + combined["offsite_nctw"]
    )

    combined["total_ctw"] = (
        combined["onsite_ctw"]
        + combined["offsite_ctw"]
    )

    combined["total_record_count"] = (
        combined["onsite_record_count"]
        + combined["offsite_record_count"]
    )

    combined["year"] = target_year

    output_columns = [
        "comid",
        "year",
        "onsite_toxconc",
        "offsite_toxconc",
        "total_toxconc",
        "onsite_nctw",
        "offsite_nctw",
        "total_nctw",
        "onsite_ctw",
        "offsite_ctw",
        "total_ctw",
        "onsite_record_count",
        "offsite_record_count",
        "total_record_count",
    ]

    combined = (
        combined[output_columns]
        .sort_values("comid")
        .reset_index(drop=True)
    )

    combined["comid"] = combined["comid"].astype(
        "int64"
    )

    return combined


def build_combined_qa(
    combined: pd.DataFrame,
    target_year: int,
    onsite_stats: dict[str, Any],
    offsite_stats: dict[str, Any],
) -> dict[str, Any]:
    """Build QA statistics for the final COMID-level dataset."""
    onsite_only = (
        combined["onsite_record_count"].gt(0)
        & combined["offsite_record_count"].eq(0)
    )

    offsite_only = (
        combined["offsite_record_count"].gt(0)
        & combined["onsite_record_count"].eq(0)
    )

    both_sources = (
        combined["onsite_record_count"].gt(0)
        & combined["offsite_record_count"].gt(0)
    )

    return {
        "target_year": target_year,
        "onsite": onsite_stats,
        "offsite": offsite_stats,
        "combined": {
            "rows": len(combined),
            "unique_comids": int(
                combined["comid"].nunique()
            ),
            "duplicate_comids": int(
                combined["comid"].duplicated().sum()
            ),
            "onsite_only_comids": int(
                onsite_only.sum()
            ),
            "offsite_only_comids": int(
                offsite_only.sum()
            ),
            "both_source_comids": int(
                both_sources.sum()
            ),
            "zero_total_toxconc_rows": int(
                combined["total_toxconc"]
                .eq(0)
                .sum()
            ),
            "negative_total_toxconc_rows": int(
                combined["total_toxconc"]
                .lt(0)
                .sum()
            ),
            "total_onsite_toxconc": float(
                combined["onsite_toxconc"].sum()
            ),
            "total_offsite_toxconc": float(
                combined["offsite_toxconc"].sum()
            ),
            "total_combined_toxconc": float(
                combined["total_toxconc"].sum()
            ),
            "total_nctw": float(
                combined["total_nctw"].sum()
            ),
            "total_ctw": float(
                combined["total_ctw"].sum()
            ),
            "total_record_count": int(
                combined[
                    "total_record_count"
                ].sum()
            ),
        },
    }


def print_summary(
    qa: dict[str, Any],
    output_path: Path,
    qa_path: Path,
) -> None:
    """Print a concise terminal summary."""
    onsite = qa["onsite"]
    offsite = qa["offsite"]
    combined = qa["combined"]

    print("\nWater Geographic Microdata preprocessing")
    print("=========================================")
    print(f"Target year: {qa['target_year']}")

    print("\nOnsite")
    print("------")
    print(
        f"Rows scanned: "
        f"{onsite['rows_scanned']:,}"
    )
    print(
        f"Target-year rows: "
        f"{onsite['target_year_rows']:,}"
    )
    print(
        f"Valid target-year rows: "
        f"{onsite['valid_target_year_rows']:,}"
    )
    print(
        f"Invalid COMID rows: "
        f"{onsite['invalid_comid_rows']:,}"
    )
    print(
        f"Unique valid COMIDs: "
        f"{onsite['unique_valid_comids']:,}"
    )

    print("\nOffsite")
    print("-------")
    print(
        f"Rows scanned: "
        f"{offsite['rows_scanned']:,}"
    )
    print(
        f"Target-year rows: "
        f"{offsite['target_year_rows']:,}"
    )
    print(
        f"Valid target-year rows: "
        f"{offsite['valid_target_year_rows']:,}"
    )
    print(
        f"Invalid COMID rows: "
        f"{offsite['invalid_comid_rows']:,}"
    )
    print(
        f"Unique valid COMIDs: "
        f"{offsite['unique_valid_comids']:,}"
    )

    print("\nCombined")
    print("--------")
    print(
        f"COMID rows: "
        f"{combined['rows']:,}"
    )
    print(
        f"Duplicate COMIDs: "
        f"{combined['duplicate_comids']:,}"
    )
    print(
        f"Onsite-only COMIDs: "
        f"{combined['onsite_only_comids']:,}"
    )
    print(
        f"Offsite-only COMIDs: "
        f"{combined['offsite_only_comids']:,}"
    )
    print(
        f"COMIDs in both sources: "
        f"{combined['both_source_comids']:,}"
    )
    print(
        f"Total combined ToxConc: "
        f"{combined['total_combined_toxconc']:.12g}"
    )
    print(
        f"Negative ToxConc rows: "
        f"{combined['negative_total_toxconc_rows']:,}"
    )

    print("\nOutputs")
    print("-------")
    print(f"Parquet: {output_path}")
    print(f"QA JSON: {qa_path}")


@dataclass(frozen=True, slots=True)
class Config:
    storage_mode: str
    version: str
    local_root_path: str
    remote_root_path: str
    offsite_raw_download_relative_path: str
    onsite_raw_download_relative_path: str
    preprocessed_microdata_relative_path: str
    qa_relative_path: str
    chunk_size: int
    dry_run: bool = False

def parse_args(argv) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate EPA RSEI Water Geographic "
            "Microdata by NHDPlus COMID."
        )
    )

    parser.add_argument(
        "-v",
        "--version",
        type=str,
        default="1.2021",
        help=(
            "Target reporting year. "
            f"Default: 1.2021"
        ),
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            "CSV rows processed per chunk. "
            f"Default: {DEFAULT_CHUNK_SIZE:,}."
        ),
    )

    parser.add_argument(
        "-l",
        "--location",
        dest='storage_mode',
        type=str,
        default="local",
        help=(
            "Local or remote storage. "
            f"Default: local"
        ),
    )

    args = parser.parse_args(argv)
    
    # Build the preprocess manifest for this indicator/version. We expect the
    # preprocess stage to define an input `offsite` (indicator download) and
    # an output: the offsite/onsite combined parquet file.
    manifest = build_manifest.get_stage_manifest(
        target_type='indicator',
        name='wastewater',
        stage='preprocess',
        version=args.version,
        environment=args.storage_mode,
    )
    print(manifest)

    inputs = manifest.get('inputs', {})
    outputs = manifest.get('outputs', {})

    if 'offsite' not in inputs or 'onsite' not in inputs:
        raise RuntimeError('Preprocess manifest missing required inputs: offsite and onsite')
    if 'preprocessed_microdata' not in outputs:
        raise RuntimeError('Preprocess manifest missing required output: preprocessed_microdata')

    # Manifest entries may already include a compiled "root" and "relative".
    off_raw_entry = inputs['offsite']
    on_raw_entry = inputs["onsite"]
    preproc_entry = outputs['preprocessed_microdata']
    qa_entry = outputs['qa']

    off_raw_rel = off_raw_entry.get('relative')
    on_raw_rel = on_raw_entry.get('relative')
    preproc_rel = preproc_entry.get('relative')
    qa_rel = qa_entry.get('relative')

    if not off_raw_rel or not isinstance(off_raw_rel, str):
        raise RuntimeError('Invalid relative path for offsite in preprocess manifest')
    if not on_raw_rel or not isinstance(on_raw_rel, str):
            raise RuntimeError('Invalid relative path for onsite in preprocess manifest')
    if not preproc_rel or not isinstance(preproc_rel, str):
        raise RuntimeError('Invalid relative path for preprocessed_microdata in preprocess manifest')
    if not qa_rel or not isinstance(qa_rel, str):
            raise RuntimeError('Invalid relative path for qa in preprocess manifest')

    local_root = resolve_path.get_indicator_root('wastewater', args.version, 'local')
    remote_root = resolve_path.get_indicator_root('wastewater', args.version, 'remote')

    return Config(
        storage_mode=args.storage_mode,
        version=args.version,
        local_root_path=local_root,
        remote_root_path=remote_root,
        offsite_raw_download_relative_path=off_raw_rel,
        onsite_raw_download_relative_path=on_raw_rel,
        preprocessed_microdata_relative_path=preproc_rel,
        qa_relative_path = qa_rel,
        chunk_size=args.chunk_size,
        #dry_run=args.dry_run
    )

# PATH HELPER FUNCTIONS
def load_fsspec_module():
	return importlib.import_module('fsspec')

def is_s3_uri(path: str) -> bool:
	return isinstance(path, str) and path.lower().startswith('s3://')

def ensure_local_parent_dir(path: str) -> None:
	if not is_s3_uri(path):
		Path(path).parent.mkdir(parents=True, exist_ok=True)

def path_exists(path: str) -> bool:
	if is_s3_uri(path):
		fsspec = load_fsspec_module()
		return bool(fsspec.open(path).fs.exists(path))
	return Path(path).exists()

def get_active_root_path(cfg: Config) -> str:
	if cfg.storage_mode == 'local':
		return cfg.local_root_path
	if cfg.storage_mode == 'remote':
		return cfg.remote_root_path
	raise ValueError(f'Unsupported storage mode: {cfg.storage_mode}')

def join_root_and_relative_path(root_path: str, relative_path: str) -> str:
	if is_s3_uri(root_path):
		return root_path.rstrip('/') + '/' + relative_path.lstrip('/')
	return str(Path(root_path) / Path(relative_path))

def get_path(cfg: Config, rel_path: str) -> str:
	return join_root_and_relative_path(get_active_root_path(cfg), rel_path)

def write_s3_or_local(out_path: str) -> None:
    # Makes sure parent directory exists but does not write
    # Not finished...
	ensure_local_parent_dir(out_path)
	if is_s3_uri(out_path):
		fsspec = load_fsspec_module()
		#with fsspec.open(out_path, 'w', encoding='utf-8', newline='') as output_stream:
		#	df.to_csv(output_stream, index=False)
		#return
    #df.to_csv(out_path, index=False)

# MAIN
def main(argv=None) -> int:
    """Run the complete microdata preprocessing workflow."""
    log_path = configure_logging()
    logging.info('Logging to %s', log_path)

    try:
        cfg = parse_args(argv)
        print(cfg)
        if not cfg.version:
            raise ValueError(
                "--version must be provided"
            )
        
        year = int(cfg.version.split(".")[1]) # Assume version is something like 1.2022. This is brittle!
        print(year)

        if cfg.chunk_size <= 0:
            raise ValueError(
                "--chunk-size must be a positive integer"
            )

        # DEFINE PATHS
        offsite_zip = get_path(cfg, cfg.offsite_raw_download_relative_path)
        onsite_zip = get_path(cfg, cfg.onsite_raw_download_relative_path)
        md_output_path = get_path(cfg, cfg.preprocessed_microdata_relative_path)
        qa_output_path = get_path(cfg, cfg.qa_relative_path)
        ONSITE_MEMBER = {2021:"NHDMicroResults_conc_aggonsite.csv", 2022:"NHDMicroResults_conc_aggOnsite.csv"}
        OFFSITE_MEMBER = {2021:"NHDMicroResults_conc_aggoffsite.csv", 2022:"NHDMicroResults_conc_aggOffsite.csv"}

        onsite, onsite_stats = process_archive(
            zip_path=Path(onsite_zip),
            member_name=ONSITE_MEMBER[year],
            source_name="onsite",
            target_year=year,
            chunk_size=cfg.chunk_size,
        )

        offsite, offsite_stats = process_archive(
            zip_path=Path(offsite_zip),
            member_name=OFFSITE_MEMBER[year],
            source_name="offsite",
            target_year=year,
            chunk_size=cfg.chunk_size,
        )

        combined = combine_sources(
            onsite=onsite,
            offsite=offsite,
            target_year=year,
        )

        if combined.empty:
            raise ValueError(
                "No valid Water Geographic Microdata "
                f"records were found for {year}."
            )

        duplicate_comids = int(
            combined["comid"].duplicated().sum()
        )

        if duplicate_comids:
            raise ValueError(
                "Combined output contains "
                f"{duplicate_comids:,} duplicate COMIDs."
            )

        write_s3_or_local(md_output_path) 
        combined.to_parquet(
            md_output_path,
            index=False,
            engine="pyarrow",
            compression="snappy",
        )

        write_s3_or_local(qa_output_path)
        qa = build_combined_qa(
            combined=combined,
            target_year=year,
            onsite_stats=onsite_stats,
            offsite_stats=offsite_stats,
        )
        with Path(qa_output_path).open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                qa,
                handle,
                indent=2,
            )

        print_summary(
            qa=qa,
            output_path=md_output_path,
            qa_path=qa_output_path,
        )

        logging.info(
            "Microdata preprocessing completed successfully"
        )
        logging.info(
            "Parquet output: %s",
            md_output_path,
        )
        logging.info(
            "QA output: %s",
            qa_output_path,
        )

        return 0

    except Exception as exc:
        logging.exception(
            "Water microdata preprocessing failed"
        )
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
