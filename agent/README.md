# agent/

The Smart Grant Agent: an [ADK](https://adk.dev/) agent on Gemini that recommends who to fund,
how much and why. It also holds the tools the agent calls, the allocator that makes the decision,
the MCP Toolbox config and the tests.

Deployed as the Cloud Run service `grant-agent`, at
https://grant-agent-173086820802.us-west2.run.app. The grant desk (`frontend/`) bundles the same
`grant_agent` package, so the desk and the agent always show the same numbers.

| Path | What it is |
|---|---|
| [`grant_agent/agent.py`](grant_agent/agent.py) | `root_agent`: the model, the instruction and the tool list |
| [`grant_agent/prompt.py`](grant_agent/prompt.py) | `SYSTEM_INSTRUCTION`: which tool answers which question, the answer format, the rules |
| [`grant_agent/tools.py`](grant_agent/tools.py) | The three Python tools, and the multi-hop graph traversal behind them |
| [`grant_agent/allocator.py`](grant_agent/allocator.py) | The judgment layer: closure risk, path weights, allocation, sensitivity. Pure Python, no BigQuery |
| [`grant_agent/requirements.txt`](grant_agent/requirements.txt) | `google-adk[toolbox]`, `google-cloud-bigquery`, `toolbox-core` |
| [`toolbox/tools.yaml`](toolbox/tools.yaml) | MCP Toolbox for Databases config: four parameterized SQL tools, with the source set to `readOnly` and limited to the `a4i_econ` dataset |
| [`toolbox/deploy.sh`](toolbox/deploy.sh) | Deploys the Toolbox to Cloud Run: service account, `tools` secret, service. Safe to re-run |
| [`tests/test_allocator.py`](tests/test_allocator.py) | Allocator unit tests |
| [`grant_tools/`](grant_tools/) | A reference cascade query and memo-reason writer. Tested, but not called by the agent |
| [`BUILD_GUIDE.md`](BUILD_GUIDE.md) | The step-by-step guide this package was built from, with the officer questions to test against |
| [`EQUITY_AUDIT_BUILD.md`](EQUITY_AUDIT_BUILD.md) | Build plan for an equity-audit tool (agent tool, desk panel, story handoff). Planned, not built yet; the current audit numbers are in [TEAM_NOTES.md](../docs/TEAM_NOTES.md#equity-audit-for-the-deck) |

## The agent

`root_agent` (in `agent.py`) is an `Agent` named `grant_agent`:

- **Model:** `GEMINI_MODEL`, default `gemini-2.5-flash`, through Vertex AI.
  `GOOGLE_CLOUD_LOCATION` defaults to `us-central1`. The agent refuses to start with any location
  other than `us-central1` or `global`, because Gemini isn't available to this project in
  us-west2.
- **Tools:** our three Python function tools. When `TOOLBOX_URL` is set, it also gets the
  Toolbox's `grant_toolset` through ADK's `ToolboxToolset`. That connects on first use, so the
  agent still loads if the Toolbox is down. Without `TOOLBOX_URL`, the instruction gains a rule:
  lookups are unavailable, so ask for a `business_id` and don't invent name matches, loans or
  tract profiles.
- **Instruction:** `prompt.py`. It routes questions to tools: "Who should we fund?" goes to
  `recommend_grants`, "Why this one?" to `get_cascade`, "Who else is exposed?" to
  `exposed_non_applicants`, and lookups to the Toolbox. It also fixes the answer format: an award
  table, reasons with the strongest chain written A → B → C, the runner-up, the assumptions, the
  evidence note and the neighborhood. And it sets the rules:
  - Every number comes from a tool result. If none gives it, say so.
  - Say "expected closures avoided", never "jobs saved".
  - Never recommend a business just because it is the most connected.
  - Call a leaf a leaf; that alone isn't a reason to reject it.
  - Compare applicants on one metric, `closures_avoided_per_10k_if_fully_funded`. Within 10% of
    each other, refuse a strict ordering and name the information that would separate them.
  - For a full memo, call `get_cascade` for the lead grant and the runner-up, and show the
    traversal, a two-hop path and the cutoff.
  - No name match means the business is unknown; several matches means ask which address.
  - Describe modeled supply links as modeled.
  - Always disclose that applicant financials and grant effectiveness are illustrative.
  - No race or ethnicity in any reasoning.

## Tools

ADK sends each tool's docstring to Gemini as its description.

### Python tools (`grant_agent/tools.py`)

| Tool | Use it for | Returns |
|---|---|---|
| `recommend_grants(budget=50000, depth=2)` | "Who should we fund?", "What does $X change?" | `budget`, `allocated`, `unallocated`, `expected_closures_avoided_total` (unweighted), `need_weighted_priority_total`, `min_link`, `recommended[]` (in priority order) and `not_funded[]` (per applicant: `award`, `pct_of_request`, `pct_of_budget`, `baseline_risk`, `risk_after_award`, `downstream_exposed`, `closures_avoided_by_this_grant`, `closures_avoided_per_10k_if_fully_funded`, `reasons[]`, and for each grant its own `runner_up`), the queue's `runner_up`, `weakest_funded`, `assumptions`, `sensitivity`, `evidence_note` |
| `get_cascade(business_id, depth=2)` | "What does this business hold up?", "Show me why" | `business`, `exposed_count`, `by_hop`, `local_alternatives_same_industry`, `is_leaf`, `traversal` (the GQL `MATCH` it ran), `min_link`, up to 15 strongest `paths[]` (`path` as "A → B → C", `hops`, `weight`, `link_intensities`), always including a multi-hop path when one exists, and `evidence_note` |
| `exposed_non_applicants(depth=2)` | "Who else is at risk?", "Should someone downstream have applied?" | Up to 15 businesses downstream of applicants that didn't apply: the chains reaching them, the strongest weight, and any real SBA charge-off |

How `recommend_grants` works:

1. It runs four BigQuery queries: the applications joined to businesses, tract need and
   same-industry counts; the corridor's average poverty; SBA charge-offs; and **one graph
   traversal for all applicants**.
2. It builds an `allocator.Applicant` for each applicant.
3. It calls `allocator.recommend()`.

The traversal (`_cascades`) runs a quantified-path GQL query:

```sql
GRAPH a4i_econ.LocalEconomy
MATCH p = (seed:Business)-[:Supplies]->{1,2}(x:Business)
WHERE seed.business_id IN (...) AND x.business_id != seed.business_id
RETURN seed.business_id AS seed_id, TO_JSON(p) AS path_json
```

It parses each path into nodes and link intensities. Then it applies two rules:

- **Weakest-link cutoff.** Any path with a link below **0.02** (2 cents per input dollar) is
  skipped. This is what stops generic suppliers, like Tradespace's data hosting, from looking
  like hubs.
- **Strongest path wins.** For each exposed business, only the strongest path is kept.

`depth` is clamped to 1 to 3 in every tool. Business IDs are checked against
`^[A-Za-z0-9._-]{1,64}$` before they go into SQL. The query returns at most 50,000 paths, and
reaching that limit raises an error rather than silently undercounting.

### MCP Toolbox tools (`toolbox/tools.yaml`)

The Toolbox runs only these four predefined, parameterized queries, so the agent can't run
arbitrary SQL.

| Tool | Returns |
|---|---|
| `list_applications` | All applications, with business, address, industry, amount requested, financials and hardship note |
| `find_business(name_contains)` | Up to 10 businesses whose name contains the text, with `business_id` |
| `loan_history(business_id)` | Real SBA 7(a) loans: bank, amount, date, status, whether charged off |
| `tract_profile(business_id)` | Tract poverty, no-vehicle rate, public assistance rate, median income (ACS 2019) |

## The allocator (`grant_agent/allocator.py`)

The unit of value is **expected business closures avoided**. Per-business employment isn't
public, so jobs aren't counted.

- **Closure risk** (illustrative, from the application): 5% + 50% × revenue drop + 35% × cash
  shortfall below six months, plus 10 points for a prior SBA charge-off. Clamped to 2–90%.
- **Risk reduction from a grant:** baseline risk × `MAX_RISK_REDUCTION` × (award ÷ request) ^
  `EFFECT_CURVE`.
- **Path weight:** the product along the chain of min(1, intensity ÷ `STRONG_LINK`), × `HOP_DECAY`
  for each hop after the first.
- **Value of a set of awards:** each funded applicant is protected by its risk reduction × need
  multiplier. Each downstream business is protected by risk reduction × `CONTAGION` ×
  uniqueness × path weight × need multiplier. Uniqueness is 1 ÷ (1 + same-industry businesses in
  the corridor). Every business, applicant or downstream, counts once, at its best protection.
  The value is the sum.
- **Optimized vs reported.** The allocation maximizes the need-weighted value
  (`need_weighted_priority_total`), and the funded list is in that priority order. The number
  reported as `expected_closures_avoided_total` (and per grant) is the same sum with the need
  multiplier set to 1, an unweighted count of closures. Need decides who gets money; it doesn't
  inflate the count.
- **Allocation:** greedy. It repeatedly gives the next slice (25% of a request, at least $2,000
  for a first award) to whoever adds the most value per dollar. It stops when the budget or the
  worthwhile moves run out. It refuses a non-finite or negative budget, a non-positive request,
  and duplicate registrations (same ID, or same name and address).
- **A runner-up for every grant** (`grant_runner_up`, the PRD's `value_grants`). Withdraw the
  grant, let the same greedy allocator re-spend that money (plus anything unallocated) on
  everyone else, and compare. Each funded row's `runner_up` gives `money_would_go_to`,
  `closures_avoided_by_this_grant` and `closures_avoided_instead` (unweighted), the
  `priority_margin_pct` on the need-weighted value the allocator optimizes, and a `verdict`:
  `this_grant`, `alternative`, or `indistinguishable` when within 10%.
- **Sensitivity:** it reruns the allocation under five alternatives and reports
  `funded_in_every_scenario`. The alternatives are contagion at 0.10 and at 0.50, a linear
  effect curve, a steeper diminishing curve, and need ignored.

Every assumption is in one dict, `ASSUMPTIONS`, and is returned with every recommendation:

| Assumption | Value | Meaning |
|---|---|---|
| `STRONG_LINK` | 0.05 | A supplier worth 5 cents per dollar of the buyer's output is a full-strength dependency |
| `HOP_DECAY` | 0.5 | Each extra hop halves the exposure |
| `CONTAGION` | 0.25 | Chance a buyer closes when a full-strength local supplier closes and isn't replaced |
| `MAX_RISK_REDUCTION` | 0.40 | Fully funding a request cuts the applicant's closure risk by 40% |
| `EFFECT_CURVE` | 0.5 | A partial award's effect is (award ÷ request) ^ 0.5: diminishing returns |
| `NEED_WEIGHT` | 0.5 | Tract poverty above the corridor average raises value (multiplier capped at 0.75–1.5×) |
| `STEP_FRACTION` | 0.25 | Awards are made in 25% slices of each request |
| `MIN_AWARD` | 2000 | Smallest grant worth administering |

`MAX_RISK_REDUCTION` scales every grant equally. It changes *how many* closures are avoided, not
*who* is funded.

## Run it locally

From the repo root, with a virtual environment that has `grant_agent/requirements.txt`
installed (see the [project README](../README.md#quick-start)). Create `agent/grant_agent/.env`;
it's gitignored:

```bash
GOOGLE_GENAI_USE_ENTERPRISE=1          # Gemini through Vertex AI (the guide's GOOGLE_GENAI_USE_VERTEXAI=TRUE also works)
GOOGLE_CLOUD_PROJECT=qwiklabs-gcp-04-524776bdd295
GOOGLE_CLOUD_LOCATION=us-central1      # Gemini isn't available to this project in us-west2
GEMINI_MODEL=gemini-2.5-flash
TOOLBOX_URL=https://toolbox-173086820802.us-west2.run.app   # optional
```

```bash
gcloud auth application-default login
cd agent
adk web            # dev UI; pick grant_agent
adk api_server     # or the HTTP API the grant desk uses
```

Questions to try are in [BUILD_GUIDE.md, step 11](BUILD_GUIDE.md#11-test-against-the-officers-afternoon).
For example: "We have $50,000. Who should we fund?", "Why isn't the biggest supplier funded?",
"What does $8,000 actually change?", and the failure cases (leaf applicant, unknown name, tiny
budget).

## Tests

```bash
cd agent && python -m pytest tests -q
```

The tests need no credentials. `tools.py` creates its BigQuery client on first use, and the
realistic test replaces `_rows`.

| Test | Checks |
|---|---|
| `test_robust_hub_is_not_funded_first` | A healthy hub with wide reach is not funded ahead of a distressed, unique supplier |
| `test_leaf_still_fundable_and_budget_respected` | A leaf with no downstream businesses can still be funded, and the budget is never exceeded |
| `test_realistic_weak_hub_is_not_funded_first` | Feeds the shape of Tradespace's 194 real paths through `_cascades`: one link at 0.021, every other path starting with a 0.015 link. It fails without the 0.02 cutoff and passes with it |
| `test_duplicate_registration_is_refused` | Two applications from the same name and address raise an error instead of being funded twice |
| `test_reported_total_is_unweighted_by_need` | The reported total is the unweighted count, and the need-weighted priority is reported separately |
| `test_applicant_downstream_of_another_counts_once` | An applicant that is also downstream of another grantee isn't counted twice |
| `test_path_limit_is_an_error_not_a_silent_truncation` | Hitting the 50,000-path limit raises an error |
| `test_every_grant_has_its_own_runner_up` | Every funded grant names a runner-up, the money re-spent never exceeds what the grant freed, and its closures count matches the grant's own |
| `test_identical_applicants_are_indistinguishable` | Two identical applicants: the runner-up is the twin and the verdict is "indistinguishable" |

## Deploy

`adk deploy cloud_run`, then restore the runtime settings. The exact commands are in
[docs/ARCHITECTURE.md, Deploying](../docs/ARCHITECTURE.md#deploying). After changing the tools,
also redeploy the grant desk, because its container bundles this package.

## `grant_tools/`: the reference cascade query

The data lane's tested version of the traversal, kept for comparison:

- [`cascade.sql`](grant_tools/cascade.sql) uses `GRAPH_TABLE` and `MATCH` over `{1,3}` hops.
  It reads routes with `NODES(p)` instead of parsing `TO_JSON(p)`, and computes path strength
  and the weakest link in SQL. It keeps the strongest path per exposed business, with
  `@min_link` (default 0.02) as the stopping rule.
- [`cascade.py`](grant_tools/cascade.py) wraps it: `trace_cascade`, `trace_many`,
  `compare_cascades` (with a 10% tie margin) and `grant_reasons`, which writes memo-ready
  reason lines. It also drops duplicate registrations (same name and address).

The production tool `tools._cascades` adopted the same 0.02 weakest-link rule. Run the
reference from `agent/` with `python -m grant_tools.cascade 114`.
