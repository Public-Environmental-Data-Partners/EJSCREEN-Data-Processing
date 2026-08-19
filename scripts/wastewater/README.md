# Wastewater Indicator

This directory contains the development pipeline for the EJScreen Wastewater Discharge indicator.

The pipeline integrates EPA RSEI Water Geographic Microdata with the NHDPlus V2.1 stream network and calculates Census block-group wastewater proximity scores. Modeled wastewater concentrations are linked to NHDPlus flowlines using COMIDs, nearby flowlines contribute to Census block scores using distance weighting, and block-level scores are aggregated to 2020 Census block groups using population weighting.

The final validated implementation uses 2021 offsite wastewater concentrations (`offsite_toxconc`).

---

# Pipeline Overview

The complete workflow consists of the following stages:

1. Download and preprocess EPA RSEI Water Geographic Microdata.
2. Download and preprocess regional NHDPlus V2.1 flowlines.
3. Join Water Geographic Microdata to NHDPlus flowlines using COMID.
4. Generate modeled wastewater flowlines for each NHDPlus Vector Processing Unit (VPU).
5. Combine regional modeled flowlines into a national CONUS dataset.
6. Calculate distance-weighted wastewater proximity scores for Census blocks.
7. Aggregate block-level scores to 2020 Census block groups using population weighting.
8. Generate state-level and national quality-assurance outputs.
9. Validate the reconstructed wastewater indicator against historical EJAM wastewater values.

---

# Scripts

## `wastewater_preprocess.py`

Preprocesses the EPA RSEI Water Geographic Microdata used by the wastewater workflow.

The preprocessing step prepares COMID-level wastewater concentration data for subsequent integration with NHDPlus flowlines.

Outputs are written under:

    pipeline/preprocessed_input/water_microdata/

---

## `wastewater_nhdplus_preprocess.py`

Preprocesses regional NHDPlus V2.1 datasets.

The script reads NHDPlus flowline geometry and associated `PlusFlowlineVAA` attributes and creates standardized regional flowline datasets for subsequent wastewater modeling.

Outputs are written under:

    pipeline/preprocessed_input/nhdplus/vpuXX/

---

## `wastewater_model_flowlines.py`

Joins preprocessed Water Geographic Microdata to each regional NHDPlus flowline dataset using COMID.

The resulting GeoParquet files contain NHDPlus flowline geometry together with modeled wastewater concentrations.

One modeled flowline dataset and associated QA information are produced for each VPU.

Outputs are written under:

    pipeline/preprocessed_input/modeled_flowlines/

---

## `wastewater_combine_flowlines.py`

Combines regional modeled wastewater flowline datasets into a national CONUS modeled-flowline dataset.

Only flowlines with positive modeled wastewater concentration are retained for the final proximity calculation.

The validated implementation uses the offsite wastewater concentration field:

    offsite_toxconc

Canonical 2021 output:

    pipeline/preprocessed_input/modeled_flowlines/wastewater_flowlines_conus_2021_positive.parquet

---

## `wastewater_proximity.py`

Calculates the wastewater proximity indicator for a single state.

For each state, the workflow:

1. identifies Census blocks relevant to the proximity calculation;
2. calculates distances between blocks and nearby wastewater-associated NHDPlus flowlines;
3. calculates distance-weighted block-level wastewater scores;
4. aggregates block-level scores to Census block groups using population weighting; and
5. writes quality-assurance information.

State outputs are written under:

    pipeline/output/indicators/<STATE>/

Each state directory contains:

    targeted_blocks.csv
    block_flowline_distances.csv
    final_bg_scores.csv
    wastewater_proximity_qa.json

---

## `wastewater_indicator.py`

Top-level orchestration script for the final indicator-generation stage.

The script:

- builds or reuses the combined national modeled-flowline dataset;
- runs the wastewater proximity calculation for one state or all configured CONUS states; and
- verifies that the expected state-level outputs were created.

The Water Geographic Microdata, NHDPlus, and regional modeled-flowline preprocessing stages must be completed before running this script.

Run all configured CONUS states:

    python wastewater/wastewater_indicator.py local

