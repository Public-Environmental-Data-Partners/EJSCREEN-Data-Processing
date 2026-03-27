# DRAFT Design Decision Record: Hazardous Waste Proximity Indicator Source Data and Filtering #

**Date:** 2026-03-18

This text is based on a draft by Gemini and on research and prototype coding done by 
AG, Gemini, and GitHub CoPilot ChatGPT 5.4

---

### 1. Problem Statement

The "Hazardous Waste Proximity" indicator requires an authoritative, focused list of Hazardous Waste facility locations (i.e. latitude and longitude). There are two general types of site:
- LGQ (large quantity generator) sites, the sources -- These are sites such as chemical plants, refineries, and large manufacturers that generate more than 2,200 lbs of hazardous waste per month.
- TSDF (Treatment, Storage, Disposal Facility) sites, the sinks -- These facilities receive waste from other companies to incinerate it, treat it, or bury it in specialized landfills.

The federal data set that acts as the raw source for the data contains a large superset of the data we need for processing.
The raw `HD_HANDLER` dataset (approx. 4.1 million records) acts as a historical registry for all RCRA-regulated entities.
[RCRA: Resource Conservation and Recovery Act] 
For our indicator, the dataset must be pruned to include only active, significant facilities (TSDFs and LQGs) to avoid over-counting from historical or minor environmental locations.

**Source Data Set:**

- [RCRAInfo Public Data Access (CSV Downloads)](https://rcrapublic.epa.gov/rcra-hwip/data-access/csv-downloads)
- File to download: `hd.zip` (Handler Data)
- Primary Table: `HD_HANDLER` (Distributed across 5 CSV files)

To assemble one authoritative list, the 5 CSV files in the zip must be filtered and then reassembled
into a single csv file for downstream processing.

### 2. Implementation: Filter Logic
(All code shown is Python using dataframe syntax. It should be easily translated into R if need be.)

**The Filter Formula:**
The following logic is proposed to filter each large dataset, `df`,  to just our required list:

`df_filtered = df[is_current & include_in_national_report & (is_tsdf | is_lqg)]`

**Variable Definitions:**

* **`is_current`**: `df['CURRENT RECORD'] == 'Y'`
*Ensures the record is the most up-to-date version in the registry.*
* **`include_in_national_report`**: `df['INCLUDE IN NATIONAL REPORT'] == 'Y'`
*Acts as a proxy for "active/significant" status by selecting facilities included in the biennial reporting cycle.*
* **`is_tsdf`**: `df['TSD ACTIVITY'] == 'Y'`
*Treatment, Storage, and Disposal facilities.*
* **`is_lqg`**: `df['FED WASTE GENERATOR'] == '1'`
*Large Quantity Generators (the highest volume hazardous waste producers).*

### 3. Observed filtering results on a data sample

Testing on `HD_HANDLER_0.csv` (~1,000,000 records) produced the following:

* **Total Records:** 1,000,000
* **Filtered Records:** 3,961 (approx. 0.4% of total)^^

*^^Note: This reduction is consistent with expectations for the EJScreen methodology, 
which focuses on a subset of approximately 20,000 to 30,000 facilities nationwide.*

### 4. Preprocessing required

Code must read all records in the zipped csvs stored within the downloaded zip
file, filter them, and then merge them into a single csv for downstream
indicator calculations.
