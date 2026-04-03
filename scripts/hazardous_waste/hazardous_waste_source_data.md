# Design Decision Record: Hazardous Waste Proximity Source Data and Filtering

**Date:** 2026-04-02
**Indicator:** Hazardous Waste Proximity (TSDF & LQG)
**Target Version:** EJScreen compatibility rebuild

---

### 1. Problem Statement

The hazardous-waste proximity indicator needs a national site list that is both geographically usable and narrower than the full RCRAInfo universe. The population we care about has two components:
- **TSDF sites**: handlers with operating treatment, storage, or disposal activity.
- **LQG sites**: large quantity generators, but only where current source files support a defensible tie to actual biennial-report participation.

The current preprocessing goal is to build one deduplicated national handler list with stable downstream fields for proximity scoring while using the best currently available source files and explicit fail-fast schema validation.

---

### 2. Current Source Decision

The current implementation is now **RCRA_FACILITIES-first**, with **BR_REPORTING_2021** as a required secondary filter for the LQG branch.

#### 2.1 Master source: `RCRA_FACILITIES.csv`

`RCRA_FACILITIES.csv` is the authoritative master table for:
- handler identity
- handler name
- city, state, ZIP
- latitude and longitude
- TSDF status
- generator status
- active-site status
- final emitted row content

The current required columns are:
- `ID_NUMBER`
- `FACILITY_NAME`
- `OPERATING_TSDF`
- `ACTIVE_SITE`
- `HREPORT_UNIVERSE_RECORD`
- `FED_WASTE_GENERATOR`
- `CITY_NAME`
- `STATE_CODE`
- `ZIP_CODE`
- `LATITUDE83`
- `LONGITUDE83`

#### 2.2 Secondary source: `BR_REPORTING_2021.zip`

`BR_REPORTING_2021.zip` is the authoritative source for identifying which handlers actually reported in the 2021 biennial cycle.

The current implementation treats the BR ZIP as one reporting universe assembled from the matching CSV members inside the archive. The BR source is used primarily as a handler-ID filter, not as the source of final output attributes.

The current required BR columns are:
- `HANDLER ID`
- `REPORT CYCLE`


---

### 3. Current Population Logic

The final population is the union of two subsets built from `RCRA_FACILITIES.csv`:

1. **Operating TSDF handlers** from the RCRA master file.
2. **BR-reporting LQG handlers** that both:
    - appear in the 2021 BR reporting universe, and
    - are classified as LQG in the RCRA master file.

This means:
- TSDF handlers are included whether or not they appear in BR reporting.
- LQG handlers are included only when they also appear in the 2021 BR reporting set.

---

### 4. Current Classification Rules

The current code uses the following source-specific rules.

#### 4.1 Active-site rule in `RCRA_FACILITIES`

Use `ACTIVE_SITE` as a coded field.

Current interpretation:
- a row is treated as active when `ACTIVE_SITE` contains alphabetic code content
- blank or non-coded values do not qualify as active

#### 4.2 Operating TSDF rule in `RCRA_FACILITIES`

Use `OPERATING_TSDF` as a coded field.

Current interpretation:
- a row is treated as TSDF when `OPERATING_TSDF` contains alphabetic code content
- blank or placeholder-only values do not qualify

In practice this is a coded-field interpretation, not a simple `Y/N` flag.

#### 4.3 LQG rule in `RCRA_FACILITIES`

Use `HREPORT_UNIVERSE_RECORD` as the LQG indicator.

Current interpretation:
- split the field on commas
- normalize pieces to uppercase trimmed status tokens
- treat a row as LQG when one of the tokens is `LQG`

This allows values such as combined statuses to qualify when they explicitly include `LQG`.

#### 4.4 BR reporting rule

Use `BR_REPORTING_2021.zip` only to build the reporter universe.

Current interpretation:
- read all configured `BR_REPORTING_2021_*.csv` members
- normalize `HANDLER ID`
- keep only rows where `REPORT CYCLE == '2021'`
- union retained handler IDs into one distinct BR reporter set

Rows in the target BR year with blank handler IDs are excluded from the reporter set and written to validation audit output.

