from pathlib import Path
import csv
import importlib.util
import sys


def load_weighted_score_udf(root_path: Path):
    """Dynamically load the WeightedScoreUDF.py module from the scripts/traffic folder.
    This avoids package/import issues when running the test script directly.
    Returns the loaded module.
    """
    udf_path = root_path / "scripts" / "traffic" / "WeightedScoreUDF.py"
    if not udf_path.exists():
        raise FileNotFoundError(f"WeightedScoreUDF.py not found at {udf_path}")

    spec = importlib.util.spec_from_file_location("WeightedScoreUDF", str(udf_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_float(val, default=0.0):
    try:
        if val is None or val == "":
            return float(default)
        return float(val)
    except Exception:
        return float(default)


def main(limit_rows=None):
    # Resolve repository root (3 levels up from this file: scripts/utilities/testing -> root)
    script_file = Path(__file__).resolve()
    repo_root = script_file.parents[3]

    # Prefer a single, simple import if the package layout supports it. This lets
    # callers who have the repo on PYTHONPATH do a straightforward import:
    #   from scripts.traffic import WeightedScoreUDF as ws
    # If that fails (e.g. scripts isn't a package or PYTHONPATH isn't set), fall
    # back to the dynamic loader above.
    try:
        ws = load_weighted_score_udf(repo_root)
    except Exception:
        print("*** Could not load WeightedScoreUDF")
        sys.exit(1)


    csv_path = repo_root / "outputs" / "traffic" / "preprocessing" / "ri_distpairs_sample.csv"
    if not csv_path.exists():
        print(f"CSV not found at {csv_path}")
        sys.exit(2)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=1):
            if limit_rows and i > limit_rows:
                break

            # Map CSV columns to the UDF inputs
            land_area = to_float(row.get("ALAND20", 0))
            water_area = to_float(row.get("AWATER20", 0))
            distance = to_float(row.get("dist", 0))
            total_pop = to_float(row.get("POP20", 0))
            aadt_volume = to_float(row.get("aadt", 0))

            # Print fields in the same order they are passed to the UDFs:
            # (land_area, water_area, distance, total_pop, aadt_volume)
            print(f"Row {i}: GEOID={row.get('GEOID20')} OBJECTID={row.get('OBJECTID')} ALAND={land_area} AWATER={water_area} dist={distance} total_pop={total_pop} aadt={aadt_volume}")

            # Call both UDF versions
            try:
                res1 = ws.WeightedScoreUDF1(land_area, water_area, distance, total_pop, aadt_volume)
            except Exception as e:
                res1 = f"ERROR: {e}"

            try:
                res2 = ws.WeightedScoreUDF2(land_area, water_area, distance, total_pop, aadt_volume)
            except Exception as e:
                res2 = f"ERROR: {e}"

            print(f"  WeightedScoreUDF1 -> adj_distance, radius, score, weighted_score = {res1}")
            print(f"  WeightedScoreUDF2 -> adj_distance, radius, score, weighted_score = {res2}")
            print("---")


if __name__ == "__main__":
    # Optionally accept a single integer CLI arg to limit rows printed
    max_rows = None
    if len(sys.argv) > 1:
        try:
            max_rows = int(sys.argv[1])
        except Exception:
            pass
    main(limit_rows=max_rows)
