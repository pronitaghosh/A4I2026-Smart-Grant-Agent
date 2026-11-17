# Agent lane: step-by-step build guide

**Track:** 🏢 Main Street Relief · **Corridor:** Central Market (230 businesses, 86 with two-hop reach, 22% poverty)

**Scenario sentence (put it at the top of the deck too):**

> *"We run relief funding for the Central Market corridor. Eleven businesses have applied, and we
> have $50,000. Here is who we fund, how much, what each grant protects downstream, and what we
> are assuming."*

**What the officer does:** asks *"Who should we fund?"*
**What the agent returns:** a list of businesses, each with **$ award**, **% of request**, and
**reasons**: its own risk, the cascade it protects (named paths, hops, link strength), local
alternatives, neighborhood need, plus the runner-up, the assumptions, and a one-line note on which
evidence is modeled.

---

## 0. The design in one picture

```
Officer ──► ADK agent (Gemini)
              │  system prompt: who the officer is, which tool when, when to refuse
              │
              ├── custom Python tools (required "tool you built")
              │     get_cascade(business_id, depth)       ← multi-hop GQL over LocalEconomy
              │     recommend_grants(budget, depth)        ← the judgment: risk × cascade × need → $ awards
              │     exposed_non_applicants(depth)          ← "nobody at the far end applied"
              │
              └── MCP Toolbox for Databases on Cloud Run (required "managed MCP server")
                    list_applications · find_business · loan_history · tract_profile
                          │
                          ▼
                 BigQuery: a4i_econ.*  +  property graph a4i_econ.LocalEconomy
```

**Why this split:** simple lookups go through MCP. The cascade and the allocation have a depth rule,
weighting and assumptions in them, so they live in code we own and can defend. That is the split
the challenge README recommends.

**The core idea that keeps this from being a centrality calculator:**

```
value of a grant = (drop in the applicant's closure risk that the money buys)
                 × (1  +  contagion × uniqueness × Σ downstream exposure)
                 × neighborhood-need multiplier
```

A big hub with healthy finances has **low closure risk**, so the money barely changes anything and
it is *not* funded first. A distressed mid-sized supplier with no local substitutes is. The units
are **expected business closures avoided**, which is a number an officer can say out loud.

---

## 1. Who does what (2–3 people, ~4.5 hours)

| Time | Person A: graph + tools | Person B: judgment + prompt | Person C: MCP + deploy |
|---|---|---|---|
| 0:00–0:30 | Steps 2–3: confirm graph exists, build `applications` + `business_tract` | Step 4: scaffold ADK, write scenario | Step 9: write `tools.yaml`, deploy Toolbox to Cloud Run |
| 0:30–1:30 | Step 5: `tools.py` (cascade GQL) | Step 6: `allocator.py` + unit tests | Step 10: deploy a *hello-world* agent to Cloud Run early |
| 1:30–2:30 | Step 7: wire tools, run `adk web` | Step 8: system prompt, iterate on the 7 officer questions | Give front-end lane the endpoint + API contract |
| 2:30–3:30 | Step 11: failure cases | Step 12: sensitivity + equity audit numbers to story lane | Redeploy real agent, min-instances=1 |
| 3:30–4:30 | Freeze. Rehearse the demo question. Record backup screenshots. | | |

Work on a branch: `git checkout -b agent`.

---

## 2. Before you start: confirm what the data lane built

You are blocked until two things exist. Check them in the BigQuery console or Cloud Shell:

```bash
gcloud config set project YOUR_PROJECT_ID
bq ls a4i_econ                       # expect businesses, supplies, draws_footfall_from, tract_demographics, ...
bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM a4i_econ.businesses'   # ~230 for Central Market
```

If the tables are missing, the data lane (or you) runs `bash scripts/load.sh central-market`. It
also creates the **Enterprise reservation that graph queries need**.

Then confirm the property graph and that a multi-hop query runs:

```sql
GRAPH a4i_econ.LocalEconomy
MATCH (seed:Business)-[:Supplies]->{1,2}(x:Business)
RETURN seed.name AS business, COUNT(DISTINCT x.business_id) AS exposed_2hop
NEXT
FILTER exposed_2hop >= 5 AND exposed_2hop <= 25
RETURN business, exposed_2hop
ORDER BY exposed_2hop DESC
```

- Error *"require a reservation with Enterprise…"*: the reservation was just created. Wait a
  minute and rerun.
- Error *"graph not found"*: the data lane hasn't run `CREATE PROPERTY GRAPH` yet. The DDL is in
  Section 13 of the notebook. Agree with them on the **graph name (`LocalEconomy`) and edge labels
  (`Supplies`, `DrawsFootfall`)**, because your code depends on those names.

---

## 3. Create the two tables the agent needs

The repo gives you businesses and edges, but **no applicants**, and **no business→tract link**.
Create both. Coordinate with the data lane so only one person runs this.

### 3a. `applications`: the officer's queue (illustrative)

