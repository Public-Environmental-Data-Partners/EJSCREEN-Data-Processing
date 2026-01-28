##  use this helper to be clear in debugging validation of scores
## and check if this is the right formula

tscore2 = function(bgfips, bg_pop_acs, 
									 blockfips, blockwt, 
									 aadt, 
									 dist, 
									 dist_min=0.1, 
									 dist_past_which_just_1_nearest=500, 
									 dist_max=10000) {
	
	units(dist) <- "m"
	units(dist_min) <- "m"
	units(dist_past_which_just_1_nearest) <- "m"
	units(dist_max) <- "m"
	
	# input has 1 row per block-segment pair 
	
	# for every distance < dist_min, set dist to dist_min
	# for every distance > dist_max, drop those segments for that block
	# for each block with NO segments at all now i.e., none with distance <= dist_max, set block score to 0
	# for each block with NO distance <= dist_past_which_just_1_nearest, drop all segments except the one segment with shortest distance (if any exist)
	# for each block, block score = sum of (blockwt * bgpop * aadt/distance) by block
	# for each bg, bg score = sum  block scores by bg
	
	dist[dist < min_dist] <- min_dist
	dist[dist > dist_max] <- NA  # drop these segments using sum(   , na.rm=T)
	
	stop('to be continued')
	
	# pdx[ , blockscore := sum(aadt_over_dist), by = "GEOID20"] # for each block, sum over all road segments near it
}
##################################################### #

tscore <- function(aadt, pop, popwt, 
									 dist, 
									 dist_min=0.1, 
									 dist_past_which_just_1_nearest=500, 
									 dist_max=10000) {
	
	# parameterized like this (without fips) there is no way to use only the 
	# single road nearest the BLOCK (bg?) where none are within 500 meters of the BLOCK (bg?)
	
	units(dist) <- "m"
	units(dist_min) <- "m"
	units(dist_past_which_just_1_nearest) <- "m"
	units(dist_max) <- "m"
	
	dist[dist < dist_min] <- dist_min
	aadt[dist > dist_past_which_just_1_nearest] <- 0 # placeholder to exclude these for now
	aadt[dist > dist_max] <- 0
	
	sum(pop * popwt * aadt / dist, na.rm = TRUE)
}
##################################################### #

compare_traffic = function(bgfips1 = "440070130024") {
	
	if (!exists("prep_dist")) {
		stop("must have prep_dist in globalenv already for helper compare_traffic() to work")
	}
	
	if (!require(EJAM)) {stop("must have installed and loaded EJAM pkg for the blockgroupstats data from EJSCREEN")}
	if (!require(data.table)) {stop("needs data.table package")}
	if (!require(dplyr)) {stop("needs dplyr package")}
	
	# map it (using helper function defined in separate file)
	print(roadmap(prep_dist, geoid = bgfips1))
	
	# filter prep_dist on fips
	# prep_dist[prep_dist$GEOID20 == "440070130024", ]
	pd <- prep_dist |>
		as.data.frame() |>
		dplyr::select(-geometry) |>
		dplyr::filter(substr(GEOID20,1,12) == bgfips1)
	pdx <- data.table::as.data.table(pd)
	
	# get actual ACS bg pop from EJSCREEN not from the Census2020 bgpop
	pdx$bgpop_ejscreen <- EJAM::blockgroupstats$pop[EJAM::blockgroupstats$bgfips == bgfips1]
	
	# Calculate Traffic Score ####
	
	scores <- unique(pdx[, .(block_group_geoid, 
													 GEOID20, 
													 
													 aadt, 
													 dist_pair,
													 block_group_pop,
													 bgpop_ejscreen, 
													 fraction_of_total)])
	
	scores <- unique(scores[ , .(
		bgpop_2020 = block_group_pop,
		bgpop_ejscreen = bgpop_ejscreen,
		
		traffic_score1 = tscore(
			aadt = aadt, 
			dist = dist_pair, 
			pop = block_group_pop, 
			popwt = fraction_of_total
		)   
		
	), by = "block_group_geoid"] )
	
	scores$traffic_score1 = prettyNum(scores$traffic_score1, big.mark = ",")
	
	# Show / compare to traffic score from EJSCREEN for 1 blockgroup
	
	bg <- EJAM::blockgroupstats[bgfips == bgfips1, .(bgfips, pop, traffic.score)]
	cat("\n  As found in EJSCREEN archived dataset   \n\n")
	bg$traffic.score = prettyNum(bg$traffic.score, big.mark = ",")
	print(t(bg))
	cat("\n")
	
	# Show calculated version of score
	cat("\n  As calculated here by our formula \n\n")
	print(t(scores))
	cat("\n\n")
	
	invisible(scores)
}
##################################################### #
