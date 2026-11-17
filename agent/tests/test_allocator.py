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

def test_realistic_weak_hub_is_not_funded_first(monkeypatch):
    from grant_agent import tools
    node = lambda bid: {"kind": "node", "properties": {"business_id": bid, "name": bid}}
    rows = []
    for i in range(194):
        ints = [0.0209] if i == 0 else ([0.015] if i < 113 else [0.015, 0.05])
        path = [node("171")]
        for hop, intensity in enumerate(ints):
            path.extend([{"kind": "edge", "properties": {"intensity": intensity}}, node(f"x{i}_{hop}")])
        rows.append({"seed_id": "171", "path_json": path})
    monkeypatch.setattr(tools, "_rows", lambda _: rows)
    hub = mk("171", 11000, 8, 9)
    hub.exposed = tools._cascades(["171"], 2)["171"]
    res = recommend([hub, mk("106", 7000, 34, 1, exposed=1, alts=28)], 18000, 0.2)
    assert res["recommended"][0]["business_id"] == "106"