Eleven applicants picked from **real** Central Market businesses, deliberately mixed: one big hub,
four with real two-hop reach, two one-hop suppliers, four leaves. The financial fields are
**invented for the demo, and you must say so**. Nothing public has revenue or cash data. Note
that an application is the one place a business's **self-reported headcount** can legitimately
come from, which fills the "no public jobs data" gap.

```sql
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
```

Check it: `SELECT COUNT(*), SUM(requested_amount) FROM a4i_econ.applications`. You want 11 rows and
roughly $100k requested, which is about twice the budget. That scarcity is the point. The `bucket`
is **not** stored, so the agent has to discover the structure from the graph on its own.

### 3b. `business_tract`: which neighborhood each business sits in

`businesses` has coordinates but no tract. Spatial-join to the public tract geometry once:

```sql
CREATE OR REPLACE TABLE a4i_econ.business_tract AS
SELECT b.business_id,
       LPAD(t.geo_id, 11, '0') AS geo_id,          -- LPAD: the leading-zero trap, Section 9
       d.poverty_rate, d.no_vehicle_rate, d.assistance_rate, d.median_income
FROM a4i_econ.businesses b
JOIN `bigquery-public-data.geo_census_tracts.us_census_tracts_national` t
  ON t.state_fips_code = '06'
 AND ST_CONTAINS(t.tract_geom, ST_GEOGPOINT(b.longitude, b.latitude))
LEFT JOIN a4i_econ.tract_demographics d
  ON d.geo_id = LPAD(t.geo_id, 11, '0');
```

Check: row count is about 230 and `COUNTIF(poverty_rate IS NULL)` is small. If every
`poverty_rate` is null, the join keys disagree; compare `geo_id` lengths.

---

## 4. Scaffold the ADK agent

In Cloud Shell, from the repo root:

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install "google-adk[toolbox]" google-cloud-bigquery toolbox-core pytest
mkdir grant_agent && cd grant_agent
touch __init__.py agent.py tools.py allocator.py prompt.py
```

```
agent/
  grant_agent/
    __init__.py        from . import agent
    agent.py           root_agent
    tools.py           BigQuery + GQL tools (Step 5)
    allocator.py       pure-Python judgment (Step 6), no BigQuery, unit-testable
    prompt.py          system instruction (Step 8)
    requirements.txt
    .env               (not committed)
  tests/test_allocator.py
  toolbox/tools.yaml   (Step 9)
```

`grant_agent/__init__.py`:

```python
from . import agent
```

`grant_agent/.env` (add `agent/grant_agent/.env` to `.gitignore`):

```bash
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash        # use the newest Gemini your lab project offers
TOOLBOX_URL=https://toolbox-xxxx.a.run.app   # filled in after Step 9
```

`grant_agent/requirements.txt`:

```
google-adk[toolbox]
google-cloud-bigquery
toolbox-core
```

---

## 5. `tools.py`: the graph traversal (Person A)

This is the **required multi-hop traversal**. Three rules come from the notebook's Section 13:

- **Depth defaults to 2.** In Central Market, `{1,3}` from a well-connected business reaches about
  200 of 230 businesses, so the cascade saturates and stops meaning anything.
- **Return `TO_JSON(p)`.** That gives you the actual path (every node and edge with its
  `intensity`) so you can *show* the traversal, not just count it.
- **One query for all applicants**, not eleven.

```python
"""BigQuery-facing tools. Every function the agent can call has a docstring:
ADK sends it to Gemini as the tool description, so write it for the model."""
import json
import os
import re
from functools import lru_cache

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from . import allocator

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
DS = f"{PROJECT}.a4i_econ"
GRAPH = "a4i_econ.LocalEconomy"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

MODELED_NOTE = (
    "Businesses, addresses, industries, SBA loans and tract demographics are real. "
    "Supply links are MODELED: the industry-to-industry purchase rates are published by the "
    "U.S. Bureau of Economic Analysis, but which specific business buys from which is our "
    "assignment (industry match + walking distance). Applicant financials are illustrative."
)


@lru_cache(maxsize=1)
def _client() -> bigquery.Client:
    """Created on first use, not at import, so the package (and the allocator tests)
    load without credentials."""
    if not PROJECT or PROJECT == "your-project-id":
        raise RuntimeError("Set GOOGLE_CLOUD_PROJECT in grant_agent/.env to your team project.")
    return bigquery.Client(project=PROJECT, location="US")


def _rows(sql: str) -> list[dict]:
    return [dict(r) for r in _client().query(sql).result()]


def _parse_path(pj) -> tuple[list[dict], list[float]]:
    """TO_JSON(p) -> ordered node list and edge intensities."""
    if isinstance(pj, str):
        pj = json.loads(pj)
    nodes, intensities = [], []
    for el in pj:
        props = el.get("properties", {}) or {}
        if el.get("kind") == "node":
            nodes.append({"id": props.get("business_id"), "name": props.get("name"),
                          "naics4": props.get("naics4")})
        elif el.get("kind") == "edge":
            intensities.append(float(props.get("intensity") or 0.0))
    return nodes, intensities


