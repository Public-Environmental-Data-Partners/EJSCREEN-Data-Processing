# INIT
library(EJAM) # See documentation for build/install
library(dplyr)
library(ACSdownload) # pak::pkg_install("ejanalysis/ACSdownload")
devtools::load_all() #install.packages("devtools")
usethis::edit_r_environ() #install.packages("usethis")

# RUN w/ local copies of AWS S3 storage files from annual update
# Configure
pipeline_dir<-file.path(
  getwd(),
  "data-raw",
  "pipeline_outputs",
  "ejscreen_acs_2022"
)

Sys.setenv(
  EJAM_PIPELINE_DIR = pipeline_dir,
  EJAM_PIPELINE_STORAGE = "local",
  EJAM_PIPELINE_YR = "2022",
  EJAM_VALIDATE_VS_PRIOR = FALSE
)

cfg <- EJAM:::pipeline_config_annual(
  yr = 2022,
  force_acs = FALSE,
  force_bg_acsdata = FALSE,
  force_bg_geodata = FALSE,
  use_provisional_bg_envirodata = FALSE,
  include_ejscreen_export = TRUE,
  pipeline_dir = pipeline_dir,
  pipeline_storage="local",
  islandareas_reference_path="~/data-raw/pipeline_outputs/ejscreen_acs_2022/epa_original_reference/2024_2.32_August_UseMe/EJSCREEN_2024_BG_with_AS_CNMI_GU_VI.csv"
)

# Update bg_envirodata file with indicator scores
library(data.table)
existing_bgenvirodata<- fread(file.path(pipeline_dir, "bg_envirodata.csv"),colClasses = c(bgfips = "character")) # Load existing bg_envirodata table
new_bgenvirodata <- fread("~/envirodata_1.2020.csv",colClasses = c(ID = "character")) # Load new values
new_bgenvirodata$bgfips <- new_bgenvirodata$ID # convert column ID to bgfips
names <- EJAM::fixnames_to_type(colnames(new_bgenvirodata), oldtype="csvname", newtype="rname", mapping_for_names=map_headernames) # Convert other EJSCREEN names e.g. ozone into EJAM names e.g. o3
colnames(new_bgenvirodata)<-names
cols <- setdiff(names(new_bgenvirodata), "bgfips") # check column names
setdiff(cols, names(existing_bgenvirodata)) # should come back empty
existing_bgenvirodata[new_bgenvirodata, on = "bgfips", (cols) := mget(paste0("i.", cols))] # replace columns/values in old bg_envirodata with those from new_bgenvirodata based on join with bgfips
fwrite(existing_bgenvirodata, file.path(pipeline_dir, "bg_envirodata.csv"))

# Run
run <- EJAM:::run_ejscreen_pipeline(cfg)