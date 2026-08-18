"""
Calculate wastewater discharge proximity scores for Census blocks and
aggregate the results to Census block groups.

The script:

1. Reads modeled wastewater flowlines for one NHDPlus VPU or from a supplied combined flowline dataset.
2. Reads Census block population weights for one state.
3. Finds modeled flowlines within 10 kilometres of each Census block centroid.
4. Calculates an inverse-distance wastewater contribution:

       contribution = total_toxconc * min(10, 1 / distance_km)

   Distances smaller than 0.1 kilometres are treated as 0.1 kilometres.
5. Sums flowline contributions for each Census block.
6. Applies the Census block population fraction.
7. Aggregates weighted scores to Census block groups.

Run from the repository's scripts directory:

    python wastewater/wastewater_proximity.py \
        --state RI \
        --vpu 01 \
        --year 2021

Alternatively, supply a combined modeled-flowline GeoParquet:

    python wastewater/wastewater_proximity.py \
        --state RI \
        --flowlines path/to/wastewater_flowlines_conus_2021_positive.parquet \
        --year 2021
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

from nhdplus_config import SUPPORTED_VPUS

DEFAULT_STATE = "RI"
DEFAULT_YEAR = 2021
DEFAULT_SEARCH_DISTANCE_METERS = 10_000.0
DEFAULT_MINIMUM_DISTANCE_KM = 0.1
DEFAULT_MAXIMUM_INVERSE_DISTANCE_WEIGHT = 10.0

MODELED_FLOWLINE_REQUIRED_COLUMNS = [
    "COMID",
    "total_toxconc",
    "geometry",
]

CENSUS_BLOCK_REQUIRED_COLUMNS = [
    "GEOID20",
    "INTPTLAT20",
    "INTPTLON20",
    "POP20",
    "block_group_geoid",
    "block_group_pop",
    "fraction_of_total",
]

TARGETED_BLOCKS_FILENAME = "targeted_blocks.csv"
BLOCK_FLOWLINE_DISTANCES_FILENAME = "block_flowline_distances.csv"
FINAL_BG_SCORES_FILENAME = "final_bg_scores.csv"
QA_FILENAME = "wastewater_proximity_qa.json"

WASTEWATER_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = WASTEWATER_DIR.parent

# TODO: Move to config/arguments
DEFAULT_VERSION = 1
DEFAULT_YEAR = 2021
MODELED_FLOWLINES_DIR = (
    Path("../pipeline/wastewater")
    / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
    / "preprocessed_input"
    / "modeled_flowlines"
)

FINAL_SCORE_COLUMN = "wastewater_score"

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(message)s"
)

def normalize_state_code(state_code: str) -> str:
    """Return a validated two-letter uppercase state abbreviation."""

    normalized = state_code.strip().upper()

    if len(normalized) != 2 or not normalized.isalpha():
        raise argparse.ArgumentTypeError(
            "State must be a two-letter postal abbreviation, such as RI."
        )

    return normalized


def positive_float(value: str) -> float:
    """Parse a command-line value as a positive floating-point number."""

    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Expected a number, received {value!r}."
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            f"Expected a positive number, received {value!r}."
        )

    return parsed

def configure_logging() -> None:
    """Configure console logging for the proximity pipeline."""

    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the proximity calculation."""

    parser = argparse.ArgumentParser(
        description=(
            "Calculate inverse-distance wastewater discharge scores for "
            "Census blocks and aggregate them to Census block groups."
        )
    )

    parser.add_argument(
        "--state",
        type=normalize_state_code,
        default=DEFAULT_STATE,
        help=(
            "Two-letter state postal abbreviation. "
            f"Default: {DEFAULT_STATE}."
        ),
    )

    flowline_source = parser.add_mutually_exclusive_group(required=True)

    flowline_source.add_argument(
        "--vpu",
        choices=SUPPORTED_VPUS,
        help=(
            "NHDPlus Vector Processing Unit. Uses the standard modeled "
            "flowline file for that VPU."
        ),
    )

    flowline_source.add_argument(
        "--flowlines",
        type=Path,
        help=(
            "Path to a modeled wastewater flowline GeoParquet. "
            "Use this for the combined CONUS positive-flowline dataset."
        ),
    )

    parser.add_argument(
        "--year",
        type=int,
        default=DEFAULT_YEAR,
        help=(
            "Water Geographic Microdata year used to create the modeled "
            f"flowlines. Default: {DEFAULT_YEAR}."
        ),
    )

    parser.add_argument(
        "--search-distance-meters",
        type=positive_float,
        default=DEFAULT_SEARCH_DISTANCE_METERS,
        help=(
            "Maximum block-to-flowline search distance in metres. "
            f"Default: {DEFAULT_SEARCH_DISTANCE_METERS:.0f}."
        ),
    )

    parser.add_argument(
        "--census-blocks-path",
        type=Path,
        default=None,
        help=(
            "Optional explicit path to the state Census block weights CSV. "
            "When omitted, the standard shared pipeline path is used."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Optional explicit output directory. When omitted, results are "
            "written under wastewater/pipeline/output/indicators/{state}."
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing proximity output files.",
    )

    return parser.parse_args()

def require_columns(
    dataframe: pd.DataFrame,
    required_columns: list[str],
    dataset_name: str,
) -> None:
    """Raise an error if a required column is missing."""

    missing = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing:
        raise RuntimeError(
            f"{dataset_name} is missing required columns: {missing}"
        )


def get_modeled_flowline_path(
    args: argparse.Namespace,
) -> Path:
    """Return the modeled wastewater flowline GeoParquet path."""

    if args.flowlines is not None:
        modeled_flowlines_path = args.flowlines.expanduser().resolve()
    else:
        modeled_flowlines_path = (
            MODELED_FLOWLINES_DIR
            / f"wastewater_flowlines_vpu{args.vpu}_{args.year}.parquet"
        ).resolve()

    if not modeled_flowlines_path.exists():
        raise FileNotFoundError(
            f"Modeled wastewater flowline file not found: "
            f"{modeled_flowlines_path}"
        )

    print("Modeled flowline input:")
    print(modeled_flowlines_path)

    return modeled_flowlines_path


def get_census_block_path(
    args: argparse.Namespace,
) -> Path:
    """Return the shared Census block weights CSV."""

    if args.census_blocks_path is not None:
        return args.census_blocks_path

    return (
        Path("../pipeline")
        / "shared"
        / "census_block_weights"
        / "1.0" # hard coded
        / "preprocessed_input"
        / f"census_block_weights_2020_w2022pops.csv" # Not sure how to handle states here v1.0 of census block weights does entire US see shared_config.json
    )


def get_output_directory(
    args: argparse.Namespace,
) -> Path:
    """Return the output directory for this state."""

    if args.output_dir is not None:
        return args.output_dir

    return (
        Path("../pipeline/wastewater")
        / f"v{DEFAULT_VERSION}.{DEFAULT_YEAR}"
        / "score_output"
        / args.state
    )


def read_modeled_flowlines(
    path: Path,
) -> gpd.GeoDataFrame:
    """Read modeled wastewater flowlines."""

    if not path.exists():
        raise FileNotFoundError(path)

    flowlines = gpd.read_parquet(path)

    require_columns(
        flowlines,
        MODELED_FLOWLINE_REQUIRED_COLUMNS,
        "Modeled wastewater flowlines",
    )

    flowlines["total_toxconc"] = pd.to_numeric(
        flowlines["total_toxconc"],
        errors="coerce",
    ).fillna(0.0)

    return flowlines


def read_census_blocks(
    path: Path,
) -> gpd.GeoDataFrame:
    """Read Census block weights and convert to a GeoDataFrame."""

    if not path.exists():
        raise FileNotFoundError(path)

    blocks = pd.read_csv(
        path,
        dtype={
            "GEOID20": str,
            "block_group_geoid": str,
        },
    )

    require_columns(
        blocks,
        CENSUS_BLOCK_REQUIRED_COLUMNS,
        "Census block weights",
    )

    blocks["block_lat"] = pd.to_numeric(
        blocks["INTPTLAT20"],
        errors="raise",
    )

    blocks["block_lon"] = pd.to_numeric(
        blocks["INTPTLON20"],
        errors="raise",
    )

    blocks["fraction_of_total"] = pd.to_numeric(
        blocks["fraction_of_total"],
        errors="raise",
    )

    gdf = gpd.GeoDataFrame(
        blocks.rename(
            columns={
                "GEOID20": "block_geoid",
            }
        ),
        geometry=gpd.points_from_xy(
            blocks["block_lon"],
            blocks["block_lat"],
        ),
        crs="EPSG:4269",
    )

    return gdf


def project_to_equal_area(
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Project geometries into CONUS Albers Equal Area.

    Distance calculations should never be performed in latitude/longitude.
    """

    return gdf.to_crs("EPSG:5070")


def ensure_output_directory(
    output_directory: Path,
) -> None:
    """Create the output directory if necessary."""

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

def identify_targeted_blocks(
    flowlines: gpd.GeoDataFrame,
    blocks: gpd.GeoDataFrame,
    search_distance_meters: float,
) -> pd.DataFrame:
    """
    Identify Census blocks that lie within the search distance
    of at least one modeled wastewater flowline.
    """

    if flowlines.empty:
        return pd.DataFrame(columns=["block_geoid"])

    if blocks.empty:
        return pd.DataFrame(columns=["block_geoid"])

    flowlines = flowlines.copy()

    #
    # Buffer every modeled flowline.
    #
    flowlines["buffer_geometry"] = flowlines.geometry.buffer(
        search_distance_meters
    )

    buffered = flowlines.set_geometry(
        "buffer_geometry"
    )

    #
    # Spatial join.
    #
    joined = gpd.sjoin(
        blocks,
        buffered,
        predicate="intersects",
        how="inner",
    )

    if joined.empty:
        return pd.DataFrame(columns=["block_geoid"])

    targeted = (
        joined[
            ["block_geoid"]
        ]
        .drop_duplicates()
        .sort_values("block_geoid")
        .reset_index(drop=True)
    )

    return targeted

def calculate_block_flowline_scores(
    targeted_blocks: pd.DataFrame,
    blocks: gpd.GeoDataFrame,
    flowlines: gpd.GeoDataFrame,
    search_distance_meters: float,
) -> pd.DataFrame:
    """
    Calculate wastewater contributions for every block-flowline pair
    within the search distance.

    A spatial index first identifies flowlines whose bounding boxes
    intersect the block's search envelope. Exact geometry distances are
    then calculated only for those candidates.

    Returns one record per qualifying block-flowline pair.
    """

    output_columns = [
        "block_geoid",
        "COMID",
        "distance_m",
        "distance_km",
        "weight",
        "total_toxconc",
        "score",
    ]

    if targeted_blocks.empty or blocks.empty or flowlines.empty:
        return pd.DataFrame(columns=output_columns)

    if blocks.crs is None or flowlines.crs is None:
        raise RuntimeError(
            "Blocks and flowlines must have defined projected CRSs."
        )

    if blocks.crs != flowlines.crs:
        raise RuntimeError(
            "Blocks and flowlines must use the same projected CRS. "
            f"Blocks: {blocks.crs}; flowlines: {flowlines.crs}."
        )

    targeted_block_ids = set(
        targeted_blocks["block_geoid"]
        .astype(str)
        .str.strip()
    )

    targeted_block_gdf = blocks[
        blocks["block_geoid"]
        .astype(str)
        .str.strip()
        .isin(targeted_block_ids)
    ].copy()

    if targeted_block_gdf.empty:
        return pd.DataFrame(columns=output_columns)

    flowline_spatial_index = flowlines.sindex
    records: list[dict[str, object]] = []

    for _, block in targeted_block_gdf.iterrows():
        block_geometry = block.geometry

        if block_geometry is None or block_geometry.is_empty:
            continue

        search_geometry = block_geometry.buffer(
            search_distance_meters
        )

        candidate_positions = list(
            flowline_spatial_index.query(
                search_geometry,
                predicate="intersects",
            )
        )

        if not candidate_positions:
            continue

        candidate_flowlines = flowlines.iloc[
            candidate_positions
        ]

        for _, flowline in candidate_flowlines.iterrows():
            flowline_geometry = flowline.geometry

            if (
                flowline_geometry is None
                or flowline_geometry.is_empty
            ):
                continue

            distance_m = float(
                block_geometry.distance(
                    flowline_geometry
                )
            )

            if distance_m > search_distance_meters:
                continue

            distance_km = max(
                distance_m / 1000.0,
                DEFAULT_MINIMUM_DISTANCE_KM,
            )

            weight = min(
                DEFAULT_MAXIMUM_INVERSE_DISTANCE_WEIGHT,
                1.0 / distance_km,
            )

            total_toxconc = float(
                flowline["total_toxconc"]
            )

            score = weight * total_toxconc

            records.append(
                {
                    "block_geoid": str(
                        block["block_geoid"]
                    ).strip(),
                    "COMID": flowline["COMID"],
                    "distance_m": distance_m,
                    "distance_km": distance_km,
                    "weight": weight,
                    "total_toxconc": total_toxconc,
                    "score": score,
                }
            )

    if not records:
        return pd.DataFrame(columns=output_columns)

    result = pd.DataFrame.from_records(
        records,
        columns=output_columns,
    )

    return (
        result
        .sort_values(
            ["block_geoid", "COMID"],
            kind="stable",
        )
        .reset_index(drop=True)
    )

def aggregate_to_block_groups(
    block_flowline_scores: pd.DataFrame,
    blocks: gpd.GeoDataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Sum flowline contributions for each Census block, apply the block
    population fraction, and aggregate the weighted values to block groups.

    Returns:
        block_scores:
            One row per Census block with its unweighted and
            population-weighted wastewater score.

        block_group_scores:
            One row per Census block group with the final wastewater score.
    """

    block_attributes = (
        blocks[
            [
                "block_geoid",
                "block_group_geoid",
                "fraction_of_total",
            ]
        ]
        .drop_duplicates(subset=["block_geoid"])
        .copy()
    )

    block_attributes["fraction_of_total"] = pd.to_numeric(
        block_attributes["fraction_of_total"],
        errors="coerce",
    ).fillna(0.0)

    if block_flowline_scores.empty:
        block_scores = block_attributes.copy()
        block_scores["block_score"] = 0.0
        block_scores["weighted_block_score"] = 0.0
    else:
        block_totals = (
            block_flowline_scores
            .groupby(
                "block_geoid",
                as_index=False,
            )["score"]
            .sum()
            .rename(
                columns={
                    "score": "block_score",
                }
            )
        )

        block_scores = block_attributes.merge(
            block_totals,
            on="block_geoid",
            how="left",
            validate="one_to_one",
        )

        block_scores["block_score"] = (
            pd.to_numeric(
                block_scores["block_score"],
                errors="coerce",
            )
            .fillna(0.0)
        )

        block_scores["weighted_block_score"] = (
            block_scores["block_score"]
            * block_scores["fraction_of_total"]
        )

    block_group_scores = (
        block_scores
        .groupby(
            "block_group_geoid",
            as_index=False,
        )["weighted_block_score"]
        .sum()
        .rename(
            columns={
                "weighted_block_score": FINAL_SCORE_COLUMN,
            }
        )
        .sort_values("block_group_geoid")
        .reset_index(drop=True)
    )

    block_scores = (
        block_scores
        .sort_values("block_geoid")
        .reset_index(drop=True)
    )

    return block_scores, block_group_scores

def write_outputs(
    output_directory: Path,
    targeted_blocks: pd.DataFrame,
    block_flowline_scores: pd.DataFrame,
    block_scores: pd.DataFrame,
    block_group_scores: pd.DataFrame,
) -> None:
    """
    Write all wastewater proximity outputs.
    """

    targeted_path = (
        output_directory
        / TARGETED_BLOCKS_FILENAME
    )

    distances_path = (
        output_directory
        / BLOCK_FLOWLINE_DISTANCES_FILENAME
    )

    final_scores_path = (
        output_directory
        / FINAL_BG_SCORES_FILENAME
    )

    qa_path = (
        output_directory
        / QA_FILENAME
    )

    targeted_blocks.to_csv(
        targeted_path,
        index=False,
    )

    block_flowline_scores.to_csv(
        distances_path,
        index=False,
    )

    block_group_scores.to_csv(
        final_scores_path,
        index=False,
    )

    qa = {
        "targeted_blocks": int(len(targeted_blocks)),
        "block_flowline_pairs": int(len(block_flowline_scores)),
        "blocks": int(len(block_scores)),
        "block_groups": int(len(block_group_scores)),
        "maximum_score": (
            float(block_group_scores[FINAL_SCORE_COLUMN].max())
            if len(block_group_scores)
            else 0.0
        ),
        "minimum_score": (
            float(block_group_scores[FINAL_SCORE_COLUMN].min())
            if len(block_group_scores)
            else 0.0
        ),
        "sum_score": (
            float(block_group_scores[FINAL_SCORE_COLUMN].sum())
            if len(block_group_scores)
            else 0.0
        ),
    }

    with qa_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            qa,
            f,
            indent=4,
        )

    print("\nFinished.")

    print(f"\nTargeted blocks:\n{targeted_path}")

    print(f"\nBlock-flowline scores:\n{distances_path}")

    print(f"\nFinal block-group scores:\n{final_scores_path}")

    print(f"\nQA summary:\n{qa_path}")

def main() -> int:
    """Run the wastewater proximity calculation."""

    configure_logging()
    args = parse_args()

    modeled_flowline_path = get_modeled_flowline_path(args)
    census_block_path = get_census_block_path(args)
    output_directory = get_output_directory(args)

    output_paths = [
        output_directory / TARGETED_BLOCKS_FILENAME,
        output_directory / BLOCK_FLOWLINE_DISTANCES_FILENAME,
        output_directory / FINAL_BG_SCORES_FILENAME,
        output_directory / QA_FILENAME,
    ]

    existing_outputs = [
        path
        for path in output_paths
        if path.exists()
    ]

    if existing_outputs and not args.overwrite:
        print(
            "ERROR: One or more output files already exist:",
            file=sys.stderr,
        )

        for path in existing_outputs:
            print(f"  {path}", file=sys.stderr)

        print(
            "\nUse --overwrite to replace them.",
            file=sys.stderr,
        )

        return 1

    logging.info(
        "Reading modeled wastewater flowlines: %s",
        modeled_flowline_path,
    )

    flowlines = read_modeled_flowlines(
        modeled_flowline_path
    )

    logging.info(
        "Loaded %s modeled flowlines.",
        f"{len(flowlines):,}",
    )

    #
    # Flowlines with zero concentration cannot contribute to the indicator.
    # Removing them substantially reduces the spatial workload.
    #
    flowlines = flowlines[
        flowlines["total_toxconc"] > 0
    ].copy()

    logging.info(
        "Positive-concentration flowlines: %s",
        f"{len(flowlines):,}",
    )

    logging.info(
        "Reading Census block weights: %s",
        census_block_path,
    )

    blocks = read_census_blocks(
        census_block_path
    )

    logging.info(
        "Loaded %s Census blocks.",
        f"{len(blocks):,}",
    )

    if flowlines.crs is None:
        raise RuntimeError(
            "Modeled wastewater flowlines do not have a defined CRS."
        )

    if blocks.crs is None:
        raise RuntimeError(
            "Census block points do not have a defined CRS."
        )

    logging.info("Projecting spatial inputs to EPSG:5070.")

    projected_flowlines = project_to_equal_area(
        flowlines
    )

    projected_blocks = project_to_equal_area(
        blocks
    )

    logging.info(
        "Identifying Census blocks within the search distance."
    )

    targeted_blocks = identify_targeted_blocks(
        flowlines=projected_flowlines,
        blocks=projected_blocks,
        search_distance_meters=args.search_distance_meters,
    )

    logging.info(
        "Targeted Census blocks: %s",
        f"{len(targeted_blocks):,}",
    )

    logging.info(
        "Calculating block-to-flowline distances "
        "and wastewater contributions."
    )

    block_flowline_scores = calculate_block_flowline_scores(
        targeted_blocks=targeted_blocks,
        blocks=projected_blocks,
        flowlines=projected_flowlines,
        search_distance_meters=args.search_distance_meters,
    )

    logging.info(
        "Block-flowline pairs: %s",
        f"{len(block_flowline_scores):,}",
    )

    logging.info(
        "Aggregating Census block scores to Census block groups."
    )

    block_scores, block_group_scores = (
        aggregate_to_block_groups(
            block_flowline_scores=block_flowline_scores,
            blocks=projected_blocks,
        )
    )

    logging.info(
        "Final Census block groups: %s",
        f"{len(block_group_scores):,}",
    )

    ensure_output_directory(
        output_directory
    )

    write_outputs(
        output_directory=output_directory,
        targeted_blocks=targeted_blocks,
        block_flowline_scores=block_flowline_scores,
        block_scores=block_scores,
        block_group_scores=block_group_scores,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
