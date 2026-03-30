## Plan: Hazardous Waste Preprocess Rebuild

Rebuild scripts/hazardous_waste/hazardous_waste_preprocess.py around the current hazardous-waste source-data design rather than trying to extend the legacy HD_HANDLER pipeline. The target pipeline now starts from `HD_REPORTING` as the primary source for identifiers, coordinates, and the working TSDF/LQG logic, with `BR_REPORTING` retained as an optional narrowing source if later testing shows that `HD_REPORTING` alone is too broad. Nested wrapper-zip to inner-zip streaming remains the intended access model for local and S3 roots where applicable. The rebuild should preserve what is still useful from the current script, specifically the config/runtime pattern, the fail-fast posture, and the audit mindset, while replacing the archive-reader and filtering core that is currently hardwired to `HD_HANDLER_*.csv` members.

**Specific rebuild approach**
1. Do not evolve the current member-by-member `HD_HANDLER` reader into the new architecture. Replace that front half deliberately.
2. Keep the same script entrypoint and the same local-or-remote invocation pattern so testing stays simple.
3. Preserve reusable infrastructure first, then rebuild the data pipeline in narrow, testable slices.
4. Reintroduce audits only after the `HD_REPORTING`-first path is proven. Do not let legacy audit plumbing dictate the new source architecture.

**Current checkpoint**
1. The proof slice for archive access and schema discovery now runs to completion.
2. Local and remote invocation plumbing should remain in place, even while testing stays local for now.
3. The next implementation slice should begin actual row-level filtering from `HD_REPORTING`.

**Steps**
1. Phase 1 - Freeze the new contract before touching code.
   Make the script contract explicit in code comments and config names: the pipeline now builds a canonical site CSV from `HD_REPORTING` first, with `BR_REPORTING` as an optional narrowing input, not from `HD_HANDLER` members. Keep the current command-line shape if possible, but rename helper names and comments so they stop implying the obsolete `HD_HANDLER` architecture.
2. Phase 1 - Separate reusable infrastructure from HD_HANDLER-specific logic.
   Identify the pieces of the current script that can survive mostly unchanged: config parsing, local/S3 path joining, fsspec-based open helpers, CSV writing helpers, and high-level run summary structure. Treat member enumeration, row-level sieve logic, and provenance fields tied to `source_member_filename` as architecture that must be rebuilt or generalized.
3. Phase 1 - Introduce explicit source descriptors, depends on step 2.
   Add configuration for each required component: wrapper archive path, inner ZIP member name if applicable, target CSV filename, and the target biennial year for `BR_REPORTING`. The code should stop assuming one input archive and start modeling the actual active sources with distinct schemas and roles.
4. Phase 1 - Build nested ZIP access helpers, depends on step 3.
   Add focused helpers that can open a wrapper ZIP via fsspec, inspect its member list, open the inner ZIP member as a binary stream, and then expose the contained CSV as a text stream. Keep these helpers fail-fast and specific to the known archive structure. This is the first proof slice for the new design.
5. Phase 1 - Prove source inventory and schema discovery, depends on step 4.
   For each active source component, log the discovered wrapper archive, inner ZIP member, target CSV, and key columns needed downstream. Hard-fail on missing files, duplicate unexpected matches, or missing required columns. This milestone should work the same for local storage and S3.
6. Phase 2 - Implement HD_REPORTING universe extraction, depends on step 5.
   Stream `HD_REPORTING`, validate the required fields, and produce the set of `HANDLER_ID`s that satisfy the approved universe logic: `GENSTATUS == 'LQG'` or `OPERATING TSDF` contains a meaningful code rather than an all-dash placeholder. Keep this logic in pure helper functions so it can be tested and reviewed in isolation.
7. Phase 2 - Implement provisional site extraction directly from HD_REPORTING, depends on step 6.
   Retain the site-level fields needed downstream from the same filtered `HD_REPORTING` rows, especially `HANDLER ID`, `HANDLER NAME`, `LOCATION LATITUDE`, and `LOCATION LONGITUDE`. This is the first slice that produces a usable site table.
8. Phase 2 - Define canonical row validation and provenance, depends on step 7.
   Introduce content validation rules that match the current architecture: valid `HANDLER_ID`, usable coordinates, recognizable `GENSTATUS`, and interpretable `OPERATING TSDF`. Replace provenance that assumed one `HD_HANDLER` member row with provenance that names the contributing dataset and source row location from `HD_REPORTING`.
9. Phase 3 - Rebuild deduplication for the HD_REPORTING-derived site table, depends on step 8.
   Deduplicate the filtered site table so the final output contains one row per `HANDLER_ID`. This dedup step should prefer the strongest available record when multiple reporting rows survive the filter.
