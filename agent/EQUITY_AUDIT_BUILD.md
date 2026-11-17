# Build: Equity Audit tool

| Part | Who | Time | Branch |
|---|---|---|---|
| **A. Agent tool** (Steps 1–7) | Agent lane | ~45 min | `agent` |
| **B. Grant desk UI** (Steps 8–14) | Front-end lane | ~60 min | `frontend` |
| **C. Story lane handoff** | Story lane | ~10 min | — |

Part B can start at the same time as Part A: the desk shows a "not available yet" line until the
agent's tool is merged, and Step 10 captures a sample to build against.

**Depends on:** BUILD_GUIDE Steps 3, 5, 6 and 7 (the `applications` and `business_tract` tables,
`tools.py`, `allocator.py` and `agent.py`) and the grant desk in `frontend/`.

## What it does, in one paragraph

After the agent recommends a funding plan, the audit checks whether the money went **where the
need is**, or **where our data happens to be strongest**. It does not change who is funded. It
reports on the plan, with numbers, so the officer (and the judges) can see its bias. Its
`memo_lines` go into the Grant Memo (PRD FR-14: *"numbers in the memo"*).

It answers four questions:

| Question | How | Why it matters |
|---|---|---|
| **Industry:** is any sector getting far more or less than its share? | Each sector's share of the money vs. its share of the corridor and of the applicants | Supply links are generated from BEA industry purchase rates, so input-heavy industries (food service, manufacturing) get many links and personal services almost none. Our allocator credits downstream links, so it will lean toward them. |
| **Edge-generator bias:** does that bias show in this plan? | Named when an input-heavy sector is over-represented, gets more money than its share of applicants, and most of that money's value comes from modeled supply cascades | PRD FR-14: the bias must be *named if it shows*. |
| **Need:** does the money land in poorer, more car-free tracts than average? | Dollar-weighted tract poverty and no-vehicle rate of funded businesses vs. the corridor and applicant averages | The README's step 3 of the audit; PRD FR-14 names both. |
| **Modeled evidence:** how much of each award rests on modeled supply links? | Each award's value split into the applicant's own survival vs. the cascade it protects | If an award is mostly cascade value, it rests on links that are our assignment, not observation. |

**It uses industry and tract poverty only. Never race or ethnicity.** The challenge excludes race
as a model input and asks for it only as an audit of output; our tract table deliberately has no
race columns, so this audit does not attempt that part, and the deck should say so.

## Why judges care

Rigor & judgment is 25 points, and one of its four named items is: *"Bias handling. Did you run the
equity audit? Did you find the industry bias we warned you about? Bring the numbers, not the
intention."* The README also says finding the bias in your own output *"is a better result than
an audit that came back clean."*

---

# Part A: the agent tool

## Step 1: Expose the value split in `allocator.py` (5 min)

The audit needs to know how much of each award's value is the applicant's own survival and how
much is the modeled cascade. Add this function above `recommend()`:

```python
def value_split(a: Applicant, award: float, corridor_poverty, A: dict = ASSUMPTIONS) -> tuple[float, float]:
    """(own_value, cascade_value) of one grant, in expected closures avoided. Per-grant view:
    overlap with other grants is ignored, so use it for the split, not for totals."""
    dp = risk_reduction(a, award, A)
    nm = need_mult(a, corridor_poverty, A)
    own = dp * nm
    cascade = sum(dp * A["CONTAGION"] * uniqueness(a) * w * nm for (w, *_rest) in a.exposed.values())
    return own, cascade
```

Then in `recommend()`, inside the `for a in apps:` loop, compute it and add two fields to each row.
Replace:

```python
        rows.append({
            "business_id": a.business_id, "name": a.name, "address": a.address,
```

with:

```python
        own_v, casc_v = value_split(a, aw, corridor_poverty, A)
        rows.append({
            "business_id": a.business_id, "name": a.name, "address": a.address,
            "own_value": round(own_v, 4), "cascade_value": round(casc_v, 4),
```

