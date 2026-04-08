"""Team — agent roster and configuration for multi-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentSpec:
    """Specification for an agent within a team.

    Each agent can use a different model and provider. The ``role`` field
    is used by the capability-match scheduler to assign tasks.
    """

    name: str
    role: str = ""
    model: str | None = None       # defaults to team/orchestrator default
    provider: str | None = None     # defaults to team/orchestrator default
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 50
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Team:
    """A named roster of agents that collaborate on a goal.

    Teams are passed to :meth:`Orchestrator.run_team` or
    :meth:`Orchestrator.run_tasks`.
    """

    name: str
    agents: list[AgentSpec]
    max_concurrency: int = 5
    shared_memory: bool = True

    def agent_names(self) -> list[str]:
        return [a.name for a in self.agents]

    def get_agent(self, name: str) -> AgentSpec | None:
        for a in self.agents:
            if a.name == name:
                return a
        return None

    def roster_description(self) -> str:
        """Format agent roster for the coordinator prompt."""
        lines = []
        for a in self.agents:
            role = a.role or "general-purpose"
            tools = ", ".join(a.tools) if a.tools else "all"
            lines.append(f"- **{a.name}** ({role}): tools=[{tools}]")
        return "\n".join(lines)