def _cascades(seed_ids: list[str], depth: int) -> dict[str, dict]:
    """Multi-hop traversal from each seed. Returns
    {seed_id: {exposed_id: (weight, hops, path_names, intensities)}} keeping the strongest path."""
    ids = [i for i in seed_ids if _SAFE_ID.match(str(i))]
    if not ids:
        return {}
    depth = max(1, min(int(depth), 3))
    id_list = ",".join(f"'{i}'" for i in ids)
    gql = f"""
    GRAPH {GRAPH}
    MATCH p = (seed:Business)-[:Supplies]->{{1,{depth}}}(x:Business)
    WHERE seed.business_id IN ({id_list}) AND x.business_id != seed.business_id
    RETURN seed.business_id AS seed_id, TO_JSON(p) AS path_json
    LIMIT 50000
    """
    out: dict[str, dict] = {i: {} for i in ids}
    for r in _rows(gql):
        nodes, ints = _parse_path(r["path_json"])
        if len(nodes) < 2:
            continue
        x = nodes[-1]["id"]
        w = allocator.path_weight(ints)
        cur = out[r["seed_id"]].get(x)
        if cur is None or w > cur[0]:
            out[r["seed_id"]][x] = (w, len(ints), [n["name"] for n in nodes], ints)
    return out


@lru_cache(maxsize=1)
def _industry_counts() -> dict[str, int]:
    return {r["naics4"]: r["n"] for r in
            _rows(f"SELECT naics4, COUNT(*) AS n FROM `{DS}.businesses` GROUP BY naics4")}


def _charged_off_ids() -> set[str]:
    try:
        return {r["business_id"] for r in _rows(
            f"SELECT DISTINCT business_id FROM `{DS}.borrowed_from` WHERE charged_off")}
    except NotFound:            # many corridors have no SBA matches at all
        return set()


# ----------------------------------------------------------------------------- tools

def get_cascade(business_id: str, depth: int = 2) -> dict:
    """Trace what is exposed if this business closes, by walking Supplies edges forward
    1..depth hops through the BigQuery property graph. Use this when the officer asks
    "what does this business hold up?" or "show me why". Returns each exposed business,
    the hop distance, the strongest path (names in order) and its link strengths.
    depth: 1-3, default 2. Three hops saturates this corridor; say so if you use it."""
    biz = _rows(f"SELECT business_id, name, address, naics4 FROM `{DS}.businesses` "
                f"WHERE business_id = '{business_id}'") if _SAFE_ID.match(business_id) else []
    if not biz:
        return {"error": f"No business with id {business_id}. Use find_business to look it up."}
    seed = biz[0]
    exposed = _cascades([business_id], depth).get(business_id, {})
    alts = _industry_counts().get(seed["naics4"], 1) - 1
    items = sorted(exposed.items(), key=lambda kv: -kv[1][0])
    return {
        "business": seed,
        "depth": depth,
        "exposed_count": len(items),
        "by_hop": {h: sum(1 for _, v in items if v[1] == h) for h in range(1, depth + 1)},
        "local_alternatives_same_industry": alts,
        "is_leaf": len(items) == 0,
        "paths": [{"exposed_id": x, "hops": v[1], "weight": round(v[0], 3),
                   "path": " → ".join(v[2]),
                   "link_intensities": [round(i, 4) for i in v[3]]} for x, v in items[:15]],
        "evidence_note": MODELED_NOTE,
    }


def recommend_grants(budget: float = 50000, depth: int = 2) -> dict:
    """Recommend which applicants to fund and how much, from the applications queue.
    Combines each applicant's closure risk, the downstream cascade it protects (multi-hop
    graph traversal), how replaceable it is locally, and tract need, then allocates the
    budget to maximise expected business closures avoided. Also returns the runner-up,
    every assumption, and a sensitivity check showing which awards survive if the
    assumptions are wrong. Use this for "who should we fund?" and "what does $X change?"."""
    apps_rows = _rows(f"""
      WITH ind AS (SELECT naics4, COUNT(*) AS n FROM `{DS}.businesses` GROUP BY naics4)
      SELECT a.application_id, a.business_id, b.name, b.address, b.naics4,
             a.requested_amount, a.revenue_drop_pct, a.months_cash_on_hand,
             a.self_reported_employees, a.hardship_note,
             t.poverty_rate, t.geo_id, ind.n - 1 AS local_alternatives
      FROM `{DS}.applications` a
      JOIN `{DS}.businesses` b USING (business_id)
      LEFT JOIN `{DS}.business_tract` t USING (business_id)
      LEFT JOIN ind ON ind.naics4 = b.naics4""")
    if not apps_rows:
        return {"error": "The applications table is empty."}
    corridor_poverty = _rows(
        f"SELECT AVG(poverty_rate) AS p FROM `{DS}.business_tract`")[0]["p"] or None
    charged = _charged_off_ids()
    casc = _cascades([r["business_id"] for r in apps_rows], depth)

    apps = [allocator.Applicant(
        business_id=r["business_id"], name=r["name"], address=r["address"],
        requested=float(r["requested_amount"]),
        revenue_drop_pct=float(r["revenue_drop_pct"]),
        months_cash=float(r["months_cash_on_hand"]),
        employees=int(r["self_reported_employees"] or 0),
        hardship_note=r["hardship_note"],
        charged_off_before=r["business_id"] in charged,
        local_alternatives=int(r["local_alternatives"] or 0),
        poverty_rate=r["poverty_rate"],
        exposed=casc.get(r["business_id"], {}),
    ) for r in apps_rows]

    result = allocator.recommend(apps, float(budget), corridor_poverty)
    result["depth"] = depth
    result["evidence_note"] = MODELED_NOTE
    return result


