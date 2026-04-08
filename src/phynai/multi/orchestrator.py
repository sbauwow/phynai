"""Orchestrator — the public API for multi-agent execution.

Three execution modes:
  - run_agent:  single agent, one-shot
  - run_team:   auto-orchestrated via coordinator + task decomposition
  - run_tasks:  explicit task pipeline with manual dependencies

Ported from the open-multi-agent TypeScript framework and hardened for
enterprise: cost budgets, timeouts, approval gates, audit logging, and
prompt injection filtering between agents.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from phynai.multi.memory import SharedMemory
from phynai.multi.pool import AgentPool
from phynai.multi.queue import TaskQueue
from phynai.multi.scheduler import Scheduler
from phynai.multi.task import Task, TaskStatus
from phynai.multi.team import AgentSpec, Team

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

_MAX_TASKS = 20  # Coordinator cannot generate more than this
_DEFAULT_TASK_TIMEOUT_S = 120  # Per-task timeout
_DEFAULT_BUDGET_USD = 5.0  # Default cost ceiling for a run_team call

# Patterns that should not flow from one agent's output to another's prompt
_INTER_AGENT_THREAT_PATTERNS = [
    r"ignore\s+(previous|all|above|prior)\s+instructions",
    r"you\s+are\s+now\s+",
    r"system\s+prompt\s+override",
    r"disregard\s+(your|all|any)\s+(instructions|rules)",
]


def _sanitize_agent_output(text: str) -> str:
    """Strip prompt injection patterns from agent output before passing to teammates."""
    for pattern in _INTER_AGENT_THREAT_PATTERNS:
        text = re.sub(pattern, "[FILTERED]", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    success: bool
    output: str
    agent_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0


@dataclass
class TeamResult:
    success: bool
    output: str
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    tasks: list[Task] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_s: float = 0.0


@dataclass
class ProgressEvent:
    type: str  # agent_start, agent_complete, task_start, task_complete, task_failed, approval, error
    agent: str = ""
    task: str = ""
    data: Any = None


# ---------------------------------------------------------------------------
# Coordinator prompt templates
# ---------------------------------------------------------------------------

_DECOMPOSITION_PROMPT = """\
You are a project coordinator. Given a goal and a team of agents, decompose
the goal into concrete tasks and assign each to the most suitable agent.

## Team
{roster}

## Goal
{goal}

## Rules
- Maximum {max_tasks} tasks.
- Each task must be self-contained enough for one agent to complete.
- Assign tasks to agents whose role matches the work.
- Use depends_on only when a task genuinely needs another's output.

Respond with a JSON array of task objects. Each object has:
- "title": short task name
- "description": what to do (detailed enough for the assigned agent)
- "assignee": agent name from the team (or null for auto-assignment)
- "depends_on": list of task titles this task depends on (empty if independent)

Output ONLY the JSON array, no markdown fences, no explanation."""

_SYNTHESIS_PROMPT = """\
You are a project coordinator. Synthesize the results from a multi-agent team
into a final, cohesive answer to the original goal.

## Original Goal
{goal}

## Completed Tasks
{completed}

## Failed Tasks
{failed}

