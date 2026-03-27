# Design Decision Record: Hazardous Waste Proximity Source Data and Filtering

**Date:** 2026-03-27
**Indicator:** Hazardous Waste Proximity (TSDF & LQG)
**Target Version:** EJScreen 2.3 Compatibility

---

### 1. Problem Statement

The "Hazardous Waste Proximity" indicator requires an authoritative, focused list of Hazardous Waste facility locations (i.e. latitude and longitude). There are two general types of site:
- **LQG (Large Quantity Generator) sites, the sources**: These are sites such as chemical plants, refineries, and large manufacturers that generate more than 2,200 lbs of hazardous waste per month.
- **TSDF (Treatment, Storage, Disposal Facility) sites, the sinks**: These facilities receive waste from other companies to incinerate it, treat it, or bury it in specialized landfills.

The federal RCRAInfo dataset contains a large superset of data (approx. 4.1 million records) acting as a historical registry. To achieve high correlation with EPA EJScreen/EJAM results, this dataset must be pruned to a specific "Regulatory Universe" of the significant facilities nationwide. The goal is to have a high correlation with the original EJAM sites and block scores but there is likely to be some drift due to the source file contents changing over time.

---

### 2. Source Data Architecture

To assemble the authoritative list, the pipeline must join three distinct relational components found within the [RCRAInfo Public Data Access](https://rcrapublic.epa.gov/rcra-hwip/data-access/csv-downloads) downloads.

| Component | Source File | Purpose |
| :--- | :--- | :--- |
| **Master Registry** | `HD_BASIC.csv` (inside `hd.zip`) | The "Phone Book": Contains the single authoritative **Latitude/Longitude** and Handler Name for every registered ID. |
| **Regulatory Universe** | `HD_REPORTING.csv` (inside `hd_reporting.zip`) | The "Scorecard": Contains the specific EPA flags for **Operating TSDFs** and **Active LQGs**. |
| **Temporal Activity** | `BR_REPORTING_2021.csv` (or 2023) | The "Safety Net": A list of IDs that actually reported hazardous waste handling in the most recent **Biennial Report** cycle. |

---

### 3. Implementation: Filter Logic

The "Hazardous Waste Proximity" universe is defined as the **Union** of facilities that are either designated as permanent infrastructure (TSDFs) or identified by recent high-volume activity (LQGs).

**The Filter Formula:**
A facility is included in the final indicator calculation if its `HANDLER_ID` meets **ANY** of the following conditions:

1.  **Operating TSDF**:
    * `HD_REPORTING['OPERATING_TSDF'] == 'Y'`
    * OR `HD_REPORTING['TSDF_UNIVERSE']` is not null.
2.  **Active LQG Universe**:
    * `HD_REPORTING['LQG_UNIVERSE']` is not null.
3.  **Recent Reporting Event**:
    * `HANDLER_ID` is present in the `BR_REPORTING` (Biennial Report) summary file for the target year (2021 or 2023).

---

### 4. Data Assembly Workflow

1.  **ID Extraction**: Extract a unique list of `HANDLER_ID`s from the `BR_REPORTING` dataset.
2.  **Universe Flagging**: Filter `HD_REPORTING` using the logic in Section 3 to identify the "National Significant Subset."
3.  **Spatial Join**: Perform an inner join between the filtered IDs and `HD_BASIC` to retrieve the `LOCATION_LATITUDE` and `LOCATION_LONGITUDE`.
4.  **Deduplication**: Ensure one record per `HANDLER_ID` to prevent score inflation from historical source records.

A design stretch goal:

See if we can build code that reaches into the original downloaded wrapper zip, hd.zip, to directly read and process records within 
its contained zips and without having to unzip the component files first.