def exposed_non_applicants(depth: int = 2) -> dict:
    """Businesses that sit downstream of applicants in the supply graph but did NOT apply,
    with any real SBA charge-off history. Use when the officer asks who else is at risk
    or whether someone at the far end of a chain should have applied."""
    ids = [r["business_id"] for r in _rows(f"SELECT business_id FROM `{DS}.applications`")]
    casc = _cascades(ids, depth)
    charged = _charged_off_ids()
    reach: dict[str, dict] = {}
    for seed, exposed in casc.items():
        for x, (w, hops, names, _) in exposed.items():
            if x in ids:
                continue
            e = reach.setdefault(x, {"business_id": x, "name": names[-1],
                                     "exposed_via": [], "max_weight": 0.0})
            e["exposed_via"].append(" → ".join(names))
            e["max_weight"] = max(e["max_weight"], round(w, 3))
            e["prior_sba_charge_off"] = x in charged
    ranked = sorted(reach.values(), key=lambda e: (-len(e["exposed_via"]), -e["max_weight"]))
    return {"count": len(ranked), "businesses": ranked[:15], "evidence_note": MODELED_NOTE}
```

**If `TO_JSON(p)` parsing returns no nodes,** print one raw row (`print(_rows(gql)[0])`) and adjust
`_parse_path` to the keys you actually see. Don't guess.

---

## 6. `allocator.py`: the judgment (Person B)

This is the tool that **holds the decision**: what exposure means, what money changes, how to
split a budget. It is pure Python, so you can test it without BigQuery. **Every assumption is in
one dict.** When the officer asks *"what are you assuming?"*, the agent reads this back.

```python
"""The judgment layer. No BigQuery. Units: expected business closures avoided."""
from dataclasses import dataclass, field

ASSUMPTIONS = {
    "STRONG_LINK": 0.05,        # a supplier worth >=5c per $ of the buyer's output = full-strength dependency
    "HOP_DECAY": 0.5,           # each extra hop halves the exposure
    "CONTAGION": 0.25,          # P(buyer closes | full-strength local supplier closes, not replaced)
    "MAX_RISK_REDUCTION": 0.40, # fully funding the request cuts the applicant's closure risk by 40%
    "EFFECT_CURVE": 0.5,        # partial award effect = (award/request) ** 0.5  (diminishing returns)
    "NEED_WEIGHT": 0.5,         # tract poverty above the corridor average raises value (capped 0.75x–1.5x)
    "STEP_FRACTION": 0.25,      # awards are made in 25% slices of each request
    "MIN_AWARD": 2000,          # smallest grant worth administering
}


@dataclass
class Applicant:
    business_id: str
    name: str
    address: str
    requested: float
    revenue_drop_pct: float
    months_cash: float
    employees: int = 0
    hardship_note: str = ""
    charged_off_before: bool = False
    local_alternatives: int = 0
    poverty_rate: float | None = None
    # exposed_id -> (weight, hops, path_names, intensities)
    exposed: dict = field(default_factory=dict)


def path_weight(intensities: list[float], A: dict = ASSUMPTIONS) -> float:
    w = 1.0
    for i in intensities:
        w *= min(1.0, i / A["STRONG_LINK"])
    return w * A["HOP_DECAY"] ** (len(intensities) - 1)


def baseline_risk(a: Applicant) -> float:
    """Illustrative closure-risk model from the application. Replace with something better."""
    p = 0.05 + 0.5 * min(a.revenue_drop_pct, 100) / 100 + 0.35 * max(0.0, (6 - a.months_cash) / 6)
    if a.charged_off_before:
        p += 0.10
    return max(0.02, min(0.90, p))


def risk_reduction(a: Applicant, award: float, A: dict) -> float:
    if award <= 0 or a.requested <= 0:
        return 0.0
    frac = min(1.0, award / a.requested)
    return baseline_risk(a) * A["MAX_RISK_REDUCTION"] * frac ** A["EFFECT_CURVE"]


def uniqueness(a: Applicant) -> float:
    return 1.0 / (1 + a.local_alternatives)


def need_mult(a: Applicant, corridor_poverty: float | None, A: dict) -> float:
    if a.poverty_rate is None or not corridor_poverty:
        return 1.0
    m = 1 + A["NEED_WEIGHT"] * (a.poverty_rate - corridor_poverty) / corridor_poverty
    return max(0.75, min(1.5, m))


