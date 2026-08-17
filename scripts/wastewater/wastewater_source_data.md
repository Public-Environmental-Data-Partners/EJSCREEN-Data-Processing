# Wastewater Discharge Source Data

The wastewater discharge indicator combines modeled chemical concentration
data from EPA RSEI Water Geographic Microdata with NHDPlus stream
geometries and 2020 Census population data.

## EPA RSEI Water Geographic Microdata

The primary environmental input is EPA Risk-Screening Environmental
Indicators (RSEI) Water Geographic Microdata.

The preprocessing workflow uses the onsite and offsite modeled water
concentration archives and aggregates the selected reporting year to
NHDPlus COMIDs.

For the validated 2021 indicator workflow, the final proximity calculation
uses the offsite modeled toxic concentration field (`offsite_toxconc`).

Canonical preprocessed output:

    pipeline/preprocessed_input/water_microdata/
    water_microdata_<year>_by_comid.parquet

The preprocessing is performed by:

    wastewater_microdata_preprocess.py

## NHDPlus V2.1

NHDPlus V2.1 provides the stream and river flowline geometries associated
with the COMIDs in the RSEI Water Geographic Microdata.

Regional NHDPlus data are downloaded by Vector Processing Unit (VPU),
preprocessed, and combined with the RSEI concentration data before the
indicator calculation.

Relevant scripts include:

    fetch_nhdplus.py
    wastewater_nhdplus_preprocess.py
    wastewater_model_flowlines.py
    wastewater_combine_flowlines.py

## 2020 Census Geography and Population Weights

The final indicator uses shared 2020 Census block population-weight files
from the EJSCREEN processing repository.

Census block internal points are used for distance calculations. Block-level
wastewater scores are weighted by each block's population fraction and then
aggregated to Census block groups.

Shared input location:

    scripts/shared/pipeline/preprocessed_input/
    census_block_weights_2020/

## Generated Data

Raw downloads, intermediate GeoParquet files, state-level indicator outputs,
QA files, and comparison products are generated locally by the pipeline and
are not intended to be committed to the repository.