#### 4.5 Coordinate rule

The final site list requires usable coordinates.

Current interpretation:
- use `LATITUDE83` and `LONGITUDE83` from `RCRA_FACILITIES`
- coordinates must parse as numeric values
- coordinates must not be `0.0`

Rows that otherwise qualify for the TSDF or BR-reporting-LQG populations but fail coordinate checks are rejected from the emitted site universe and written to validation audit output.

---

### 5. Current Data Assembly Workflow

The active workflow is now:

1. **Inventory and validate source schemas**
    Confirm that `RCRA_FACILITIES.csv` and `BR_REPORTING_2021.zip` exist and expose the required columns. The code fails fast on missing files, missing members, or missing column names.

2. **Build the BR reporter set**
    Read the BR ZIP members, keep only `REPORT CYCLE == 2021`, normalize handler IDs, and union them into one distinct reporter set.

3. **Build the TSDF subset from `RCRA_FACILITIES`**
    Keep rows that satisfy both:
    - active-site rule
    - operating-TSDF rule

4. **Build the BR-reporting LQG subset from `RCRA_FACILITIES`**
    Keep rows that satisfy both:
    - handler ID appears in the BR reporter set
    - `HREPORT_UNIVERSE_RECORD` includes `LQG`

5. **Validate retained rows at site level**
    Reject rows with missing normalized handler IDs or unusable coordinates.

6. **Deduplicate within each subset for audit clarity**
    If multiple qualifying RCRA rows survive for the same `HANDLER ID` within the TSDF path or the BR-reporting-LQG path, the code keeps the strongest row and writes the duplicate group to dedup audit output.

7. **Union the two subsets**
    Merge TSDF rows and BR-reporting-LQG rows on `HANDLER ID`.

8. **Annotate overlap and finalize row flags**
    Rows are marked as:
    - `tsdf`
    - `lqg`
    - `both`

9. **Emit outputs**
    The script writes the finalized RCRA+BR population to the canonical output path.

---

### 6. Current Output Contract

The downstream output contract remains a handler-level CSV with stable field names. The emitted site rows currently include:
- `HANDLER ID`
- `HANDLER NAME`
- `LOCATION CITY`
- `LOCATION STATE`
- `LOCATION ZIP`
- `LOCATION LATITUDE`
- `LOCATION LONGITUDE`
- `OPERATING TSDF`
- `FED WASTE GENERATOR`
- `IN A UNIVERSE`
- `ACTIVE SITE`
- `is_lqg_site`
- `is_tsdf_site`
- `site_class`
- `source_dataset`
- `source_member_filename`
- `source_member_row_number`

Current source/provenance behavior:
- `source_dataset` is normalized to the master-source logical name after final validation
- `source_member_filename` and `source_member_row_number` preserve traceability back to the contributing RCRA master row

The proximity phase should treat the canonical output as the single authoritative site input and should not need to reconstruct source filtering logic.

---

### 7. Current Audit Outputs

The active implementation now writes three source-aware audit artifacts alongside the main outputs:

1. **Parse audit**
    Present for contract stability, but currently expected to be empty because the active RCRA+BR path uses fail-fast CSV readers rather than a raw-record parse-recovery stage.

2. **Validation audit**
    Captures rows rejected from the active pipeline, including:
    - BR 2021 rows with missing normalized handler IDs
    - qualifying RCRA TSDF rows with missing handler IDs or unusable coordinates
    - qualifying RCRA BR-reporting-LQG rows with missing handler IDs or unusable coordinates

3. **Dedup audit**
    Captures duplicate qualifying rows within the TSDF path or the BR-reporting-LQG path when multiple master rows compete for the same `HANDLER ID`.

These audits are now described in terms of the two-source RCRA+BR pipeline rather than the obsolete HD-centered flow.

---

### 8. Logging and Reconciliation

The current logs report source-aware counts for:
- BR reporting extraction
- RCRA classification summary
- TSDF subset counts
- BR-reporting LQG subset counts
- union summary, including overlap counts
- combined-population validation summary
- audit row counts and audit file paths
- final canonical output row count

This is the current best-available reconstruction using the best code and input datasets presently in hand.
