from google.cloud import bigquery
import pytest

client = bigquery.Client()
GRAPH_NAME = "a4i_econ.LocalEconomy"  # Update if named differently in sql/

def run_query(sql: str):
    return list(client.query(sql).result())

def test_tc01_graph_execution():
    rows = run_query(f"""
        SELECT * FROM GRAPH_TABLE({GRAPH_NAME} MATCH (b:Business) RETURN b.business_id) LIMIT 1
    """)
    assert len(rows) == 1

def test_tc02_unique_keys():
    rows = run_query("""
        SELECT COUNT(*) - COUNT(DISTINCT business_id) AS dupes FROM `a4i_econ.businesses`
    """)
    assert rows[0].dupes == 0

def test_tc03_multihop_cascade():
    rows = run_query(f"""
        SELECT * FROM GRAPH_TABLE(
            {GRAPH_NAME}
            MATCH p = (seed:Business)-[:Supplies]->{{2,3}}(exposed:Business)
            RETURN seed.business_id AS seed_id, exposed.business_id AS exposed_id
        ) LIMIT 5
    """)
    assert len(rows) > 0, "Expected at least one multi-hop (2-3 hop) cascade path"

def test_tc04_anchor_reversal():
    rows = run_query(f"""
        SELECT * FROM GRAPH_TABLE(
            {GRAPH_NAME}
            MATCH (emp:Employer)-[:DrawsFootfall]->(retail:Business)<-[:Supplies]-(upstream:Business)
            RETURN emp.block_id, upstream.business_id
        ) LIMIT 5
    """)
    assert len(rows) > 0, "Expected upstream suppliers reachable via anchor closure reversal"

def test_tc05_leaf_isolation():
    # Pick a leaf node ID dynamically in python first to avoid subquery limitations in GQL
    leaf_rows = run_query("""
        SELECT b.business_id FROM `a4i_econ.businesses` b
        LEFT JOIN `a4i_econ.supplies` s ON b.business_id = s.supplier_id
        WHERE s.supplier_id IS NULL LIMIT 1
    """)
    if not leaf_rows:
        pytest.skip("No leaf nodes found in dataset")
    
    leaf_id = leaf_rows[0].business_id
    
    rows = run_query(f"""
        SELECT * FROM GRAPH_TABLE(
            {GRAPH_NAME}
            MATCH p = (seed:Business)-[:Supplies]->{{1,3}}(exposed:Business)
            WHERE seed.business_id = '{leaf_id}'
            RETURN exposed.business_id AS exposed_id
        )
    """)
    assert len(rows) == 0