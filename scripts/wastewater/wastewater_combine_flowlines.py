"""Combine positive-concentration modeled wastewater flowlines nationally.

This script discovers modeled VPU GeoParquet files, retains flowlines with
positive offsite_toxconc values, validates COMID uniqueness, and writes a
national positive-flowline GeoParquet for state-level proximity processing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from nhdplus_config import SUPPORTED_VPUS


WASTEWATER_DIR = Path(__file__).resolve().parent
DEFAULT_VERSION=1
DEFAULT_YEAR=2021
DEFAULT_INPUT_DIR = (
    Path("../pipeline/wastewater")
    / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
    / "preprocessed_input"
    / "modeled_flowlines"
)

COMID_COLUMN = "COMID"
CONCENTRATION_COLUMN = "offsite_toxconc"
TARGET_CRS = "EPSG:4269"


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine positive-concentration modeled wastewater flowlines "
            "from all configured CONUS VPUs."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Modeled VPU input directory (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2021,
        help="Modeled wastewater flowline year. Default: 2021.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "National GeoParquet output path. "
            "If omitted, a year-specific filename is created automatically."
        ),
    )
    parser.add_argument(
        "--qa-output",
        type=Path,
        default=None,
        help=(
            "QA JSON output path. "
            "If omitted, a year-specific filename is created automatically."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Allow creation from only the modeled VPU files currently "
            "available. Without this option, all configured VPUs are required."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing outputs.",
    )
    return parser.parse_args()


def resolve_vpu_path(
    input_dir: Path,
    vpu: str,
    year: int,
) -> Path:
    return (
        input_dir
        / f"wastewater_flowlines_vpu{vpu}_{year}.parquet"
    )


def normalize_comid(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")

    if numeric.isna().any():
        bad_count = int(numeric.isna().sum())
        raise RuntimeError(
            f"{bad_count:,} modeled flowlines have invalid or missing COMIDs."
        )

    return numeric.astype("int64")


def main() -> int:
    args = get_args()

    if args.output is None:
        args.output = (
            args.input_dir
            / f"wastewater_flowlines_conus_{args.year}_positive.parquet"
        )

    if args.qa_output is None:
        args.qa_output = (
            args.input_dir
            / f"wastewater_flowlines_conus_{args.year}_positive_qa.json"
        )

    if args.output.exists() and not args.overwrite:
        raise FileExistsError(
            f"Output already exists: {args.output}\n"
            "Use --overwrite to replace it."
        )

    if args.qa_output.exists() and not args.overwrite:
        raise FileExistsError(
            f"QA output already exists: {args.qa_output}\n"
            "Use --overwrite to replace it."
        )

    available_paths: list[tuple[str, Path]] = []
    missing_vpus: list[str] = []

    for vpu in SUPPORTED_VPUS:
        path = resolve_vpu_path(
            args.input_dir,
            vpu,
            args.year,
        )
        if path.exists():
            available_paths.append((vpu, path))
        else:
            missing_vpus.append(vpu)

    if not available_paths:
        raise FileNotFoundError(
            f"No modeled VPU GeoParquet files found in {args.input_dir}"
        )

    if missing_vpus and not args.allow_partial:
        missing_text = ", ".join(missing_vpus)
        raise RuntimeError(
            "Modeled flowline files are missing for these VPUs: "
            f"{missing_text}\n"
            "Run the regional pipeline first, or use --allow-partial for testing."
        )

    print(f"Configured VPUs: {len(SUPPORTED_VPUS):,}")
    print(f"Available modeled VPUs: {len(available_paths):,}")

    if missing_vpus:
        print(f"Missing modeled VPUs: {', '.join(missing_vpus)}")

    positive_frames: list[gpd.GeoDataFrame] = []
    vpu_summaries: list[dict[str, object]] = []

    expected_columns: list[str] | None = None

    for vpu, path in available_paths:
        print()
        print(f"Reading VPU {vpu}:")
        print(path)

        flowlines = gpd.read_parquet(path)

        if flowlines.crs is None:
            raise RuntimeError(f"VPU {vpu} has no CRS: {path}")

        required = {
            COMID_COLUMN,
            CONCENTRATION_COLUMN,
            flowlines.geometry.name,
        }
        missing_columns = sorted(required.difference(flowlines.columns))
        if missing_columns:
            raise RuntimeError(
                f"VPU {vpu} is missing columns: "
                f"{', '.join(missing_columns)}"
            )

        if flowlines.crs.to_string() != TARGET_CRS:
            flowlines = flowlines.to_crs(TARGET_CRS)

        flowlines[COMID_COLUMN] = normalize_comid(
            flowlines[COMID_COLUMN]
        )

        concentration = pd.to_numeric(
            flowlines[CONCENTRATION_COLUMN],
            errors="coerce",
        )

        positive = flowlines.loc[concentration > 0].copy()

        positive[CONCENTRATION_COLUMN] = concentration.loc[
            concentration > 0
        ].astype("float64")

        # The wastewater indicator follows the historical offsite-only
        # RSEI Water Geographic Microdata methodology. Preserve the
        # downstream proximity interface by storing offsite concentration
        # in total_toxconc in the combined indicator input.
        positive["total_toxconc"] = positive[
            CONCENTRATION_COLUMN
        ].astype("float64")

        positive["source_vpu"] = vpu

        current_columns = list(positive.columns)
        if expected_columns is None:
            expected_columns = current_columns
        elif current_columns != expected_columns:
            raise RuntimeError(
                f"VPU {vpu} columns do not match the first modeled VPU."
            )

        print(f"Modeled rows: {len(flowlines):,}")
        print(f"Positive-concentration rows: {len(positive):,}")

        vpu_summaries.append(
            {
                "vpu": vpu,
                "input_path": str(path),
                "modeled_rows": int(len(flowlines)),
                "positive_rows": int(len(positive)),
            }
        )

        positive_frames.append(positive)

    print()
    print("Combining positive-concentration flowlines...")

    combined = gpd.GeoDataFrame(
        pd.concat(positive_frames, ignore_index=True),
        geometry=positive_frames[0].geometry.name,
        crs=TARGET_CRS,
    )

    duplicate_mask = combined.duplicated(
        subset=[COMID_COLUMN],
        keep=False,
    )
    duplicate_rows = int(duplicate_mask.sum())
    duplicate_comids = int(
        combined.loc[duplicate_mask, COMID_COLUMN].nunique()
    )

    if duplicate_rows:
        duplicate_examples = (
            combined.loc[
                duplicate_mask,
                [COMID_COLUMN, "source_vpu"],
            ]
            .sort_values([COMID_COLUMN, "source_vpu"])
            .head(20)
        )

        raise RuntimeError(
            "Duplicate COMIDs were found across modeled VPUs.\n"
            f"Duplicate rows: {duplicate_rows:,}\n"
            f"Duplicate COMIDs: {duplicate_comids:,}\n"
            f"Examples:\n{duplicate_examples.to_string(index=False)}"
        )

    combined = combined.sort_values(
        [COMID_COLUMN],
        kind="stable",
    ).reset_index(drop=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.qa_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Writing national GeoParquet:\n{args.output}")
    combined.to_parquet(args.output, index=False)

    qa_summary = {
        "year": int(args.year),
        "configured_vpus": list(SUPPORTED_VPUS),
        "available_vpus": [vpu for vpu, _ in available_paths],
        "missing_vpus": missing_vpus,
        "partial_output": bool(missing_vpus),
        "vpu_count": int(len(available_paths)),
        "output_rows": int(len(combined)),
        "output_columns": int(len(combined.columns)),
        "output_crs": combined.crs.to_string(),
        "duplicate_comid_rows": duplicate_rows,
        "duplicate_comids": duplicate_comids,
        "concentration_column": CONCENTRATION_COLUMN,
        "vpu_summaries": vpu_summaries,
        "output_path": str(args.output),
    }

    print(f"Writing QA summary:\n{args.qa_output}")
    args.qa_output.write_text(
        json.dumps(qa_summary, indent=2) + "\n",
        encoding="utf-8",
    )

    print()
    print("National positive-flowline combination completed.")
    print(f"Rows: {len(combined):,}")
    print(f"Columns: {len(combined.columns):,}")
    print(f"CRS: {combined.crs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
