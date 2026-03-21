# debug_npl_ids.py
import geopandas as gpd
import pandas as pd
import re
from pathlib import Path

NPL_GDB = "pipeline/test_data/downloads/NPL_Boundaries_20260217/NPL_Boundaries.gdb"
LAYER = "SITE_BOUNDARIES_SF"
TARGETED_CSV = Path("pipeline/test_data/targeted_block_groups.csv")

def norm(x):
    if pd.isna(x): return ""
    s = str(x).strip()
    s = re.sub(r"\.0+$","",s)
    return s.lower()

print("Reading NPL layer:", NPL_GDB, "layer:", LAYER)
npl = gpd.read_file(NPL_GDB, layer=LAYER)
print("NPL columns:", list(npl.columns))
print("NPL rows:", len(npl))

if TARGETED_CSV.exists():
    tgt = pd.read_csv(TARGETED_CSV, dtype=str)
    tgt_ids = set(tgt['EPA_ID'].astype(str).apply(norm).unique())
    print("Targeted unique EPA_IDs:", len(tgt_ids))
    print("Sample targeted (first 5):", list(sorted(tgt_ids))[:5])
else:
    tgt_ids = None
    print("Targeted CSV not found at", TARGETED_CSV)

# inspect likely columns
candidates = [c for c in npl.columns if ('epa' in c.lower() or 'site' in c.lower() or npl[c].dtype == object)]
print("\nCandidate columns to inspect:", candidates)

for c in candidates:
    try:
        vals = npl[c].astype(str).head(2000).apply(norm).unique()
    except Exception as e:
        print(f"\nColumn {c}: ERROR reading samples: {e}")
        continue
    print(f"\nColumn {c} — sample unique (up to 5) count={len(vals)}:")
    print(list(vals)[:5])
    if tgt_ids:
        inter = set(vals) & tgt_ids
        print("Intersection with targeted IDs (up to 5):", list(inter)[:5], "count:", len(inter))

# If you want, print first 5 non-empty EPA_PROGRAM values
if 'EPA_PROGRAM' in npl.columns:
    print("\nFirst 5 EPA_PROGRAM values (raw):")
    print(npl['EPA_PROGRAM'].astype(str).head(5).tolist())