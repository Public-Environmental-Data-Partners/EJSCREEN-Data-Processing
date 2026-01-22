if (FALSE) {

  ## SCRIPT TRYING OUT THE FUNCTIONS THAT MAP AND COMPARE SCORES


  # mydir = '.' #  mydir = '~/Documents/EJAM 2026'
  # save(prep_dist, file = file.path(mydir, "prep_dist.rda"))
  # load(file.path(mydir, "prep_dist.rda"))

  ## examples of maps

  ## 1 block
  roadmap(prep_dist, geoid = "440030222022026") # 500 is default cutoff
  roadmap(prep_dist, geoid = "440030222022026", dist = 9999)
  roadmap(prep_dist, geoid = "440030222022026", dist = 20)

  ## all blocks in 1 blockgroup
  roadmap(prep_dist, geoid = "440030222022") # 500 is default cutoff
  roadmap(prep_dist, geoid = "440030222022", dist = 300)
  roadmap(prep_dist, geoid = "440030222022", dist = 20)

  ##  random blockgroup
  geoid = sample(prep_dist$block_group_geoid, 1)
  roadmap(prep_dist, geoid=geoid )
  # e.g. 440050401021

  ## all blocks in the entire dataset, all segments within 500 meters of any
  ## is too slow this way
  # roadmap(prep_dist)

  ##################################################### #
  #  1 blockgroup
  bgfips1 = "440070130024"

################################################################################################################## #

## random block group in this dataset
geoid = sample(prep_dist$block_group_geoid, 1)
compare_traffic(geoid)

bgfips1 = "440070130024"

blockpoints[x, .(lat, lon,  fips), on = "blockid"]

EJAM::mapfast(bg, radius = EJAM:::convert_units(500, from = "m", towhat = "mi"))

x <- EJAM::getblocksnearby_from_fips(fips = bgfips1)
EJAM:::latlon_join_on_blockid(x)
x[blockpoints, `:=`(lat=lat, lon=lon, blockid=blockid, blockwt=blockwt, bgfips=fips), on = "blockid"]
x$fips <- NULL
x$distance <- NULL
x[blockid2fips, blockfips := blockfips, on = "blockid"]
head(x)
EJAM::mapfast(x, radius = EJAM:::convert_units(500, "meters", "miles"))
EJAM::mapfast(x, radius = EJAM:::convert_units( 10, "meters", "miles"), color = 'black')


}
