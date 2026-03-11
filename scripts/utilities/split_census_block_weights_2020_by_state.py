"""Split the national 2020 census block weights CSV into one file per state.

This is a one-off utility script. Run it from `scripts/utilities`.
It reads:
    ../superfund/pipeline/test_data/downloads/census_block_weights_2020.csv

It writes per-state CSVs to:
    ../superfund/pipeline/test_data/downloads/census_block_weights_2020/
"""

from __future__ import annotations

import csv
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = SCRIPT_DIR.parent / "superfund" / "pipeline" / "test_data" / "downloads"
INPUT_CSV = DOWNLOADS_DIR / "census_block_weights_2020.csv"
OUTPUT_DIR = DOWNLOADS_DIR / "census_block_weights_2020"
STATE_COLUMN = "state_abb"


def main() -> int:
    if not INPUT_CSV.exists():
        raise SystemExit(f"Input CSV not found: {INPUT_CSV}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    writers: dict[str, csv.DictWriter[str]] = {}
    handles: dict[str, object] = {}
    row_counts: dict[str, int] = {}
    total_rows = 0

    try:
        with INPUT_CSV.open("r", newline="", encoding="utf-8-sig") as infile:
            reader = csv.DictReader(infile)
            if reader.fieldnames is None:
                raise SystemExit(f"Input CSV has no header row: {INPUT_CSV}")
            if STATE_COLUMN not in reader.fieldnames:
                raise SystemExit(
                    f"Expected column '{STATE_COLUMN}' in {INPUT_CSV}; found {reader.fieldnames}"
                )

            for row_number, row in enumerate(reader, start=2):
                state_abb = (row.get(STATE_COLUMN) or "").strip().upper()
                if not state_abb:
                    raise SystemExit(
                        f"Blank '{STATE_COLUMN}' at input row {row_number}; cannot determine output file"
                    )

                if state_abb not in writers:
                    output_path = OUTPUT_DIR / f"census_block_weights_2020_{state_abb}.csv"
                    handle = output_path.open("w", newline="", encoding="utf-8")
                    writer = csv.DictWriter(handle, fieldnames=reader.fieldnames)
                    writer.writeheader()
                    handles[state_abb] = handle
                    writers[state_abb] = writer
                    row_counts[state_abb] = 0

                writers[state_abb].writerow(row)
                row_counts[state_abb] += 1
                total_rows += 1
    finally:
        for handle in handles.values():
            handle.close()

    print(f"Wrote {len(row_counts)} state files to: {OUTPUT_DIR}")
    print(f"Total data rows processed: {total_rows}")
    for state_abb in sorted(row_counts):
        print(f"  {state_abb}: {row_counts[state_abb]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())