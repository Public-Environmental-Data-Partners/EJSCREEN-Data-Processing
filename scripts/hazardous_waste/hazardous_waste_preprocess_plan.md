## Plan: Hazardous Waste Preprocess Rebuild

Rebuild scripts/hazardous_waste/hazardous_waste_preprocess.py around a new primary source model. The pipeline should now use RCRA_FACILITIES.csv as the master handler table and BR_REPORTING_2021.zip as a required secondary source for identifying which LQGs actually filed a 2021 Biennial Report. The final emitted site CSV stays the same, but the plan document, config JSON and config class, and preprocessing implementation all need a substantial rewrite to reflect this new logic.

## Target population

Build one deduplicated handler list consisting of:

1. all operating TSDF handlers from RCRA_FACILITIES
2. plus handlers that both:
   a. appear in the 2021 BR reporting universe
   b. are classified as LQG in RCRA_FACILITIES

This means the final regulatory population is:

1. TSDFs independent of BR participation
2. LQGs only when they actually reported in the 2021 Biennial Report

## Core source roles

1. RCRA_FACILITIES.csv is now the master source for handler identity, site attributes, TSDF flags, generator status, and final emitted row content.
2. BR_REPORTING_2021.zip is the authoritative source for whether a facility reported in the 2021 biennial cycle.
3. The BR source is used as an ID filter, not as the main provider of output attributes, except possibly for audit or reconciliation outputs.
4. The old HD_REPORTING-first design is obsolete for this rebuild.

## Specific rebuild approach

1. Do not try to incrementally adapt the current HD_REPORTING-centered filtering core. Replace that core deliberately.
2. Keep the existing script entrypoint and local-or-remote runtime pattern if practical so testing stays simple.
3. Keep the output contract stable for downstream proximity code.
4. Rebuild in small slices: source config, source readers, BR reporter set extraction, RCRA filtering, union and dedup, then audits.
5. Use the quick reference script at scripts/hazardous_waste/pipeline/test_data/downloads/quickRCRAfilter.py as evidence for the intended RCRA_FACILITIES plus BR-driven design, but not as production architecture.

## Processing outline
Notes:
- We want to log pertinent counts (usually of input and output rows) for each of the steps below. 
- We don't write "fallback" code for column names. The code should fail fast if a column name we are expecting does not
appear in the input data with a clear log entry. That will allow the user to determine the actual column name and specify
a correction.

1. Load the master handler universe from RCRA_FACILITIES.csv.
   This is the main working table and the source of final handler rows.
2. Build the TSDF subset from RCRA_FACILITIES.
   Select handlers with any operating TSDF activity according to the approved coded-field interpretation.
3. Build the 2021 BR reporter universe from BR_REPORTING_2021.zip.
   Read the two CSV members inside the ZIP, normalize handler IDs, and union them into one distinct reporting set.
4. Build the BR-reporting LQG subset.
   Join the BR reporter ID set back to RCRA_FACILITIES and keep only handlers whose generator status corresponds to LQG.
5. Union TSDF handlers and BR-reporting LQGs.
   A handler should be included if it is in either population.
6. Deduplicate to one row per handler ID.
   Preserve the strongest available canonical row from RCRA_FACILITIES.
7. Emit the same canonical output schema used by downstream proximity processing.


## Important assumptions

1. Current RCRA_FACILITIES attributes are being used to classify present-day TSDF and LQG status.
2. The BR file is the authoritative filter for whether a handler reported in 2021.
3. The LQG portion of the final universe is therefore an approximation of 2021-aligned LQGs based on current master-file classification plus 2021 BR participation.
4. TSDF handlers are included whether or not they appear in the BR reporting set.

## Rewrite scope

1. Update the plan document to describe the new RCRA_FACILITIES-first architecture.
2. Rewrite the preprocess config JSON so it explicitly models:
   a. the master RCRA file
   b. the BR reporting ZIP
   c. the expected BR member CSVs or matching rules
   d. the biennial year
   e. required columns for each source
3. Rewrite the config class and config-loading helpers to reflect multiple distinct source roles instead of an HD_REPORTING-first mental model.
4. Rewrite the preprocessing core to implement the new source readers and population assembly logic.
5. Keep the downstream output shape unchanged unless a later validation step proves that a compatibility adjustment is required.

## Steps

1. Phase 1 - Freeze the new contract before touching code.
   Make the plan and config terminology explicit: RCRA_FACILITIES is the master table, BR_REPORTING is a required reporting filter, and the final output remains the current canonical site CSV.
2. Phase 1 - Separate reusable runtime infrastructure from obsolete source logic.
   Preserve the current local or S3 path handling, environment loading, fail-fast posture, and CSV writing helpers. Treat the HD_REPORTING-specific readers, filters, and provenance rules as replaceable architecture.
