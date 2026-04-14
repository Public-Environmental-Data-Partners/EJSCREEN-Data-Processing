#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 ]]; then
	echo "Usage: $0 <state_code> [storage_mode] [preprocess] [generator_net]"
	echo "  state_code: required two-letter state code, for example MT"
	echo "  storage_mode: optional local or remote, defaults to local"
	echo "  preprocess: optional Y or N, defaults to N"
	echo "  generator_net: optional narrow, medium, or broad, defaults to narrow"
	exit 1
fi

STATE_CODE="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
STORAGE_MODE="${2:-local}"
STORAGE_MODE="$(printf '%s' "$STORAGE_MODE" | tr '[:upper:]' '[:lower:]')"
PREPROCESS="${3:-N}"
PREPROCESS="$(printf '%s' "$PREPROCESS" | tr '[:lower:]' '[:upper:]')"
GENERATOR_NET="${4:-narrow}"
GENERATOR_NET="$(printf '%s' "$GENERATOR_NET" | tr '[:upper:]' '[:lower:]')"

if [[ "$STORAGE_MODE" != "local" && "$STORAGE_MODE" != "remote" ]]; then
	echo "Invalid storage_mode: $STORAGE_MODE"
	echo "Expected local or remote"
	exit 1
fi

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
REMOTE_PIPELINE_DIR="s3://pedp-data-preserved/ejscreen-data-processing/hazardous_waste/pipeline/"

if [[ "$PREPROCESS" == "Y" ]]; then
	echo "Starting hazardous-waste preprocess for $STATE_CODE in $STORAGE_MODE mode"
	if ! python3 "$SCRIPT_DIR/hazardous_waste_preprocess.py" "$STORAGE_MODE" --generator-net "$GENERATOR_NET"; then
		echo "Hazardous-waste preprocess failed in $STORAGE_MODE mode. See scripts/hazardous_waste/hwpre.log for details."
		exit 1
	fi
	echo "Completed hazardous-waste preprocess for $STATE_CODE"
fi

if [[ "$STORAGE_MODE" == "remote" ]]; then
	INPUT_PATH="$REMOTE_PIPELINE_DIR"
	OUTPUT_PATH="$REMOTE_PIPELINE_DIR"
else
	INPUT_PATH="$PIPELINE_DIR"
	OUTPUT_PATH="$PIPELINE_DIR"
fi

echo "Starting hazardous-waste proximity for $STATE_CODE in $STORAGE_MODE mode"
if ! python3 "$SCRIPT_DIR/hazardous_waste_proximity.py" \
	--state "$STATE_CODE" \
	--input-path "$INPUT_PATH" \
	--output-path "$OUTPUT_PATH"; then
	echo "Hazardous-waste proximity failed for state $STATE_CODE in $STORAGE_MODE mode. See scripts/hazardous_waste/hwprox.log for details."
	exit 1
fi
echo "Completed hazardous-waste proximity for $STATE_CODE"

if [[ "$STORAGE_MODE" == "remote" ]]; then
	echo "Skipping compareScores2Ejam.py for remote mode because the validation harness currently reads local files only."
	exit 0
fi

echo "Starting compareScores2Ejam.py for $STATE_CODE"
python3 "$VALIDATION_DIR/compareScores2Ejam.py" \
	--state "$STATE_CODE" \
	--file-ejam "$VALIDATION_OUTPUT_DIR/$STATE_CODE/ejam_hazardous_waste_subset.csv" \
	--score-ejam proximity.tsdf \
	--file-b "$PIPELINE_DIR/$STATE_CODE/final_bg_scores.csv" \
	--score-new hazardous_waste_score \
	--out "$VALIDATION_OUTPUT_DIR/$STATE_CODE/${STATE_CODE}_compare_ejam_HW_subset_to_weighted_scores.png"
echo "Completed compareScores2Ejam.py for $STATE_CODE"