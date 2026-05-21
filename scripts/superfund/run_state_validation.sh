#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
	echo "Usage: $0 <state_code> [storage_mode]"
	echo "  state_code: required two-letter state code, for example MT"
	echo "  storage_mode: optional local or remote, defaults to local"
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

echo "Starting Superfund proximity for $STATE_CODE in $STORAGE_MODE mode"
if ! python3 "$SCRIPT_DIR/superfund_preprocess.py" \
	"$STORAGE_MODE"; then
	echo "Superfund preprocess failed in $STORAGE_MODE mode."
	exit 1
fi
echo "Completed Superfund preprocess"

echo "Starting Superfund indicator for $STATE_CODE in $STORAGE_MODE mode"
if ! python3 "$SCRIPT_DIR/superfund_indicator.py" \
	"$STORAGE_MODE" \
	--state "$STATE_CODE"; then
	echo "Superfund indicator failed for state $STATE_CODE in $STORAGE_MODE mode."
	exit 1
fi
echo "Completed Superfund indicator for $STATE_CODE"

if [[ "$STORAGE_MODE" == "remote" ]]; then
	echo "Skipping compareScores2Ejam.py for remote mode because the validation harness currently reads local files only."
	exit 0
fi

echo "Starting compareScores2Ejam.py for $STATE_CODE"
python3 "$VALIDATION_DIR/compareScores2Ejam.py" \
	--state "$STATE_CODE" \
	--file-ejam "$VALIDATION_OUTPUT_DIR/$STATE_CODE/ejam_superfund_subset.csv" \
	--score-ejam proximity.npl \
	--file-b "$PIPELINE_DIR/$STATE_CODE/final_bg_scores.csv" \
	--score-new superfund_score \
	--out "$VALIDATION_OUTPUT_DIR/$STATE_CODE/${STATE_CODE}_compare_ejam_superfund_subset_to_weighted_scores.png"
echo "Completed compareScores2Ejam.py for $STATE_CODE"