Run `python -m pytest tests` (from `agent/`). The existing tests must still pass.

---

## Step 2: Create `grant_agent/audit.py`, the pure audit logic (15 min)

Pure Python, no BigQuery, so it is unit-testable. Thresholds are constants at the top: say them
out loud in the demo if asked. It returns `memo_lines`, four sentences for the memo: the biggest
industry skew, poverty, car access, and whether the edge generator's bias shows.

```python
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
```

---

## Step 3: Cache the plan and add the `audit_allocation` tool in `tools.py` (10 min)

**3a. Cache the plan.** The memo calls `recommend_grants` and then `audit_allocation`, and the grant
desk loads both too. Without a cache, the audit reruns the graph query, the allocation and its five
sensitivity reruns, roughly doubling time to memo against the PRD's 60-second target. With it, the
audit reuses the exact plan the officer just saw.

Add `import copy` to the imports at the top. Then rename the existing function: replace

```python
def recommend_grants(budget: float = 50000, depth: int = 2) -> dict:
    """Recommend which applicants to fund and how much, from the applications queue.
```

with

```python
def _compute_plan(budget: float = 50000, depth: int = 2) -> dict:
    """Recommend which applicants to fund and how much, from the applications queue.
```

and add the cached tool right after `_compute_plan` ends (just above `def exposed_non_applicants`):

```python
@lru_cache(maxsize=8)
def _cached_plan(budget: float, depth: int) -> dict:
    return _compute_plan(budget, depth)


def recommend_grants(budget: float = 50000, depth: int = 2) -> dict:
    """Recommend which applicants to fund and how much, from the applications queue.
    Combines each applicant's closure risk, the downstream cascade it protects (multi-hop
    graph traversal), how replaceable it is locally, and tract need, then allocates the
    budget to maximise expected business closures avoided. Also returns the runner-up,
    every assumption, and a sensitivity check showing which awards survive if the
    assumptions are wrong. Use this for "who should we fund?" and "what does $X change?"."""
    plan = _cached_plan(float(budget), int(depth))
    if "error" in plan:
        _cached_plan.cache_clear()  # don't keep a failure around
    return copy.deepcopy(plan)      # callers can't alter the cached plan
```

The cache lives as long as the process. If the data lane reloads `applications` mid-session,
restart the agent (or call `_cached_plan.cache_clear()`).

**3b. The audit tool.** Add the import next to the existing `from . import allocator`:

```python
from . import audit as equity
```

Add the tool at the end of the file:

```python
def audit_allocation(budget: float = 50000, depth: int = 2) -> dict:
    """Equity audit of the funding plan recommend_grants makes for this budget. Checks whether
    any industry gets far more or less than its share of the money (our modeled supply links
    favour input-heavy industries like food service), whether the money lands in poorer tracts
    than average, and how many awards rest mainly on modeled supply links. Use when the officer
    asks "is this fair?", "who does this leave out?", or "is there bias in this?", and for the
    memo's equity lines (memo_lines) after a recommendation. Uses industry, poverty and car
    access only, never race. Reuses the cached plan, so it adds little time."""
    plan = recommend_grants(budget, depth)
    if "error" in plan:
        return plan
    biz = _rows(f"""
      SELECT b.business_id, b.naics4, t.poverty_rate, t.no_vehicle_rate
      FROM `{DS}.businesses` b
      LEFT JOIN `{DS}.business_tract` t USING (business_id)""")
    result = equity.audit(
        funded=plan["recommended"],
        naics_by_id={r["business_id"]: r["naics4"] for r in biz},
        applicant_ids=[r["business_id"] for r in plan["recommended"] + plan["not_funded"]],
        tract_by_id={r["business_id"]: r for r in biz},
    )
    result["plan"] = {"budget": plan["budget"], "allocated": plan["allocated"],
                      "funded": [{"name": r["name"], "award": r["award"]} for r in plan["recommended"]]}
    return result
```

