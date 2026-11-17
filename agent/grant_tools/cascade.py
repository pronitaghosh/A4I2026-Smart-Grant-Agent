"""Cascade tracing and grant reasons for the Smart Grant Agent.

Every number in a reason comes from a BigQuery property-graph query over the
Central Market corridor. The language model only narrates what these functions
return; it never computes a cascade or a strength itself.

Direction: Supplies runs supplier -> buyer, so following it forward from an
applicant traces who is exposed if the applicant closes.
"""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path

from google.cloud import bigquery

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-524776bdd295")
LOCATION = "US"

# Stopping rule: a chain counts only if every link in it is a real dependency,
# at least 2 cents of each input dollar. Generic suppliers that every business
# buys a little from (data hosting, IT services) fall below it.
MIN_LINK = 0.02

# Two values that differ by less than this share are treated as
# indistinguishable: the agent must say the graph cannot separate them.
TIE_MARGIN = 0.10

TOP_NAMED = 5

CASCADE_SQL = Path(__file__).with_name("cascade.sql").read_text()

MODELED_NOTE = (
    "Supply links are modeled: the industry-to-industry purchase rates are published "
    "by the Bureau of Economic Analysis, but which business buys from which is "
    "assigned by industry match and walking distance."
)
STRENGTH_NOTE = (
    "Strength is the share of a downstream business's input spending that traces "
    "back to the applicant, multiplied along the chain."
)

# The corridor data does not change during a session, so cascades and business
# records are cached for the life of the process.
_cascades: dict[tuple[str, float], list[dict]] = {}
_businesses: dict[str, dict] = {}


@lru_cache(maxsize=1)
def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT, location=LOCATION)


def _query(sql: str, params: list) -> list[dict]:
    job = _client().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [dict(row.items()) for row in job.result()]


def businesses(business_ids: list[str]) -> dict[str, dict]:
    """Name, address and industry for each business_id, fetched in one query."""
    missing = [i for i in dict.fromkeys(business_ids) if i not in _businesses]
    if missing:
        rows = _query(
            "SELECT business_id, name, address, naics4 FROM a4i_econ.businesses "
            "WHERE business_id IN UNNEST(@ids)",
            [bigquery.ArrayQueryParameter("ids", "STRING", missing)],
        )
        for row in rows:
            _businesses[row["business_id"]] = row
    unknown = [i for i in business_ids if i not in _businesses]
    if unknown:
        raise ValueError(f"No business with business_id {', '.join(unknown)} in the corridor.")
    return {i: dict(_businesses[i]) for i in business_ids}


