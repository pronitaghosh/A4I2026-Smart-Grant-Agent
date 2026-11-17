"""Grant desk: the officer-facing funding queue for the Smart Grant Agent.

The desk reads the agent lane's own tools (agent/grant_agent/tools.py, see
agent/BUILD_GUIDE.md): recommend_grants() for the queue and get_cascade() for each
line map. The screen therefore shows the same numbers the agent says.

Until that package is merged, the desk serves a saved sample of exactly that output
(frontend/sample/), captured by running the build guide's allocator on the live
a4i_econ tables, and says so on screen.

The "Ask the agent" box forwards questions to the deployed ADK agent (AGENT_URL),
using the API-server contract in the build guide, step 10.

Run locally from the repo root:
    .venv/bin/uvicorn app:app --app-dir frontend --port 8080
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = Path(__file__).with_name("sample")
PROJECT = os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-524776bdd295")
DS = f"{PROJECT}.a4i_econ"
AGENT_APP = os.environ.get("AGENT_APP", "grant_agent")
sys.path.insert(0, str(ROOT / "agent"))

# Local development only: until application-default credentials exist, a gcloud
# access token can stand in (GCLOUD_ACCESS_TOKEN="$(gcloud auth print-access-token)").
# On Cloud Run the service account is used and this is never set.
_bq = None
if os.environ.get("GCLOUD_ACCESS_TOKEN"):
    from google.cloud import bigquery
    from google.oauth2.credentials import Credentials

    _bq = bigquery.Client(project=PROJECT, location="US",
                          credentials=Credentials(token=os.environ["GCLOUD_ACCESS_TOKEN"]))

DATA_MODE = os.environ.get("GRANT_DATA_MODE", "auto")
if DATA_MODE not in {"auto", "live", "sample"}:
    raise RuntimeError("GRANT_DATA_MODE must be auto, live, or sample.")
try:
    if DATA_MODE == "sample":
        raise ImportError("Saved sample mode selected")
    from grant_agent import tools as agent_tools  # the agent lane's tools

    # The package can exist before its tools are written (the scaffold has empty files).
    missing = [f for f in ("recommend_grants", "get_cascade") if not hasattr(agent_tools, f)]
    if missing:
        raise ImportError(f"grant_agent.tools has no {', '.join(missing)} yet")
    if _bq is not None:
        agent_tools._client = lambda: _bq
    SOURCE = "live"
except ImportError as exc:
    if DATA_MODE == "live":
        raise
    logging.getLogger(__name__).warning("Using saved sample: %s", exc)
    agent_tools = None
    SOURCE = "sample"

app = FastAPI(title="Grant desk")


@lru_cache(maxsize=2)
def _sample(name: str) -> dict:
    return json.loads((SAMPLE / f"{name}.json").read_text())


def _client():
    global _bq
    if _bq is None:
        from google.cloud import bigquery

        _bq = bigquery.Client(project=PROJECT, location="US")
    return _bq


@lru_cache(maxsize=8)
def _live_recommendation(budget: float) -> dict:
    rec = agent_tools.recommend_grants(budget)
    if "error" in rec:
        raise HTTPException(503, f"The agent's recommend_grants tool said: {rec['error']}")
    ids = [r["business_id"] for r in rec["recommended"] + rec["not_funded"]]
    from google.cloud import bigquery

    rows = _client().query(
        f"SELECT business_id, geo_id, poverty_rate, no_vehicle_rate FROM `{DS}.business_tract` "
        "WHERE business_id IN UNNEST(@ids)",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids)]),
    ).result()
    avg = list(_client().query(f"SELECT AVG(poverty_rate) AS p FROM `{DS}.business_tract`").result())[0]["p"]
    return {**rec, "neighborhood": {r["business_id"]: dict(r.items()) for r in rows},
            "corridor_poverty": avg}


@app.get("/api/recommendation")
def recommendation(budget: float = 50000) -> dict:
    if SOURCE == "live":
        try:
            return {"source": "live", **_live_recommendation(budget)}
        except HTTPException:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Live recommendation failed")
            raise HTTPException(503, "Live data is unavailable; retry or use saved sample mode.") from exc
    s = _sample("recommend_grants")
    if budget != s["recommend_grants"]["budget"]:
        raise HTTPException(422, "This saved sample supports only its captured $50,000 budget.")
    return {"source": "sample", "captured": s["meta"]["captured"], **s["recommend_grants"],
            "neighborhood": s["neighborhood"], "corridor_poverty": s["meta"]["corridor_poverty"]}


@app.get("/api/cascade/{business_id}")
def cascade(business_id: str) -> dict:
    if SOURCE == "live":
        c = agent_tools.get_cascade(business_id)
        if "error" in c:
            raise HTTPException(404, c["error"])
        return c
    c = _sample("get_cascade")["get_cascade"].get(business_id)
    if c is None:
        raise HTTPException(404, f"Business {business_id} is not in the applicant queue.")
    return c


class Question(BaseModel):
    question: str
    business_id: str | None = None


def _post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


@app.post("/api/ask")
def ask(q: Question) -> dict:
    base = os.environ.get("AGENT_URL", "").rstrip("/")
    if not base:
        raise HTTPException(503, "The agent isn't connected yet. Set AGENT_URL to the deployed "
                                 "agent's address and restart the grant desk.")
    user, session = "grant-desk", uuid.uuid4().hex
    text = q.question if not q.business_id else f"{q.question} (business_id {q.business_id})"
    try:
        _post(f"{base}/apps/{AGENT_APP}/users/{user}/sessions/{session}", {})
        events = _post(f"{base}/run", {
            "app_name": AGENT_APP, "user_id": user, "session_id": session,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        })
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"The agent returned HTTP {e.code}. Check its Cloud Run logs.") from e
    except (ValueError, TypeError) as e:
        raise HTTPException(502, "The agent returned an invalid response.") from e
    except OSError as e:
        raise HTTPException(502, f"Couldn't reach the agent at {base}: {e}.") from e
    if not isinstance(events, list) or any(not isinstance(ev, dict) for ev in events):
        raise HTTPException(502, "The agent returned an invalid event list.")
    if any(ev.get(k) for ev in events for k in ("errorCode", "errorMessage", "error_code", "error_message")):
        raise HTTPException(502, "The agent could not complete this request. Please retry.")
    texts = []
    for ev in events:
        content = ev.get("content") or {}
        parts = content.get("parts") or []
        if ev.get("partial") or content.get("role") != "model":
            continue
        if any(p.get("functionCall") or p.get("functionResponse") for p in parts):
            continue
        text = "\n".join(p["text"] for p in parts if p.get("text") and not p.get("thought"))
        if text:
            texts.append(text)
    if not texts:
        raise HTTPException(502, "The agent answered without any text. Try rephrasing the question.")
    return {"answer": texts[-1]}


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="static")