It reads the cached plan from 3a, so the audit always describes exactly the plan the officer saw for
that budget, without recomputing it.

---

## Step 4: Register it in `agent.py` (1 min)

```python
from .tools import get_cascade, recommend_grants, exposed_non_applicants, audit_allocation

tools = [recommend_grants, get_cascade, exposed_non_applicants, audit_allocation]
```

---

## Step 5: Teach the prompt when to use it (5 min)

In `prompt.py`, add to **TOOLS: WHEN TO USE WHICH**:

```
- "Is this fair?", "who does this leave out?", "any bias?" -> audit_allocation(budget), with the
  same budget as the recommendation. Report the findings, the need comparison (poverty and car
  access), whether the edge generator's bias shows, and the caveats.
```

Add as item 7 of **HOW TO ANSWER "WHO SHOULD WE FUND?"**:

```
7. Equity check: call audit_allocation with the same budget and include its memo_lines verbatim:
   the biggest industry skew, poverty, car access, and whether the edge generator's bias shows.
   The plan is cached, so this does not recompute it.
```

Add to **RULES**:

```
- An over-represented industry is a finding to report, not a reason to change the plan on your
  own. Offer to rerun with need weight or contagion changed if the officer wants to correct it.
- If a skew is traced to the applicant pool, say the applicants are a scenario the team chose,
  not real demand.
```

---

## Step 6: Unit tests, `tests/test_audit.py` (10 min)

```python
from grant_agent.audit import audit

NAICS = {**{f"r{i}": "7225" for i in range(3)},      # 3 restaurants
         **{f"p{i}": "8121" for i in range(5)},      # 5 personal services
         **{f"m{i}": "3118" for i in range(2)}}      # 2 manufacturers
TRACT = {b: {"poverty_rate": 0.30 if b.startswith("r") else 0.15,
             "no_vehicle_rate": 0.60 if b.startswith("r") else 0.40} for b in NAICS}

def row(bid, award, own=0.1, casc=0.3):
    return {"business_id": bid, "name": bid, "award": award, "own_value": own, "cascade_value": casc}

def food(res):
    return next(r for r in res["industry"] if r["sector"] == "Food service")

def test_edge_generator_bias_named_when_cascades_drive_an_input_heavy_sector():
    funded = [row("r0", 8000), row("r1", 8000), row("p0", 2000, casc=0.0)]
    res = audit(funded, NAICS, ["r0", "r1", "p0", "p1", "p2"], TRACT)
    assert food(res)["flag"] == "over" and food(res)["edge_generator_bias"]
    assert "edge generator" in food(res)["likely_cause"]
    assert res["edge_generator_bias"] == {"shows": True, "sectors": ["Food service"]}
    assert res["memo_lines"][3].startswith("Edge-generator bias shows")

def test_same_skew_without_cascades_is_the_allocator_not_the_bias():
    funded = [row("r0", 8000, casc=0.0), row("r1", 8000, casc=0.0), row("p0", 2000, casc=0.0)]
    res = audit(funded, NAICS, ["r0", "r1", "p0", "p1", "p2"], TRACT)
    assert food(res)["flag"] == "over" and not food(res)["edge_generator_bias"]
    assert "allocator" in food(res)["likely_cause"]
    assert res["memo_lines"][3].startswith("No sign of the edge generator")

def test_sector_that_did_not_apply_is_blamed_on_applicant_pool():
    funded = [row("r0", 5000), row("p0", 5000)]
    res = audit(funded, NAICS, ["r0", "p0"], TRACT)
    mfg = next(r for r in res["industry"] if r["sector"] == "Manufacturing")
    assert mfg["flag"] == "under" and mfg["likely_cause"] == "no applicants from this sector"

def test_memo_lines_carry_poverty_and_car_numbers_dollar_weighted():
    funded = [row("r0", 9000), row("p0", 1000, casc=0.0)]
    res = audit(funded, NAICS, ["r0", "p0"], TRACT)
    assert len(res["memo_lines"]) == 4
    assert res["memo_lines"][1].startswith("Residents below poverty: 28%")   # (0.30*9000 + 0.15*1000) / 10000
    assert res["memo_lines"][2].startswith("Households without a car: 58%")  # (0.60*9000 + 0.40*1000) / 10000
    assert abs(res["need"]["poverty_rate"]["funded_dollar_weighted"] - 0.285) < 1e-9
    assert [d["name"] for d in res["modeled_evidence"]["cascade_driven_awards"]] == ["r0"]

def test_small_input_heavy_sector_is_still_caught():
    # Manufacturing under 5% of the corridor, but most of the money, mostly via cascades.
    na = {**{f"x{i}": "4511" for i in range(46)}, "m0": "3118", "m1": "3118", "p0": "8121", "p1": "8121"}
    tr = {b: {"poverty_rate": 0.2, "no_vehicle_rate": 0.5} for b in na}
    funded = [row("m0", 12000), row("m1", 10000), row("p0", 4000, casc=0.0)]
    res = audit(funded, na, ["m0", "m1", "p0", "p1"], tr)
    mfg = next(r for r in res["industry"] if r["sector"] == "Manufacturing")
    assert mfg["corridor_share"] < 0.05 and mfg["flag"] == "over" and mfg["edge_generator_bias"]
    assert res["edge_generator_bias"]["sectors"] == ["Manufacturing"]

def test_caveats_say_the_applicants_are_a_chosen_scenario():
    res = audit([row("r0", 5000)], NAICS, ["r0"], TRACT)
    assert any("scenario the team chose" in c for c in res["caveats"])
```