def _dedupe(rows: list[dict]) -> list[dict]:
    """Count each location once. The registry holds some businesses twice under the
    same name and address; rows arrive strongest first, so the first one is kept."""
    seen: set[tuple[str, str]] = set()
    out = []
    for e in rows:
        key = (e["name"].strip().lower(), (e["address"] or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        e["strength"] = round(e["strength"], 4)
        e["weakest_link"] = round(e["weakest_link"], 4)
        e["route_ids"] = list(e["route_ids"])
        out.append(e)
    return out


def trace_many(business_ids: list[str], min_link: float = MIN_LINK) -> dict[str, list[dict]]:
    """Exposed businesses for several applicants, from one graph query.

    Returns a mapping from each business_id to its exposed businesses, strongest
    first, each with hops, strength, weakest link, route and route_ids.
    """
    missing = [i for i in dict.fromkeys(business_ids) if (i, min_link) not in _cascades]
    if missing:
        rows = _query(
            CASCADE_SQL,
            [
                bigquery.ArrayQueryParameter("seed_ids", "STRING", missing),
                bigquery.ScalarQueryParameter("min_link", "FLOAT64", min_link),
            ],
        )
        grouped: dict[str, list[dict]] = {i: [] for i in missing}
        for row in rows:
            grouped[row.pop("seed_id")].append(row)
        for i, exposed in grouped.items():
            _cascades[(i, min_link)] = _dedupe(exposed)
    return {i: copy.deepcopy(_cascades[(i, min_link)]) for i in business_ids}


def summarize(exposed: list[dict]) -> dict:
    return {
        "businesses_exposed": len(exposed),
        "two_or_more_hops": sum(1 for e in exposed if e["hops"] >= 2),
        "exposure": round(sum(e["strength"] for e in exposed), 3),
    }


def trace_cascade(business_id: str, min_link: float = MIN_LINK) -> dict:
    """Find the businesses exposed if this business closes.

    Follows Supplies forward from the business for 1 to 3 hops in the BigQuery
    property graph a4i_econ.LocalEconomy, keeps only chains whose every link is at
    least min_link, and keeps the strongest such path to each exposed business.

    Args:
        business_id: The applicant's business_id from a4i_econ.businesses.
        min_link: Stopping rule; 0.02 means every link is at least 2 cents per input dollar.

    Returns:
        The applicant, every exposed business with hops, strength and route,
        a summary, and the GQL query that produced it.
    """
    exposed = trace_many([business_id], min_link)[business_id]
    return {
        "applicant": businesses([business_id])[business_id],
        "min_link": min_link,
        "exposed": exposed,
        "summary": summarize(exposed),
        "query": CASCADE_SQL,
    }


def _label(e: dict, names: list[str]) -> str:
    """A business name, with its address when another exposed business shares the name."""
    return f"{e['name']}, {e['address']}" if names.count(e["name"]) > 1 else e["name"]


def _protects_line(c: dict) -> str:
    a, s, ex = c["applicant"], c["summary"], c["exposed"]
    if not ex:
        return (
            f"{a['name']} has no downstream business linked by {c['min_link']} or more "
            "per input dollar. A grant here protects the business itself, not a cascade."
        )
    names = [e["name"] for e in ex]
    named = "; ".join(
        f"{_label(e, names)} ({e['hops']} hop{'s' if e['hops'] > 1 else ''}, strength {e['strength']:.3f})"
        for e in ex[:TOP_NAMED]
    )
    more = f", and {len(ex) - TOP_NAMED} more" if len(ex) > TOP_NAMED else ""
    return (
        f"If {a['name']} ({a['address']}) closes, {s['businesses_exposed']} businesses "
        f"downstream lose a supplier they depend on, {s['two_or_more_hops']} of them two "
        f"or more hops away. Strongest: {named}{more}."
    )


def compare_cascades(applicant_id: str, runner_up_id: str, min_link: float = MIN_LINK) -> dict:
    """Compare what funding the applicant protects against funding the runner-up.

    Args:
        applicant_id: business_id of the business recommended for funding.
        runner_up_id: business_id of the next-best candidate.
        min_link: Stopping rule shared by both cascades.

    Returns:
        Both summaries, the overlap between the cascades, what only the applicant
        protects, and a verdict: "applicant", "runner_up" or "indistinguishable".
    """
    a = trace_cascade(applicant_id, min_link)
    r = trace_cascade(runner_up_id, min_link)
    ids_r = {e["business_id"] for e in r["exposed"]}
    only_a = [e for e in a["exposed"] if e["business_id"] not in ids_r]

    ea, er = a["summary"]["exposure"], r["summary"]["exposure"]
    if max(ea, er) == 0 or abs(ea - er) / max(ea, er) < TIE_MARGIN:
        verdict = "indistinguishable"
    else:
        verdict = "applicant" if ea > er else "runner_up"

    return {
        "applicant": {**a["applicant"], **a["summary"]},
        "runner_up": {**r["applicant"], **r["summary"]},
        "shared_businesses": len({e["business_id"] for e in a["exposed"]} & ids_r),
        "only_applicant_protects": [_label(e, [x["name"] for x in only_a]) for e in only_a[:TOP_NAMED]],
        "only_applicant_count": len(only_a),
        "verdict": verdict,
    }


def grant_reasons(applicant_id: str, runner_up_id: str | None = None) -> dict:
    """Write the cascade-based reasons for funding one applicant.

    Covers what the grant protects, the traversal behind it, why this applicant
    and not the runner-up, and the honest caveats. Dollar amounts and the
    probability a grant changes come from the allocation tool, not from here.

    Args:
        applicant_id: business_id of the business recommended for funding.
        runner_up_id: Optional business_id of the next-best candidate.

    Returns:
        Reason lines keyed by memo element, plus the routes and query to show.
    """
    c = trace_cascade(applicant_id)
    reasons = {
        "what_it_protects": _protects_line(c),
        "traversal_shown": [e["route"] for e in c["exposed"][:TOP_NAMED]],
        "query": c["query"],
        "strength_means": STRENGTH_NOTE,
        "modeled_evidence": MODELED_NOTE,
        "caveat": (
            "Exposed is not doomed: each downstream business could find another "
            "supplier, at a cost. A grant changes the chance the applicant stays "
            "open; it does not guarantee it."
        ),
    }
    if runner_up_id:
        cmp = compare_cascades(applicant_id, runner_up_id)
        a, r = cmp["applicant"], cmp["runner_up"]
        if cmp["verdict"] == "indistinguishable":
            line = (
                f"The graph cannot separate {a['name']} (exposure {a['exposure']}) from "
                f"{r['name']} (exposure {r['exposure']}): they differ by less than "
                f"{TIE_MARGIN:.0%}. Decide between them on something other than the cascade."
            )
        else:
            winner, loser = (a, r) if cmp["verdict"] == "applicant" else (r, a)
            shared = cmp["shared_businesses"]
            overlap = (
                f"{shared} businesses sit in both cascades and count once."
                if shared else "The two cascades do not overlap."
            )
            line = (
                f"{winner['name']} protects more: exposure {winner['exposure']} across "
                f"{winner['businesses_exposed']} businesses, against {loser['exposure']} across "
                f"{loser['businesses_exposed']} for {loser['name']}. {overlap}"
            )
            if cmp["verdict"] == "applicant" and cmp["only_applicant_protects"]:
                line += " Only funding " + a["name"] + " protects: " + ", ".join(
                    cmp["only_applicant_protects"]) + "."
        reasons["why_not_runner_up"] = line
        reasons["comparison"] = cmp
    return reasons


if __name__ == "__main__":
    import json
    import sys

    applicant = sys.argv[1] if len(sys.argv) > 1 else "114"
    runner_up = sys.argv[2] if len(sys.argv) > 2 else None
    out = grant_reasons(applicant, runner_up)
    out.pop("query")
    print(json.dumps(out, indent=2, default=str))
