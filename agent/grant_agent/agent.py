import os
from google.adk.agents import Agent

from .prompt import SYSTEM_INSTRUCTION
from .tools import get_cascade, recommend_grants, exposed_non_applicants

tools = [recommend_grants, get_cascade, exposed_non_applicants]

# MCP Toolbox (Step 9). Optional so the agent still runs locally before Toolbox is up.
# ToolboxToolset connects on first use, not at import, so an unreachable Toolbox
# cannot stop the agent from loading. Needs: pip install "google-adk[toolbox]".
if os.getenv("TOOLBOX_URL"):
    from google.adk.tools.toolbox_toolset import ToolboxToolset
    tools.append(ToolboxToolset(server_url=os.environ["TOOLBOX_URL"], toolset_name="grant_toolset"))

root_agent = Agent(
    name="grant_agent",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    description="Advises a city economic-development officer on Main Street relief grants for the Central Market corridor.",
    instruction=SYSTEM_INSTRUCTION,
    tools=tools,
)
