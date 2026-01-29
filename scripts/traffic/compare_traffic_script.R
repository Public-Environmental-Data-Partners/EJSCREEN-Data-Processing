if (FALSE) {
	
	## SCRIPT TRYING OUT THE FUNCTIONS THAT MAP AND COMPARE SCORES
	
	##################################################### #
	## start in the right folder (to load helper functions)
	if (basename(getwd()) != 'EJSCREEN-Data-Processing') {
		if (!require(rstudioapi)) {
			install.packages("rstudioapi")
			if (!require(rstudioapi)) {message("install rstudioapi package to use interactive function rstudioapi::selectDirectory()")}
			stop("must be in EJSCREEN-Data-Processing folder to run this script")
		}
		mydir <- rstudioapi::selectDirectory(caption = "select the EJSCREEN-Data-Processing folder, to start there")
		oldfolder=getwd()
		on.exit(setwd(oldfolder))
		setwd(mydir)
		if (basename(getwd()) != 'EJSCREEN-Data-Processing') {
			stop("must be in EJSCREEN-Data-Processing folder to run this script")
		}
	}
	if (!dir.exists('./scripts/traffic')) {
		stop('this script expects a folder with R functions at EJSCREEN-Data-Processing/scripts/traffic')
	}
	##################################################### #
	## load R packages 
	if (!require(EJAM)) {
		stop("must have installed and loaded EJAM pkg for the blockgroupstats data from EJSCREEN")
	}
	if (!require(dplyr)) {
		install.packages("dplyr")
		if (!require(dplyr)) {
			stop("must have the dplyr package installed")
		}
	}
	if (!require(units)) {
		install.packages("units")
		if (!require(units)) {
			stop("must have the units package installed to set units as meters, e.g.")
		}
	}
	if (!require(data.table)) {
		install.packages("data.table")
		if (!require(data.table)) {
			stop("must have the data.table package installed to use these functions/script")
		}
	}
	if (!require(mapview)) {
		install.packages("mapview")
		if (!require(mapview)) {
			stop("must have the mapview package installed")
		}
	}
	##################################################### #
	## load helper functions
	source('./scripts/traffic/roadmap.R')
	source('./scripts/traffic/compare_traffic.R')
	
	##################################################### #

	## Load (if available) a saved copy of block data and traffic data
	if (!exists("prep_dist")) {
		mydir <- '~/Documents/EJAM 2026' # e.g., if it had been saved there
		if (!dir.exists(mydir)) {
			mydir <- "."
		}
		## earlier had created prep_dist and saved it: 
		# save(prep_dist, file = file.path(mydir, "prep_dist.rda"))
		saved_data <- file.path(mydir, "prep_dist.rda")
		if (file.exists(saved_data)) {
			load(file = saved_data)
		} else {
			stop('needs prep_dist dataset')
		}
	}
	################################################################################################################## #
	## Examples of mapping roads near 1 block or 1 bg
	
	## 1 block
	roadmap(prep_dist, geoid = "440030222022026") # 500 is default cutoff
	roadmap(prep_dist, geoid = "440030222022026", dist = 9999)
	roadmap(prep_dist, geoid = "440030222022026", dist = 20)
	
	## 1 blockgroup, with all its blocks
	roadmap(prep_dist, geoid = "440030222022") # 500 is default cutoff
	roadmap(prep_dist, geoid = "440030222022", dist = 300)
	roadmap(prep_dist, geoid = "440030222022", dist = 20)
	
	## 1 random blockgroup
	geoid = sample(prep_dist$block_group_geoid, 1)
	roadmap(prep_dist, geoid=geoid )
	# e.g. 440050401021
	
	## Mapping all blocks in the entire dataset, all segments within 500 meters of any
	## is too slow this way
	# roadmap(prep_dist)
	
	################################################################################################################## #
	
	## Examine a specific bg or random block group(s) 
	
	bgfips1 = "440070130024"
	bgfips1 = sample(prep_dist$block_group_geoid, 1)
	compare_traffic(bgfips1)
	
	
	# get original traffic score 
	bg <- EJAM::blockgroupstats[bgfips == bgfips1, .(bgfips, pop, traffic.score)]
	# get block coordinates
	EJAM::mapfast(bg, radius = EJAM:::convert_units(500, from = "m", towhat = "mi"))
	x <- EJAM::getblocksnearby_from_fips(fips = bgfips1)
	EJAM:::latlon_join_on_blockid(x)
	x[blockpoints, `:=`(lat=lat, lon=lon, blockid=blockid, blockwt=blockwt, bgfips=fips), on = "blockid"]
	x$fips <- NULL
	x$distance <- NULL
	x[blockid2fips, blockfips := blockfips, on = "blockid"]
	head(x)
	blockpoints[x, .(lat, lon,  fips), on = "blockid"]
	
	EJAM::mapfast(x, radius = EJAM:::convert_units(500, "meters", "miles"))
	EJAM::mapfast(x, radius = EJAM:::convert_units( 10, "meters", "miles"), color = 'black')
	
	
}
