# might want to use this helper to be clear?
tscore = function(aadt, dist, pop, popwt) {
  sum(pop * popwt * aadt / dist)
}
##################################################### #

compare_traffic = function(bgfips1 = "440070130024") {

  if (!exists("prep_dist")) {stop("must have prep_dist in globalenv already")}

  # map it
  roadmap(prep_dist, geoid = bgfips1)

  # filter prep_dist on fips
  # prep_dist[prep_dist$GEOID20 == "440070130024", ]
  pd = prep_dist %>%
    as.data.frame() %>%
    select(-geometry) %>%
    filter(substr(GEOID20,1,12) == bgfips1)
  pdx = as.data.table(pd)

  # get actual ACS bg pop from EJSCREEN not from the Census2020 bgpop
  if (!require(EJAM)) {stop("must have installed and loaded EJAM pkg for the blockgroupstats data from EJSCREEN")}
  pdx$bgpop_ejscreen = EJAM::blockgroupstats$pop[EJAM::blockgroupstats$bgfips == bgfips1]

  # Calculate Traffic Score ####

  pdx[ , aadt_over_dist := aadt / dist_pair]
  pdx[ , blockscore := sum(aadt_over_dist), by = "GEOID20"]

  # scores = (pdx[, .(block_group_geoid, GEOID20, blockscore, block_group_pop, fraction_of_total)])
  scores <- unique(pdx[, .(block_group_geoid, GEOID20, blockscore, block_group_pop, bgpop_ejscreen, fraction_of_total)])

  scores <- unique(scores[ , .(
    block_group_pop = block_group_pop,
    bgpop_ejscreen = bgpop_ejscreen,
    # traffic_score = tscore(pop = block_group_pop, aadt = aadt, dist = dist_pair, popwt = fraction_of_total)
    # traffic_score = sum(blockscore * block_group_pop * fraction_of_total)
    traffic_score = sum(blockscore * bgpop_ejscreen * fraction_of_total)
  ), by = "block_group_geoid"] )

  # Show calculated version of score
  cat("As calculated here by our formula \n\n")
  print(scores)
  cat("\n\n")

  # Show / compare to traffic score from EJSCREEN for 1 blockgroup
  library(EJAM)
  bg <- EJAM::blockgroupstats[bgfips == bgfips1, .(bgfips, pop, traffic.score)]
  cat("As found in EJSCREEN archived dataset   \n\n")
  print(bg)
  cat("\n\n")

  invisible(scores)
}
##################################################### #