Run: `cd agent && python -m pytest tests`. The allocator tests and the 6 new audit tests must pass.

---

## Step 7: Try it in `adk web` (5 min)

```bash
cd agent && .venv/bin/adk web
```

Ask, in order:

1. *"We have $50,000. Who should we fund?"* The memo should end with the four equity lines:
   biggest skew, poverty, car access, and the edge-generator verdict, each with its numbers.
   Time it: it should not take noticeably longer than before the audit (the plan is cached).
2. *"Is this fair?"* Expect the full audit: industry table, poverty and car access, whether the
   edge generator's bias shows, cascade-driven awards, caveats (including the scenario one).
3. *"Fix the food-service skew."* The agent should **not** silently change the plan; it should offer
   to rerun with different assumptions and explain the trade-off.

---

---

# Part B: the grant desk UI

Work on the `frontend` branch. The desk already imports the agent's tools directly
(`frontend/app.py`), so the audit follows the same pattern as `recommend_grants`: a server
endpoint calls the agent's `audit_allocation`, and `app.js` lays it out. **No figure is computed
in the browser.**

What the officer gets:

```
┌ Header ────────────────────────────────────────────────────────────────────┐
│ Central Market relief fund                         $50,000 allocated of …  │
│ EQUITY CHECK  Food service gets 46% of the money vs 28% of …  Open audit → │
└────────────────────────────────────────────────────────────────────────────┘
┌ Queue ─────────┐┌ Case pane: "Equity audit of this plan" ────────────────────┐
│ Recommended    ││ Headline                                                   │
│  …             ││ Sector        Corridor   Applicants   Money      Flag      │
│                ││ Food service  ███ 28%    ████ 36%     █████ 46%  OVER      │
│                ││ Retail        ██ 18%     █ 9%         0%         UNDER     │
│                ││ Findings · Need (poverty) · Modeled links · Caveats        │
└────────────────┘└────────────────────────────────────────────────────────────┘
```

Plus, in every case view, a small tag on awards that rest mainly on modeled supply links.

## Step 8: Server endpoint in `frontend/app.py` (10 min)

Add below the `cascade()` endpoint:

