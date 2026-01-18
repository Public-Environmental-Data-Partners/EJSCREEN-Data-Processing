# WeightedScoreUDF --
# This code is intended to recreate the functionality of the WeightedScoreUDF function
# used in the ejscreen traffic proximity processing script, Highway_processing_1_step1_pig.txt,
# from: https://github.com/USEPA-clone/ejscreen-traffic-proximity-processing/tree/main
#
# I used Gemini heavily to analyze and understand the original Pig script and
# produce this Python version.
#
# This file includes 2 versions of the function, in increasing order of speculativeness:
# WeightedScoreUDF1 -- a naive implementation with the weighted score calculated
#                      as (aadt_volume / adjusted distance) * total_pop
# WeightedScoreUDF2 -- applies a land_area filter, if it is 0, we return 0 for
#                      both the raw and weighted scores.
#
# TODO: Once these functions have been tested for valid input/output behavior, we should
# investigate how land_area and water_area should be further used to adjust the score calculations.
# For example, it may be that the weighting to be applied is not simply total_pop but rather
# a population density such as total_pop / land_area (after land_area is verified to be non-zero).
#
# Input:
#   land_area (aland) -- land area of the census block
#   water_area (awater) -- water area of the census block
#   distance -- distance from block point to road
#   total_pop (totpop) -- population of the census block
#   aadt_volume (aadt_vn) -- traffic volume (annual avg. daily traffic)
# Output, as a tuple:
#   adj_distance_lt -- minimum of 5m or actual distance (prevent divide by 0)
#   radius_lt -- said to be a fixed, magic 500
#   score_lt -- raw score, calculated as aadt_volume / distance
#   weighted_score -- score_lt * total_pop

def WeightedScoreUDF1(land_area, water_area, distance, total_pop, aadt_volume):
    # TODO: neither land_area or water_area currently used.
    # and the radius_lt is assumed not to be used within this function.
    # Figure out if we need to be using any of them for our calculation.
    #
    # Prevent artificially high scores due to zero or near-zero distances.
    # Enforce a minimum distance of 5 meters
    adj_distance_lt = min(5.0, distance)

    # We appear to be setting a fixed radius value to pass along for the benefit
    # of downstream code. This is understood to be a magic number.
    # AI recommends the 500 value, probably based on search results
    # such as: https://careshq.org/ss_whatsnewitem/ej-screen-traffic-proximity/
    radius_lt = 500.0

    # raw score is traffic volume divided by adjusted distance
    score_lt = aadt_volume / adj_distance_lt

    # weighted score is raw score times total population
    weighted_score = score_lt * total_pop

    # Return the actual distance value we used, that fixed radius,
    # and both the raw score and the weighted score.
    return (adj_distance_lt, radius_lt, score_lt, weighted_score)

def WeightedScoreUDF2(land_area, water_area, distance, total_pop, aadt_volume):
    # Difference from UDF1 is that we check land_area for 0 before calculating scores.
    #
    # Prevent artificially high scores due to zero or near-zero distances.
    # Enforce a minimum distance of 5 meters
    adj_distance_lt = min(5.0, distance)

    # We appear to be setting a fixed radius value to pass along for the benefit
    # of downstream code. This is understood to be a magic number.
    # AI recommends the 500 value, probably based on search results
    # such as: https://careshq.org/ss_whatsnewitem/ej-screen-traffic-proximity/
    radius_lt = 500.0

    score_lt = 0.0
    weighted_score = 0.0
    # TODO: consider if this should be changed to be a "close to 0" test.
    if land_area != 0:
        # raw score is traffic volume divided by adjusted distance
        score_lt = aadt_volume / adj_distance_lt

        # weighted score is raw score times total population
        weighted_score = score_lt * total_pop
    # else both scores remain 0.0 since land area is 0

    # Return the actual distance value we used, that fixed radius,
    # and both the raw score and the weighted score.
    return (adj_distance_lt, radius_lt, score_lt, weighted_score)

def main():
    """
    Simple test harness for WeightedScoreUDF1 and WeightedScoreUDF2.
    Uses the same values for repeated calls and includes at least one sample
    with land_area == 0.
    """
    samples = [
        # (land_area, water_area, distance, total_pop, aadt_volume)
        (1000, 50, 10.0, 200, 15000),   # normal plausible block
        (1000, 50, 3.0, 200, 15000),      # nearly same values, but distance < 5
        (0, 1000, 10.0, 50, 15000),      # zero land area case
    ]

    for idx, (aland, awater, dist, pop, aadt) in enumerate(samples, start=1):
        print(f"Sample {idx}: aland={aland}, awater={awater}, distance={dist}, totpop={pop}, aadt={aadt}")

        # Call WeightedScoreUDF1 once
        res1 = WeightedScoreUDF1(aland, awater, dist, pop, aadt)
        print(f"  WeightedScoreUDF1 -> adj_distance, radius, score, weighted_score = {res1}")

        # Call WeightedScoreUDF2 once
        res2 = WeightedScoreUDF2(aland, awater, dist, pop, aadt)
        print(f"  WeightedScoreUDF2 -> adj_distance, radius, score, weighted_score = {res2}")



# Only run the tests when executed directly, not when imported as a module
if __name__ == "__main__":
    main()
