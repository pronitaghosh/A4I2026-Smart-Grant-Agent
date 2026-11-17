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
