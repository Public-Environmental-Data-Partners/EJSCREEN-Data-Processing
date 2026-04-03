## Plan: Hazardous Waste Preprocess Overview

## Current implemented design

The active preprocessing script builds one deduplicated handler population consisting of:

1. operating TSDF handlers from `RCRA_FACILITIES.csv`
2. handlers that both:
   a. appear in the 2021 BR reporting universe
   b. are classified as LQG in `RCRA_FACILITIES.csv`

This means the current emitted population is:

1. TSDFs whether or not they appear in BR reporting
2. LQGs only when they appear in the 2021 BR reporting set

## Current source roles

1. `RCRA_FACILITIES.csv` is the master source for handler identity, site attributes, TSDF status, generator classification, coordinates, and emitted row content.
2. `BR_REPORTING_2021.zip` is the authoritative source for whether a handler reported in the 2021 biennial cycle.
3. The BR source is used as a handler-ID filter for the LQG branch, not as the source of emitted row attributes.

## Current processing outline

The code currently performs these stages:

1. Load config and resolve either local or remote root paths.
2. Validate source presence and schema for `RCRA_FACILITIES.csv` and `BR_REPORTING_2021.zip`.
3. Build the 2021 BR reporter universe by reading the configured BR CSV members and unioning normalized handler IDs.
4. Classify RCRA rows for active-site status, operating TSDF status, and LQG status.
5. Build the TSDF subset from the RCRA master file.
6. Build the BR-reporting LQG subset by intersecting BR reporters with RCRA rows classified as LQG.
7. Validate retained rows for normalized handler ID and usable coordinates.
8. Deduplicate duplicate qualifying rows within each subset for audit clarity.
9. Union the TSDF and BR-reporting LQG subsets and annotate overlap through `site_class`.
10. Write source-aware audit outputs.
11. Emit the canonical hazardous-waste site output.

## Current output and audit behavior

The active preprocess pipeline now writes all of the following under the configured output root:

1. `outputs/hazardous_waste_filtered.csv`
2. `outputs/hazardous_waste_parse_audit.csv`
3. `outputs/hazardous_waste_validation_audit.csv`
4. `outputs/hazardous_waste_dedup_audit.csv`

The canonical output remains the file expected by downstream proximity processing.

The audit contract is currently:

1. parse audit: present for contract stability, currently expected to be empty under the active fail-fast CSV reader path
2. validation audit: BR reporter extraction rejects and RCRA site-level validation rejects
3. dedup audit: duplicate qualifying rows within the TSDF or BR-reporting-LQG subset paths

## Important assumptions still in force

1. Present-day `RCRA_FACILITIES.csv` attributes are being used to classify current TSDF and LQG status.
2. `BR_REPORTING_2021.zip` is the authoritative filter for whether a handler reported in the 2021 cycle.
3. The LQG branch is therefore an approximation of 2021-aligned LQGs based on current master-file classification plus 2021 BR participation.
4. Column names are treated fail-fast. The script does not attempt fallback column-name guessing.

## Relevant active files

- `scripts/hazardous_waste/hazardous_waste_preprocess.py`
- `scripts/hazardous_waste/hazardous_waste_preprocess_config.json`
- `scripts/hazardous_waste/hazardous_waste_source_data.md`
- `scripts/hazardous_waste/hazardous_waste_proximity.py`
- `scripts/hazardous_waste/run_state_validation.sh`
