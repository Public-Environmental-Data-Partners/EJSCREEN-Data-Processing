# Shared Pipeline Source Data Inventory

**Status:** Draft working document for source-of-truth acquisition notes.

This document is intended to answer one operational question before any shared fetch automation is written:

> For each file or file family expected under `scripts/shared/pipeline/downloads` or `scripts/shared/pipeline/preprocessed_input`, what is the authoritative upstream source, and how should we retrieve it?

The goal is to document the correct native URL, landing page, API, or generation process first. Only after those details are verified should we write any shared fetch or refresh scripts.

---

## 1. Shared Folder Scope

The shared downloads area is intended for source files or derived shared artifacts that are consumed by more than one indicator pipeline.

Current intended local folder layout:

- `scripts/shared/pipeline/downloads/`-- for individual files (expected to be mostly zips)
- `scripts/shared/pipeline/downloads/{type}/`-- for multiple files of the same type, such as tiger files and census block weights
- `scripts/shared/pipeline/preprocessed_input/{type}`-- for the results of any preprocessing code (which, presumably, used something in the downloads folders as input)


Current active consumer:

- `scripts/hazardous_waste/hazardous_waste_proximity.py`

Potential additional consumers:

- other proximity-style indicators that need the same Census geography assets

---

## 2. Shared File Inventory

### 2.1 TIGER/Line Census Block Group ZIPs

**Target path pattern**

- `scripts/shared/pipeline/downloads/tiger_lines/2020/bg/tl_2020_{FIPS}_bg.zip`

**Used for**

- block-group geometries used in proximity targeting and final score output coverage

**Current filename convention**

- one ZIP per state or territory, as indicated by fips code for the state
- example: `tl_2020_56_bg.zip` (56 => WY)

**Expected download approach**

- Scripted download of the file for each state + DC and PR for now. The list will probably
expand to include some territories.


**Native download URL for ftp/https downloads**

- Landing page: https://www2.census.gov/geo/tiger/TIGER2020/BG/
Note that data for other years is available simply by using the folder name `TIGER{four digit year}`


**Refresh cadence / version notes**

- 2020 vintage is currently assumed by downstream code
- We are pinned to this vintage while we are verifying our indicators against existing EJAM results.
- We will presumably update to a newer vintage when ready.

**Verification notes after download**

- confirm ZIP opens successfully
- confirm expected block-group shapefile members are present
- confirm state FIPS in filename matches expected state

---

### 2.2 Census Block Weights CSVs

**Local target path pattern**

- `scripts/shared/pipeline/preprocessed_input/census_block_weights_2020/census_block_weights_2020_{STATE}.csv`

**Used for**

- block centroid coordinates
- block-to-block-group membership
- block population
- block-group population totals
- population weighting fractions used in final aggregation

**Current filename convention**

- one CSV per state or territory
- example: `census_block_weights_2020_MT.csv`

**Expected upstream source type**

- derived shared artifact, not necessarily a single native download

**Authoritative origin**

- TODO: document whether this file is generated entirely within this repo or copied from another maintained source

**If generated in-repo, generation entry point**

- likely related script: `scripts/utilities/generate_block_wts.R`
- TODO: confirm the exact end-to-end generation workflow and required raw inputs

**Native source landing page(s) or API(s) for raw inputs**

- TODO

**Manual retrieval or generation notes**

- TODO

**Refresh cadence / version notes**

- currently tied to 2020 Census geography and population fields
- TODO: document whether regeneration is expected or whether these files should be treated as pinned reference assets

**Verification notes after creation or download**

- confirm required columns exist:
  - `GEOID20`
  - `INTPTLAT20`
  - `INTPTLON20`
  - `POP20`
  - `block_group_geoid`
  - `block_group_pop`
  - `fraction_of_total`
- confirm one file exists per state needed for testing or production runs

---

## 3. Open Questions

- Are there any additional shared geospatial assets that should live under `scripts/shared/pipeline/downloads` now, even if the current hazardous-waste code does not consume them yet?
- Should shared downloads mirror the exact upstream filenames, or should we keep the repo-specific naming conventions currently assumed by code?
- For S3, do we want a strict mirror of the `scripts/shared/pipeline/downloads` layout?
- For derived files like the census block weights, do we want to document both the raw upstream sources and the local generation command in this file?

---

## 4. Handoff Notes

Do not write shared fetch automation until the following are filled in and verified:

- native source landing page
- native download URL or API details
- any required authentication or access conditions
- refresh/version policy
- expected filename convention in the shared pipeline

Once those are confirmed, this document can be used as the specification for a future `shared_fetch.py` or similar refresh utility.