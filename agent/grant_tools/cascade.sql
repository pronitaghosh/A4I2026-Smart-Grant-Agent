-- Cascades for one or more applicants: every business downstream along Supplies, 1 to 3 hops,
-- keeping the strongest path from each applicant to each exposed business.
--   Stopping rule: every link in a chain must be at least @min_link (0.02 = 2 cents of each
--   input dollar). This drops generic suppliers that everyone buys a little from.
--   Weight: path strength = product of the link intensities along the chain.
WITH paths AS (
  SELECT * FROM GRAPH_TABLE(a4i_econ.LocalEconomy
    MATCH p = (s:Business)-[d:Supplies]->{1,3}(x:Business)
    WHERE s.business_id IN UNNEST(@seed_ids) AND s.business_id != x.business_id
    RETURN s.business_id AS seed_id,
           x.business_id AS business_id, x.name AS name, x.address AS address, x.naics4 AS naics4,
           ARRAY_LENGTH(d) AS hops,
           (SELECT EXP(SUM(LN(z.intensity))) FROM UNNEST(d) AS z) AS strength,
           (SELECT MIN(z.intensity) FROM UNNEST(d) AS z) AS weakest_link,
           ARRAY(SELECT n.business_id FROM UNNEST(NODES(p)) AS n WITH OFFSET o ORDER BY o) AS route_ids,
           ARRAY(SELECT n.name FROM UNNEST(NODES(p)) AS n WITH OFFSET o ORDER BY o) AS route_names)
  WHERE weakest_link >= @min_link
),
best AS (
  SELECT seed_id, business_id, ANY_VALUE(name) AS name, ANY_VALUE(address) AS address,
         ANY_VALUE(naics4) AS naics4,
         ARRAY_AGG(STRUCT(hops, strength, weakest_link, route_ids, route_names)
                   ORDER BY strength DESC, hops LIMIT 1)[OFFSET(0)] AS path
  FROM paths GROUP BY seed_id, business_id
)
SELECT seed_id, business_id, name, address, naics4, path.hops AS hops, path.strength AS strength,
       path.weakest_link AS weakest_link, path.route_ids AS route_ids,
       ARRAY_TO_STRING(path.route_names, ' -> ') AS route
FROM best
ORDER BY seed_id, strength DESC, hops, name;
