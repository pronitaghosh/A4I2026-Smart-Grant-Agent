"""Hub check (PRD FR-11): the same budget allocated three ways, on the live tables.

  1. Centrality only: most supply links first, full requests, until the money runs out.
  2. Distress only:   highest closure risk first, full requests, until the money runs out.
  3. Ours:            allocator.recommend(), i.e. risk x cascade x need.

All three are scored with the same model (allocator.portfolio_value): expected closures
avoided, unweighted, as recommend_grants reports it, and the need-weighted priority our
allocator optimizes. Ours wins on its own objective by construction; the useful output is
who moves between the lists, and why.

Run from the repo root, with BigQuery read access to the lab project:
    gcloud auth application-default login
    .venv/bin/python scripts/hub_check.py [budget]
Or, without application-default credentials:
    GCLOUD_ACCESS_TOKEN="$(gcloud auth print-access-token)" .venv/bin/python scripts/hub_check.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-524776bdd295")

from grant_agent import allocator, tools  # noqa: E402

if os.environ.get("GCLOUD_ACCESS_TOKEN"):
    from google.cloud import bigquery
    from google.oauth2.credentials import Credentials

    _bq = bigquery.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"], location="US",
                          credentials=Credentials(token=os.environ["GCLOUD_ACCESS_TOKEN"]))
    tools._client = lambda: _bq

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 50000.0
DS = tools.DS

rows = tools._rows(f"""
  WITH ind AS (SELECT naics4, COUNT(*) AS n FROM `{DS}.businesses` GROUP BY naics4),
  links AS (SELECT supplier_id AS business_id, COUNT(DISTINCT buyer_id) AS n
            FROM `{DS}.supplies` GROUP BY 1)
  SELECT a.business_id, b.name, b.address, a.requested_amount, a.revenue_drop_pct,
         a.months_cash_on_hand, a.self_reported_employees, a.hardship_note,
         t.poverty_rate, ind.n - 1 AS alts, IFNULL(links.n, 0) AS links
  FROM `{DS}.applications` a
  JOIN `{DS}.businesses` b USING (business_id)
  LEFT JOIN `{DS}.business_tract` t USING (business_id)
  LEFT JOIN ind ON ind.naics4 = b.naics4
  LEFT JOIN links USING (business_id)""")
corridor_poverty = tools._rows(f"SELECT AVG(poverty_rate) AS p FROM `{DS}.business_tract`")[0]["p"]
charged = tools._charged_off_ids()
cascades = tools._cascades([r["business_id"] for r in rows], 2)

apps = [allocator.Applicant(
    business_id=r["business_id"], name=r["name"], address=r["address"],
    requested=float(r["requested_amount"]), revenue_drop_pct=float(r["revenue_drop_pct"]),
    months_cash=float(r["months_cash_on_hand"]), employees=int(r["self_reported_employees"] or 0),
    hardship_note=r["hardship_note"], charged_off_before=r["business_id"] in charged,
    local_alternatives=int(r["alts"] or 0), poverty_rate=r["poverty_rate"],
    exposed=cascades.get(r["business_id"], {}),
) for r in rows]
links = {r["business_id"]: r["links"] for r in rows}
by_id = {a.business_id: a for a in apps}


def fill(order):
    """Full requests in rank order, skipping any that no longer fit."""
    awards, left = {a.business_id: 0.0 for a in apps}, BUDGET
    for a in order:
        if a.requested <= left:
            awards[a.business_id] = a.requested
            left -= a.requested
    return awards


ours = allocator.recommend(apps, BUDGET, corridor_poverty)
plans = {
    "Centrality only (most supply links first)": fill(sorted(apps, key=lambda a: -links[a.business_id])),
    "Distress only (highest closure risk first)": fill(sorted(apps, key=lambda a: -allocator.baseline_risk(a))),
    "Ours (risk x cascade x need)": {r["business_id"]: r["award"]
                                     for r in ours["recommended"] + ours["not_funded"]},
}

print(f"Budget ${BUDGET:,.0f}, {len(apps)} applicants\n")
print(f"{'Applicant':32s} {'links':>5} {'risk':>5} {'downstream':>10}  " + "  ".join(
    f"{n.split(' (')[0]:>12}" for n in plans))
for a in sorted(apps, key=lambda a: -links[a.business_id]):
    awards = "  ".join(f"{plans[n][a.business_id]:>12,.0f}" for n in plans)
    print(f"{a.name[:32]:32s} {links[a.business_id]:>5} {allocator.baseline_risk(a):>5.0%} "
          f"{len(a.exposed):>10}  {awards}")
print()
RAW = {**allocator.ASSUMPTIONS, "NEED_WEIGHT": 0.0}
for name, awards in plans.items():
    closures = allocator.portfolio_value(apps, awards, corridor_poverty, RAW)
    priority = allocator.portfolio_value(apps, awards, corridor_poverty)
    print(f"{name}: spent ${sum(awards.values()):,.0f}, expected closures avoided {closures:.2f}, "
          f"need-weighted priority {priority:.2f}")
print("\nlinks = supply buyers before the weakest-link rule; downstream = businesses exposed "
      "after it (depth 2, every link >= 0.02).")
