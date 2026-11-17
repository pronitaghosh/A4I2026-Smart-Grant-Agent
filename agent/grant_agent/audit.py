"""Equity audit of a funding plan. Pure Python, no BigQuery.
Uses industry and tract poverty only. Never race or ethnicity."""
from collections import Counter

# NAICS 2-digit sectors that survive the notebook's storefront filter.
SECTORS = {
    "31": "Manufacturing", "32": "Manufacturing", "33": "Manufacturing",
    "44": "Retail", "45": "Retail", "51": "Information", "54": "Professional services",
    "62": "Health care", "71": "Arts & recreation", "72": "Food service",
    "81": "Personal & repair services",
}
# Industries that buy from many others, so the BEA-based edge generator gives them many supply
# links: the bias the challenge README warns about (personal services buy from almost no one).
INPUT_HEAVY = {"Food service", "Manufacturing"}
OVER, UNDER = 1.5, 0.67        # flag a sector at >= 1.5x or <= 0.67x its corridor share
MIN_SHARE = 0.05               # ratio flags need 5% of the corridor: one business swings tiny sectors
OVER_GAP = 0.10                # ...but money >= 10 points above corridor share is flagged at any size
CASCADE_DRIVEN = 0.5           # money is cascade-driven if >50% of its value is modeled cascade


def sector(naics4) -> str:
    return SECTORS.get(str(naics4 or "")[:2], "Other")


def _shares(weights: Counter) -> dict:
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total else {}


def _wavg(pairs) -> float | None:
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw else None


def _cascade_share(r: dict) -> float:
    own, casc = r.get("own_value") or 0.0, r.get("cascade_value") or 0.0
    return casc / (own + casc) if own + casc > 0 else 0.0


def _compare(label: str, funded_v, corridor_v, more: str, less: str) -> str | None:
    if funded_v is None or not corridor_v:
        return None
    if abs(funded_v - corridor_v) < 0.005:
        direction = "about the corridor average"
    else:
        direction = more if funded_v > corridor_v else less
    return (f"{label}: {funded_v:.0%} where the money lands (dollar-weighted) vs "
            f"{corridor_v:.0%} corridor average, {direction}.")