Run one state:

    python wastewater/wastewater_indicator.py local --state RI

Existing outputs are reused by default. To replace them:

    python wastewater/wastewater_indicator.py local --state RI --overwrite

---

## `wastewater_national_qa.py`

Summarizes completed wastewater indicator outputs across the national processing extent.

The QA workflow reports information including:

- total Census block groups;
- state-level record counts;
- wastewater score distributions;
- missing values;
- zero values; and
- national summary statistics.

Outputs are written under:

    pipeline/output/qa/

---

## `fetch_nhdplus.py`

Supports acquisition of the NHDPlus V2.1 source datasets required by the wastewater workflow.

NHDPlus data are organized by Vector Processing Unit (VPU) before preprocessing.

---

## `nhdplus_config.py` and `nhdplus_vpu_manifest.csv`

Provide configuration information for the regional NHDPlus V2.1 datasets used by the wastewater workflow.

The VPU configuration defines the regional NHDPlus inputs required for national CONUS processing.

---

# Source Data

The wastewater workflow uses three main groups of source data.

## EPA RSEI Water Geographic Microdata

EPA RSEI Water Geographic Microdata provide modeled wastewater concentrations associated with NHDPlus stream-network segments.

The final validated workflow uses:

    Reporting year: 2021
    Concentration field: offsite_toxconc
    Join identifier: COMID

COMID provides the shared identifier used to connect the Water Geographic Microdata to NHDPlus flowline geometry.

---

## NHDPlus V2.1

NHDPlus V2.1 provides the hydrographic stream-network geometry used by the proximity calculation.

Regional NHDPlus datasets are processed by Vector Processing Unit (VPU). Flowline geometry and associated attributes are standardized before they are joined to the wastewater concentration data.

---

## Census Geography

The final indicator uses 2020 Census geography.

Wastewater proximity is first calculated at the Census-block level. Block scores are then aggregated to Census block groups using population weighting.

Shared Census geography and block-weight inputs are maintained under the repository's shared pipeline directories.

---

# Validation

The reconstructed wastewater indicator was validated against the historical EJAM wastewater indicator.

Reference field:

    proximity.npdes

Pipeline field:

    wastewater_score

Validation utilities include:

    utilities/validation/ejam2csv.py
    utilities/validation/compareScores2Ejam.py

Rhode Island was used as the primary development and validation case.

Alternative wastewater concentration fields were evaluated during development. The offsite concentration field (`offsite_toxconc`) produced substantially stronger agreement with the historical EJAM indicator than the alternative combined concentration measure and was therefore retained for the final national workflow.

The validated Rhode Island implementation produced approximately:

    Pearson correlation:  0.95
    Spearman correlation: 0.93

These results indicate strong agreement with the historical wastewater indicator while not representing an exact numerical reproduction.

---

# National Outputs

The completed workflow produces state-level outputs under:

    pipeline/output/indicators/<STATE>/

for the 48 contiguous states and Washington, D.C.

This corresponds to 49 CONUS jurisdictions.

Each state contains:

    targeted_blocks.csv
    block_flowline_distances.csv
    final_bg_scores.csv
    wastewater_proximity_qa.json

National QA outputs are written to:

    pipeline/output/qa/

Generated downloads, intermediate datasets, state outputs, and comparison products are pipeline artifacts and are not intended to serve as source-code files.

---

# Validated Methodology

The current validated implementation uses:

- 2021 EPA RSEI Water Geographic Microdata;
- offsite wastewater concentrations (`offsite_toxconc`);
- NHDPlus V2.1 flowlines;
- COMID-based integration of wastewater concentrations and stream geometry;
- distance-weighted block-level proximity scoring;
- population-weighted aggregation to 2020 Census block groups; and
- validation against the historical EJAM `proximity.npdes` indicator.

---

# Current Status

Completed:

- Water Geographic Microdata preprocessing;
- regional NHDPlus preprocessing;
- modeled wastewater flowline generation across 21 VPUs;
- national CONUS modeled-flowline combination;
- state-level wastewater proximity generation across 49 CONUS jurisdictions;
- national QA generation;
- Rhode Island validation against EJAM; and
- project workflow documentation.