```python
@lru_cache(maxsize=8)
def _live_audit(budget: float) -> dict:
    a = agent_tools.audit_allocation(budget)
    if "error" in a:
        raise HTTPException(503, f"The agent's audit_allocation tool said: {a['error']}")
    return a


@app.get("/api/audit")
def audit(budget: float = 50000) -> dict:
    if SOURCE == "live":
        # Never pair a live plan with a sample audit: they would describe different plans.
        if not hasattr(agent_tools, "audit_allocation"):
            raise HTTPException(404, "The agent's audit_allocation tool isn't merged yet.")
        return {"source": "live", **_live_audit(budget)}
    if not (SAMPLE / "audit_allocation.json").exists():
        raise HTTPException(404, "No equity audit sample captured yet (see Step 10).")
    s = _sample("audit_allocation")
    return {"source": "sample", "captured": s["meta"]["captured"], **s["audit_allocation"]}
```

Raise the sample cache, since there are now three sample files. Replace:

```python
@lru_cache(maxsize=2)
def _sample(name: str) -> dict:
```

with:

```python
@lru_cache(maxsize=4)
def _sample(name: str) -> dict:
```

`audit_allocation` reads the same cached plan as `recommend_grants` (Step 3a), so the desk's two
calls compute the plan once and the audit describes exactly the plan on screen.

## Step 9: Fix local live mode in `frontend/app.py` (2 min)

The desk's local-development path injects a token-based BigQuery client with
`agent_tools._bq = _bq`. Since BUILD_GUIDE Step 7, `tools.py` creates its client on first use
through `_client()` and no longer reads `_bq`, so that line does nothing and local live mode
(including the audit) fails without application-default credentials. Replace:

```python
    if _bq is not None:
        agent_tools._bq = _bq
```

with:

```python
    if _bq is not None:
        agent_tools._client = lambda: _bq  # tools.py creates its client through _client()
```

Cloud Run is unaffected: there the service account is used and `_bq` is never set.

## Step 10: Capture the sample, `frontend/sample/capture_audit.py` (5 min)

The desk serves saved samples until the agent's tools are inside its container. Capture the
audit sample **from the live tables**, never by hand, the same way the other samples were made:

```python
"""Capture frontend/sample/audit_allocation.json from the live a4i_econ tables.
Run from the repo root where BigQuery works (Cloud Shell), with GOOGLE_CLOUD_PROJECT set:
    agent/.venv/bin/python frontend/sample/capture_audit.py
"""
import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "agent"))
from grant_agent import tools  # noqa: E402

audit = tools.audit_allocation(50000)
if "error" in audit:
    sys.exit(f"audit_allocation failed: {audit['error']}")

# The audit must describe the same plan as the saved recommendation sample.
rec = json.loads((HERE / "recommend_grants.json").read_text())["recommend_grants"]
saved = sorted(r["name"] for r in rec["recommended"])
audited = sorted(f["name"] for f in audit["plan"]["funded"])
if saved != audited:
    print("WARNING: recommend_grants.json shows a different plan. Recapture it too, "
          "or the audit will not match the queue on screen.")

out = {"meta": {"captured": datetime.date.today().isoformat(),
                "source": "agent/grant_agent audit_allocation on live a4i_econ tables"},
       "audit_allocation": audit}
(HERE / "audit_allocation.json").write_text(json.dumps(out, indent=1))
print("wrote", HERE / "audit_allocation.json")
print("headline:", audit["headline"])
```

## Step 11: Header line in `frontend/static/index.html` (1 min)

Add the equity line under the sample banner. Replace:

```html
      <p class="source-banner" id="source-banner" hidden></p>
```

with:

```html
      <p class="source-banner" id="source-banner" hidden></p>
      <p class="audit-strip" id="audit-strip" hidden></p>
```

## Step 12: Render the audit in `frontend/static/app.js` (30 min)

**12a. State.** Replace:

