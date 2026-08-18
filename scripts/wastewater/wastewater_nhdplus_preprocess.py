"""
Preprocess regional NHDPlus V2.1 flowlines for the EJScreen wastewater
indicator.

This script:

1. Reads NHDFlowline geometry from NHDSnapshot.gdb.
2. Reads PlusFlowlineVAA.dbf.
3. Joins NHDFlowline.Permanent_Identifier to PlusFlowlineVAA.ComID.
4. Runs join QA checks.
5. Writes a regional GeoParquet file.

Supported VPUs are defined centrally in nhdplus_config.py.

Run from the repository's scripts directory:

    python wastewater/wastewater_nhdplus_preprocess.py --vpu 01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from nhdplus_config import VPU_CONFIG

import geopandas as gpd
import pandas as pd
import pyogrio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read and join regional NHDPlus flowline geometry and "
            "PlusFlowlineVAA attributes."
        )
    )

    parser.add_argument(
        "--vpu",
        required=True,
        choices=sorted(VPU_CONFIG),
        help="NHDPlus Vector Processing Unit, such as 01.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing regional GeoParquet output.",
    )

    return parser.parse_args()


def normalize_join_id(
    values: pd.Series,
    field_name: str,
) -> pd.Series:
    """
    Convert an identifier field to nullable 64-bit integers.

    Invalid, blank, or nonnumeric values become missing values.
    """

    normalized = pd.to_numeric(values, errors="coerce").astype("Int64")

    invalid_count = int(normalized.isna().sum())

    if invalid_count:
        print(
            f"Warning: {field_name} contains "
            f"{invalid_count:,} missing or invalid values."
        )

    return normalized


def find_column(
    columns: pd.Index,
    requested_name: str,
) -> str:
    """Find a column using a case-insensitive comparison."""

    matches = [
        column
        for column in columns
        if column.lower() == requested_name.lower()
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one column matching {requested_name!r}, "
            f"but found {matches}."
        )

    return matches[0]


def main() -> int:
    args = parse_args()
    config = VPU_CONFIG[args.vpu]

    DEFAULT_VERSION=1 # temporarily hard coding
    DEFAULT_YEAR=2021 # temporarily hard coding

    # TODO: MOVE TO CONFIG
    vpu_input_directory = (
        Path("../pipeline/wastewater")
        / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
        / "downloads"
        / f"{DEFAULT_YEAR}"
        / f"vpu{args.vpu}"
    )

    geodatabase_path = (
        vpu_input_directory
        / config["snapshot_relative_path"]
    )

    vaa_path = (
        vpu_input_directory
        / config["vaa_relative_path"]
    )

    # TODO: MOVE TO CONFIG
    output_directory = (
        Path("../pipeline/wastewater")
        / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
        / "preprocessed_input"
        / "vpu"
    )

    output_path = (
        output_directory
        / f"nhdplus_vpu{args.vpu}_flowline_vaa.parquet"
    )

    qa_path = (
        output_directory
        / f"nhdplus_vpu{args.vpu}_flowline_vaa_qa.json"
    )

    if not geodatabase_path.exists():
        print(
            f"ERROR: NHDSnapshot geodatabase not found:\n"
            f"{geodatabase_path}",
            file=sys.stderr,
        )
        return 1

    if not vaa_path.exists():
        print(
            f"ERROR: PlusFlowlineVAA table not found:\n"
            f"{vaa_path}",
            file=sys.stderr,
        )
        return 1

    if output_path.exists() and not args.overwrite:
        print(
            f"ERROR: Output already exists:\n{output_path}\n\n"
            "Use --overwrite to replace it.",
            file=sys.stderr,
        )
        return 1

    print("Reading NHDFlowline geometry...")
    print(f"Source: {geodatabase_path}")

    flowlines = gpd.read_file(
        geodatabase_path,
        layer="NHDFlowline",
    )

    print(f"NHDFlowline rows: {len(flowlines):,}")
    print(f"NHDFlowline CRS: {flowlines.crs}")

    permanent_identifier_column = find_column(
        flowlines.columns,
        "Permanent_Identifier",
    )

    print("\nReading PlusFlowlineVAA...")
    print(f"Source: {vaa_path}")

    vaa = pyogrio.read_dataframe(
        vaa_path,
        read_geometry=False,
    )

    # Some NHDPlus regions include optional travel-time attributes that
    # are not present nationally. Remove them so every VPU has the same
    # output schema.

    vaa = vaa.drop(
        columns=["PathTime", "TravTime"],
        errors="ignore",
    )

    print(f"PlusFlowlineVAA rows: {len(vaa):,}")

    comid_column = find_column(
        vaa.columns,
        "ComID",
    )

    flowlines["COMID"] = normalize_join_id(
        flowlines[permanent_identifier_column],
        permanent_identifier_column,
    )

    vaa["COMID"] = normalize_join_id(
        vaa[comid_column],
        comid_column,
    )

    flowline_missing_comid = int(
        flowlines["COMID"].isna().sum()
    )

    vaa_missing_comid = int(
        vaa["COMID"].isna().sum()
    )

    flowline_duplicate_comid = int(
        flowlines["COMID"].dropna().duplicated().sum()
    )

    vaa_duplicate_comid = int(
        vaa["COMID"].dropna().duplicated().sum()
    )

    if vaa_duplicate_comid:
        raise RuntimeError(
            "PlusFlowlineVAA contains duplicate COMID values. "
            f"Duplicate row count: {vaa_duplicate_comid:,}"
        )

    # Avoid retaining two copies of the original VAA identifier.
    if comid_column != "COMID":
        vaa = vaa.drop(columns=[comid_column])

    print("\nJoining NHDFlowline to PlusFlowlineVAA...")

    joined = flowlines.merge(
        vaa,
        on="COMID",
        how="left",
        validate="m:1",
        indicator=True,
        suffixes=("", "_vaa"),
    )

    matched_rows = int((joined["_merge"] == "both").sum())
    geometry_only_rows = int(
        (joined["_merge"] == "left_only").sum()
    )

    vaa_comids = set(
        vaa["COMID"].dropna().astype(int)
    )

    flowline_comids = set(
        flowlines["COMID"].dropna().astype(int)
    )

    vaa_only_comids = vaa_comids - flowline_comids
    shared_comids = vaa_comids & flowline_comids

    joined = joined.drop(columns=["_merge"])

    qa_summary = {
        "vpu": args.vpu,
        "region": config["region"],
        "nhdflowline_rows": len(flowlines),
        "vaa_rows": len(vaa),
        "joined_rows": len(joined),
        "matched_rows": matched_rows,
        "geometry_only_rows": geometry_only_rows,
        "vaa_only_comids": len(vaa_only_comids),
        "shared_comids": len(shared_comids),
        "flowline_missing_comid": flowline_missing_comid,
        "vaa_missing_comid": vaa_missing_comid,
        "flowline_duplicate_comid_rows": flowline_duplicate_comid,
        "vaa_duplicate_comid_rows": vaa_duplicate_comid,
        "crs": str(joined.crs),
        "geometry_type_counts": {
            str(key): int(value)
            for key, value in joined.geom_type.value_counts(
                dropna=False
            ).items()
        },
    }

    print("\nJoin QA")
    print(f"Joined rows: {len(joined):,}")
    print(f"Matched rows: {matched_rows:,}")
    print(f"Geometry-only rows: {geometry_only_rows:,}")
    print(f"VAA-only COMIDs: {len(vaa_only_comids):,}")
    print(f"Shared COMIDs: {len(shared_comids):,}")
    print(
        "Flowline duplicate COMID rows: "
        f"{flowline_duplicate_comid:,}"
    )
    print(
        "VAA duplicate COMID rows: "
        f"{vaa_duplicate_comid:,}"
    )

    if len(joined) != len(flowlines):
        raise RuntimeError(
            "The left join changed the number of flowline rows. "
            f"Before: {len(flowlines):,}; after: {len(joined):,}."
        )

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(f"\nWriting GeoParquet:\n{output_path}")

    joined.to_parquet(
        output_path,
        index=False,
    )

    with qa_path.open("w", encoding="utf-8") as qa_file:
        json.dump(
            qa_summary,
            qa_file,
            indent=2,
        )

    print(f"Writing QA summary:\n{qa_path}")

    print("\nRegional NHDPlus preprocessing completed.")
    print(f"Output rows: {len(joined):,}")
    print(f"Output columns: {len(joined.columns):,}")
    print(f"Output CRS: {joined.crs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
