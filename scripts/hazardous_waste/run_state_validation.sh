#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
	echo "Usage: $0 <state_code> [preprocess] [generator_net]"
	echo "  state_code: required two-letter state code, for example MT"
	echo "  preprocess: optional Y or N, defaults to N"
	echo "  generator_net: optional narrow, medium, or broad, defaults to narrow"
	exit 1
fi

STATE_CODE="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
PREPROCESS="${2:-N}"
PREPROCESS="$(printf '%s' "$PREPROCESS" | tr '[:lower:]' '[:upper:]')"
GENERATOR_NET="${3:-narrow}"
GENERATOR_NET="$(printf '%s' "$GENERATOR_NET" | tr '[:upper:]' '[:lower:]')"

if [[ "$PREPROCESS" != "Y" && "$PREPROCESS" != "N" ]]; then
	echo "Invalid preprocess flag: $PREPROCESS"
	echo "Expected Y or N"
	exit 1
fi

if [[ "$GENERATOR_NET" != "narrow" && "$GENERATOR_NET" != "medium" && "$GENERATOR_NET" != "broad" ]]; then
	echo "Invalid generator_net: $GENERATOR_NET"
	echo "Expected narrow, medium, or broad"
	exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATION_DIR="$SCRIPT_DIR/../utilities/validation"
VALIDATION_OUTPUT_DIR="$VALIDATION_DIR/output"
PIPELINE_DIR="$SCRIPT_DIR/pipeline"

if [[ "$PREPROCESS" == "Y" ]]; then
	python3 "$SCRIPT_DIR/hazardous_waste_preprocess.py" local --generator-net "$GENERATOR_NET"
fi

python3 "$SCRIPT_DIR/hazardous_waste_proximity.py" \
	--state "$STATE_CODE" \
	--input-path "$PIPELINE_DIR" \
	--output-path "$PIPELINE_DIR"

python3 "$VALIDATION_DIR/compareScores2Ejam.py" \
	--state "$STATE_CODE" \
	--file-ejam "$VALIDATION_OUTPUT_DIR/$STATE_CODE/ejam_hazardous_waste_subset.csv" \
	--score-ejam proximity.tsdf \
	--file-b "$PIPELINE_DIR/$STATE_CODE/final_bg_scores.csv" \
	--out "$VALIDATION_OUTPUT_DIR/$STATE_CODE/${STATE_CODE}_compare_ejam_HW_subset_to_weighted_scores.png"