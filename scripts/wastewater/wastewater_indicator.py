"""Run the wastewater discharge indicator-generation workflow.

This script coordinates the final wastewater indicator-generation components.

Process summary:
- Select one CONUS state or all configured CONUS states.
- Build or reuse the combined national positive-concentration flowline dataset.
- Run the wastewater proximity calculation for each selected state.
- Verify that each state produces the expected output files.

The Water Geographic Microdata, NHDPlus, and regional modeled-flowline
preprocessing steps must be completed before running this script.

Runtime arguments:
- storage_mode
    Required. Either local or remote.
- --state
    Optional two-letter state code. If omitted, process all CONUS states.
- --year
    Optional wastewater microdata year. Default: 2021.
- --flowlines
    Optional path to an existing combined national positive-flowline
    GeoParquet. When supplied, the flowline-combination step is skipped.
- --output-root
    Optional root directory for state-level indicator outputs.
- --overwrite
    Replace existing pipeline outputs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

WASTEWATER_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = WASTEWATER_DIR.parent

MODELED_FLOWLINES_DIR = (
    WASTEWATER_DIR
    / "pipeline"
    / "preprocessed_input"
    / "modeled_flowlines"
)

COMBINE_SCRIPT = WASTEWATER_DIR / "wastewater_combine_flowlines.py"
PROXIMITY_SCRIPT = WASTEWATER_DIR / "wastewater_proximity.py"

OUTPUT_ROOT = (
    WASTEWATER_DIR
    / "pipeline"
    / "output"
    / "indicators"
)

STATE_OUTPUT_FILENAMES = (
    "targeted_blocks.csv",
    "block_flowline_distances.csv",
    "final_bg_scores.csv",
    "wastewater_proximity_qa.json",
)

STORAGE_MODES = ("local", "remote")


if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.state_config import (  # noqa: E402
    StateConfig,
    get_state_config,
    get_state_config_list,
)


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration for the wastewater indicator pipeline."""

    storage_mode: str
    state: str | None
    year: int
    flowlines: Path | None
    output_root: Path
    overwrite: bool


def normalize_state_code(state_code: str) -> str:
    """Return a validated uppercase two-letter state code."""

    normalized = state_code.strip().upper()

    if len(normalized) != 2 or not normalized.isalpha():
        raise argparse.ArgumentTypeError(
            "State must be a two-letter postal abbreviation, such as RI."
        )

    return normalized


