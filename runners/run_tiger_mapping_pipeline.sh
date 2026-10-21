#!/usr/bin/env bash
#
# Fetches the TIGER boundary data used by Additional Demographics mapping and
# compiles the block-group and tract state archives into US-wide ZIP files.
#
# All stdout/stderr from each step is appended to run_tiger_mapping_pipeline.out,
# with a timestamp on every start/finish/failure announcement. The console only
# shows brief, untimestamped start/finish/failure lines.
#
# Stops immediately (fail-fast) if any step fails; later steps are skipped.
#
# Requires the project virtual environment to already be active (see
# scripts/readme.md).
#
# Usage:
#   ./run_tiger_mapping_pipeline.sh [-l local|remote]
#
# Example:
#   ./run_tiger_mapping_pipeline.sh --location local

set -euo pipefail

usage() {
  echo "Usage: $0 [-l|--location local|remote]" >&2
  exit 1
}

LOCATION="local"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -l|--location)
      [[ $# -ge 2 ]] || usage
      LOCATION="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

case "$LOCATION" in
  local|remote) ;;
  *) echo "Invalid location: $LOCATION (expected local or remote)" >&2; usage ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
OUT_FILE="$SCRIPT_DIR/run_tiger_mapping_pipeline.out"

# Create the log file if it does not exist; preserve previous runs.
touch "$OUT_FILE"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

# Writes a timestamped line to the log and the same message to the console.
announce() {
  local message="$1"
  echo "[$(timestamp)] $message" >> "$OUT_FILE"
  echo "$message"
}

# Runs a step, capturing its output and stopping the workflow on failure.
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

cd "$REPO_ROOT"

announce "==============================================="
announce "TIGER mapping pipeline starting (location=$LOCATION)"
echo "Full step output: $OUT_FILE"

run_step "fetch: state-level TIGER boundaries" \
  python scripts/shared/fetch_raw.py --indicator shared --download tiger_state_2020 -s all -l "$LOCATION"

run_step "fetch: county-level TIGER boundaries" \
  python scripts/shared/fetch_raw.py --indicator shared --download tiger_county_2020 -s all -l "$LOCATION"

run_step "fetch: tract-level TIGER boundaries" \
  python scripts/shared/fetch_raw.py --indicator shared --download tiger_tract_2020 -s all -l "$LOCATION"

run_step "fetch: block-group-level TIGER boundaries" \
  python scripts/shared/fetch_raw.py --indicator shared --download tiger_bg_2020 -s all -l "$LOCATION"

run_step "compile: US-wide block-group TIGER archive" \
  python scripts/shared/tiger_compiler.py -g bg -l "$LOCATION"

run_step "compile: US-wide tract TIGER archive" \
  python scripts/shared/tiger_compiler.py -g tract -l "$LOCATION"

announce "TIGER mapping pipeline finished successfully"
announce "==============================================="