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