def parse_args(argv: list[str] | None = None) -> Config:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the wastewater discharge indicator pipeline for one "
            "CONUS state or all configured CONUS states."
        )
    )

    parser.add_argument(
        "storage_mode",
        choices=STORAGE_MODES,
        help=(
            "Select local or remote storage mode. "
            "Only local mode is currently implemented."
        ),
    )

    parser.add_argument(
        "--state",
        type=normalize_state_code,
        help=(
            "Optional two-letter state postal abbreviation. "
            "If omitted, all CONUS states are processed."
        ),
    )

    parser.add_argument(
        "--year",
        type=int,
        default=2021,
        help="Wastewater microdata year. Default: 2021.",
    )

    parser.add_argument(
        "--flowlines",
        type=Path,
        default=None,
        help=(
            "Optional combined national positive-flowline GeoParquet. "
            "When supplied, the flowline-combination step is skipped."
        ),
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT,
        help=(
            "Root directory for state indicator outputs. "
            f"Default: {OUTPUT_ROOT}"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing pipeline outputs.",
    )

    args = parser.parse_args(argv)

    return Config(
        storage_mode=args.storage_mode,
        state=args.state,
        year=args.year,
        flowlines=args.flowlines,
        output_root=args.output_root,
        overwrite=args.overwrite,
    )

def get_combined_flowlines_path(year: int) -> Path:
    """Return the default combined national flowline path for a year."""

    return (
        MODELED_FLOWLINES_DIR
        / f"wastewater_flowlines_conus_{year}_positive.parquet"
    )


def load_state_targets(selected_state: str | None) -> list[StateConfig]:
    """Return the selected state or all configured CONUS states."""

    if selected_state is not None:
        state_config = get_state_config(selected_state)

        conus_codes = {
            state.postal
            for state in get_state_config_list("CONUS")
        }

        if state_config.postal not in conus_codes:
            raise RuntimeError(
                f"State {state_config.postal} is not part of the "
                "configured CONUS wastewater extent."
            )

        return [state_config]

    return get_state_config_list("CONUS")


def run_command(command: list[str]) -> None:
    """Run one pipeline command and stop if it fails."""

    print()
    print("Running:")
    print(" ".join(str(value) for value in command))
    print()

    subprocess.run(
        command,
        check=True,
        cwd=SCRIPTS_DIR,
    )


def run_combine_flowlines(config: Config) -> None:
    """Build the combined national positive-flowline dataset."""

    if config.flowlines is not None:
        if not config.flowlines.exists():
            raise FileNotFoundError(
                f"Custom flowline input not found: {config.flowlines}"
            )

        print()
        print("Using supplied combined national flowlines.")
        print("Skipping flowline combination.")
        return

    combined_flowlines_path = get_combined_flowlines_path(config.year)

    if combined_flowlines_path.exists() and not config.overwrite:
        print()
        print("Combined national flowlines already exist.")
        print("Skipping flowline combination.")
        return

    command = [
        sys.executable,
        str(COMBINE_SCRIPT),
        "--year",
        str(config.year),
    ]

    if config.overwrite:
        command.append("--overwrite")

    run_command(command)


def run_state_proximity(
    config: Config,
    state_config: StateConfig,
) -> None:
    """Run the wastewater proximity workflow for one state."""

    state_output_dir = config.output_root / state_config.postal

    expected_outputs = [
        state_output_dir / filename
        for filename in STATE_OUTPUT_FILENAMES
    ]

    all_outputs_exist = all(
        path.exists()
        for path in expected_outputs
    )

    if all_outputs_exist and not config.overwrite:
        print(
            f"All proximity outputs already exist for "
            f"{state_config.postal}. Skipping processing."
        )
        return

    flowline_path = (
        config.flowlines
        if config.flowlines is not None
        else get_combined_flowlines_path(config.year)
    )

    command = [
        sys.executable,
        str(PROXIMITY_SCRIPT),
        "--state",
        state_config.postal,
        "--year",
        str(config.year),
        "--flowlines",
        str(flowline_path),
        "--output-dir",
        str(state_output_dir),
    ]

    if config.overwrite:
        command.append("--overwrite")

    run_command(command)


def verify_state_output(
    config: Config,
    state_config: StateConfig,
) -> Path:
    """Verify that all expected state outputs exist."""

    state_output_dir = config.output_root / state_config.postal

    expected_outputs = [
    state_output_dir / filename
    for filename in STATE_OUTPUT_FILENAMES
    ]

    missing_outputs = [
        path
        for path in expected_outputs
        if not path.exists()
    ]

    if missing_outputs:
        formatted_paths = "\n".join(
            f"  {path}"
            for path in missing_outputs
        )

        raise FileNotFoundError(
            f"Missing wastewater outputs for {state_config.postal}:\n"
            f"{formatted_paths}"
        )

    final_output_path = state_output_dir / "final_bg_scores.csv"

    print(
        f"Verified all expected outputs for "
        f"{state_config.postal}."
    )

    return final_output_path


def main(argv: list[str] | None = None) -> int:
    """Run the wastewater indicator pipeline."""

    config = parse_args(argv)

    if config.storage_mode != "local":
        raise NotImplementedError(
            "Only local mode is currently implemented."
        )

    state_targets = load_state_targets(config.state)

    print("Wastewater indicator configuration")
    print(f"Storage mode: {config.storage_mode}")
    print(f"Overwrite: {config.overwrite}")
    print(
        "States: "
        + ", ".join(state.postal for state in state_targets)
    )

    run_combine_flowlines(config)

    for state_config in state_targets:
        print()
        print("=" * 60)
        print(f"Processing {state_config.postal}: {state_config.name}")
        print("=" * 60)

        run_state_proximity(config, state_config)
        verify_state_output(
            config,
            state_config,
        )

    print()
    print("Wastewater proximity processing completed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())