def portfolio_value(apps, awards, corridor_poverty, A=ASSUMPTIONS) -> float:
    """Expected closures avoided by a whole set of awards. A downstream business exposed to two
    funded applicants is counted once (at its best protection), not twice."""
    total, best = 0.0, {}
    for a in apps:
        dp = risk_reduction(a, awards.get(a.business_id, 0.0), A)
        if dp == 0:
            continue
        nm = need_mult(a, corridor_poverty, A)
        total += dp * nm                                  # the applicant itself survives
        for x, (w, *_rest) in a.exposed.items():          # ...and so might what it supplies
            c = dp * A["CONTAGION"] * uniqueness(a) * w * nm
            best[x] = max(best.get(x, 0.0), c)
    return total + sum(best.values())


def allocate(apps, budget, corridor_poverty, A=ASSUMPTIONS):
    """Greedy: repeatedly give the next slice of money to whoever buys the most
    closures-avoided per dollar, until money or worthwhile moves run out."""
    awards = {a.business_id: 0.0 for a in apps}
    spent = 0.0
    while True:
        base = portfolio_value(apps, awards, corridor_poverty, A)
        move = None
        for a in apps:
            cur = awards[a.business_id]
            step = max(500.0, round(a.requested * A["STEP_FRACTION"] / 500) * 500)
            new = min(a.requested, cur + step)
            if cur == 0:
                new = max(new, min(a.requested, A["MIN_AWARD"]))
            cost = new - cur
            if cost <= 0 or spent + cost > budget:
                continue
            gain = (portfolio_value(apps, {**awards, a.business_id: new}, corridor_poverty, A) - base) / cost
            if move is None or gain > move[0]:
                move = (gain, a.business_id, new, cost)
        if move is None or move[0] <= 0:
            return awards, spent
        _, bid, new, cost = move
        awards[bid] = new
        spent += cost


def _reasons(a: Applicant, award: float, corridor_poverty, A) -> list[str]:
    hop = {1: 0, 2: 0, 3: 0}
    for (_w, h, *_r) in a.exposed.values():
        hop[h] = hop.get(h, 0) + 1
    r = [f"Baseline closure risk {baseline_risk(a):.0%}: reports {a.revenue_drop_pct:.0f}% revenue drop, "
         f"{a.months_cash:.0f} months of cash. ({a.hardship_note})"]
    if not a.exposed:
        r.append("No downstream businesses in the supply graph (a leaf): the case rests on its own survival.")
    else:
        top = max(a.exposed.values(), key=lambda v: v[0])
        r.append(f"Supplies {hop[1]} businesses directly and {hop[2]} more at two hops; strongest chain: "
                 f"{' → '.join(top[2])} (link intensities {', '.join(f'{i:.3f}' for i in top[3])}).")
        r.append(f"{a.local_alternatives} other local business(es) in the same industry"
                 + (": buyers could switch, so the cascade is discounted." if a.local_alternatives
                    else ": no local substitute, so the full cascade counts."))
    if a.poverty_rate is not None and corridor_poverty:
        r.append(f"Tract poverty {a.poverty_rate:.0%} vs corridor average {corridor_poverty:.0%}.")
    if a.charged_off_before:
        r.append("Real SBA record shows a prior charged-off loan (raises risk).")
    if baseline_risk(a) < 0.2 and len(a.exposed) >= 10:
        r.append(f"Well connected but financially robust (baseline risk {baseline_risk(a):.0%}): each dollar "
                 "changes its odds little, so its value comes almost entirely from the cascade it protects.")
    return r


SENSITIVITY = {
    "weak_contagion (0.10)":   {"CONTAGION": 0.10},
    "strong_contagion (0.50)": {"CONTAGION": 0.50},
    "linear_effect (split less)": {"EFFECT_CURVE": 1.0},
    "steep_diminishing (split more)": {"EFFECT_CURVE": 0.3},
    "ignore_need": {"NEED_WEIGHT": 0.0},
}


