-- Cascade for one applicant: every business downstream along Supplies, 1 to 3 hops,
-- keeping the strongest path to each.
--   Stopping rule: every link in a chain must be at least @min_link (0.02 = 2 cents of each
--   input dollar). This drops generic suppliers that everyone buys a little from.
--   Weight: path strength = product of the link intensities along the chain.
WITH paths AS (
  SELECT * FROM GRAPH_TABLE(a4i_econ.econ_graph
    MATCH p = (s:Business {business_id: @seed_id})-[d:Supplies]->{1,3}(x:Business)
    WHERE s.business_id != x.business_id
    RETURN x.business_id AS business_id, x.name AS name, x.address AS address, x.naics4 AS naics4,
           ARRAY_LENGTH(d) AS hops,
           (SELECT EXP(SUM(LN(z.intensity))) FROM UNNEST(d) AS z) AS strength,
           (SELECT MIN(z.intensity) FROM UNNEST(d) AS z) AS weakest_link,
           ARRAY(SELECT n.name FROM UNNEST(NODES(p)) AS n WITH OFFSET o ORDER BY o) AS route)
  WHERE weakest_link >= @min_link
),
best AS (
  SELECT business_id, ANY_VALUE(name) AS name, ANY_VALUE(address) AS address, ANY_VALUE(naics4) AS naics4,
         ARRAY_AGG(STRUCT(hops, strength, weakest_link, route) ORDER BY strength DESC, hops LIMIT 1)[OFFSET(0)] AS path
  FROM paths GROUP BY business_id
)
SELECT business_id, name, address, naics4, path.hops AS hops, path.strength AS strength,
       path.weakest_link AS weakest_link, ARRAY_TO_STRING(path.route, ' -> ') AS route
FROM best
ORDER BY strength DESC, hops, name;
