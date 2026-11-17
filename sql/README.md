# sql/

The BigQuery objects the agent and the grant desk read, on top of the tables
`scripts/load.sh central-market` loads into `a4i_econ`. Run them in order from the repo root;
each one replaces what it creates, so re-running is safe.

```bash
bq query --use_legacy_sql=false < sql/01_create_graph.sql
bq query --use_legacy_sql=false < sql/02_applications.sql
bq query --use_legacy_sql=false < sql/03_business_tract.sql
```

| File | Creates | Notes |
|---|---|---|
| `01_create_graph.sql` | Property graph `a4i_econ.LocalEconomy` | Nodes `Business`, `Employer`; edges `Supplies` (supplier to buyer), `DrawsFootfall` (block to business). Names match `agent/BUILD_GUIDE.md`. |
| `02_applications.sql` | Table `a4i_econ.applications` | 11 applicants, verbatim from the build guide. Businesses are real; every financial field is illustrative and flagged `illustrative = TRUE`. |
| `03_business_tract.sql` | Table `a4i_econ.business_tract` | Each business's census tract and need signal. Deviates from the guide on purpose; see below. |

## Why `business_tract` uses ACS 2019, not `tract_demographics`

The guide joins `a4i_econ.tract_demographics`. That table holds 7 tracts picked by bounding
box, and only 1 contains a corridor business, so 228 of 230 businesses had no poverty rate.

Joining the census directly has a second trap: BigQuery's tract boundaries use 2010 tract
numbers, while ACS 2020 5-year uses 2020 numbers (tract 0176.01, home to 171 of our 230
businesses, was split into 0176.02 to 0176.04). ACS 2019 5-year uses the 2010 numbers and
covers all 6 corridor tracts. Formulas match the notebook's Section 10.

Result, checked 2026-11-17: 230 of 230 businesses have demographics; corridor average poverty
21.5% (the README says 22%). Say "2019 five-year estimates" when you quote these numbers.

## Checks worth rerunning

```sql
SELECT COUNT(*), SUM(requested_amount) FROM a4i_econ.applications;        -- 11, $127,000
SELECT COUNT(*), COUNTIF(poverty_rate IS NULL) FROM a4i_econ.business_tract;  -- 230, 0
```
