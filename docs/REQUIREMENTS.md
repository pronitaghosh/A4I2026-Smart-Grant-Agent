# Requirements status

Each requirement in the [PRD](PRD.pdf), with what the repo and the deployed system do about it.
As of 2026-11-17, on `main` plus the open PRs #10 (desk Markdown rendering), #13 (applicant
queue) and #15 (review fixes). #10 and #13 are live. #15 goes live with the next deploy.

**Done** means it works on the deployed system. **Partial** means part of the acceptance
criterion is met; the gap is named. **Not started** means nothing exists yet.

## Functional requirements

### P0: the demo and every hard requirement

| ID | Requirement | Status | Evidence, and the gap if any |
|---|---|---|---|
| FR-1 | Situation intake: corridor, budget, applicants, what happened | Partial | The agent takes the budget and question in plain language, and `recommend_grants(budget)` reruns for any amount. The prompt doesn't yet tell the agent to restate the corridor, budget, applicant count and events before analyzing |
| FR-2 | Applicant queue from real corridor businesses | Done | [`sql/02_applications.sql`](../sql/02_applications.sql): 11 real businesses, $118,000 requested. Financial fields are flagged `illustrative = TRUE` |
| FR-3 | Property graph over `a4i_econ` | Done | [`sql/01_create_graph.sql`](../sql/01_create_graph.sql): `Business` and `Employer` nodes, `Supplies` and `DrawsFootfall` edges. Keys checked unique; see [TEAM_NOTES.md](TEAM_NOTES.md) |
| FR-4 | Cascade traversal, 1 to 3 hops, with a stopping rule | Done | `_cascades` in [`tools.py`](../agent/grant_agent/tools.py): a quantified-path GQL `MATCH` over `Supplies`, depth 1 to 3. The stopping rule is that every link must be ≥ 0.02. Empty cascades are reported (`is_leaf`) |
| FR-5 | Grant valuation tool we built | Done | [`allocator.py`](../agent/grant_agent/allocator.py): deterministic, and every assumption is named in `ASSUMPTIONS` and returned with each result. It reports an unweighted count of expected closures avoided, and keeps the need-weighted priority separate |
| FR-6 | Allocation within budget, with a runner-up | Done | Greedy allocation in 25% slices that never exceeds the budget. Shared downstream businesses count once. **Every grant has its own runner-up**: withdraw it, re-spend the money with the same allocator, and compare closures avoided. The verdict is "indistinguishable" within 10%. Live: every grant beats its alternative by 41–98%; Sour Flour's $5,000 avoids 3.14 closures, the same money elsewhere 0.05. The queue-level `runner_up` (who just missed) is kept |
| FR-7 | Grant Memo with the seven elements | Partial | `prompt.py` fixes the answer format: award table, reasons with the strongest chain A → B → C, runner-up, assumptions, evidence note, neighborhood. For "the traversal shown", `get_cascade` returns the GQL it ran, the cutoff and a two-hop path, and the prompt asks for them in a full memo. No automated check yet confirms all seven elements appear for every funded business |
| FR-8 | Traceability and refusal | Partial | The prompt rules are "every number from a tool", and "within 10% on `closures_avoided_per_10k_if_fully_funded`, refuse a strict ordering". Every tool result carries an `evidence_note`. There's no automated citation check or scripted tie test yet. `grant_tools.compare_cascades` has a tested 10% tie verdict that isn't wired in |
| FR-9 | Consume a managed MCP server | Done | MCP Toolbox for Databases on Cloud Run. Its four tools in [`tools.yaml`](../agent/toolbox/tools.yaml) are attached through `ToolboxToolset`. Name lookups (`find_business`), loans and tract profiles go through it |
| FR-10 | Deploy behind a live demo link | Done | Cloud Run us-west2: [grant desk](https://grant-desk-173086820802.us-west2.run.app), `grant-agent`, `toolbox`. Each keeps one instance warm |

### P1: where the rigor score is won

| ID | Requirement | Status | Evidence, and the gap if any |
|---|---|---|---|
| FR-11 | Hub check against centrality-only and distress-only rankings | Done (as documentation) | [`scripts/hub_check.py`](../scripts/hub_check.py) and the [README table](../README.md#hub-check-the-same-50000-three-ways). Centrality-only funds Tradespace $11,000; ours declines it, with the reason. The agent can explain the hub case when asked, but the comparison isn't an agent tool |
| FR-12 | Downstream fragility from SBA charge-offs | Partial | A charge-off raises an applicant's own risk. `exposed_non_applicants` flags downstream businesses with a prior charge-off, and `loan_history` (MCP) looks them up. Fragility isn't a weight on downstream businesses (the PRD's f_k is 1). Only 4 loans exist in the corridor |
| FR-13 | Sensitivity | Done | Five alternative assumption sets. `sensitivity.funded_in_every_scenario` says which awards hold, and the desk tags each case. Live: 6 of 9 awards hold in every scenario |
| FR-14 | Equity audit | Done | The agent's `audit_allocation` tool ([`audit.py`](../agent/grant_agent/audit.py)) audits every plan: industry mix against the corridor and the applicants, tract poverty and car access where the money lands, and reliance on modeled links. It tests for the edge generator's bias and names the likely cause of each skew. Its memo lines go into the Grant Memo; the desk shows an "Equity check" line and an audit view. Live: food service 1.8x its corridor share, traced to the applicant pool; no edge-generator bias by the tool's test |
| FR-15 | Funding-queue UI beyond `adk web` | Done | The grant desk ([`frontend/`](../frontend/README.md)): queue, budget bar, one cascade line map per applicant, and "Ask the agent" |
| FR-16 | Export the memo to PDF or Google Docs | Not started | |

### P2: differentiators

| ID | Requirement | Status | Notes |
|---|---|---|---|
| FR-17 | Charge-off backtest | Not started | With 4 loans in the corridor, any result would be anecdotal |
| FR-18 | Anchor-closure what-if | Not started | `exposed_non_applicants` covers the "never applied" half, but via `Supplies` from applicants, not `DrawsFootfall` from an anchor |
| FR-19 | Better edge generator | Not started | |
| FR-20 | Intake agent that writes edges | Not started | |

## Non-functional requirements

| Area | Target | Status |
|---|---|---|
| Determinism | Same inputs, identical allocation | Met. Traversal in SQL, scoring and allocation in Python. Gemini only writes prose |
| Latency | Full memo under 60 s; follow-ups under 20 s | Met. About 15 s for a full recommendation through the agent and about 5 s for a lookup. The desk's first load after a deploy takes about 9 s, then it's cached |
| Demo reliability | Minimum instances 1 for the agent and Toolbox | Met, and for the desk too |
| Location | BigQuery in US; Cloud Run in us-west2 | Met. Gemini is called at Vertex AI's `global` endpoint: us-west2 returns `FAILED_PRECONDITION`, and 3.8 Flash isn't served regionally |
| Readability | Written for a council member | Met in the desk: reasons and assumptions in plain English. Memo length isn't enforced |
| Privacy | No individual-level personal data | Met. The filtered registry is used as provided |
| Access | Least-privilege service accounts | Partial. The Toolbox has its own narrow account; the desk and agent use the default compute account |
| Auditability | Run log keyed by run ID | Not met. Mitigation: the allocation is deterministic and reproducible from the tables |
| Cost | Enterprise reservation for graph queries | Not needed. Graph queries ran on on-demand billing in this project |
| Data licensing | Public domain, CC0 or US Government works | Met. Everything added (ACS 2019 via BigQuery public data) is US Government work |

## Release checks

| Check | Target | Result |
|---|---|---|
| Memo elements present | 7 of 7 for every funded business | Not automated. Set by the prompt's format; check by hand in QA |
| Uncited figures | 0 | Not automated. Set by the prompt's rules |
| Graph changes the decision | At least 1 funded business differs from the distress-only ranking | **Met.** The Warfield Theater, Non Plus Ultra, Soliome and Eddy St Bodega are funded by ours and not by distress-only. Distress-only puts $25,000 into two hotels with no cascade ([hub check](../README.md#hub-check-the-same-50000-three-ways)) |
| Time to memo | Under 60 s | Met, about 15 s |
| Pitch video | Under 3 minutes | Story lane |

## PRD open questions, as decided

| Question | Decision |
|---|---|
| How many applicants, and how chosen | 11, chosen on purpose: the robust hub, three real cascades, three one-hop food businesses, four leaves. Financials use the build guide's illustrative formulas |
| Front end: `adk web` or a funding-queue UI | The funding-queue UI (the grant desk). The agent's `adk web` dev UI is also deployed |
| Gemini model and region | `gemini-3.8-flash` at the `global` endpoint, the newest Flash this project can call. us-west2 returns `FAILED_PRECONDITION`, and us-central1 and us-west1 serve only 2.5 Flash |
| Graph reservation | Not needed. Creating one hit a Qwiklabs-wide quota, but graph queries ran without it |
| P2 differentiator | None yet. The P1 items were prioritized |
