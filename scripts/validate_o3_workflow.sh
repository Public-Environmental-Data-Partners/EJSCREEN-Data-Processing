#!/usr/bin/env bash
set -euo pipefail

# Validate O3 workflow: fetch raw -> preprocess -> score
# Run this from the repository `scripts/` folder or execute directly from the project root.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PYTHON=${PYTHON:-python3}

echo "Using Python interpreter: $PYTHON"

fail() {
  rc=$1
  shift
  echo "ERROR: $* (exit $rc)" >&2
  exit $rc
}

echo "[1/3] Fetching raw O3 data (indicator=o3, version=1.0, storage=local)"
$PYTHON shared/fetch_raw.py --indicator o3 --location local --version 1.0 || fail $? "fetch_raw failed"

echo "[2/3] Running o3_preprocess (storage=local, version=1.0)"
$PYTHON o3/o3_preprocess.py local -v 1.0 || fail $? "o3_preprocess failed"

echo "[3/3] Running o3_score (storage=local, version=1.0) for a small number of states -- not CT!!"
$PYTHON o3/o3_score.py --location local --state MT -v 1.0 || fail $? "o3_score failed"
$PYTHON o3/o3_score.py --location local --state NJ -v 1.0 || fail $? "o3_score failed"
$PYTHON o3/o3_score.py --location local --state RI -v 1.0 || fail $? "o3_score failed"
$PYTHON o3/o3_score.py --location local --state WY -v 1.0 || fail $? "o3_score failed"

# Note: the following commandline is expected to fail due to NAs in the group block weights data for CT
# $PYTHON o3/o3_score.py --location local --state CT -v 1.0 || echo "Expected failure for CT due to NAs in block weights"

echo "O3 validation workflow completed successfully. No comparisons were run"