def audit(funded: list[dict], naics_by_id: dict, applicant_ids: list[str], tract_by_id: dict) -> dict:
    """
    funded:        recommend()["recommended"] rows (business_id, name, award, own_value, cascade_value)
    naics_by_id:   {business_id: naics4} for EVERY business in the corridor
    applicant_ids: business_ids that applied (funded or not)
    tract_by_id:   {business_id: {"poverty_rate": .., "no_vehicle_rate": ..}} for every business
    """
    corridor = _shares(Counter(sector(n) for n in naics_by_id.values()))
    applicants = _shares(Counter(sector(naics_by_id.get(b)) for b in applicant_ids))
    money_w, cascade_w = Counter(), Counter()
    for r in funded:
        s = sector(naics_by_id.get(r["business_id"]))
        money_w[s] += r["award"]
        cascade_w[s] += r["award"] * _cascade_share(r)
    money = _shares(money_w)

    # ---- 1. Industry, and whether the edge generator's bias shows
    industry, ranked = [], []
    for s in sorted(set(corridor) | set(applicants) | set(money)):
        c, a, m = corridor.get(s, 0.0), applicants.get(s, 0.0), money.get(s, 0.0)
        vs_corridor = m / c if c else None
        sector_cascade = cascade_w[s] / money_w[s] if money_w[s] else None
        flag = ""
        if (vs_corridor is not None and c >= MIN_SHARE and vs_corridor >= OVER) or m - c >= OVER_GAP:
            flag = "over"      # the gap rule keeps small input-heavy sectors (manufacturing) visible
        elif vs_corridor is not None and c >= MIN_SHARE and vs_corridor <= UNDER:
            flag = "under"
        # The bias shows when an input-heavy sector is over-represented, gets MORE money than its
        # share of applicants (so the allocator amplified it), and that money comes mostly from
        # modeled supply cascades.
        bias = (flag == "over" and s in INPUT_HEAVY and a > 0 and m > a
                and (sector_cascade or 0.0) > CASCADE_DRIVEN)
        # Separate "who applied" from "how the allocator chose", or the audit blames the wrong thing.
        if not flag:
            cause = ""
        elif bias:
            cause = ("the edge generator's bias toward input-heavy industries "
                     "(most of this money comes from modeled supply cascades)")
        elif a == 0:
            cause = "no applicants from this sector"
        elif m / a >= OVER or m / a <= UNDER:
            cause = "the allocator (money share differs from applicant share)"
        else:
            cause = "the applicant pool (the scenario's chosen applicants, not real demand)"
        industry.append({"sector": s, "corridor_share": round(c, 3), "applicant_share": round(a, 3),
                         "money_share": round(m, 3),
                         "money_vs_corridor": round(vs_corridor, 2) if c >= MIN_SHARE else None,
                         "cascade_share_of_money": round(sector_cascade, 2) if sector_cascade is not None else None,
                         "flag": flag, "likely_cause": cause, "edge_generator_bias": bias})
        if flag:
            # A ratio over a tiny corridor share is meaningless (one business is 50x), so say "small sector".
            multiple = f"{vs_corridor:.1f}x" if c >= MIN_SHARE else "small sector"
            ranked.append((abs(m - c), f"{s} is {flag}-represented: {m:.0%} of the money vs {c:.0%} "
                                       f"of corridor businesses ({multiple}). Likely cause: {cause}."))
    # Biggest gap between money share and corridor share first.
    industry_findings = [text for _gap, text in sorted(ranked, key=lambda f: -f[0])]

    bias_sectors = [r["sector"] for r in industry if r["edge_generator_bias"]]
    if bias_sectors:
        b = next(r for r in industry if r["sector"] == bias_sectors[0])
        bias_line = (f"Edge-generator bias shows: {', '.join(bias_sectors)} gets {b['money_share']:.0%} of the "
                     f"money vs {b['applicant_share']:.0%} of applicants, and "
                     f"{b['cascade_share_of_money']:.0%} of that money's value comes from modeled supply cascades.")
    else:
        bias_line = "No sign of the edge generator's bias toward input-heavy industries in this plan."

    # ---- 2. Need: poverty AND car access
    def rate(bid, key):
        return (tract_by_id.get(bid) or {}).get(key)

    need = {}
    for key in ("poverty_rate", "no_vehicle_rate"):
        need[key] = {
            "corridor_avg": _wavg((rate(b, key), 1) for b in naics_by_id),
            "applicant_avg": _wavg((rate(b, key), 1) for b in applicant_ids),
            "funded_dollar_weighted": _wavg((rate(r["business_id"], key), r["award"]) for r in funded),
        }
    p, v = need["poverty_rate"], need["no_vehicle_rate"]
    poverty_line = _compare("Residents below poverty", p["funded_dollar_weighted"], p["corridor_avg"],
                            "poorer than average", "less poor than average")
    car_line = _compare("Households without a car", v["funded_dollar_weighted"], v["corridor_avg"],
                        "more car-free than average", "less car-free than average")

    # ---- 3. Modeled evidence
    driven = [{"business_id": r["business_id"], "name": r["name"], "award": r["award"],
               "cascade_share": round(_cascade_share(r), 2)}
              for r in funded if _cascade_share(r) > CASCADE_DRIVEN]
    plan_cascade_share = _wavg((_cascade_share(r), r["award"]) for r in funded
                               if (r.get("own_value") or 0) + (r.get("cascade_value") or 0) > 0)
    driven_line = (f"{len(driven)} of {len(funded)} awards are driven mainly by modeled supply links"
                   + (f" ({', '.join(d['name'] for d in driven)})." if driven else "."))

    # ---- What goes in the memo: the biggest skew, poverty, car access, and the bias verdict.
    memo_lines = [industry_findings[0] if industry_findings else "No sector is over- or under-represented.",
                  poverty_line or "Poverty: no census data for the funded businesses.",
                  car_line or "Car access: no census data for the funded businesses.",
                  bias_line]
    findings = industry_findings + [x for x in (poverty_line, car_line) if x] + [bias_line, driven_line]

    return {
        "headline": memo_lines[0],
        "memo_lines": memo_lines,
        "findings": findings,
        "industry": industry,
        "edge_generator_bias": {"shows": bool(bias_sectors), "sectors": bias_sectors},
        "need": {k: {kk: (round(vv, 3) if vv is not None else None) for kk, vv in val.items()}
                 for k, val in need.items()},
        "modeled_evidence": {
            "plan_cascade_share_dollar_weighted": round(plan_cascade_share, 2) if plan_cascade_share is not None else None,
            "cascade_driven_awards": driven,
        },
        "caveats": [
            f"Only {len(funded)} funded businesses: shares move a lot with one award. Read directions, not decimals.",
            "The applicant queue is a scenario the team chose (sql/02_applications.sql) to include a hub, "
            "real cascades, one-hop food businesses and leaves. A skew traced to the applicant pool "
            "reflects that choice, not real demand.",
            "Supply links are modeled from BEA industry rates, which favour input-heavy industries.",
            "Tract location comes from business coordinates; demographics are ACS 2019 five-year estimates.",
            "Race and ethnicity are not used anywhere, including this audit.",
        ],
        "thresholds": {"over": OVER, "under": UNDER, "min_sector_share": MIN_SHARE, "over_gap": OVER_GAP,
                       "cascade_driven": CASCADE_DRIVEN, "input_heavy_sectors": sorted(INPUT_HEAVY)},
    }
