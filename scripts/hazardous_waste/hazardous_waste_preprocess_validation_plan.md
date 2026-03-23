## Plan: Hazardous Waste Preprocess Audits

Enhance scripts/hazardous_waste/hazardous_waste_preprocess.py so it validates raw HD_HANDLER member rows before they enter the main filter flow, separates structural parse failures from post-parse data-quality failures and from deduplication outcomes, and preserves original source provenance for every audited row. The resulting pipeline should emit one canonical site CSV plus three distinct audit outputs: parse audit, validation audit, and dedup audit. Every audit record should include the original member filename and original logical row number from that member. Only rows that pass parse validation and content validation should be eligible for the canonical dataset.

**Steps**
1. Phase 1 - Reframe the preprocessing pipeline into four stages.
   Make the pipeline stages explicit: member enumeration, raw-row parse validation, post-parse content validation plus sieve, and final deduplication. This separates structural CSV problems from business-rule failures and keeps the proximity input contract clear.
2. Phase 1 - Add a parse-validation stage at the member-reader boundary, depends on step 1.
   Intercept rows before they are handed to the main filter flow. Use a parser path that can identify logical CSV records from each HD_HANDLER member and determine whether each record resolves to the expected column count. Records that fail structural parse validation should not enter the filtered provisional dataset.
3. Phase 1 - Preserve source provenance from the start, depends on step 2.
   For every successfully parsed row, attach provenance fields before downstream filtering: original member filename and original logical row number within that member. Carry these fields through provisional output and all audit outputs so any downstream issue can be traced back to the source member and source row.
4. Phase 1 - Define the three audit artifacts and canonical artifact, depends on step 3.
   Use four output families under the active root:
   - canonical filtered+deduped CSV: outputs/hazardous_waste_filtered.csv
   - parse audit CSV: outputs/hazardous_waste_parse_audit.csv
   - validation audit CSV: outputs/hazardous_waste_validation_audit.csv
   - dedup audit CSV: outputs/hazardous_waste_dedup_audit.csv
   Keep the existing provisional filtered artifact only if it still serves debugging value; if retained, treat it as an internal staging artifact rather than a peer audit product.
5. Phase 1 - Define parse-audit behavior, depends on step 4.
   Parse audit should capture rows that fail structural CSV expectations before dataframe processing. Each parse-audit row should include, at minimum, original member filename, original logical row number, parse error classification, and the raw record text or a safe excerpt sufficient for debugging. These rows are rejected from all downstream processing.
6. Phase 1 - Define validation-audit behavior, depends on step 4.
   Validation audit should capture rows that parse correctly into the expected schema but fail required downstream content checks. This includes rows with blank or unusable HANDLER ID, irrecoverably invalid coordinates when coordinates are required for site use, or any other row-level content failure that should exclude the record from canonical output. These rows are not parse failures and should not be mixed with dedup outcomes.
7. Phase 1 - Keep the sieve only for structurally valid rows, depends on step 6.
   Apply the current hazardous-waste filter logic only after raw rows have passed parse validation and have been turned into a trusted dataframe shape. This keeps multiline quoted notes and similar messy-but-valid text in scope while excluding structurally broken records.
8. Phase 1 - Rework provisional output to retain provenance, depends on step 7.
   If the provisional filtered artifact remains in the design, include original member filename and original member row number columns in it. This ensures dedup and later investigations can always trace a row back to the source without relying on provisional line numbers alone.
9. Phase 1 - Rework dedup finalization to audit only dedup outcomes, depends on step 8.
   Keep dedup audit focused on duplicates, coordinate conflicts, and tie-break selections among already-validated rows. The dedup audit should include the original member filename and original member row number from the retained provenance fields for every competing row in a duplicate group.
10. Phase 1 - Update exact-duplicate handling, depends on step 9.
    Continue removing exact duplicates before site-level tie-break logic, but treat exact-duplicate removals as a dedup outcome and include enough provenance in the dedup audit or summary logging to show which source rows collapsed together. This prevents exact-duplicate disappearances from becoming invisible.
