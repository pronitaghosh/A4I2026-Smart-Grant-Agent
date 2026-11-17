"""Verified cascade query over a4i_econ.LocalEconomy (reference implementation).

The agent lane's tools live in agent/grant_agent/ (see agent/BUILD_GUIDE.md). This package
keeps a tested multi-hop query and its stopping rule for them to reuse or compare against.
"""

from .cascade import businesses, compare_cascades, grant_reasons, summarize, trace_cascade, trace_many

__all__ = ["trace_cascade", "trace_many", "compare_cascades", "grant_reasons", "businesses", "summarize"]
