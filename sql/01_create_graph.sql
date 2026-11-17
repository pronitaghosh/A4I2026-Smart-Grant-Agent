-- Smart Grant Agent: property graph over the Central Market corridor.
-- Arrows point from the thing depended upon to the thing that depends on it:
--   Supplies:      supplier -> buyer      (follow forward = exposure)
--   DrawsFootfall: employer block -> business
-- Naming rule (notebook Section 13, trap 1): dataset-qualified, unbackticked table
-- names in NODE/EDGE TABLES; bare table names in REFERENCES.
CREATE OR REPLACE PROPERTY GRAPH a4i_econ.econ_graph
  NODE TABLES (
    a4i_econ.businesses
      KEY (business_id)
      LABEL Business PROPERTIES (business_id, name, address, zip, naics4, latitude, longitude),
    a4i_econ.employer_blocks
      KEY (block_id)
      LABEL Employer PROPERTIES (block_id, tract, jobs)
  )
  EDGE TABLES (
    a4i_econ.supplies
      KEY (edge_id)
      SOURCE KEY (supplier_id) REFERENCES businesses (business_id)
      DESTINATION KEY (buyer_id) REFERENCES businesses (business_id)
      LABEL Supplies PROPERTIES (edge_id, intensity, metres),
    a4i_econ.draws_footfall_from
      KEY (edge_id)
      SOURCE KEY (block_id) REFERENCES employer_blocks (block_id)
      DESTINATION KEY (business_id) REFERENCES businesses (business_id)
      LABEL DrawsFootfall PROPERTIES (edge_id, worker_share, metres)
  );
