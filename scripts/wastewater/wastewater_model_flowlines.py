"""
Create regional modeled wastewater flowlines by joining Water Geographic
Microdata to preprocessed NHDPlus flowlines.

This script:

1. Reads the regional NHDPlus flowline/VAA GeoParquet.
2. Reads the national Water Geographic Microdata by COMID.
3. Joins the datasets by COMID.
4. Preserves every regional flowline.
5. Zero-fills wastewater values for flowlines without microdata.
6. Writes a modeled regional GeoParquet and QA summary.

Supported VPUs are defined centrally in nhdplus_config.py.

Run from the repository's scripts directory:

    python wastewater/wastewater_model_flowlines.py --vpu 01 --year 2021
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from nhdplus_config import SUPPORTED_VPUS

import geopandas as gpd
import pandas as pd


MICRODATA_VALUE_COLUMNS = [
    "onsite_toxconc",
    "offsite_toxconc",
    "total_toxconc",
    "onsite_nctw",
    "offsite_nctw",
    "total_nctw",
    "onsite_ctw",
    "offsite_ctw",
    "total_ctw",
]

MICRODATA_COUNT_COLUMNS = [
    "onsite_record_count",
    "offsite_record_count",
    "total_record_count",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join Water Geographic Microdata to regional NHDPlus "
            "flowlines by COMID."
        )
    )

    parser.add_argument(
        "--vpu",
        required=True,
        choices=SUPPORTED_VPUS,
        help="NHDPlus Vector Processing Unit, such as 01.",
    )

    parser.add_argument(
        "--year",
        type=int,
        default=2021,
        help="Water Geographic Microdata year. Default: 2021.",
    )

    parser.add_argument(
        "--microdata-path",
        type=Path,
        default=None,
        help=(
            "Optional path to a Water Geographic Microdata Parquet. "
            "If omitted, uses the standard pipeline path for the "
            "selected year."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory for modeled-flowline and QA outputs. "
            "If omitted, uses the standard pipeline modeled-flowlines "
            "directory."
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing modeled-flowline outputs.",
    )

    return parser.parse_args()


def normalize_comid(
    values: pd.Series,
    field_name: str,
) -> pd.Series:
    """Convert a COMID field to nullable 64-bit integers."""

    normalized = pd.to_numeric(
        values,
        errors="coerce",
    ).astype("Int64")

    missing_count = int(normalized.isna().sum())

    if missing_count:
        print(
            f"Warning: {field_name} contains "
            f"{missing_count:,} missing or invalid COMIDs."
        )

    return normalized


def require_columns(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    dataset_name: str,
) -> None:
    """Raise an error when required columns are absent."""

    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        raise RuntimeError(
            f"{dataset_name} is missing required columns: "
            f"{missing_columns}"
        )


def finite_summary(
    series: pd.Series,
) -> dict[str, float | int | None]:
    """Return a JSON-safe summary for a numeric series."""

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    )

    valid = numeric.dropna()

    if valid.empty:
        return {
            "count": 0,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "sum": None,
        }

    return {
        "count": int(valid.count()),
        "minimum": float(valid.min()),
        "maximum": float(valid.max()),
        "mean": float(valid.mean()),
        "sum": float(valid.sum()),
    }


def main() -> int:
    args = parse_args()

    DEFAULT_VERSION = 1
    DEFAULT_YEAR=2021

    flowline_path = (
        Path("../pipeline/wastewater")
        / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
        / "preprocessed_input"
        / "vpu"
        / f"nhdplus_vpu{args.vpu}_flowline_vaa.parquet"
    )
    
    if args.microdata_path is None:
        microdata_path = (
            Path("../pipeline/wastewater")
            / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
            / "preprocessed_input"
            / f"water_microdata_{args.year}_by_comid.parquet"
        )
    else:
        microdata_path = args.microdata_path

    if args.output_dir is None:
        output_directory = (
            Path("../pipeline/wastewater")
            / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
            / "preprocessed_input"
            / "modeled_flowlines"
        )
    else:
        output_directory = args.output_dir

    output_path = (
        output_directory
        / f"wastewater_flowlines_vpu{args.vpu}_{args.year}.parquet"
    )

    qa_path = (
        output_directory
        / f"wastewater_flowlines_vpu{args.vpu}_{args.year}_qa.json"
    )

    if not flowline_path.exists():
        print(
            f"ERROR: Regional NHDPlus input not found:\n"
            f"{flowline_path}",
            file=sys.stderr,
        )
        return 1

    if not microdata_path.exists():
        print(
            f"ERROR: Water Geographic Microdata input not found:\n"
            f"{microdata_path}",
            file=sys.stderr,
        )
        return 1

    existing_outputs = [
        path
        for path in [output_path, qa_path]
        if path.exists()
    ]

    if existing_outputs and not args.overwrite:
        print(
            "ERROR: One or more outputs already exist:",
            file=sys.stderr,
        )

        for path in existing_outputs:
            print(f"  {path}", file=sys.stderr)

        print(
            "\nUse --overwrite to replace them.",
            file=sys.stderr,
        )
        return 1

    print("Reading regional NHDPlus flowlines...")
    print(f"Source: {flowline_path}")

    flowlines = gpd.read_parquet(flowline_path)

    print(f"Flowline rows: {len(flowlines):,}")
    print(f"Flowline columns: {len(flowlines.columns):,}")
    print(f"Flowline CRS: {flowlines.crs}")

    require_columns(
        flowlines,
        ["COMID", "geometry"],
        "Regional NHDPlus flowlines",
    )

    print("\nReading Water Geographic Microdata...")
    print(f"Source: {microdata_path}")

    microdata = pd.read_parquet(microdata_path)

    print(f"Microdata rows: {len(microdata):,}")
    print(f"Microdata columns: {len(microdata.columns):,}")

    required_microdata_columns = (
        ["comid", "year"]
        + MICRODATA_VALUE_COLUMNS
        + MICRODATA_COUNT_COLUMNS
    )

    require_columns(
        microdata,
        required_microdata_columns,
        "Water Geographic Microdata",
    )

    flowlines["COMID"] = normalize_comid(
        flowlines["COMID"],
        "flowlines.COMID",
    )

    microdata["COMID"] = normalize_comid(
        microdata["comid"],
        "microdata.comid",
    )

    flowline_missing_comids = int(
        flowlines["COMID"].isna().sum()
    )

    microdata_missing_comids = int(
        microdata["COMID"].isna().sum()
    )

    flowline_duplicate_comids = int(
        flowlines["COMID"].dropna().duplicated().sum()
    )

    microdata_duplicate_comids = int(
        microdata["COMID"].dropna().duplicated().sum()
    )

    if flowline_missing_comids:
        raise RuntimeError(
            "Regional flowlines contain missing or invalid COMIDs. "
            f"Count: {flowline_missing_comids:,}"
        )

    if microdata_missing_comids:
        raise RuntimeError(
            "Water Geographic Microdata contains missing or invalid "
            f"COMIDs. Count: {microdata_missing_comids:,}"
        )

    if flowline_duplicate_comids:
        raise RuntimeError(
            "Regional flowlines contain duplicate COMIDs. "
            f"Duplicate row count: {flowline_duplicate_comids:,}"
        )

    if microdata_duplicate_comids:
        raise RuntimeError(
            "Water Geographic Microdata contains duplicate COMIDs. "
            f"Duplicate row count: {microdata_duplicate_comids:,}"
        )

    available_years = sorted(
        int(year)
        for year in microdata["year"].dropna().unique()
    )

    if available_years != [args.year]:
        raise RuntimeError(
            "The microdata file contains unexpected years. "
            f"Expected only {args.year}; found {available_years}."
        )

    microdata = microdata.drop(
        columns=["comid"],
    )

    print("\nJoining microdata to regional flowlines...")

    joined = flowlines.merge(
        microdata,
        on="COMID",
        how="left",
        validate="1:1",
        indicator=True,
    )

    matched_mask = joined["_merge"] == "both"
    unmatched_mask = joined["_merge"] == "left_only"

    matched_rows = int(matched_mask.sum())
    unmatched_rows = int(unmatched_mask.sum())

    joined["has_water_microdata"] = matched_mask

    joined["year"] = joined["year"].fillna(
        args.year
    ).astype("int64")

    for column in MICRODATA_VALUE_COLUMNS:
        joined[column] = pd.to_numeric(
            joined[column],
            errors="coerce",
        ).fillna(0.0).astype("float64")

    for column in MICRODATA_COUNT_COLUMNS:
        joined[column] = pd.to_numeric(
            joined[column],
            errors="coerce",
        ).fillna(0).astype("int64")

    joined = joined.drop(columns=["_merge"])

    if len(joined) != len(flowlines):
        raise RuntimeError(
            "The join changed the number of regional flowline rows. "
            f"Before: {len(flowlines):,}; "
            f"after: {len(joined):,}."
        )

    if int(joined["has_water_microdata"].sum()) != matched_rows:
        raise RuntimeError(
            "The has_water_microdata field does not match the "
            "join result."
        )

    matched_zero_total_toxconc = int(
        (
            joined["has_water_microdata"]
            & joined["total_toxconc"].eq(0)
        ).sum()
    )

    matched_positive_total_toxconc = int(
        (
            joined["has_water_microdata"]
            & joined["total_toxconc"].gt(0)
        ).sum()
    )

    negative_total_toxconc = int(
        joined["total_toxconc"].lt(0).sum()
    )

    if negative_total_toxconc:
        raise RuntimeError(
            "Negative total_toxconc values were found after joining. "
            f"Count: {negative_total_toxconc:,}"
        )

    flowline_comid_set = set(
        flowlines["COMID"].astype("int64")
    )

    microdata_comid_set = set(
        microdata["COMID"].astype("int64")
    )

    shared_comids = (
        flowline_comid_set
        & microdata_comid_set
    )

    flowline_only_comids = (
        flowline_comid_set
        - microdata_comid_set
    )

    microdata_only_comids = (
        microdata_comid_set
        - flowline_comid_set
    )

    qa_summary = {
        "vpu": args.vpu,
        "year": args.year,
        "input_flowline_path": str(flowline_path),
        "input_microdata_path": str(microdata_path),
        "output_path": str(output_path),
        "flowline_rows": int(len(flowlines)),
        "microdata_rows_national": int(len(microdata)),
        "output_rows": int(len(joined)),
        "matched_flowline_rows": matched_rows,
        "unmatched_flowline_rows": unmatched_rows,
        "shared_comids": int(len(shared_comids)),
        "flowline_only_comids": int(
            len(flowline_only_comids)
        ),
        "microdata_only_comids": int(
            len(microdata_only_comids)
        ),
        "flowline_missing_comids": flowline_missing_comids,
        "microdata_missing_comids": microdata_missing_comids,
        "flowline_duplicate_comid_rows": (
            flowline_duplicate_comids
        ),
        "microdata_duplicate_comid_rows": (
            microdata_duplicate_comids
        ),
        "matched_positive_total_toxconc_rows": (
            matched_positive_total_toxconc
        ),
        "matched_zero_total_toxconc_rows": (
            matched_zero_total_toxconc
        ),
        "negative_total_toxconc_rows": (
            negative_total_toxconc
        ),
        "total_toxconc_summary_all_flowlines": (
            finite_summary(joined["total_toxconc"])
        ),
        "total_toxconc_summary_matched_flowlines": (
            finite_summary(
                joined.loc[
                    joined["has_water_microdata"],
                    "total_toxconc",
                ]
            )
        ),
        "total_record_count_all_flowlines": int(
            joined["total_record_count"].sum()
        ),
        "crs": str(joined.crs),
        "geometry_type_counts": {
            str(key): int(value)
            for key, value in joined.geom_type.value_counts(
                dropna=False
            ).items()
        },
    }

    print("\nJoin QA")
    print(f"Output rows: {len(joined):,}")
    print(f"Matched flowlines: {matched_rows:,}")
    print(f"Unmatched flowlines: {unmatched_rows:,}")
    print(
        "Matched flowlines with positive total_toxconc: "
        f"{matched_positive_total_toxconc:,}"
    )
    print(
        "Matched flowlines with zero total_toxconc: "
        f"{matched_zero_total_toxconc:,}"
    )
    print(
        "Microdata COMIDs outside VPU "
        f"{args.vpu}: {len(microdata_only_comids):,}"
    )
    print(
        "Total record count on matched VPU flowlines: "
        f"{joined['total_record_count'].sum():,}"
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(f"\nWriting modeled GeoParquet:\n{output_path}")

    joined.to_parquet(
        output_path,
        index=False,
    )

    print(f"Writing QA summary:\n{qa_path}")

    with qa_path.open(
        "w",
        encoding="utf-8",
    ) as qa_file:
        json.dump(
            qa_summary,
            qa_file,
            indent=2,
        )

    print("\nModeled regional flowlines completed.")
    print(f"Rows: {len(joined):,}")
    print(f"Columns: {len(joined.columns):,}")
    print(f"CRS: {joined.crs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
