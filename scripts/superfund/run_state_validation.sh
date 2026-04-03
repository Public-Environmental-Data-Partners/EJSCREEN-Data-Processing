#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
	echo "Usage: $0 <state_code>"
	echo "  state_code: required two-letter state code, for example MT"
	exit 1
fi

STATE_CODE="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"

if [[ ${#STATE_CODE} -ne 2 ]]; then
	echo "Invalid state code: $STATE_CODE"
	echo "Expected a two-letter postal code"
	exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATION_DIR="$SCRIPT_DIR/../utilities/validation"
VALIDATION_OUTPUT_DIR="$VALIDATION_DIR/output"
PIPELINE_DIR="$SCRIPT_DIR/pipeline"

python3 "$SCRIPT_DIR/superfund_npl_proximity.py" \
	--state "$STATE_CODE" \
	--input-path "$PIPELINE_DIR" \
	--output-path "$PIPELINE_DIR"

python3 "$VALIDATION_DIR/compareScores2Ejam.py" \
	--state "$STATE_CODE" \
	--file-ejam "$VALIDATION_OUTPUT_DIR/$STATE_CODE/ejam_superfund_subset.csv" \
	--score-ejam proximity.npl \
	--file-b "$PIPELINE_DIR/$STATE_CODE/final_bg_scores.csv" \
	--out "$VALIDATION_OUTPUT_DIR/$STATE_CODE/${STATE_CODE}_compare_ejam_superfund_subset_to_weighted_scores.png"