-- Copied verbatim from agent/BUILD_GUIDE.md (step 3a) so the agent lane's tools find what they expect.
CREATE OR REPLACE TABLE a4i_econ.applications AS
WITH direct AS (
  SELECT supplier_id AS business_id, COUNT(DISTINCT buyer_id) AS direct_buyers
  FROM a4i_econ.supplies GROUP BY 1
),
two_hop AS (
  SELECT a.supplier_id AS business_id, COUNT(DISTINCT b.buyer_id) AS hop2
  FROM a4i_econ.supplies a
  JOIN a4i_econ.supplies b ON b.supplier_id = a.buyer_id
  WHERE b.buyer_id != a.supplier_id
  GROUP BY 1
),
tagged AS (
  SELECT bz.business_id,
         IFNULL(d.direct_buyers, 0) AS direct_buyers,
         IFNULL(t.hop2, 0)          AS hop2,
         RANK() OVER (ORDER BY IFNULL(d.direct_buyers, 0) DESC) AS reach_rank
  FROM a4i_econ.businesses bz
  LEFT JOIN direct d USING (business_id)
  LEFT JOIN two_hop t USING (business_id)
),
bucketed AS (
  SELECT *, CASE WHEN reach_rank = 1 THEN 'hub'
                 WHEN hop2 > 0       THEN 'deep'
                 WHEN direct_buyers > 0 THEN 'one_hop'
                 ELSE 'leaf' END AS bucket
  FROM tagged
),
picked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY bucket ORDER BY FARM_FINGERPRINT(business_id)) AS rn
  FROM bucketed
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
FROM picked
WHERE (bucket = 'hub' AND rn <= 1)
   OR (bucket = 'deep' AND rn <= 4)
   OR (bucket = 'one_hop' AND rn <= 2)
   OR (bucket = 'leaf' AND rn <= 4);