def recommend(apps, budget, corridor_poverty, A=ASSUMPTIONS) -> dict:
    awards, spent = allocate(apps, budget, corridor_poverty, A)
    total = portfolio_value(apps, awards, corridor_poverty, A)

    rows = []
    for a in apps:
        aw = awards[a.business_id]
        lose_if_dropped = total - portfolio_value(apps, {**awards, a.business_id: 0.0}, corridor_poverty, A)
        solo = portfolio_value([a], {a.business_id: a.requested}, corridor_poverty, A)
        rows.append({
            "business_id": a.business_id, "name": a.name, "address": a.address,
            "requested": a.requested, "award": aw,
            "pct_of_request": round(100 * aw / a.requested) if a.requested else 0,
            "pct_of_budget": round(100 * aw / budget, 1) if budget else 0,
            "baseline_risk": round(baseline_risk(a), 2),
            "risk_after_award": round(baseline_risk(a) - risk_reduction(a, aw, A), 2),
            "downstream_exposed": len(a.exposed),
            "closures_avoided_by_this_grant": round(lose_if_dropped, 3),
            "closures_avoided_per_10k_if_fully_funded": round(10000 * solo / a.requested, 3) if a.requested else 0,
            "reasons": _reasons(a, aw, corridor_poverty, A),
        })
    funded = sorted([r for r in rows if r["award"] > 0], key=lambda r: -r["closures_avoided_by_this_grant"])
    unfunded = sorted([r for r in rows if r["award"] == 0],
                      key=lambda r: -r["closures_avoided_per_10k_if_fully_funded"])

    scen = {}
    for name, override in SENSITIVITY.items():
        aw2, _ = allocate(apps, budget, corridor_poverty, {**A, **override})
        scen[name] = sorted(b for b, v in aw2.items() if v > 0)
    stable = set(b for b, v in awards.items() if v > 0)
    for ids in scen.values():
        stable &= set(ids)

    return {
        "budget": budget, "allocated": spent, "unallocated": budget - spent,
        "expected_closures_avoided_total": round(total, 3),
        "recommended": funded,
        "not_funded": unfunded,
        # If everyone got something, the "runner-up" is the grant closest to being cut.
        "runner_up": unfunded[0] if unfunded else (funded[-1] if funded else None),
        "weakest_funded": funded[-1] if funded else None,
        "assumptions": A,
        "sensitivity": {
            "funded_ids_by_scenario": scen,
            "funded_in_every_scenario": sorted(stable),
            "note": "MAX_RISK_REDUCTION scales every grant equally, so it changes HOW MANY closures "
                    "are avoided, not WHO is funded. The other assumptions can change who.",
        },
    }
```

### Unit tests: `agent/tests/test_allocator.py`

These prove the two behaviours judges will ask about. Run them with `cd agent && python -m pytest tests`.

```python
from grant_agent.allocator import Applicant, recommend, path_weight

def mk(bid, req, drop, cash, exposed=0, alts=0, pov=0.2):
    ex = {f"x{bid}{i}": (path_weight([0.05]), 1, [bid, f"x{i}"], [0.05]) for i in range(exposed)}
    return Applicant(bid, bid, "addr", req, drop, cash, local_alternatives=alts, poverty_rate=pov, exposed=ex)

def test_robust_hub_is_not_funded_first():
    hub = mk("hub", 10000, 5, 12, exposed=30)            # huge reach, healthy
    mid = mk("mid", 10000, 45, 1, exposed=8)             # medium reach, distressed, unique
    res = recommend([hub, mid], 10000, 0.2)
    assert res["recommended"][0]["business_id"] == "mid"

def test_leaf_still_fundable_and_budget_respected():
    apps = [mk("leaf", 8000, 50, 1), mk("sup", 12000, 30, 2, exposed=5, alts=3)]
    res = recommend(apps, 15000, 0.2)
    assert res["allocated"] <= 15000
    assert any(r["award"] > 0 for r in res["recommended"])
```

---

## 7. `agent.py`: wire it together

```python
import os
from google.adk.agents import Agent

from .prompt import SYSTEM_INSTRUCTION
from .tools import get_cascade, recommend_grants, exposed_non_applicants

tools = [recommend_grants, get_cascade, exposed_non_applicants]

# MCP Toolbox (Step 9). Optional so the agent still runs locally before Toolbox is up.
# ToolboxToolset connects on first use, not at import, so an unreachable Toolbox
# cannot stop the agent from loading. Needs: pip install "google-adk[toolbox]".
if os.getenv("TOOLBOX_URL"):
    from google.adk.tools.toolbox_toolset import ToolboxToolset
    tools.append(ToolboxToolset(server_url=os.environ["TOOLBOX_URL"], toolset_name="grant_toolset"))

