"""
Download and extract the regional NHDPlus V2.1 inputs required by the
EJScreen wastewater indicator.

NHDPlus inputs are configured by Vector Processing Unit (VPU) in
nhdplus_config.py.

Downloaded products:
    1. NHDSnapshotFGDB
       Contains the NHDFlowline geometry.

    2. NHDPlusAttributes
       Contains PlusFlowlineVAA and related routing tables.

Run from the repository's scripts directory:

    python wastewater/fetch_nhdplus.py --vpu 01
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from nhdplus_config import VPU_CONFIG

import py7zr


BUCKET_URL = "https://dmap-data-commons-ow.s3.amazonaws.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and extract regional NHDPlus inputs."
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
        help="Redownload archives and replace extracted directories.",
    )

    return parser.parse_args()


def download_file(
    url: str,
    destination: Path,
    overwrite: bool = False,
) -> None:
    """Download a file unless it already exists."""

    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not overwrite:
        print(f"Archive already exists, skipping: {destination}")
        return

    temporary_path = destination.with_suffix(
        destination.suffix + ".part"
    )

    if temporary_path.exists():
        temporary_path.unlink()

    print(f"Downloading: {url}")
    print(f"Destination: {destination}")

    request = Request(
        url,
        headers={"User-Agent": "EJSCREEN-Data-Processing/1.0"},
    )

    try:
        with urlopen(request) as response:
            expected_size = response.headers.get("Content-Length")

            with temporary_path.open("wb") as output_file:
                shutil.copyfileobj(response, output_file)

    except HTTPError as error:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"HTTP error {error.code} while downloading {url}"
        ) from error

    except URLError as error:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Network error while downloading {url}: {error.reason}"
        ) from error

    actual_size = temporary_path.stat().st_size

    if expected_size is not None:
        expected_size_int = int(expected_size)

        if actual_size != expected_size_int:
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Downloaded file size does not match the server's "
                f"Content-Length. Expected {expected_size_int:,} bytes, "
                f"received {actual_size:,} bytes."
            )

    temporary_path.replace(destination)

    print(
        "Download complete: "
        f"{destination.name} ({actual_size / 1024**2:,.2f} MiB)"
    )


def extract_archive(
    archive_path: Path,
    output_directory: Path,
    overwrite: bool = False,
) -> None:
    """Extract a 7z archive into the requested directory."""

    if output_directory.exists() and overwrite:
        print(f"Removing existing extraction: {output_directory}")
        shutil.rmtree(output_directory)

    output_directory.mkdir(parents=True, exist_ok=True)

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive_entries = archive.getnames()

        existing_entries = [
            output_directory / entry
            for entry in archive_entries
        ]

        if (
            archive_entries
            and all(path.exists() for path in existing_entries)
            and not overwrite
        ):
            print(
                "Archive appears to be fully extracted, skipping: "
                f"{archive_path.name}"
            )
            return

        print(f"Extracting: {archive_path}")
        print(f"Extraction directory: {output_directory}")
        archive.extractall(path=output_directory)

    print(f"Extraction complete: {archive_path.name}")


def find_required_inputs(root_directory: Path) -> tuple[Path, Path]:
    """Locate the required geodatabase and PlusFlowlineVAA table."""

    geodatabases = sorted(
        root_directory.rglob("NHDSnapshot.gdb")
    )

    vaa_tables = sorted(
        root_directory.rglob("PlusFlowlineVAA.dbf")
    )

    if len(geodatabases) != 1:
        raise RuntimeError(
            "Expected exactly one NHDSnapshot.gdb, but found "
            f"{len(geodatabases)} under {root_directory}."
        )

    if len(vaa_tables) != 1:
        raise RuntimeError(
            "Expected exactly one PlusFlowlineVAA.dbf, but found "
            f"{len(vaa_tables)} under {root_directory}."
        )

    return geodatabases[0], vaa_tables[0]


def main() -> int:
    args = parse_args()
    config = VPU_CONFIG[args.vpu]

    vpu_directory = (
        Path("wastewater")
        / "pipeline"
        / "raw_input"
        / "nhdplus"
        / f"vpu{args.vpu}"
    )

    archives_directory = vpu_directory / "archives"
    snapshot_directory = vpu_directory / "snapshot_extracted"
    attributes_directory = vpu_directory / "attributes_extracted"

    snapshot_key = config["snapshot_key"]
    attributes_key = config["attributes_key"]

    snapshot_archive = (
        archives_directory / Path(snapshot_key).name
    )

    attributes_archive = (
        archives_directory / Path(attributes_key).name
    )

    try:
        download_file(
            url=f"{BUCKET_URL}/{snapshot_key}",
            destination=snapshot_archive,
            overwrite=args.overwrite,
        )

        download_file(
            url=f"{BUCKET_URL}/{attributes_key}",
            destination=attributes_archive,
            overwrite=args.overwrite,
        )

        extract_archive(
            archive_path=snapshot_archive,
            output_directory=snapshot_directory,
            overwrite=args.overwrite,
        )

        extract_archive(
            archive_path=attributes_archive,
            output_directory=attributes_directory,
            overwrite=args.overwrite,
        )

        geodatabase, vaa_table = find_required_inputs(
            vpu_directory
        )

    except Exception as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        return 1

    print("\nNHDPlus fetch completed successfully.")
    print(f"VPU: {args.vpu}")
    print(f"Region: {config['region']}")
    print(f"NHDSnapshot geodatabase: {geodatabase}")
    print(f"PlusFlowlineVAA table: {vaa_table}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