Provide a clear, complete answer that addresses the original goal."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Multi-agent orchestrator with auto-decomposition and task DAG execution.

    Parameters
    ----------
    default_model:
        Default LLM model for agents without an explicit model.
    default_provider:
        Default LLM provider (anthropic, openai, etc.).
    max_concurrency:
        Max concurrent agent runs (default 5).
    scheduler_strategy:
        Task assignment strategy: round-robin, least-busy, capability-match,
        dependency-first (default).
    budget_usd:
        Maximum total spend across all agents. Execution halts if exceeded.
    task_timeout_s:
        Per-task execution timeout in seconds (default 120).
    on_progress:
        Callback for real-time progress events.
    on_approval:
        Called after each task batch completes. Return False to abort remaining
        tasks. Enables human-in-the-loop gates.
    """

    def __init__(
        self,
        default_model: str = "claude-sonnet-4-6",
        default_provider: str = "anthropic",
        max_concurrency: int = 5,
        scheduler_strategy: str = "dependency-first",
        budget_usd: float = _DEFAULT_BUDGET_USD,
        task_timeout_s: float = _DEFAULT_TASK_TIMEOUT_S,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        on_approval: Callable[[list[Task], list[Task]], Awaitable[bool]] | None = None,
    ) -> None:
        self._default_model = default_model
        self._default_provider = default_provider
        self._max_concurrency = max_concurrency
        self._scheduler = Scheduler(strategy=scheduler_strategy)
        self._budget_usd = budget_usd
        self._task_timeout_s = task_timeout_s
        self._on_progress = on_progress
        self._on_approval = on_approval
        self._spent_usd = 0.0

    def _emit(self, event: ProgressEvent) -> None:
        if self._on_progress:
            try:
                self._on_progress(event)
            except Exception:
                pass

    def _check_budget(self) -> bool:
        """Return True if budget is still available."""
        return self._spent_usd < self._budget_usd

    def _track_cost(self, result: AgentResult) -> None:
        """Track cumulative spend."""
        self._spent_usd += result.cost_usd

    # -------------------------------------------------------------------
    # Mode 1: Single agent
    # -------------------------------------------------------------------

    async def run_agent(self, spec: AgentSpec, prompt: str) -> AgentResult:
        """Run a single agent on a prompt."""
        self._emit(ProgressEvent(type="agent_start", agent=spec.name))
        result = await self._execute_agent(spec, prompt)
        self._track_cost(result)
        self._emit(ProgressEvent(type="agent_complete", agent=spec.name, data=result.success))
        return result

    # -------------------------------------------------------------------
    # Mode 2: Auto-orchestrated team
    # -------------------------------------------------------------------

    async def run_team(self, team: Team, goal: str) -> TeamResult:
        """Auto-decompose a goal into tasks and execute with a team.

        Flow:
          1. Coordinator agent decomposes goal into task specs
          2. Tasks are loaded into a dependency queue
          3. Scheduler assigns tasks to agents
          4. Execution loop runs tasks in parallel respecting dependencies
          5. Coordinator synthesizes the final result
        """
        start = time.monotonic()
        self._spent_usd = 0.0

        # Phase 1: Decompose
        logger.info("Decomposing goal into tasks...")
        coordinator = AgentSpec(
            name="_coordinator",
            role="coordinator",
            model=self._default_model,
            provider=self._default_provider,
            max_iterations=5,
        )
        decomposition_prompt = _DECOMPOSITION_PROMPT.format(
            roster=team.roster_description(),
            goal=goal,
            max_tasks=_MAX_TASKS,
        )
        decomp_result = await self._execute_agent(coordinator, decomposition_prompt)
        self._track_cost(decomp_result)
        if not decomp_result.success:
            return TeamResult(
                success=False,
                output=f"Decomposition failed: {decomp_result.output}",
                duration_s=time.monotonic() - start,
            )

        # Phase 2: Parse tasks
        tasks = self._parse_task_specs(decomp_result.output, team)
        if not tasks:
            return TeamResult(
                success=False,
                output="Coordinator produced no tasks.",
                duration_s=time.monotonic() - start,
            )
        logger.info("Decomposed into %d tasks", len(tasks))

        # Phase 3-4: Execute
        team_result = await self.run_tasks(team, tasks)

        # Phase 5: Synthesize
        if not self._check_budget():
            team_result.output = f"Budget exhausted (${self._spent_usd:.2f} / ${self._budget_usd:.2f}). Partial results returned."
        else:
            completed = [t for t in team_result.tasks if t.status == TaskStatus.completed]
            failed = [t for t in team_result.tasks if t.status == TaskStatus.failed]

            completed_text = "\n\n".join(
                f"### {t.title} ({t.assignee})\n{t.result}" for t in completed
            ) or "(none)"
            failed_text = "\n".join(
                f"- {t.title}: {t.error}" for t in failed
            ) or "(none)"

            synthesis_prompt = _SYNTHESIS_PROMPT.format(
                goal=goal, completed=completed_text, failed=failed_text,
            )
            synth_result = await self._execute_agent(coordinator, synthesis_prompt)
            self._track_cost(synth_result)
            team_result.output = synth_result.output

        team_result.total_cost_usd = self._spent_usd
        team_result.duration_s = time.monotonic() - start

        logger.info(
            "Team run complete: %d tasks, $%.4f, %.1fs",
            len(team_result.tasks), self._spent_usd, team_result.duration_s,
        )
        return team_result

    # -------------------------------------------------------------------
    # Mode 3: Explicit task pipeline
    # -------------------------------------------------------------------

    async def run_tasks(self, team: Team, tasks: list[Task]) -> TeamResult:
        """Execute an explicit task list with dependency resolution."""
        queue = TaskQueue()
        queue.add_all(tasks)

        pool = AgentPool(max_concurrency=team.max_concurrency)
        memory = SharedMemory() if team.shared_memory else None
        agent_results: dict[str, AgentResult] = {}
        total_in = 0
        total_out = 0

        # Execution loop — run rounds until the queue is drained
        while not queue.is_done():
            # Budget check
            if not self._check_budget():
                logger.warning("Budget exhausted ($%.2f / $%.2f), aborting remaining tasks",
                               self._spent_usd, self._budget_usd)
                for t in queue.by_status(TaskStatus.pending) + queue.by_status(TaskStatus.blocked):
                    t.status = TaskStatus.skipped
                    t.error = "Budget exhausted"
                break

            # Assign unassigned pending tasks
            self._scheduler.auto_assign(queue, team.agents)
            pending = queue.pending()

            if not pending:
                blocked = queue.by_status(TaskStatus.blocked)
                in_progress = queue.by_status(TaskStatus.in_progress)
                if not in_progress and blocked:
                    for t in blocked:
                        queue.fail(t.id, "Deadlocked — dependencies cannot be satisfied")
                    break
                await asyncio.sleep(0.1)
                continue

            # Execute batch — build per-task coroutines with proper variable capture
            for task in pending:
                queue.start(task.id)

            async def _make_task_runner(t: Task) -> tuple[str, str | Exception]:
                """Closure with proper capture of task variable."""
                try:
                    output = await asyncio.wait_for(
                        self._run_single_task(t, team, memory),
                        timeout=self._task_timeout_s,
                    )
                    return t.id, output
                except asyncio.TimeoutError:
                    return t.id, RuntimeError(f"Task timed out after {self._task_timeout_s}s")
                except Exception as e:
                    return t.id, e

            coros = [_make_task_runner(t) for t in pending]
            pairs = await asyncio.gather(*coros)
            results: dict[str, str | Exception] = dict(pairs)

            # Process results
            for task in pending:
                outcome = results.get(task.id)
                if isinstance(outcome, Exception):
                    queue.fail(task.id, str(outcome))
                    self._emit(ProgressEvent(type="task_failed", task=task.title, data=str(outcome)))
                    logger.warning("Task failed: '%s' — %s", task.title, outcome)
                elif isinstance(outcome, str) and outcome.startswith("FAILED:"):
                    queue.fail(task.id, outcome[7:].strip())
                    self._emit(ProgressEvent(type="task_failed", task=task.title))
                else:
                    queue.complete(task.id, str(outcome or ""))
                    self._emit(ProgressEvent(type="task_complete", task=task.title))
                    logger.info("Task completed: '%s' (%s)", task.title, task.assignee)

            # Approval gate
            if self._on_approval:
                completed = queue.by_status(TaskStatus.completed)
                next_up = queue.pending()
                if next_up:
                    self._emit(ProgressEvent(type="approval", data={"completed": len(completed), "next": len(next_up)}))
                    approved = await self._on_approval(completed, next_up)
                    if not approved:
                        logger.info("Approval denied — aborting remaining tasks")
                        for t in queue.by_status(TaskStatus.pending) + queue.by_status(TaskStatus.blocked):
                            t.status = TaskStatus.skipped
                            t.error = "Aborted by operator"
                        break

        all_ok = all(t.status == TaskStatus.completed for t in queue.all_tasks())
        return TeamResult(
            success=all_ok,
            output="",
            agent_results=agent_results,
            tasks=queue.all_tasks(),
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_cost_usd=self._spent_usd,
        )

    # -------------------------------------------------------------------
    # Internal: run a single task with an agent
    # -------------------------------------------------------------------

    async def _run_single_task(
        self, task: Task, team: Team, memory: SharedMemory | None,
    ) -> str:
        """Execute one task. Returns output string or 'FAILED: ...' on error."""
        spec = team.get_agent(task.assignee or "")
        if spec is None:
            raise RuntimeError(f"No agent named '{task.assignee}'")

        # Build prompt with task context + sanitized shared memory
        prompt_parts = [task.description]
        if memory:
            mem_summary = memory.summary()
            if mem_summary:
                prompt_parts.append(
                    "\n\n## Context from teammates\n" + _sanitize_agent_output(mem_summary)
                )
        prompt = "\n\n".join(prompt_parts)

        self._emit(ProgressEvent(type="task_start", agent=spec.name, task=task.title))
        result = await self._execute_agent(spec, prompt)
        self._track_cost(result)

        if result.success and memory:
            memory.write(spec.name, task.title, _sanitize_agent_output(result.output))

        return result.output if result.success else f"FAILED: {result.output}"

    # -------------------------------------------------------------------
    # Internal: execute a single agent via PhynaiAgent
    # -------------------------------------------------------------------

    async def _execute_agent(self, spec: AgentSpec, prompt: str) -> AgentResult:
        """Build and run a PhynaiAgent for a single task."""
        from phynai.agent import (
            PhynaiAgent,
            PhynaiClientManager,
            PhynaiContextManager,
            PhynaiCostLedger,
            PhynaiSessionStore,
        )
        from phynai.runtime import PhynaiToolRuntime
        from phynai.tools import register_core_tools
        from phynai.contracts.work import WorkItem

        model = spec.model or self._default_model
        provider = spec.provider or self._default_provider
        api_key = os.environ.get("PHYNAI_API_KEY", "")

        client = PhynaiClientManager(
            provider=provider,
            model=model,
            api_key=api_key,
        )

        tools = PhynaiToolRuntime()
        register_core_tools(tools)

        # Enforce tool allowlist from AgentSpec — if spec.tools is set,
        # remove any tools not in the allowlist (security: privilege isolation)
        if spec.tools:
            allowed = set(spec.tools)
            registered = [t.name for t in tools.list_tools()]
            for tool_name in registered:
                if tool_name not in allowed:
                    tools.unregister(tool_name)

        system = spec.system_prompt or f"You are {spec.name}, a {spec.role or 'general-purpose'} agent."
        context = PhynaiContextManager(system_prompt=system)
        session = PhynaiSessionStore()
        ledger = PhynaiCostLedger()

        agent = PhynaiAgent(
            client=client,
            tools=tools,
            context=context,
            session=session,
            ledger=ledger,
        )

        work = WorkItem(
            prompt=prompt,
            source="multi",
        )
        if spec.max_iterations:
            work.constraints.max_iterations = spec.max_iterations

        start = time.monotonic()
        result = await agent.run(work)
        elapsed = time.monotonic() - start
        cost = result.cost

        return AgentResult(
            success=result.status.value == "completed",
            output=result.response or result.error or "",
            agent_name=spec.name,
            input_tokens=cost.input_tokens if cost else 0,
            output_tokens=cost.output_tokens if cost else 0,
            cost_usd=cost.estimated_cost_usd if cost else 0.0,
            duration_s=round(elapsed, 2),
        )

    # -------------------------------------------------------------------
    # Task spec parsing
    # -------------------------------------------------------------------

    @staticmethod
    def _parse_task_specs(raw: str, team: Team) -> list[Task]:
        """Parse coordinator output into Task objects.

        Handles JSON in markdown fences or raw JSON arrays.
        Caps at _MAX_TASKS to prevent runaway decomposition.
        """
        # Strip markdown fences
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        try:
            specs = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if not match:
                logger.warning("Could not parse task specs from coordinator output")
                return []
            try:
                specs = json.loads(match.group())
            except json.JSONDecodeError:
                return []

        if not isinstance(specs, list):
            return []

        # Cap task count
        if len(specs) > _MAX_TASKS:
            logger.warning("Coordinator generated %d tasks, capping at %d", len(specs), _MAX_TASKS)
            specs = specs[:_MAX_TASKS]

        # Build tasks with title-based dependency resolution
        tasks: list[Task] = []
        title_to_id: dict[str, str] = {}

        for s in specs:
            if not isinstance(s, dict):
                continue
            task = Task(
                title=s.get("title", "Untitled"),
                description=s.get("description", ""),
                assignee=s.get("assignee"),
            )
            title_to_id[task.title.lower()] = task.id
            tasks.append(task)

        # Resolve depends_on from titles to IDs
        for i, s in enumerate(specs):
            if not isinstance(s, dict) or i >= len(tasks):
                continue
            deps = s.get("depends_on", [])
            if isinstance(deps, list):
                resolved = []
                for dep_title in deps:
                    if not isinstance(dep_title, str):
                        continue
                    dep_id = title_to_id.get(dep_title.lower())
                    if dep_id:
                        resolved.append(dep_id)
                    else:
                        logger.warning("Unknown dependency '%s' in task '%s'", dep_title, tasks[i].title)
                tasks[i].depends_on = resolved

        # Validate assignees
        valid_names = set(team.agent_names())
        for task in tasks:
            if task.assignee and task.assignee not in valid_names:
                logger.warning("Unknown assignee '%s' for task '%s', will auto-assign", task.assignee, task.title)
                task.assignee = None

        return tasks
