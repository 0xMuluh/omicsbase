"""Backend-neutral coding-agent runtime facade.

The workspace workflow talks to this package instead of depending directly on
one coding-agent vendor.  Backends expose the same event-shaped dictionaries
already consumed by the workspace and task streams, which keeps the existing
SSE contract stable while allowing another agent adapter to be added later.
"""

from .contract import AgentEvent, AgentSession, AgentTurnRequest, CodingAgentBackend
from .registry import get_coding_agent_backend, register_coding_agent_backend, stream_coding_agent

__all__ = [
    "AgentEvent",
    "AgentSession",
    "AgentTurnRequest",
    "CodingAgentBackend",
    "get_coding_agent_backend",
    "register_coding_agent_backend",
    "stream_coding_agent",
]
