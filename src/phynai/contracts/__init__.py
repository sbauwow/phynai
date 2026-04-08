"""PhynAI Contract Definitions — all layers.

Pure data classes (Pydantic BaseModel) and protocol interfaces (typing.Protocol).
No implementation lives here. This is the shared vocabulary of the system.
"""

# Layer 1-2: Tool Runtime
from phynai.contracts.events import Event, EventType, ToolEvent
from phynai.contracts.tools import Risk, ToolCall, ToolHandler, ToolMetadata, ToolResult
from phynai.contracts.middleware import (
    Middleware,
    MiddlewareContext,
    MiddlewarePhase,
    MiddlewareResult,
)
from phynai.contracts.policy import PolicyCheck, PolicyDecision, PolicyVerdict
from phynai.contracts.runtime import ToolRuntime

# Layer 3: Agent Core
from phynai.contracts.work import (
    Artifact,
    CostRecord,
    WorkConstraints,
    WorkItem,
    WorkPriority,
    WorkResult,
    WorkStatus,
)
from phynai.contracts.agent import (
    AgentCore,
    ClientManager,
    ContextManager,
    CostLedger,
    SessionStore,
)

# Layer 5: Interfaces
from phynai.contracts.interfaces import (
    CLIInterface,
    GatewayInterface,
    Interface,
)

__all__ = [
    # Events
    "Event", "EventType", "ToolEvent",
    # Tools
    "Risk", "ToolCall", "ToolHandler", "ToolMetadata", "ToolResult",
    # Middleware
    "Middleware", "MiddlewareContext", "MiddlewarePhase", "MiddlewareResult",
    # Policy
    "PolicyCheck", "PolicyDecision", "PolicyVerdict",
    # Runtime
    "ToolRuntime",
    # Work
    "Artifact", "CostRecord", "WorkConstraints", "WorkItem", "WorkPriority", "WorkResult", "WorkStatus",
    # Agent
    "AgentCore", "ClientManager", "ContextManager", "CostLedger", "SessionStore",
    # Interfaces
    "CLIInterface", "GatewayInterface", "Interface",
]
