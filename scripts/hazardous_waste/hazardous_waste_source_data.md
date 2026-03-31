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
- `LOCATION CITY`
- `LOCATION STATE`
- `LOCATION ZIP`
- `LOCATION LATITUDE`
- `LOCATION LONGITUDE`
- `OPERATING TSDF`
- `FED WASTE GENERATOR`
- `STATE WASTE GENERATOR`
- `IN A UNIVERSE`
- `ACTIVE SITE`

That means the first implementation path does **not** depend on a separate coordinate join. Instead, the pipeline can attempt to derive both the site universe logic and the site locations from `HD_REPORTING` alone.

`BR_REPORTING_2023` remains relevant, but it is now treated as a **possible narrowing filter** rather than a mandatory source. If testing shows that `HD_REPORTING` alone yields too many sites, includes too many stale records, or otherwise appears broader than the intended indicator universe, then `BR_REPORTING` can be layered in as a secondary constraint to prefer recent reporting activity.

---

### 3. Working Filter Logic

The current working assumption is that a site should be included if it satisfies either the TSDF condition or the federal LQG condition, and also passes the active/universe guardrail.

**The provisional filter formula:**

1. **Operating TSDF**
    Use `OPERATING TSDF` as the TSDF indicator.

    The field does not behave like a simple `Y/N` flag. Values such as `L--S--`, `---S--`, and similar coded strings represent positive TSDF classifications, while `------` represents absence of TSDF status.

    The current working interpretation is therefore:
    - include as TSDF when `OPERATING TSDF` is present and not equal to `------`
    - exclude as TSDF when `OPERATING TSDF` is blank or equal to `------`

2. **Federal Large Quantity Generator**
    Use `FED WASTE GENERATOR` as the LQG indicator.

    The current working interpretation is:
    - include as LQG when `FED WASTE GENERATOR == '1'`
    - do not use `STATE WASTE GENERATOR` to qualify rows for output

3. **State generator count is informational only**
    `STATE WASTE GENERATOR == '1'` is still counted and logged during preprocessing, but it is not part of the current selection mask.

4. **Active/universe guardrail**
    Even if a row matches the TSDF or federal-LQG condition, it must also satisfy the active-site guardrail.

    The current working interpretation is:
    - include only when `ACTIVE SITE == 'H'` or `IN A UNIVERSE == 'Y'`
    - exclude rows that fail both of those tests

5. **Coordinate guardrail**
    The current implementation rejects rows with unusable coordinates.

    The current working interpretation is:
    - include only when both `LOCATION LATITUDE` and `LOCATION LONGITUDE` parse as numbers
    - exclude rows where either coordinate is blank, non-numeric, or exactly `0.0`

6. **Optional BR tightening pass**
    If testing suggests that `HD_REPORTING` alone is too broad or includes too many records that are not sufficiently current, then `BR_REPORTING_2023` may be used as an additional narrowing step.

    In that case, the working rule would be:
    - start from the `HD_REPORTING`-derived TSDF/federal-LQG universe
    - optionally require presence in `BR_REPORTING_2023` for categories where current activity matters
    - evaluate the effect empirically before making that tightening rule permanent

---

### 4. Data Assembly Workflow

The current workflow is intentionally simpler than the prior three-source design.

1. **Read and validate `HD_REPORTING`**
    Confirm that the file contains the expected identifier, status, and coordinate fields.

2. **Build the provisional site universe**
    Keep rows where either:
    - `FED WASTE GENERATOR == '1'`, or
    - `OPERATING TSDF != '------'`

    Then apply the guardrail:
    - `ACTIVE SITE == 'H'` or `IN A UNIVERSE == 'Y'`

3. **Retain coordinates directly from `HD_REPORTING`**
    Use `LOCATION CITY`, `LOCATION STATE`, `LOCATION ZIP`, `LOCATION LATITUDE`, and `LOCATION LONGITUDE` from the same filtered rows.

4. **Deduplicate by `HANDLER_ID`**
    Ensure one retained record per handler so the final site file does not inflate downstream scores. The current implementation sorts candidate rows by site class and simple provenance tie-breaks, then keeps one row per `HANDLER ID`.

5. **Optionally test `BR_REPORTING_2023` as a narrowing filter**
    If the provisional `HD_REPORTING` universe looks too broad, compare results with a version that requires recent reporting activity or otherwise uses `BR_REPORTING_2023` to tighten the site set.

---

### 5. Current Output Contract

The current preprocess output is a canonical site CSV with one row per retained `HANDLER ID` and the following key columns:
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

The current logs also report:
- rows matching the TSDF condition
- rows matching the federal LQG condition
- rows matching the state generator condition
- rows passing the active/universe guardrail
- total qualifying rows before coordinate validation
- validation rejects for missing handler IDs or unusable coordinates
- rows removed by `HANDLER ID` deduplication
