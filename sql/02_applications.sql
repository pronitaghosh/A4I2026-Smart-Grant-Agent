-- a4i_econ.applications: the officer's queue of 11 applications.
--
-- The businesses are real. Every financial field is illustrative, derived deterministically from
-- business_id with the formulas from agent/BUILD_GUIDE.md step 3a, and flagged illustrative = TRUE.
--
-- Applicants are chosen on purpose, not at random. The guide's random "deep" picks landed on tech
-- and parking firms whose supply links are all weak: with every link required to be >= 0.02, they
-- had 0 to 2 businesses downstream, so the graph never changed a decision. This queue has:
--   hub      171 Tradespace            113 buyers, all weak links; financially robust (8% drop, 9 months cash)
--   deep     16  Sour Flour            97 dependent businesses with every link >= 0.02: 69 direct, 28 at two hops
--            114 The Warfield Theater  7 (2 at two hops)
--            66  Non Plus Ultra        6 (2 at two hops)
--   one_hop  135 Honeybee Foods, 85 Supremo Pizza, 106 Blue Bottle Coffee
--   leaf     211 SF Proper Hotel - Villon, 185 Soliome, 73 Eddy St Bodega, 92 Oak Tree Hotel
CREATE OR REPLACE TABLE a4i_econ.applications AS
WITH picked AS (
  SELECT business_id, 'hub' AS bucket FROM UNNEST(['171']) AS business_id
  UNION ALL SELECT business_id, 'deep'    FROM UNNEST(['16', '114', '66']) AS business_id
  UNION ALL SELECT business_id, 'one_hop' FROM UNNEST(['135', '85', '106']) AS business_id
  UNION ALL SELECT business_id, 'leaf'    FROM UNNEST(['211', '185', '73', '92']) AS business_id
)
SELECT
  CONCAT('APP-', LPAD(CAST(ROW_NUMBER() OVER (ORDER BY FARM_FINGERPRINT(business_id)) AS STRING), 3, '0')) AS application_id,
  business_id,
  CAST(5000 + MOD(ABS(FARM_FINGERPRINT(CONCAT(business_id, 'amt'))), 11) * 1000 AS FLOAT64) AS requested_amount,
  -- The hub is given healthy finances on purpose: it is the "robust hub" case.
  IF(bucket = 'hub', 8.0,  CAST(10 + MOD(ABS(FARM_FINGERPRINT(CONCAT(business_id, 'rev'))), 45) AS FLOAT64)) AS revenue_drop_pct,
  IF(bucket = 'hub', 9.0,  CAST(1  + MOD(ABS(FARM_FINGERPRINT(CONCAT(business_id, 'cash'))), 6) AS FLOAT64)) AS months_cash_on_hand,
  2 + MOD(ABS(FARM_FINGERPRINT(CONCAT(business_id, 'emp'))), 14) AS self_reported_employees,
  CASE MOD(ABS(FARM_FINGERPRINT(CONCAT(business_id, 'why'))), 4)
    WHEN 0 THEN 'Foot traffic down since nearby office vacancies; behind on supplier invoices.'
    WHEN 1 THEN 'Rent increase at lease renewal; need bridge funding for three months.'
    WHEN 2 THEN 'Equipment failure; repair cost exceeds cash reserves.'
    ELSE 'Lost a major customer; payroll at risk next month.'
  END AS hardship_note,
  TRUE AS illustrative   -- every financial field above is invented for the demo
FROM picked;
