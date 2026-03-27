## Plan: Hazardous Waste Preprocess Rebuild

Rebuild scripts/hazardous_waste/hazardous_waste_preprocess.py around the current hazardous-waste source-data design rather than trying to extend the legacy HD_HANDLER pipeline. The target pipeline assembles one canonical hazardous-waste site table from three relational components: `HD_BASIC` for authoritative coordinates and handler names, `HD_REPORTING` for regulatory-universe flags, and `BR_REPORTING` for recent reporting activity. Nested wrapper-zip to inner-zip streaming is the intended access model for both local and S3 roots. The rebuild should preserve what is still useful from the current script, specifically the config/runtime pattern, the fail-fast posture, and the audit mindset, while replacing the archive-reader and filtering core that is currently hardwired to `HD_HANDLER_*.csv` members.

**Specific rebuild approach**
1. Do not evolve the current member-by-member `HD_HANDLER` reader into the new architecture. Replace that front half deliberately.
2. Keep the same script entrypoint and the same local-or-remote invocation pattern so testing stays simple.
3. Preserve reusable infrastructure first, then rebuild the data pipeline in narrow, testable slices.
4. Reintroduce audits only after the three-source assembly path is proven. Do not let legacy audit plumbing dictate the new source architecture.

**Steps**
1. Phase 1 - Freeze the new contract before touching code.
   Make the script contract explicit in code comments and config names: the pipeline now builds a canonical site CSV from `HD_BASIC`, `HD_REPORTING`, and `BR_REPORTING`, not from `HD_HANDLER` members. Keep the current command-line shape if possible, but rename config fields and helper names so they stop implying a single outer archive and a single member pattern.
2. Phase 1 - Separate reusable infrastructure from HD_HANDLER-specific logic.
   Identify the pieces of the current script that can survive mostly unchanged: config parsing, local/S3 path joining, fsspec-based open helpers, CSV writing helpers, and high-level run summary structure. Treat member enumeration, row-level sieve logic, and provenance fields tied to `source_member_filename` as architecture that must be rebuilt or generalized.
3. Phase 1 - Introduce explicit source descriptors, depends on step 2.
   Add configuration for each required component: wrapper archive path, inner ZIP member name if applicable, target CSV filename, and the target biennial year for `BR_REPORTING`. The code should stop assuming one input archive and start modeling three source streams with distinct schemas and roles.
4. Phase 1 - Build nested ZIP access helpers, depends on step 3.
   Add focused helpers that can open a wrapper ZIP via fsspec, inspect its member list, open the inner ZIP member as a binary stream, and then expose the contained CSV as a text stream. Keep these helpers fail-fast and specific to the known archive structure. This is the first proof slice for the new design.
5. Phase 1 - Prove source inventory and schema discovery, depends on step 4.
   For each of the three source components, log the discovered wrapper archive, inner ZIP member, target CSV, and key columns needed downstream. Hard-fail on missing files, duplicate unexpected matches, or missing required columns. This milestone should work the same for local storage and S3.
6. Phase 2 - Implement BR universe extraction, depends on step 5.
   Read only the fields needed from `BR_REPORTING`, normalize `HANDLER_ID`, and produce a unique set of IDs for the target biennial cycle. This stage should be independently testable and should not depend on `HD_REPORTING` or `HD_BASIC` yet.
7. Phase 2 - Implement HD_REPORTING universe extraction, depends on step 5.
   Stream `HD_REPORTING`, validate the required regulatory fields, and produce the set of `HANDLER_ID`s that satisfy the approved universe logic: operating TSDF, `TSDF_UNIVERSE` present, or `LQG_UNIVERSE` present. Keep this logic in pure helper functions so it can be tested and reviewed in isolation.
8. Phase 2 - Build the union universe artifact in memory or as a lightweight intermediate, depends on steps 6 and 7.
   Combine the `HD_REPORTING` and `BR_REPORTING` ID sets into one deduplicated candidate universe keyed by `HANDLER_ID`. Add simple reconciliation logging so we know how many IDs came from each source and how many overlap.
9. Phase 3 - Implement HD_BASIC join and site extraction, depends on step 8.
   Stream `HD_BASIC`, validate the coordinate and handler-name fields, and keep only rows whose `HANDLER_ID` is present in the union universe. This is where the pipeline stops being an ID-only workflow and becomes a site table with names and coordinates.
