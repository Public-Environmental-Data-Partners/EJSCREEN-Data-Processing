## Plan: Hazardous Waste Preprocess

Build scripts/hazardous_waste/hazardous_waste_preprocess.py as a narrow, stepwise pipeline that first proves one-layer ZIP access works the same for local paths and S3, then layers in CSV member inspection, CSV header access, chunked reading, sieve logic, one-member-CSV local output, one-member-CSV AWS output, and only then expands to all `HD_HANDLER_*.csv` members in the archive. Reuse the config/runtime structure from the superfund template where it helps readability and testability, but use fsspec+s3fs plus zipfile as the primary storage/access path so local and remote runs exercise the same code path. Fail early on missing files, wrong schema, or storage issues; do not add broad fallbacks. The script should grow in place across slices, keeping the same basic invocation pattern rather than adding a mode selector for old milestones.

**Steps**
1. Phase 1 - Scaffold the script shell in c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py.
   Add a Config dataclass and get_config() pattern modeled after template_for_pipeline_processing.py, but define only the settings needed for this pipeline: root URI, outer ZIP name, output URI or prefix, chunk size, and any fixed filename defaults. Keep logging simple and noisy. Do not add a slice selector or backward-compatibility runner for earlier milestones.
2. Phase 1 - Implement path and storage helpers, depends on step 1.
   Reuse the template’s structure around config and path joining, but switch the actual data-access path to fsspec.open() so both local and s3:// inputs are read through the same abstraction. Add one focused helper to open the outer ZIP as a binary stream and another to normalize joined paths. Do not add boto3-only read/write branches for the main pipeline.
3. Phase 1 - First proof slice: enumerate archive members only, depends on step 2.
   Open the outer ZIP, list entries, filter to the expected `HD_HANDLER_*.csv` members, and log the names plus count. No CSV reading yet. This is the first user-facing test milestone and should be usable against both local storage and S3 with identical behavior using the same script arguments pattern we keep throughout development.
4. Phase 1 - Add explicit schema and structure assertions for the archive layout, depends on step 3.
   Hard-fail if the expected `HD_HANDLER_*.csv` inventory is missing, empty, duplicated unexpectedly, or named differently than assumed. This keeps later steps specific to the known input rather than defensive for many variants.
5. Phase 2 - Second proof slice: open one CSV member and inspect it, depends on step 4.
   Open the first expected `HD_HANDLER_*.csv` member from inside the outer ZIP without extraction, confirm that it is readable as a CSV stream, and log enough information to prove we are looking at the expected structure. This isolates ZIP-to-CSV streaming before introducing pandas-heavy logic.
6. Phase 2 - Third proof slice: read CSV header only from one member CSV, depends on step 5.
   Open the member CSV as a stream and read just enough to capture the header row and column names. Log the column count and selected field names required by the sieve: CURRENT RECORD, INCLUDE IN NATIONAL REPORT, TSD ACTIVITY, FED WASTE GENERATOR, LOCATION LONGITUDE, LOCATION LATITUDE. Hard-fail if any required columns are missing.
7. Phase 2 - Fourth proof slice: chunk-read one member CSV without filtering, depends on step 6.
   Use pandas.read_csv(..., chunksize=...) on the streamed member CSV and log chunk counts and row totals for one file only. This proves the memory model and installed dependencies before any filtering logic is introduced.
8. Phase 3 - Port the sieve logic into isolated worker functions, depends on step 7.
   Lift the actual hazardous-waste filtering rules from analyze_raw_TSDF_file.py into small pure functions such as required-column validation, create_filter_masks(), and apply_filter_to_chunk(). Preserve the exact logic: CURRENT RECORD == Y, INCLUDE IN NATIONAL REPORT == Y, and TSD ACTIVITY == Y or FED WASTE GENERATOR == 1. Do not add geospatial conversion yet.
9. Phase 3 - Fifth proof slice: filter one member CSV and report counts only, depends on step 8.
   Run the chunked sieve over one member CSV, accumulate only per-chunk and total survivor counts, and log the yield. This gives a clean checkpoint that the filter logic matches expectations before file output is added.
10. Phase 3 - Sixth proof slice: write filtered rows from one member CSV to a local CSV output, depends on step 9.
    Start with full source columns preserved. Write the header once, append surviving chunk rows, and verify the result locally first. Keep file-handle ownership explicit and keep the processing and output concerns separated so the next slice can target AWS without reworking the sieve.
