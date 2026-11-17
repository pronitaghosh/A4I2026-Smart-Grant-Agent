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