10. Phase 3 - Define canonical row validation and provenance, depends on step 9.
   Introduce content validation rules that match the new architecture: valid `HANDLER_ID`, usable coordinates, and any other required site-level fields. Replace provenance that assumed one `HD_HANDLER` member row with provenance that names the contributing dataset and source row location from `HD_BASIC`, and if useful, separately records whether the qualifying universe signal came from `HD_REPORTING`, `BR_REPORTING`, or both.
11. Phase 3 - Rebuild deduplication for the joined site table, depends on step 10.
   Deduplicate after the `HD_BASIC` join so the final output contains one row per `HANDLER_ID`. This is a different role than the current dedup step. It is now resolving multiple registry rows per handler after relational assembly, not collapsing duplicate streamed member rows from one archive format.
12. Phase 4 - Reattach audit outputs in source-aware form, depends on step 11.
   Adapt the existing parse, validation, and dedup audit model so it matches the new three-source pipeline. Parse and validation audits may need to be emitted per dataset stage rather than pretending every failure came from one `HD_HANDLER` member. Preserve the same fail-fast and traceability goals, but do not force the old audit column names if they become misleading.
13. Phase 4 - Finalize output behavior, depends on step 12.
   Publish one canonical hazardous-waste site CSV for downstream proximity work, plus any approved audit artifacts. Keep the proximity contract simple: downstream code should consume only the canonical site file and should not need to understand archive topology or source-specific filter logic.

**Relevant files**
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py — current implementation to rebuild around the new three-source architecture.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_source_data.md — governing design decision record for source roles, filter logic, and assembly workflow.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess_validation_plan.md — source of the current audit philosophy; useful, but it must be adapted to the new source model.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\superfund\template_for_pipeline_processing.py — reuse its config/runtime organization where it still improves clarity.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\utilities\analyze_raw_TSDF_file.py — reference for prior hazardous-waste column assumptions only; do not let it dictate the new architecture.

**Verification**
1. After step 4, prove that one local root and one `s3://` root can both enumerate and open the required wrapper archive and inner ZIP member for each source component.
2. After step 5, confirm the required schemas are present in `HD_BASIC`, `HD_REPORTING`, and the target `BR_REPORTING` file, and that the script aborts cleanly on any mismatch.
3. After step 6, confirm the `BR_REPORTING` stage produces a deduplicated `HANDLER_ID` set for the requested year.
4. After step 7, confirm the `HD_REPORTING` stage produces the expected regulatory-universe ID set using the approved union logic.
5. After step 8, log and inspect counts for `BR` IDs, `HD_REPORTING` IDs, overlap, and final union size.
6. After step 9, confirm every emitted site row came from `HD_BASIC` and has a `HANDLER_ID` present in the union universe.
7. After step 11, confirm the canonical output has at most one row per `HANDLER_ID`.
8. After step 12, confirm the audit outputs, if enabled in that slice, describe the new dataflow correctly and do not refer to obsolete `HD_HANDLER` member provenance.

**Decisions**
- Nested wrapper-zip to inner-zip streaming is the target design, even if implementation may temporarily defer a piece of it for practicality.
- Rebuild the pipeline in the existing script file rather than creating a second competing script.
- Preserve the current local/S3 abstraction and fail-fast behavior.
- Treat the current `HD_HANDLER`-specific processing core as obsolete architecture, not as a base to incrementally generalize.
- Keep the new build incremental and testable: first archive access, then source-specific ID extraction, then join, then canonicalization, then audits.
- Do not start proximity-side changes until the preprocessing rebuild approach is approved and the canonical output contract is stable.

**Further Considerations**
1. The current three-audit model is still conceptually useful, but some audit columns and provenance names will likely need to become dataset-aware instead of member-aware.
2. If nested ZIP access proves painful with direct streaming for a specific component, keep the fallback narrow and explicit rather than silently changing the whole storage model.
3. `BR_REPORTING` may not live in the exact same wrapper-zip pattern as `HD_BASIC` and `HD_REPORTING`; model that difference directly in config instead of burying it in special cases.
4. If the `HD_BASIC` table is too large for a naive in-memory join with the union ID set, keep the ID set in memory and stream `HD_BASIC` in chunks rather than loading the full registry at once.

**Minimal Dependency Sequence**
1. Archive inspection and nested ZIP streaming helpers: Python standard library plus `fsspec`.
2. Local and S3 storage abstraction: `s3fs` in addition to `fsspec` for remote mode.
3. Chunked CSV reads and joins: `pandas`.
4. If remote credentials are sourced from `.env`: `python-dotenv`.
5. Do not add geopandas, shapely, pyarrow, or boto3 unless the rebuild proves one is strictly necessary.