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

def test_skew_that_follows_the_applicant_pool_is_not_called_bias():
    # Food is over-represented against the corridor and its money is mostly cascade value, but it
    # gets less money than its share of applicants: the allocator did not amplify it, so the skew
    # comes from who applied, not from the edge generator.
    funded = [row("r0", 3000), row("r1", 2000), row("p0", 2000, casc=0.0)]
    res = audit(funded, NAICS, ["r0", "r1", "r2", "p0"], TRACT)
    assert food(res)["flag"] == "over" and not food(res)["edge_generator_bias"]
    assert food(res)["likely_cause"].startswith("the applicant pool")
    assert res["edge_generator_bias"]["shows"] is False

def test_caveats_say_the_applicants_are_a_chosen_scenario():
    res = audit([row("r0", 5000)], NAICS, ["r0"], TRACT)
    assert any("scenario the team chose" in c and "not real demand" in c for c in res["caveats"])
