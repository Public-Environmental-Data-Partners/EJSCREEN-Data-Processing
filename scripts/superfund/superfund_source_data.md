# Superfund Source Data

- Raw NPL boundaries ZIP download URL:
  - https://edg.epa.gov/data/PUBLIC/OLEM/OLEM-OSRTI/NPL_Boundaries.zip
- Canonical preprocess output:
  - `preprocessed_input/npl_boundaries/NPL_Boundaries.gdb`
- Notes:
  - The raw delivery format is a ZIP archive fetched by `scripts/fetch_raw.py`.
  - The preprocess step extracts the single `.gdb` directory into one stable local path for the indicator step.