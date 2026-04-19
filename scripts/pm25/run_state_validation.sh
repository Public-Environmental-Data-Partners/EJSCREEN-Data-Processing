#!/usr/bin/env bash

# Run the PM2.5 indicator for one state and, in local mode, compare the output
# against a PM2.5 EJAM subset CSV using compareScores2Ejam.py.
#
# Defaults follow the current working PM2.5 validation setup:
# - EJAM subset file: scripts/utilities/validation/output/{STATE}/ejam_pm25_subset.csv
# - EJAM score column: pm
# - pipeline score column: pm25_score

set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 ]]; then
	echo "Usage: $0 <state_code> [storage_mode] [ejam_subset_csv] [ejam_score_column]"
	echo "  state_code: required two-letter state code, for example RI"
	echo "  storage_mode: optional local or remote, defaults to local"
	echo "  ejam_subset_csv: optional local path to the PM2.5 EJAM subset CSV"
	echo "                   defaults to scripts/utilities/validation/output/{STATE}/ejam_pm25_subset.csv"
	echo "  ejam_score_column: optional EJAM PM2.5 score column name"
	echo "                    defaults to pm"
	exit 1
fi

STATE_CODE="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
STORAGE_MODE="${2:-local}"
STORAGE_MODE="$(printf '%s' "$STORAGE_MODE" | tr '[:upper:]' '[:lower:]')"

if [[ ${#STATE_CODE} -ne 2 ]]; then
	echo "Invalid state code: $STATE_CODE"
	echo "Expected a two-letter postal code"
	exit 1
fi

if [[ "$STORAGE_MODE" != "local" && "$STORAGE_MODE" != "remote" ]]; then
	echo "Invalid storage_mode: $STORAGE_MODE"
	echo "Expected local or remote"
	exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATION_DIR="$SCRIPT_DIR/../utilities/validation"
VALIDATION_OUTPUT_DIR="$VALIDATION_DIR/output"
PIPELINE_DIR="$SCRIPT_DIR/pipeline/output/indicators"
EJAM_FILE="${3:-$VALIDATION_OUTPUT_DIR/$STATE_CODE/ejam_pm25_subset.csv}"
EJAM_SCORE_COLUMN="${4:-pm}"

echo "Starting PM2.5 indicator for $STATE_CODE in $STORAGE_MODE mode"
if ! python3 "$SCRIPT_DIR/pm25_indicator.py" \
	"$STORAGE_MODE" \
	--state "$STATE_CODE"; then
	echo "PM2.5 indicator failed for state $STATE_CODE in $STORAGE_MODE mode."
	exit 1
fi
echo "Completed PM2.5 indicator for $STATE_CODE"

if [[ "$STORAGE_MODE" == "remote" ]]; then
	echo "Skipping compareScores2Ejam.py for remote mode because the validation harness currently reads local files only."
	exit 0
fi

if [[ ! -f "$EJAM_FILE" ]]; then
	echo "PM2.5 EJAM subset file not found: $EJAM_FILE"
	echo "Provide it as the third argument once it exists."
	exit 1
fi

echo "Starting compareScores2Ejam.py for $STATE_CODE"
python3 "$VALIDATION_DIR/compareScores2Ejam.py" \
	--state "$STATE_CODE" \
	--file-ejam "$EJAM_FILE" \
	--score-ejam "$EJAM_SCORE_COLUMN" \
	--file-b "$PIPELINE_DIR/$STATE_CODE/final_bg_scores.csv" \
	--score-new pm25_score \
	--out "$VALIDATION_OUTPUT_DIR/$STATE_CODE/${STATE_CODE}_compare_ejam_pm25_subset_to_final_bg_scores.png"
echo "Completed compareScores2Ejam.py for $STATE_CODE"