root_agent = Agent(
    name="grant_agent",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    description="Advises a city economic-development officer on Main Street relief grants for the Central Market corridor.",
    instruction=SYSTEM_INSTRUCTION,
    tools=tools,
)
```

Run it locally:

```bash
cd agent
adk web            # opens the dev UI; pick grant_agent
# or: adk run grant_agent
```

First message to try: **"We have $50,000. Who should we fund?"**

---

## 8. `prompt.py`: the system instruction (Person B, the hardest part)

Iterate on this against the seven officer questions (Step 11). Keep it explicit about **when to
refuse** and **what to say it doesn't know**.

```python
SYSTEM_INSTRUCTION = """
You are a grant advisor for a San Francisco economic-development officer running Main Street relief
funding for the Central Market corridor. The officer has a fixed budget (default $50,000) and a
queue of applicants. Your job is a recommendation they can defend in a meeting: who to fund, how
much, and why, with the evidence shown.

TOOLS: WHEN TO USE WHICH
- "Who should we fund?", "split the budget", "what does $X change?" -> recommend_grants(budget).
  Never invent awards yourself; the amounts come from that tool.
- "Why this one?", "show me the chain", "what does X hold up?" -> get_cascade(business_id).
- "Who else is exposed?", "should someone else have applied?" -> exposed_non_applicants().
- Looking up an applicant, a business by name, loan history, or neighborhood data -> the
  Toolbox tools (list_applications, find_business, loan_history, tract_profile).
- If you only have a name, call find_business first to get the business_id.

HOW TO ANSWER "WHO SHOULD WE FUND?"
Return, in this order:
1. A table: Business | Address | Award $ | % of request | Closures avoided (expected) | One-line reason.
2. For each funded business, 2-4 bullet reasons taken from the tool's `reasons`, including the
   strongest cascade path written as A → B → C with hop count and link strength.
3. Runner-up (the tool's runner_up): if anyone was left unfunded, it is who just missed; say the
   specific number that separates them from the weakest funded applicant. If every applicant got
   something, runner_up is the grant closest to being cut; say that, and do not call it unfunded.
4. "What we are assuming": list the assumptions in plain English (e.g. "full funding cuts an
   applicant's closure risk by 40%"), and which awards survive the sensitivity check
   (funded_in_every_scenario) versus which depend on the assumptions.
5. One line on evidence: the evidence_note from the tool, verbatim.
6. Neighborhood: tract poverty for where the money lands.

RULES
- Every number you state must come from a tool result. If no tool gives it, say you don't know.
- Say "expected closures avoided", not "jobs saved". Per-business employment is not public;
  employee counts come only from the self-reported applications.
- Never recommend a business just because it is the most connected. If the hub is not funded,
  explain why (financially robust, so the money changes little).
- If an applicant has no downstream edges, say plainly that it is a leaf and its case rests on
  its own survival. That is not a reason to reject it.
- If two applicants are within ~10% of each other on closures avoided, say the data cannot
  meaningfully separate them, and name what extra information would (e.g. lease status,
  inspection history).
- If the budget cannot meaningfully reduce anyone's risk, say so instead of spreading money thin.
- Supply relationships are modeled, not observed. Do not describe a modeled link as fact
  ("X buys from Y"); say "is modeled as a likely supplier to".
- Applicant financials in this demo are illustrative. Say so if asked.
- Do not use race or ethnicity in any reasoning. Neighborhood need uses tract poverty only.
- Be concise. The officer is busy.
"""
```

---

## 9. MCP Toolbox on Cloud Run (Person C, start early)

This satisfies **"consume a Google-managed MCP server."** The Toolbox only runs the SQL you define
here, so the agent cannot run arbitrary queries.

`agent/toolbox/tools.yaml`. Replace `YOUR_PROJECT_ID`. The Toolbox config format has changed
between releases, so check it against the README for the version you deploy.

```yaml
sources:
  bq:
    kind: bigquery
    project: YOUR_PROJECT_ID
    location: US

tools:
  list_applications:
    kind: bigquery-sql
    source: bq
    description: List every grant application with business name, address, amount requested and hardship.
    statement: |
      SELECT a.application_id, a.business_id, b.name, b.address, b.naics4,
             a.requested_amount, a.revenue_drop_pct, a.months_cash_on_hand,
             a.self_reported_employees, a.hardship_note
      FROM a4i_econ.applications a JOIN a4i_econ.businesses b USING (business_id)
      ORDER BY a.application_id

  find_business:
    kind: bigquery-sql
    source: bq
    description: Find businesses in the corridor whose name contains the search text. Returns business_id.
    parameters:
      - name: name_contains
        type: string
        description: Part of the business name, case-insensitive.
    statement: |
      SELECT business_id, name, address, naics4
      FROM a4i_econ.businesses
      WHERE LOWER(name) LIKE CONCAT('%', LOWER(@name_contains), '%')
      LIMIT 10

  loan_history:
    kind: bigquery-sql
    source: bq
    description: Real SBA 7(a) loans for a business, including whether any were charged off (the business failed to repay).
    parameters:
      - name: business_id
        type: string
        description: The business_id.
    statement: |
      SELECT bank_name, gross_approval, approval_date, loan_status, charged_off
      FROM a4i_econ.borrowed_from WHERE business_id = @business_id

  tract_profile:
    kind: bigquery-sql
    source: bq
    description: Census tract demographics (poverty, vehicle access, public assistance, median income) for a business's location.
    parameters:
      - name: business_id
        type: string
        description: The business_id.
    statement: |
      SELECT geo_id, poverty_rate, no_vehicle_rate, assistance_rate, median_income
      FROM a4i_econ.business_tract WHERE business_id = @business_id

toolsets:
  grant_toolset: [list_applications, find_business, loan_history, tract_profile]
```

If `borrowed_from` doesn't exist in your corridor, delete `loan_history` from the file.

Deploy:

```bash
export PROJECT_ID=$(gcloud config get-value project)
gcloud services enable run.googleapis.com secretmanager.googleapis.com

gcloud iam service-accounts create toolbox-sa
for role in roles/bigquery.jobUser roles/bigquery.dataViewer roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member serviceAccount:toolbox-sa@$PROJECT_ID.iam.gserviceaccount.com --role $role
done

gcloud secrets create tools --data-file=agent/toolbox/tools.yaml
# after edits: gcloud secrets versions add tools --data-file=agent/toolbox/tools.yaml

