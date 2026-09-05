#!/usr/bin/env bash
#
# Runs the full local pm25 indicator pipeline: precheck -> fetch -> preprocess -> score.
#
# All stdout/stderr from each step is written to pm25.out (overwritten each run,
# with a timestamp on every start/finish/failure announcement). The console
# only shows brief, untimestamped start/finish/failure lines, so you can tell
# from the terminal that the run is still making progress without it filling
# up with the full per-step logging.
#
# Stops immediately (fail-fast) if any step fails; later steps are skipped.
#
# Requires the project virtual environment to already be active (see
# scripts/readme.md).
#
# Usage:
#   ./run_pm25_pipeline.sh -v <version>
#
# Example:
#   ./run_pm25_pipeline.sh -v 1.2022

set -euo pipefail

usage() {
  echo "Usage: $0 -v <version>  (e.g. -v 1.2022)" >&2
  exit 1
}

VERSION=""
while getopts ":v:" opt; do
  case "$opt" in
    v) VERSION="$OPTARG" ;;
    *) usage ;;
  esac
done

[[ -z "$VERSION" ]] && usage

# State is intentionally fixed to "all"; location is local-only for now.
STATE="all"
LOCATION="local"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
OUT_FILE="$SCRIPT_DIR/pm25.out"

# Overwrite the log file for this run.
: > "$OUT_FILE"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

# Writes a timestamped line to pm25.out and the same message (untimestamped) to the console.
announce() {
  local message="$1"
  echo "[$(timestamp)] $message" >> "$OUT_FILE"
  echo "$message"
}

# Runs a step, sending all of its stdout/stderr to pm25.out. Announces start/finish
# and, on failure, stops the whole script immediately (fail-fast).
run_step() {
  local step_name="$1"
  shift
  announce "START: $step_name"
  set +e
  "$@" >> "$OUT_FILE" 2>&1
  local exit_code=$?
  set -e
  if [[ $exit_code -eq 0 ]]; then
    announce "FINISH: $step_name"
  else
    announce "FAILED: $step_name (exit code $exit_code)"
    echo "See $OUT_FILE for details." >&2
    exit "$exit_code"
  fi
}

# Indicator commands are documented as run from the `scripts` folder.
cd "$SCRIPTS_DIR"

announce "PM2.5 pipeline starting (version=$VERSION, state=$STATE, location=$LOCATION)"
echo "Full step output: $OUT_FILE"

run_step "precheck: shared census_block_weights asset" \
  python3 "$SCRIPT_DIR/verify_census_block_weights.py" --indicator pm25 --version "$VERSION"

run_step "fetch: raw pm25 download" \
  python3 shared/fetch_raw.py --indicator pm25 -v "$VERSION" -l "$LOCATION"

run_step "preprocess: tract annual averages" \
  python3 pm25/pm25_preprocess.py -v "$VERSION" -l "$LOCATION"

run_step "score: block-group expansion (all states)" \
  python3 pm25/pm25_score.py -v "$VERSION" -s "$STATE" -l "$LOCATION"

announce "PM2.5 pipeline finished successfully"