11. Phase 1 - Tighten publication rules, depends on step 9.
    Only rows that pass parse validation, pass content validation, survive the sieve, and survive dedup finalization may appear in the canonical CSV. Parse-audit and validation-audit rows are rejected before canonical eligibility; dedup-audit rows may include the retained canonical row flagged as such plus the discarded competitors.
12. Phase 1 - Expand summary logging, depends on step 11.
    Log counts for raw records examined, parse failures, validation failures, sieve survivors, exact duplicates removed, duplicate groups, coordinate conflicts, and final canonical rows. This becomes the run-level reconciliation summary.
13. Phase 2 - Keep the proximity contract simple, depends on step 12.
    Update the hazardous-waste proximity plan to consume only the canonical output and treat it as structurally validated, content validated, and deduplicated. The proximity code should not need to solve parse ambiguity or audit classification problems.

**Relevant files**
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py — target for adding raw-row parse validation, provenance capture, and the three audit paths.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess_dedup_plan.md — prior dedup-only plan that should be superseded or extended by the three-audit design.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\design_decisions\hazardous_waste_source_data.md — source-data rationale and filter assumptions that define the post-parse sieve.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\utilities\analyze_raw_TSDF_file.py — reference for expected columns and hazardous-waste filter assumptions.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\pipeline\test_data\outputs\hazardous_waste_filtered_pre_dedup.csv — useful comparison artifact for understanding how messy-but-valid multiline field content currently appears after parsing.

**Verification**
1. Confirm every parse-audit row contains original member filename and original member row number, and that none of those rows appear in validation audit, dedup audit, provisional filtered output, or canonical output.
2. Confirm rows with quoted embedded newlines that still parse to the expected schema are accepted into the main flow rather than misclassified as parse failures.
3. Confirm rows with structural parse failures are rejected before sieve logic and written to the parse audit with enough raw context to debug the source file.
4. Confirm validation-audit rows contain original member filename and original member row number and represent post-parse content failures only, not parse failures or dedup outcomes.
5. Confirm dedup-audit rows contain original member filename and original member row number for every competing candidate in a duplicate group.
6. Confirm the canonical CSV contains only rows that passed parse validation and content validation and that duplicate HANDLER ID values are still eliminated by finalization.
7. Confirm run-level summary counts reconcile: raw records = parse failures + structurally valid rows; structurally valid rows = validation failures + rows entering sieve; sieve survivors = dedup rejects plus canonical rows.
8. If the provisional filtered artifact is retained, confirm its provenance fields are sufficient to trace any canonical or dedup-audit row back to the original member and row.

**Decisions**
- Approved direction: valid site information is a prerequisite for valid proximity calculations, so preprocessing should absorb the structural-data-quality burden rather than pushing it downstream.
- Approved audit model: three distinct audits with separate meanings.
  parse audit = raw records that fail structural CSV parsing
  validation audit = rows that parse structurally but fail required content checks
  dedup audit = rows or groups that survive parsing and validation but are involved in exact-duplicate removal or site-level dedup decisions
- Approved provenance requirement: every audit output must include original member filename and original logical row number from that member.
- Recommended provenance behavior: carry original member filename and original member row number through the provisional filtered artifact as well, so downstream debugging and dedup tracing do not depend only on provisional file line numbers.
- Recommended parse rule: if a raw row resolves to the expected schema with a correct CSV parser, accept it as structurally valid even if a field contains embedded CR or LF characters.
- Recommended rejection rule: if a raw row does not resolve to the expected schema, reject it from the main flow and write it only to parse audit.
- Excluded from this plan: any attempt to repair malformed source rows automatically; the focus is classify, reject where appropriate, and preserve enough information for review.

**Further Considerations**
1. The implementation may need a lower-level CSV parsing path than the current direct pandas chunk reader if pandas cannot reliably expose failed-record provenance at the granularity you want.
2. If raw parse-audit rows contain sensitive or excessively long text, store a bounded raw excerpt plus error classification rather than the entire raw record, but keep enough context for diagnosis.
3. Once this three-audit model is implemented, revisit whether the provisional filtered artifact still needs to be persisted or whether canonical plus audits is sufficient for routine runs.