11. Phase 3 - Seventh proof slice: write filtered rows from one member CSV to AWS output, depends on step 10.
   Using the same one-member-CSV processing logic, add S3 output writing and validate that remote output works cleanly. Treat this as its own milestone because AWS output is a distinct technical risk. If append semantics are awkward on S3, choose the simplest implementation that still preserves correctness for one-member-CSV testing and document that choice in the code and plan.
12. Phase 4 - Generalize from one member CSV to all `HD_HANDLER_*.csv` members into one consolidated output, depends on step 11.
   Loop over all enumerated CSV members, stream/filter each CSV in turn, append to the same output target, and maintain total input and survivor counts. Explicitly close member streams and ZIP handles between iterations. Because one-member-CSV local and AWS paths are already proven, this step should mostly be expansion rather than architectural change.
13. Phase 4 - Finalize operational behavior, depends on step 12.
    Add end-of-run summary logging, argument validation, and a clear nonzero exit on any failure. Keep the script intentionally specific to this archive structure and avoid recovery branches or silent skipping.

**Relevant files**
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\hazardous_waste_preprocess.py — script being built in incremental slices; Slice 2 currently inventories `HD_HANDLER_*.csv` members and inspects the first member header preview from the outer ZIP.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\hazardous_waste\processingSieveDesignPromptForCopilot.md — source of the staged streaming architecture and chunked iteration ideas, though the current confirmed local archive uses one layer of zipping rather than ZIP-within-ZIP.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\superfund\template_for_pipeline_processing.py — reuse the Config dataclass, get_config(), logging setup, and helper-organization pattern; adapt away from boto3-first I/O toward fsspec-first I/O.
- c:\openSource\dataPreservation\EJSCREEN-Data-Processing\scripts\utilities\analyze_raw_TSDF_file.py — source of the exact sieve/filter logic and required column names; do not reuse its single-file, in-memory architecture.

**Verification**
1. After step 3, run the script against one local root and one s3:// root and confirm the same `HD_HANDLER_*.csv` member names and counts are logged.
2. After step 5, confirm the selected member CSV is readable from the outer ZIP and matches the actual archive contents.
3. After step 6, verify the required column names are discovered exactly as expected and the script aborts cleanly if a required field is missing.
4. After step 7, compare the one-file total row count from chunked reading to the CSV’s expected total to confirm no rows are dropped before filtering.
5. After step 9, compare survivor counts from the new one-file chunked filter with the exploratory result from analyze_raw_TSDF_file.py for the same source file.
6. After step 10, inspect the one-file local output CSV to confirm header-once behavior and that full source columns were preserved.
7. After step 11, repeat the same one-file test with S3 output and confirm the AWS-written file matches the locally written result.
8. After step 12, confirm total output row count equals the sum of survivors from all member-CSV runs and that only one consolidated CSV is produced.

**Decisions**
- The first milestone is enumeration only: log `HD_HANDLER_*.csv` member names and counts, with no persisted inventory file.
- Use fsspec plus s3fs as the primary local/S3 abstraction.
- Early output should keep full source columns rather than trimming schema immediately.
- Move AWS output earlier: prove one-member-CSV local output first, then one-member-CSV AWS output, then generalize to all `HD_HANDLER_*.csv` members.
- Fail hard and early on missing files, wrong structure, or schema mismatches.
- Defer any GeoDataFrame or geometry conversion until the filtered CSV workflow is already proven.
- Grow the same script and same basic invocation pattern over time; do not add independent slice-running modes.
- Accept that early slices may be partially throwaway; prioritize confidence-building checkpoints over building the final architecture in one pass.

**Further Considerations**
1. If the real archive contains additional non-target files, lock the expected `HD_HANDLER_*.csv` filename pattern during step 5 before any filtering code is written.
2. For the one-member-CSV AWS write milestone, decide implementation based on actual S3 append constraints: either a simple one-shot write of accumulated surviving rows for that single member CSV or a streaming-compatible approach if testing shows it is straightforward.
3. If dependency installation needs to stay especially conservative, treat fsspec-only local enumeration as a pre-step and add s3fs only before the first remote test.

**Minimal Dependency Sequence**
1. Initial scaffold and local outer-ZIP enumeration: Python standard library only, specifically argparse, dataclasses, logging, pathlib, io, and zipfile.
2. Local CSV header reads and chunked CSV iteration: add pandas.
3. Local and S3 access through one storage abstraction: add fsspec.
4. Actual s3:// support for remote input and output tests: add s3fs.
5. If AWS credentials are loaded from a local .env file rather than the shell or AWS profile chain: add python-dotenv.
6. Do not add geopandas, shapely, pyarrow, or boto3 for this preprocessing plan unless a later implementation detail proves one of them is strictly necessary.