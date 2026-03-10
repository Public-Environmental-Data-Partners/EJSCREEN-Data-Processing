################################################################################################################## #
# function to view simple map of road segments and block points,
# to help in diagnosing or validating calculations of traffic proximity score,
# with filters on distance cutoff and bg or block FIPS
#
# spdf - a spatial data.frame like prep_dist as created by the Process2020.R script
# geoid - optional FIPS code(s) - can be NULL to show all, a blockgroup FIPS to show all blocks in the bg, or a block FIPS, or vector of 1 type of fips, all with leading zero where relevant
# dist - optional distance cutoff in meters, limits map contents to distances no more than dist

roadmap <- function(spdf,
										geoid = NULL,
										dist = 500
) {
	
	require(data.table)
	require(units)
	# filter on FIPS
	these_blocks <- if (is.null(geoid)) { rep(TRUE, NROW(spdf))} else {
		if (all(nchar(geoid)) > 12) {
			spdf$GEOID20 %in% geoid
		} else {
			nc = unique(nchar(geoid))
			substr(spdf$GEOID20,1,nc) %in% geoid
		}
	}
	prep_dist <- spdf[these_blocks, unique(c(
		c("GEOID20", "block_group_geoid", "block_group_pop", "dist_pair", "geometry"),
		c("ID", "aadt", "dist_pair", "geometry", "line_geom")))]
	rm(spdf)
	
	# for each block point, get distance to nearest road segment
	pd <- data.table::copy(data.table(prep_dist[,c("GEOID20", "dist_pair")]))
	pd[, dist_nearest := min(dist_pair), by = "GEOID20"]
	prep_dist$dist_nearest <- pd$dist_nearest
	rm(pd)
	
	# filter on distance
	cutoff <- dist
	# once units package is loaded, this should work
	units(cutoff) <- structure(list(numerator = "m", denominator = character(0)), class = "symbolic_units")
	prep_dist_lines <- prep_dist[prep_dist$dist_pair <= cutoff, c("ID", "line_geom", "aadt", "dist_pair", "dist_nearest")]
	prep_dist_lines$geometry <- prep_dist_lines$line_geom
	
	# map the road segments
	if (NROW(prep_dist_lines) == 0) {
		segmentmap <- NULL
		cat("no road segments are within the cutoff distance \n")
	} else {
		segdata <- unique(prep_dist_lines[ , c(
			"ID", "geometry", "aadt")])
		ncolors <- min(NROW(segdata), 400)
		colorlist = c("black", "red", "lightblue", "gray", "blue", "orange", "darkgreen", "green", "purple", "brown", "hotpink2")
		if (ncolors < length(colorlist)) {
			colors2use = colorlist[1:ncolors]
		} else {
			colors2use = c(colorlist, sample(colorlist, ncolors - length(colorlist), replace = TRUE))  
		}
		
		segmentmap <- mapview( segdata,
													 # random color for each road segment
													 color = colors2use, zcol="ID", legend=F)
	}
	
	# map the block points beyond cutoff
	if (sum(prep_dist$dist_pair > cutoff, na.rm = T) > 0) {
		farmap <- mapview( unique(prep_dist[prep_dist$dist_pair > cutoff , c(
			"GEOID20", "block_group_geoid", "block_group_pop", "dist_nearest", "geometry"
		)]), color = "black", col.regions ="gray", layer.name	= paste0("beyond cutoff distance of ", round(cutoff, 1), " meters"), legend=T )#, cex=3)
	} else {
		farmap <- NULL
		# cat("none of the block points are beyond the cutoff \n")
	}
	
	# map the block points within cutoff distance
	if (sum(prep_dist$dist_pair <= cutoff, na.rm = T) > 0) {
		nearmap <- mapview( unique(prep_dist[prep_dist$dist_pair <= cutoff , c(
			"GEOID20", "block_group_geoid", "block_group_pop", "dist_nearest", "geometry"
		)]), color = "black", col.regions ="red", layer.name	= paste0("within ", round(cutoff, 1), " meters of nearest road segment"), legend=T  )#, cex=3)
	} else {
		nearmap <- NULL
		cat("no relevant block points are within the cutoff distance \n")
	}
	
	return(farmap + segmentmap + nearmap)
}
################################################################################################################## #