gcloud run deploy toolbox \
  --image us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest \
  --service-account toolbox-sa@$PROJECT_ID.iam.gserviceaccount.com \
  --region us-central1 \
  --set-secrets "/app/tools.yaml=tools:latest" \
  --set-env-vars "PROJECT_ID=$PROJECT_ID" \
  --args="--tools-file=/app/tools.yaml","--address=0.0.0.0","--port=8080" \
  --min-instances=1 \
  --allow-unauthenticated   # hackathon shortcut; read-only predefined SQL over public data
```

Put the service URL into `TOOLBOX_URL` in `.env`. If the agent can't reach it, check
`gcloud run services logs read toolbox --region us-central1`.

---

## 10. Deploy the agent to Cloud Run (deploy early, redeploy often)

```bash
cd agent
adk deploy cloud_run \
  --project $PROJECT_ID --region us-central1 \
  --service_name grant-agent --with_ui \
  ./grant_agent

# Make sure the runtime has the env vars and minimum instances:
gcloud run services update grant-agent --region us-central1 --min-instances=1 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-central1,GEMINI_MODEL=gemini-2.5-flash,TOOLBOX_URL=https://toolbox-xxxx.a.run.app
```

The Cloud Run service account needs **BigQuery Job User**, **BigQuery Data Viewer** and **Vertex
AI User**. If BigQuery calls fail with a permissions error, that's the cause.

**Give the front-end lane this contract** (the ADK API server):

```
POST /apps/grant_agent/users/{user}/sessions/{session}          # create a session
POST /run   {"app_name":"grant_agent","user_id":"...","session_id":"...",
             "new_message":{"role":"user","parts":[{"text":"We have $50,000. Who should we fund?"}]}}
```

The response is a list of events. The event whose part is a `functionResponse` named
`recommend_grants` holds the **structured JSON** (awards, reasons, paths), so a custom UI can render
a funding table and draw one cascade without parsing the prose.

---

## 11. Test against the officer's afternoon

Run every one of these in `adk web` before the demo and fix the prompt or tools until each answer
is defensible:

| Officer asks | Expected behaviour |
|---|---|
| "We have $50,000. Who should we fund?" | Table with $ and %, reasons, runner-up, assumptions, evidence line |
| "Why fund X and not Y?" | Calls `get_cascade` on both and compares paths, risk and alternatives with numbers |
| "Why isn't the biggest supplier funded?" | Robust hub: low baseline risk, so the money changes little |
| "X has suppliers downstream. Are any already fragile?" | `loan_history` on the far-end businesses; reports real charge-offs if any |
| "Nobody at the far end applied. Should they have?" | `exposed_non_applicants` |
| "What does $8,000 actually change?" | States the risk-reduction assumption and the before/after risk; admits it's an assumption |
| "Whose neighborhood does this help?" | `tract_profile` / poverty vs corridor average |
| "Write it up, I present Friday." | Full Grant Memo (the README's 7 elements) |

**Failure cases you must handle (Rigor & judgment):**

- **Leaf applicant:** says "no downstream businesses" and doesn't fabricate a cascade.
- **Unknown business name:** uses `find_business`, and asks which one if there are several matches.
- **Indistinguishable applicants:** says the data can't separate them.
- **Tiny budget (try $3,000):** says honestly what it can and can't do.
- **Duplicate registrations:** if the same business shows up twice (Section 11 warns about this),
  the agent should flag it, not double-count it.

---

## 12. What to hand the story lane (the scoring moments)

Collect these numbers from your own runs. Judges reward them, and they have to appear in the deck
or video:

1. **"When does it not fund the biggest hub?"** Show the hub's row: high reach, low baseline risk,
   low closures avoided per $10k.
2. **The traversal, shown:** one `get_cascade` path chain from a funded applicant, e.g.
   `Supplier → Café → Event Hall` with link intensities. (Section 13's `ANY SHORTEST` query
   renders it as a picture in the notebook.)
3. **Sensitivity:** "These N awards hold under every assumption we tested; these depend on
   contagion strength." Note the honest line too: grant effectiveness changes *how many* closures
   are avoided, not *who* gets funded.
4. **Equity audit:** funded businesses' industries vs the corridor mix (are restaurants
   over-represented because the edge generator sees them better?), and funded tracts' poverty vs
   the corridor average. One quick query over `recommend_grants` output against
   `businesses`/`business_tract`.
5. **Evidence honesty:** the `MODELED_NOTE` sentence, verbatim.

---

## 13. If you get ahead (optional, in order of payoff)

1. **Ground truth:** run `baseline_risk`-style scoring over businesses with SBA loans and check
   whether the charged-off ones score higher. That's the only real failure data in the challenge.
2. **Footfall fragility:** add a `DrawsFootfall` traversal so an applicant that depends on a single
   large employer block gets a higher baseline risk.
3. **"Assume one loss is unavoidable":** rerun `allocate` with one business forced closed (its
   exposure set to zero) and show whether the recommendation changes.
4. **Intake agent:** a sub-agent that interviews an applicant about real suppliers and appends
   observed edges to `supplies`, turning modeled links into reported ones.