3. Phase 1 - Redesign configuration, depends on step 2.
   Define distinct config sections for the master RCRA source and the BR reporting source. Model path, archive details, target year, CSV members, required columns, and any source-specific ID normalization rules directly in config.
4. Phase 1 - Prove source inventory and schema discovery, depends on step 3.
   Validate that the configured RCRA_FACILITIES file exists and exposes the required TSDF, generator-status, ID, and site columns. Validate that BR_REPORTING_2021.zip exists, that both target CSV members are discoverable, and that handler ID columns exist in both.
5. Phase 2 - Build the BR reporter set extractor, depends on step 4.
   Read the two BR CSVs as one reporting universe, normalize handler IDs, and emit a unique set of BR-reporting handler IDs for the configured biennial year. This stage should be independently testable and auditable.
6. Phase 2 - Build the master RCRA row classifier, depends on step 4.
   Implement focused helpers for identifying operating TSDF handlers and LQG handlers from RCRA_FACILITIES using approved column names and coded-value rules.
7. Phase 2 - Build the TSDF subset from RCRA_FACILITIES, depends on step 6.
   Filter the master table to operating TSDF handlers and retain the columns needed for the canonical downstream output.
8. Phase 2 - Build the BR-reporting LQG subset, depends on steps 5 and 6.
   Join the BR reporter ID set to the RCRA master table and keep only LQG handlers from that intersection.
9. Phase 3 - Union and deduplicate the two populations, depends on steps 7 and 8.
   Combine TSDF handlers and BR-reporting LQGs, annotate overlap where useful, and deduplicate to one canonical row per handler ID.
10. Phase 3 - Rebuild validation and provenance rules, depends on step 9.
    Validate handler IDs, coordinates, and required output fields from the master RCRA row used in the final result. Update provenance so it accurately reflects whether a row came from the TSDF path, the BR-reporting-LQG path, or both.
11. Phase 4 - Reattach audit outputs in source-aware form, depends on step 10.
    Adapt parse, validation, and dedup audits so they describe the new two-source pipeline. Audits should distinguish between BR reporter extraction failures and RCRA master-row validation failures.
12. Phase 4 - Finalize output behavior, depends on step 11.
    Emit the same canonical hazardous-waste site CSV expected by downstream proximity processing, with stable field names and one row per handler.

## Relevant files

- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py — main script to rewrite around the RCRA_FACILITIES plus BR_REPORTING design.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess_config.json — config file to redesign for explicit source roles and BR year handling.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\pipeline\test_data\downloads\quickRCRAfilter.py — quick reference for the new source logic; useful for intent, not production structure.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess_validation_plan.md — audit philosophy reference that will need adaptation to the new source flow.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\superfund\template_for_pipeline_processing.py — reference for clean config and runtime structure where still applicable.

## Verification

1. Confirm the configured RCRA_FACILITIES source can be read in local mode and any required remote mode.
2. Confirm BR_REPORTING_2021.zip can be opened and both expected CSV members can be discovered and read.
3. Confirm the BR stage emits a deduplicated set of reporting handler IDs.
4. Confirm the TSDF stage includes handlers with operating TSDF activity even when absent from BR.
5. Confirm the LQG stage includes only handlers that are both LQG in RCRA_FACILITIES and present in the BR reporter set.
6. Confirm the final union contains one row per handler ID.
7. Confirm overlap counts are logged: TSDF-only, BR-reporting-LQG-only, and handlers qualifying through both paths.
8. Confirm the final emitted CSV preserves the existing downstream output contract.
9. Confirm audit outputs, if enabled in the current slice, refer to the new source roles and not to obsolete HD_REPORTING assumptions.

## Decisions

1. RCRA_FACILITIES.csv is the new primary source.
2. BR_REPORTING is required, not optional, for constructing the LQG portion of the final universe.
3. The BR source should be treated as one reporting universe assembled from the two CSVs inside the ZIP.
4. The final output remains unchanged for downstream consumers.
5. The current HD_REPORTING-first preprocessing design should be treated as obsolete for this rebuild.
6. The rewrite should stay incremental and testable: source discovery, BR reporter set, RCRA classification, union and dedup, then audits.

## Further considerations

1. The exact RCRA_FACILITIES column names for operating TSDF and LQG classification need to be frozen in config and validated early.
2. If BR member filenames vary by year, config should describe match rules rather than hardcoded literal names buried in code.
3. If multiple master rows exist for one handler ID, dedup preference rules should be explicit and reviewable.
4. If audit outputs become too tied to the old architecture, simplify them first rather than preserving misleading legacy shapes.

## Minimal dependency sequence

1. Archive access and local or remote file opening: Python standard library plus fsspec.
2. Local and S3 storage abstraction: s3fs in addition to fsspec for remote mode.
3. CSV reads, joins, filtering, and deduplication: pandas.
4. If remote credentials are sourced from .env: python-dotenv.
5. Do not add heavier geospatial or storage dependencies unless the rewrite proves one is strictly necessary.