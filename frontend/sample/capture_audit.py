"""Capture frontend/sample/audit_allocation.json from the live a4i_econ tables.

Run from the repo root where BigQuery works (Cloud Shell), with the agent's dependencies
installed and the project exported (this script does not read grant_agent/.env):

    export GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project)
    agent/.venv/bin/python frontend/sample/capture_audit.py

It refuses to write if the live plan differs from the saved recommend_grants.json, because the
desk would then show an audit of a different plan from its queue. Recapture that sample too, or
pass --force to write anyway.
"""
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUDGET = 50000

if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
    sys.exit("Set the project first: export GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project)")

sys.path.insert(0, str(HERE.parents[1] / "agent"))
from grant_agent import tools  # noqa: E402

audit = tools.audit_allocation(BUDGET)
if "error" in audit:
    sys.exit(f"audit_allocation failed: {audit['error']}")

# The audit must describe the same plan as the saved recommendation sample: same names, same awards.
rec = json.loads((HERE / "recommend_grants.json").read_text())["recommend_grants"]
saved = sorted((r["name"], float(r["award"])) for r in rec["recommended"])
audited = sorted((f["name"], float(f["award"])) for f in audit["plan"]["funded"])
if rec["budget"] != BUDGET or saved != audited:
    print("The live plan differs from frontend/sample/recommend_grants.json:")
    for name, award in sorted(set(saved) ^ set(audited)):
        print(f"  {'saved' if (name, award) in saved else 'live '}: {name} ${award:,.0f}")
    if "--force" not in sys.argv:
        sys.exit("Not written. Recapture recommend_grants.json from the same tables, or pass --force.")
    print("--force: writing anyway.")

out = {"meta": {"captured": datetime.date.today().isoformat(),
                "source": "agent/grant_agent audit_allocation on live a4i_econ tables"},
       "audit_allocation": audit}
(HERE / "audit_allocation.json").write_text(json.dumps(out, indent=1))
print("wrote", HERE / "audit_allocation.json")
print("headline:", audit["headline"])
