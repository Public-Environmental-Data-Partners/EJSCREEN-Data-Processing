# Design Decision Record: Hazardous Waste Proximity Source Data and Filtering

**Date:** 2026-03-30
**Indicator:** Hazardous Waste Proximity (TSDF & LQG)
**Target Version:** EJScreen 2.3 Compatibility

---

### 1. Problem Statement

The "Hazardous Waste Proximity" indicator requires a focused national list of hazardous-waste sites with usable coordinates. There are two general classes of site we care about:
- **LQG (Large Quantity Generator) sites, the sources**: facilities that generate hazardous waste at the large-quantity level.
- **TSDF (Treatment, Storage, Disposal Facility) sites, the sinks**: facilities that receive, treat, store, or dispose of hazardous waste.

The full federal RCRAInfo download is much broader than the subset needed for EJ-style proximity scoring. The preprocessing objective is therefore to identify a defensible national site universe that is both geographically usable and reasonably aligned to the original EJScreen/EJAM concept, even if some drift remains because the source files have changed over time.

---

### 2. Current Source Decision

Based on inspection of the local files currently in hand, the working design is now **HD_REPORTING-first**.

`HD_REPORTING` currently appears to provide all of the core fields needed for an initial rebuild:
- `HANDLER ID`
- `HANDLER NAME`
- `LOCATION LATITUDE`
- `LOCATION LONGITUDE`
- `GENSTATUS`
- `OPERATING TSDF`

That means the first implementation path does **not** depend on a separate coordinate join. Instead, the pipeline can attempt to derive both the site universe logic and the site locations from `HD_REPORTING` alone.

`BR_REPORTING_2023` remains relevant, but it is now treated as a **possible narrowing filter** rather than a mandatory source. If testing shows that `HD_REPORTING` alone yields too many sites, includes too many stale records, or otherwise appears broader than the intended indicator universe, then `BR_REPORTING` can be layered in as a secondary constraint to prefer recent reporting activity.

---

### 3. Working Filter Logic

The current working assumption is that a site should be included if it satisfies either the TSDF condition or the LQG condition.

**The provisional filter formula:**

1. **Operating TSDF**
    Use `OPERATING TSDF` as the TSDF indicator.

    The field does not behave like a simple `Y/N` flag. In the local file inspection, values such as `L--S--` and `---S--` appear to represent positive TSDF classifications, while `------` appears to represent absence of TSDF status.

    The current working interpretation is therefore:
    - include as TSDF when `OPERATING TSDF` contains one or more meaningful letter codes
    - exclude as TSDF when `OPERATING TSDF` is blank or all dashes

2. **Large Quantity Generator**
    Use `GENSTATUS` as the LQG indicator.

    The observed values include:
    - `LQG`
    - `SQG`
    - `VSQG`
    - `N`

    The current working interpretation is:
    - include as LQG when `GENSTATUS == 'LQG'`
    - do not include `SQG`, `VSQG`, or `N` in the initial site universe

3. **Optional BR tightening pass**
    If testing suggests that `HD_REPORTING` alone is too broad or includes too many records that are not sufficiently current, then `BR_REPORTING_2023` may be used as an additional narrowing step.

    In that case, the working rule would be:
    - start from the `HD_REPORTING`-derived TSDF/LQG universe
    - optionally require presence in `BR_REPORTING_2023` for categories where current activity matters
    - evaluate the effect empirically before making that tightening rule permanent

---

### 4. Data Assembly Workflow

The current workflow is intentionally simpler than the prior three-source design.

1. **Read and validate `HD_REPORTING`**
    Confirm that the file contains the expected identifier, status, and coordinate fields.

2. **Build the provisional site universe**
    Keep rows where either:
    - `GENSTATUS == 'LQG'`, or
    - `OPERATING TSDF` indicates an actual TSDF classification rather than an all-dash placeholder.

3. **Retain coordinates directly from `HD_REPORTING`**
    Use `LOCATION LATITUDE` and `LOCATION LONGITUDE` from the same filtered rows.

4. **Deduplicate by `HANDLER_ID`**
    Ensure one retained record per handler so the final site file does not inflate downstream scores.

5. **Optionally test `BR_REPORTING_2023` as a narrowing filter**
    If the provisional `HD_REPORTING` universe looks too broad, compare results with a version that requires recent reporting activity or otherwise uses `BR_REPORTING_2023` to tighten the site set.
