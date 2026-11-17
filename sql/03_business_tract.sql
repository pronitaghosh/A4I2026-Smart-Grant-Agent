-- business_tract: the census tract each business sits in, with that tract's need signal.
-- Based on agent/BUILD_GUIDE.md step 3b, with one deliberate change.
--
-- Why not join a4i_econ.tract_demographics, as the guide does: that table holds 7 tracts
-- chosen by bounding box, and only 1 of them contains a corridor business. 228 of 230
-- businesses came back with no poverty rate.
--
-- Why ACS 2019 5-year, not 2020: the public tract boundaries in BigQuery use 2010 tract
-- numbers (e.g. 06075017601, home to 171 of our 230 businesses). ACS 2020 5-year switched
-- to 2020 numbers (0176.01 split into 0176.02-04), so 4 of our 6 tracts have no 2020 row.
-- ACS 2019 5-year uses the same 2010 numbers as the boundaries and covers all 6.
-- Formulas match notebook Section 10 (cell "pull tract demographics").
CREATE OR REPLACE TABLE a4i_econ.business_tract AS
SELECT
  b.business_id,
  LPAD(t.geo_id, 11, '0') AS geo_id,
  SAFE_DIVIDE(SAFE_CAST(a.poverty AS FLOAT64),
              SAFE_CAST(a.pop_determined_poverty_status AS FLOAT64))          AS poverty_rate,
  SAFE_DIVIDE(SAFE_CAST(a.no_cars AS FLOAT64), SAFE_CAST(a.households AS FLOAT64)) AS no_vehicle_rate,
  SAFE_DIVIDE(SAFE_CAST(a.households_public_asst_or_food_stamps AS FLOAT64),
              SAFE_CAST(a.households AS FLOAT64))                             AS assistance_rate,
  SAFE_CAST(a.median_income AS FLOAT64)                                       AS median_income,
  'ACS 2019 5-year' AS acs_vintage
FROM a4i_econ.businesses b
JOIN `bigquery-public-data.geo_census_tracts.us_census_tracts_national` t
  ON t.state_fips_code = '06'
 AND ST_CONTAINS(t.tract_geom, ST_GEOGPOINT(b.longitude, b.latitude))
LEFT JOIN `bigquery-public-data.census_bureau_acs.censustract_2019_5yr` a
  ON LPAD(a.geo_id, 11, '0') = LPAD(t.geo_id, 11, '0');
