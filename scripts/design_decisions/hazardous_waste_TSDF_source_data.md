# DRAFT Design Decision Record: TSDF/LQG Indicator Filtering #

**Date:** 2026-03-18

This text drafted by Gemini based on research and prototype coding done by AG, Gemini, and GitHub CoPilot ChatGPT 5.4

**Source Data:**

- [RCRAInfo Public Data Access (CSV Downloads)](https://rcrapublic.epa.gov/rcra-hwip/data-access/csv-downloads)

- File to download: `hd.zip` (Handler Data)

- Primary Table: `HD_HANDLER` (Distributed across 5 CSV files)

---

### 1. Problem Statement

The raw `HD_HANDLER` dataset (approx. 4.1 million records) acts as a historical registry for all RCRA-regulated entities. For the "Hazardous Waste Proximity" indicator, the dataset must be pruned to include only active, significant facilities (TSDFs and Large Quantity Generators) to avoid over-counting historical or minor environmental footprints.

### 2. Implementation: Filter Logic

The following logic was implemented in the Python pipeline to isolate relevant sites:

**The Filter Formula:**

```python
df_filtered = df[is_current & include_in_national_report & (is_tsdf | is_lqg)]

```

**Variable Definitions:**

* **`is_current`**: `df['CURRENT RECORD'] == 'Y'`
*Ensures the record is the most up-to-date version in the registry.*
* **`include_in_national_report`**: `df['INCLUDE IN NATIONAL REPORT'] == 'Y'`
*Acts as a proxy for "active/significant" status by selecting facilities included in the biennial reporting cycle.*
* **`is_tsdf`**: `df['TSD ACTIVITY'] == 'Y'`
*Treatment, Storage, and Disposal facilities.*
* **`is_lqg`**: `df['FED WASTE GENERATOR'] == '1'`
*Large Quantity Generators (the highest volume hazardous waste producers).*

### 3. Observed Results (Audit)

Testing on `HD_HANDLER_0.csv` (1,000,000 records) produced the following:

* **Total Records:** 1,000,000
* **Filtered Records:** 3,961 (approx. 0.4% of total)

*Note: This reduction is consistent with expectations for the EJScreen methodology, which focuses on a subset of approximately 20,000 to 30,000 facilities nationwide.*

### 4. Spatial Configuration

* **Initial CRS:** `EPSG:4326` (WGS 84) based on `LOCATION LATITUDE` and `LOCATION LONGITUDE`.
* **Metric CRS:** To be determined by state (e.g., `EPSG:3338` for AK, `ESRI:102007` for HI) via the external `state_config.json`.
* **Geometry:** Points (Site-to-Block Centroid).
* **Search Radius:** 10 km.
* **Scoring:** Inverse distance weight with a **maximum cap of 10** for distances < 0.1 km.

---

**Next Steps:**
When you return, would you like to apply this logic in a loop across the remaining 4 handler files, or move on to researching the **RMP (Risk Management Plan)** data structure?