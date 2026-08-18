#!/usr/bin/env python3
"""
Preprocess EPA RSEI Water Geographic Microdata.

The workflow streams the all-years onsite and offsite CSV files directly
from their ZIP archives, filters to a target reporting year, removes invalid
NHDPlus COMIDs, aggregates modeled concentration metrics by COMID, combines
onsite and offsite results, and writes a compact Parquet dataset.

Primary output:
    pipeline/wastewater/v1.2021/preprocessed_input/
    water_microdata_<year>_by_comid.parquet

QA output:
    pipeline/wastewater/v1.2021/preprocessed_input/
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

import pandas as pd


WASTEWATER_DIR = Path(__file__).resolve().parent
print(WASTEWATER_DIR)
PIPELINE_DIR = Path("../pipeline") # Assumes running from "scripts"

DEFAULT_YEAR = 2021
DEFAULT_VERSION = 1

DEFAULT_ZIP_DIR = (
    PIPELINE_DIR
    / "wastewater"
    / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
    / "downloads"
    / f"{DEFAULT_YEAR}"
)

DEFAULT_OUTPUT_DIR = (
    PIPELINE_DIR
    / "wastewater"
    / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
    / "preprocessed_input"
)

DEFAULT_ONSITE_ZIP = (
    DEFAULT_ZIP_DIR
    / "NHDMicroResults_conc_aggonsite.zip" # Note that in 2022 Onsite is capitalized
)

DEFAULT_OFFSITE_ZIP = (
    DEFAULT_ZIP_DIR
    / "NHDMicroResults_conc_aggoffsite.zip" # Note that in 2022 Offsite is capitalized
)

ONSITE_MEMBER = "NHDMicroResults_conc_aggonsite.csv" # Note that in 2022 Onsite is capitalized
OFFSITE_MEMBER = "NHDMicroResults_conc_aggoffsite.csv" # Note that in 2022 Offsite is capitalized

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

LOG_PATH = WASTEWATER_DIR / "wastewater_microdata_preprocess.log"


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


def configure_logging() -> None:
    """Configure terminal and file logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(
                LOG_PATH,
                mode="a",
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
        force=True,
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


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate EPA RSEI Water Geographic "
            "Microdata by NHDPlus COMID."
        )
    )

    parser.add_argument(
        "--year",
        type=int,
        default=DEFAULT_YEAR,
        help=(
            "Target reporting year. "
            f"Default: {DEFAULT_YEAR}."
        ),
    )

    parser.add_argument(
        "--onsite-zip",
        type=Path,
        default=DEFAULT_ONSITE_ZIP,
        help="Path to the onsite microdata ZIP.",
    )

    parser.add_argument(
        "--offsite-zip",
        type=Path,
        default=DEFAULT_OFFSITE_ZIP,
        help="Path to the offsite microdata ZIP.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for Parquet and QA outputs.",
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

    return parser.parse_args()


def main() -> int:
    """Run the complete microdata preprocessing workflow."""
    configure_logging()

    try:
        args = parse_args()

        if args.year <= 0:
            raise ValueError(
                "--year must be a positive integer"
            )

        if args.chunk_size <= 0:
            raise ValueError(
                "--chunk-size must be a positive integer"
            )

        onsite_zip = (
            args.onsite_zip
            .expanduser()
            .resolve()
        )

        offsite_zip = (
            args.offsite_zip
            .expanduser()
            .resolve()
        )

        output_dir = (
            args.output_dir
            .expanduser()
            .resolve()
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_path = (
            output_dir
            / (
                "water_microdata_"
                f"{args.year}_by_comid.parquet"
            )
        )

        qa_path = (
            output_dir
            / f"water_microdata_{args.year}_qa.json"
        )

        onsite, onsite_stats = process_archive(
            zip_path=onsite_zip,
            member_name=ONSITE_MEMBER,
            source_name="onsite",
            target_year=args.year,
            chunk_size=args.chunk_size,
        )

        offsite, offsite_stats = process_archive(
            zip_path=offsite_zip,
            member_name=OFFSITE_MEMBER,
            source_name="offsite",
            target_year=args.year,
            chunk_size=args.chunk_size,
        )

        combined = combine_sources(
            onsite=onsite,
            offsite=offsite,
            target_year=args.year,
        )

        if combined.empty:
            raise ValueError(
                "No valid Water Geographic Microdata "
                f"records were found for {args.year}."
            )

        duplicate_comids = int(
            combined["comid"].duplicated().sum()
        )

        if duplicate_comids:
            raise ValueError(
                "Combined output contains "
                f"{duplicate_comids:,} duplicate COMIDs."
            )

        combined.to_parquet(
            output_path,
            index=False,
            engine="pyarrow",
            compression="snappy",
        )

        qa = build_combined_qa(
            combined=combined,
            target_year=args.year,
            onsite_stats=onsite_stats,
            offsite_stats=offsite_stats,
        )

        with qa_path.open(
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
            output_path=output_path,
            qa_path=qa_path,
        )

        logging.info(
            "Microdata preprocessing completed successfully"
        )
        logging.info(
            "Parquet output: %s",
            output_path,
        )
        logging.info(
            "QA output: %s",
            qa_path,
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
