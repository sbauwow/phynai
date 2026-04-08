"""PhynAI Multi-Agent — team orchestration, task DAGs, and concurrent execution.

Ported from the open-multi-agent TypeScript framework. Three execution modes:
  - run_agent:  single agent, one-shot
  - run_team:   auto-orchestrated via coordinator + task decomposition
  - run_tasks:  explicit task pipeline with dependencies
"""

from phynai.multi.orchestrator import Orchestrator
from phynai.multi.team import Team, AgentSpec
from phynai.multi.task import Task, TaskStatus
from phynai.multi.memory import SharedMemory

__all__ = [
    "Orchestrator",
    "Team",
    "AgentSpec",
    "Task",
    "TaskStatus",
    "SharedMemory",
]