10. Phase 3 - Add optional BR narrowing logic, depends on step 9.
   Read only the fields needed from `BR_REPORTING`, normalize `HANDLER_ID`, and produce a unique set of IDs for the target biennial cycle. Apply that set only as an explicitly optional tightening pass so we can compare before-and-after universes empirically.
11. Phase 3 - Add reconciliation logging for the optional BR pass, depends on step 10.
   When the BR narrowing path is enabled, log how many handlers are in the provisional `HD_REPORTING` universe, how many are in the BR year-specific set, how many overlap, and how many sites remain after tightening.
12. Phase 4 - Reattach audit outputs in source-aware form, depends on step 11.
   Adapt the existing parse, validation, and dedup audit model so it matches the current pipeline. Parse and validation audits may need to be emitted per dataset stage rather than pretending every failure came from one `HD_HANDLER` member. Preserve the same fail-fast and traceability goals, but do not force the old audit column names if they become misleading.
13. Phase 4 - Finalize output behavior, depends on step 12.
   Publish one canonical hazardous-waste site CSV for downstream proximity work, plus any approved audit artifacts. Keep the proximity contract simple: downstream code should consume only the canonical site file and should not need to understand archive topology or source-specific filter logic.

**Relevant files**
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py — current implementation to rebuild around the current `HD_REPORTING`-first architecture.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_source_data.md — governing design decision record for source roles, filter logic, and assembly workflow.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess_validation_plan.md — source of the current audit philosophy; useful, but it must be adapted to the new source model.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\superfund\template_for_pipeline_processing.py — reuse its config/runtime organization where it still improves clarity.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\utilities\analyze_raw_TSDF_file.py — reference for prior hazardous-waste column assumptions only; do not let it dictate the new architecture.

**Verification**
1. After step 4, prove that one local root and one `s3://` root can both enumerate and open the required wrapper archive and inner ZIP member for each source component.
2. After step 5, confirm the required schemas are present in `HD_REPORTING` and the target `BR_REPORTING` file, and that the script aborts cleanly on any mismatch.
3. After step 6, confirm the `HD_REPORTING` stage produces the expected LQG-or-TSDF universe using the approved working logic.
4. After step 7, confirm every emitted provisional site row comes directly from `HD_REPORTING` and carries the needed identifiers, names, and coordinates.
5. After step 9, confirm the canonical output has at most one row per `HANDLER_ID`.
6. After step 10, if BR tightening is enabled, confirm the `BR_REPORTING` stage produces a deduplicated `HANDLER_ID` set for the requested year.
7. After step 11, inspect overlap and shrinkage counts for the optional BR pass before deciding whether to keep it.
8. After step 12, confirm the audit outputs, if enabled in that slice, describe the new dataflow correctly and do not refer to obsolete `HD_HANDLER` member provenance.

**Decisions**
- Nested wrapper-zip to inner-zip streaming is the target design, even if implementation may temporarily defer a piece of it for practicality.
- Rebuild the pipeline in the existing script file rather than creating a second competing script.
- Preserve the current local/S3 abstraction and fail-fast behavior.
- Treat the current `HD_HANDLER`-specific processing core as obsolete architecture, not as a base to incrementally generalize.
- Keep the new build incremental and testable: first archive access, then `HD_REPORTING` filtering and site extraction, then canonicalization, then optional BR tightening, then audits.
- Do not start proximity-side changes until the preprocessing rebuild approach is approved and the canonical output contract is stable.

**Further Considerations**
1. The current three-audit model is still conceptually useful, but some audit columns and provenance names will likely need to become dataset-aware instead of member-aware.
2. If nested ZIP access proves painful with direct streaming for a specific component, keep the fallback narrow and explicit rather than silently changing the whole storage model.
3. `BR_REPORTING` may not live in the exact same wrapper-zip pattern as `HD_REPORTING`; model that difference directly in config instead of burying it in special cases.
4. If multiple `HD_REPORTING` rows survive for the same handler, keep the dedup preference rules explicit and reviewable rather than letting file order decide silently.

**Minimal Dependency Sequence**
1. Archive inspection and nested ZIP streaming helpers: Python standard library plus `fsspec`.
2. Local and S3 storage abstraction: `s3fs` in addition to `fsspec` for remote mode.
3. Chunked CSV reads and joins: `pandas`.
4. If remote credentials are sourced from `.env`: `python-dotenv`.
5. Do not add geopandas, shapely, pyarrow, or boto3 unless the rebuild proves one is strictly necessary.