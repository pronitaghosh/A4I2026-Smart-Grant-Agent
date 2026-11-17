import os
from google.adk.agents import Agent

os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
if os.environ["GOOGLE_CLOUD_LOCATION"] not in {"us-central1", "global"}:
    raise RuntimeError("For this deployment, set GOOGLE_CLOUD_LOCATION=us-central1 or global.")

from .prompt import SYSTEM_INSTRUCTION
from .tools import get_cascade, recommend_grants, exposed_non_applicants, audit_allocation

tools = [recommend_grants, get_cascade, exposed_non_applicants, audit_allocation]

# MCP Toolbox (Step 9). Optional so the agent still runs locally before Toolbox is up.
# ToolboxToolset connects on first use, not at import, so an unreachable Toolbox
# cannot stop the agent from loading. Needs: pip install "google-adk[toolbox]".
if os.getenv("TOOLBOX_URL"):
    from google.adk.tools.toolbox_toolset import ToolboxToolset
    tools.append(ToolboxToolset(server_url=os.environ["TOOLBOX_URL"], toolset_name="grant_toolset"))

instruction = SYSTEM_INSTRUCTION
if not os.getenv("TOOLBOX_URL"):
    instruction += "\nMCP lookups are unavailable: request a business_id when needed; " \
                   "do not invent name matches, loans, or tract profiles. Disclose this limitation."

root_agent = Agent(
    name="grant_agent",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    description="Advises a city economic-development officer on Main Street relief grants for the Central Market corridor.",
    instruction=instruction,
    tools=tools,
)