```js
const state = { rec: null, selected: null, order: [] };
```

with:

```js
const state = { rec: null, audit: null, selected: null, order: [] };
```

**12b. The audit view.** Add this block above the `/* Ask the agent */` comment:

```js
/* Equity audit: renders audit_allocation(). Industry and tract poverty only, never race. */
const SECTOR_COLS = [["corridor_share", "Corridor", "corridor"], ["applicant_share", "Applicants", "applicants"],
                     ["money_share", "Money", "money"]];

function renderAuditStrip() {
  const strip = document.getElementById("audit-strip");
  strip.hidden = false;
  const a = state.audit;
  if (a.error) {
    strip.replaceChildren(h("strong", {}, "Equity check"), h("span", { class: "muted" }, `Not available: ${a.error}`));
    return;
  }
  strip.replaceChildren(h("strong", {}, "Equity check"), h("span", {}, a.headline),
    h("button", { type: "button", onclick: showAudit }, "Open the audit"));
}

function shareCell(v, max, kind) {
  return h("td", {}, h("div", { class: `share ${kind}` },
    h("span", { class: "share-bar", "aria-hidden": "true" },
      h("span", { style: `width: ${max ? (100 * v) / max : 0}%` })),
    h("span", { class: "share-num" }, pct(v))));
}

function industryTable(rows) {
  const shown = rows.filter((r) => r.corridor_share || r.applicant_share || r.money_share);
  const max = Math.max(...shown.flatMap((r) => SECTOR_COLS.map(([k]) => r[k])));
  return h("div", { class: "table-scroll" }, h("table", { class: "audit-table" },
    h("caption", {}, "Each sector's share of corridor businesses, of applicants, and of the money"),
    h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Sector"),
      ...SECTOR_COLS.map(([, label]) => h("th", { scope: "col" }, label)), h("th", { scope: "col" }, "Flag"))),
    h("tbody", {}, ...shown.map((r) => h("tr", { class: r.flag ? "flagged" : "" },
      h("th", { scope: "row" }, r.sector),
      ...SECTOR_COLS.map(([k, , kind]) => shareCell(r[k], max, kind)),
      h("td", {}, r.flag ? [h("span", { class: `tag ${r.flag}` },
                              `${r.flag} · ${r.money_vs_corridor != null ? `${r.money_vs_corridor}x` : "small sector"}`),
                            r.edge_generator_bias ? h("span", { class: "tag bias" }, "edge-generator bias") : null,
                            h("span", { class: "cause" }, r.likely_cause)] : "")))))) ;
}

function needLines(need) {
  const fmt = (x) => (x == null ? "n/a" : pct(x));
  const line = (label, n) => h("p", {}, h("strong", {}, `${label}: `),
    `${fmt(n.funded_dollar_weighted)} where the money lands (dollar-weighted), ` +
    `${fmt(n.applicant_avg)} across applicants, ${fmt(n.corridor_avg)} corridor average.`);
  return [line("Residents below poverty", need.poverty_rate), line("Households without a car", need.no_vehicle_rate),
          h("p", { class: "fine" }, "ACS 2019 five-year estimates, by census tract.")];
}

function modeledBlock(m) {
  const share = m.plan_cascade_share_dollar_weighted;
  const list = m.cascade_driven_awards;
  return [
    h("p", {}, share == null ? "No funded awards." :
      `${pct(share)} of the plan's value (dollar-weighted) comes from the supply cascades grants protect, ` +
      "which rest on modeled links."),
    list.length
      ? h("ul", { class: "reason-list driven-list" }, ...list.map((d) => h("li", {},
          h("button", { type: "button", onclick: () => select(d.business_id) }, d.name),
          ` · ${money.format(d.award)} · ${pct(d.cascade_share)} of its value from the cascade`)))
      : h("p", { class: "fine" }, "No award rests mainly on modeled links."),
  ];
}

function renderAudit(a) {
  const t = a.thresholds;
  return [
    h("p", { class: "case-status audit" }, "Equity audit of this plan"),
    h("h2", {}, "Where the money goes"),
    h("p", { class: "audit-headline" }, a.headline),
    a.source === "sample" ? h("p", { class: "fine" }, `Sample run captured ${a.captured}.`) : null,
    reason("In the memo", h("ul", { class: "reason-list memo-lines" }, ...a.memo_lines.map((t) => h("li", {}, t)))),
    industryTable(a.industry),
    h("div", { class: "reasons" },
      reason("Findings", h("ul", { class: "reason-list" }, ...a.findings.map((f) => h("li", {}, f)))),
      reason("Who the money reaches", ...needLines(a.need)),
      reason("Resting on modeled links", ...modeledBlock(a.modeled_evidence)),
      reason("Read this with care",
        h("ul", { class: "reason-list fine" }, ...a.caveats.map((c) => h("li", {}, c))),
        h("p", { class: "fine" }, `Flags: at least ${t.over}x or at most ${t.under}x a sector's corridor share ` +
          `(sectors with at least ${pct(t.min_sector_share)} of corridor businesses), or money at least ` +
          `${Math.round(t.over_gap * 100)} points above corridor share at any size.`))),
  ];
}

function showAudit() {
  state.selected = null;
  document.querySelectorAll(".queue-item button").forEach((b) => b.setAttribute("aria-current", "false"));
  const pane = document.getElementById("case");
  pane.replaceChildren(...renderAudit(state.audit));
  pane.querySelector("h2").scrollIntoView({ block: "nearest" });
}

function modeledTag(a) {
  const d = state.audit?.modeled_evidence?.cascade_driven_awards?.find((x) => x.business_id === a.business_id);
  return d ? h("span", { class: "tag modeled" }, `${pct(d.cascade_share)} of value from modeled links`) : null;
}
```

**12c. Tag each case.** In `riskLine`, replace:

```js
  return h("p", { class: "risk" }, change, tag);
```

with:

```js
  return h("p", { class: "risk" }, change, tag, st === "not_funded" ? null : modeledTag(a));
```

**12d. Load it.** In `init()`, replace:

```js
async function init() {
  try {
    state.rec = await getJSON("/api/recommendation");
```

with:

```js
async function init() {
  // The audit loads alongside the plan; if it fails, the desk still works without it.
  const audit = getJSON("/api/audit").catch((e) => ({ error: e.message }));
  try {
    state.rec = await getJSON("/api/recommendation");
```

and replace:

```js
  renderDesk();
  renderQueue();
  if (state.order.length) select(state.order[0].business_id);
}
```

with:

```js
  renderDesk();
  renderQueue();
  if (state.order.length) select(state.order[0].business_id);
  state.audit = await audit;
  renderAuditStrip();
  if (location.hash === "#audit" && !state.audit.error) showAudit();  // deep link for the demo
  else if (state.selected) select(state.selected);  // redraw the open case with its modeled-links tag
}
```

## Step 13: Styles in `frontend/static/styles.css` (5 min)

Add above the `@media (max-width: 880px)` block. It reuses the desk's colours: ultramarine for
money, stone for the corridor baseline, amber for anything to look at twice.

```css
/* Equity audit */
.audit-strip { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; margin: 10px 0 0; max-width: 78ch; font-size: 13.5px; }
.audit-strip strong { font: 700 11px/1.2 var(--display); letter-spacing: 0.08em; text-transform: uppercase; color: var(--amber); }
.audit-strip button,
.driven-list button { padding: 0; border: 0; background: none; color: var(--line); font: 700 13.5px var(--display); text-decoration: underline; cursor: pointer; }
.audit-strip button:focus-visible,
.driven-list button:focus-visible { outline: 3px solid var(--line); outline-offset: 2px; }

.case-status.audit { color: var(--amber); }
.audit-headline { margin: 0 0 22px; max-width: 64ch; font: 600 17px/1.45 var(--body); }

.audit-table { width: 100%; min-width: 860px; max-width: 900px; border-collapse: collapse; margin: 0 0 6px; font-size: 13.5px; font-variant-numeric: tabular-nums; }
.audit-table caption { caption-side: bottom; padding-top: 6px; text-align: left; font-size: 12.5px; color: var(--muted); }
.audit-table th, .audit-table td { padding: 8px 10px; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: middle; }
.audit-table thead th { font: 700 11px/1.3 var(--display); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.audit-table tbody th { font-weight: 500; white-space: nowrap; }
.audit-table tr.flagged th { font-weight: 700; }
.share { display: grid; grid-template-columns: minmax(70px, 1fr) 3em; gap: 8px; align-items: center; min-width: 120px; }
.share-bar { height: 10px; border-radius: 2px; overflow: hidden; background: rgb(23 33 26 / 0.07); }
.share-bar span { display: block; height: 100%; }
.share.corridor .share-bar span { background: var(--stone); }
.share.applicants .share-bar span { background: var(--line-2); opacity: 0.5; }
.share.money .share-bar span { background: var(--line); }
.share-num { text-align: right; }
.tag.over, .tag.under { margin-left: 0; color: var(--amber); border: 1px solid var(--amber); }
.tag.modeled { color: var(--muted); border: 1px solid var(--stone); }
.tag.bias { margin-left: 6px; color: #fff; background: var(--amber); }
.memo-lines { margin: 0 0 22px; }
.cause { display: block; margin-top: 3px; font-size: 12px; color: var(--muted); }
.audit-table td:last-child { min-width: 230px; }
.audit-table + .reasons, .table-scroll + .reasons { margin-top: 26px; }
```

## Step 14 (test): run the desk and check it

```bash
# from the repo root
.venv/bin/uvicorn app:app --app-dir frontend --port 8080
```

Open http://localhost:8080 and check:

1. The header shows **EQUITY CHECK** with the headline and **Open the audit**.
   (http://localhost:8080/#audit opens straight onto the audit: use it for the demo video.)
2. **Open the audit** fills the case pane: headline, the four **In the memo** lines, the sector
   table with three bars per row, flagged rows tagged *over* / *under* (plus *edge-generator bias*
   when it shows) with their likely cause, findings, poverty and car access, the modeled-links
   list, caveats.
3. Clicking a business in **Resting on modeled links** opens its case, and that case shows the grey
   *"…% of value from modeled links"* tag next to the risk line.
4. Stop the server, rename `frontend/sample/audit_allocation.json`, restart: the header says
   *"Equity check · Not available: …"* and the rest of the desk still works.
5. At phone width the sector table scrolls sideways inside its box (it keeps an 860px minimum so
   rows stay compact); the page itself does not scroll sideways.
6. Ask the agent box: *"Is this plan fair?"* gets the agent's full audit as formatted text.

Then redeploy the desk with `frontend/cloudbuild.yaml`, as in `docs/TEAM_NOTES.md`.

---

# Part C: hand the story lane the numbers

From the desk's audit view (or the agent's *"Is this fair?"* answer), one slide's worth:

- The audit's four `memo_lines`, word for word: they are what the memo says.
- The most over-represented sector, its multiple (e.g. *1.7x its corridor share*), and its likely
  cause. If the cause is the applicant pool, say the applicants are a scenario the team chose.
- Whether the edge generator's bias shows (PRD FR-14), with its numbers, or that it doesn't.
- Where the money lands on poverty and car access vs. the corridor average.
- How many awards rest mainly on modeled supply links.
- A screenshot of the audit view. If the bias shows, the sentence: *"We found the industry bias the
  challenge warned about in our own output, and the memo names it every time."* If it doesn't:
  *"We checked for the industry bias the challenge warned about; on this plan it doesn't show, and
  here are the numbers."*
