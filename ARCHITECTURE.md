# ARCHITECTURE.md — PhynAI Agent

## Overview

PhynAI Agent is a 4-layer AI agent runtime. It runs as an interactive CLI, a
one-shot command runner, or a Slack gateway. A single `PhynaiAgent` instance
drives the LLM-to-tool loop. There is no orchestrator layer — interfaces feed
`WorkItem`s directly to the agent core.

## Design Principles

1. **Contracts first.** All layers communicate through typed dataclasses in
   `phynai.contracts`. No layer imports another layer's internals. If it
   crosses a boundary, it has a contract.

2. **Scoped state.** No process-global singletons. Every session carries its
   own `SessionStore`, `CostLedger`, and `ContextManager`. Two agents in the
   same process do not share mutable state.

3. **Async-native core with sync wrappers.** The agent loop, tool runtime,
   and event bus are async. Sync entry points call `asyncio.run()` at the
   boundary. No sync code inside the core.

4. **Every tool declares metadata.** Tools expose a `ToolMetadata` struct
   (name, description, risk level, parameters, mutates, requires_confirmation).
   The registry rejects tools without complete metadata.

5. **Event-sourced execution journal.** Every tool invocation emits a typed
   event to the `ExecutionJournal`, which writes to `~/.phynai/journal.db`
   (SQLite) on every `record()` call. Falls back to in-memory if the DB is
   unavailable. The journal survives process restarts.

6. **Security-first defaults.** No shell injection (exec-based subprocesses
   only), path traversal blocked, SSRF blocked, Slack gateway default-deny,
   per-user rate limiting, session files chmod 0o600, auth tokens chmod 0o600,
   `user_id` propagated through all layers.

## 4-Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│  L5  INTERFACES                                         │
│  CLI REPL  ·  Slack Gateway (Socket Mode)               │
│  Accepts user input, emits WorkItems                    │
├─────────────────────────────────────────────────────────┤
│  L3  AGENT CORE                                         │
│  PhynaiAgent  ·  ClientManager  ·  ContextManager       │
│  SessionStore  ·  CostLedger                            │
│  Receives one WorkItem, drives LLM ↔ tool loop          │
├─────────────────────────────────────────────────────────┤
│  L2  TOOL RUNTIME                                       │
│  ToolRegistry  ·  PolicyPipeline  ·  Middleware         │
│  EventBus  ·  ExecutionJournal (SQLite)                 │
│  Validates and executes tool calls                      │
├─────────────────────────────────────────────────────────┤
│  L1  TOOLS                                              │
│  terminal · file_tools · web_tools · ms365              │
│  + skills (auto-loaded from ~/.phynai/skills/)          │
└─────────────────────────────────────────────────────────┘

Data flow:  L5 → L3 → L2 → L1
```

L4 (orchestrator) was intentionally removed. L5 passes `WorkItem`s directly to
L3. There is no scheduler, no queue, and no dependency graph. Layers only
depend downward — L3 never imports from L5, L2 never imports from L3.

## The Work Contract

`WorkItem` and `WorkResult` are the central types that cross the L5→L3
boundary.

```python
@dataclass
class WorkItem:
    id: str                  # unique, assigned at creation
    prompt: str              # natural-language objective
    context: dict            # arbitrary key-value context
    constraints: list        # budget, timeout, tool restrictions
    priority: int            # default 0
    parent_id: str | None    # reserved for future use
    session_id: str | None   # links to a persisted session
    source: str              # "cli", "slack", etc.
    user_id: str | None      # caller identity, propagated to all layers
    metadata: dict           # interface-specific extras
    created_at: datetime
```

The interface constructs a `WorkItem` and calls `PhynaiAgent.run(item)`. The
agent drives the LLM loop, dispatches tool calls through L2, and returns a
`WorkResult` containing status, output, and cost summary.

## Layer-by-Layer Breakdown

### L5 — Interfaces (`src/phynai/interfaces/`)

Thin protocol translators. Each interface converts external input into a
`WorkItem` and passes the `WorkResult` back to the caller.

- **`cli.py`** — Interactive REPL with readline, spinner, and color output.
  Wraps each user message in a `WorkItem` and prints the result.
- **`gateway.py`** — `PhynaiGateway` base class with per-user sliding-window
  rate limiting (5 req / 10 sec). `SlackGateway` extends it with Socket Mode
  event handling. Requires `SLACK_ALLOWED_USERS` to be set — hard fail if
  empty (default deny). Error messages shown to users are generic; full
  exceptions are logged internally only.

Entry point: **`cli_main.py`** — `phynai` CLI, argument parsing, structured
JSON logging via `_JsonFormatter`.

### L3 — Agent Core (`src/phynai/agent/`)

- **`loop.py`** — `PhynaiAgent`: the conversation driver. Maintains the
  message history, calls the LLM, parses tool calls from responses, dispatches
  to L2, and loops until the LLM produces a final answer or a budget/turn
  limit is reached.
- **`client.py`** — `httpx`-based LLM client. Supports Anthropic natively and
  any OpenAI-compatible endpoint. Handles streaming and non-streaming modes.
- **`context.py`** — `ContextManager`: builds the system prompt and applies
  token-based conversation compression when the window fills.
- **`session.py`** — `PhynaiSessionStore`: reads and writes
  `~/.phynai/sessions/<id>.json` with `chmod 0o600`. Stores message history
  and metadata across restarts.
- **`cost.py`** — In-memory `CostLedger`: accumulates token counts and
  estimated API cost for the current session.

### L2 — Tool Runtime (`src/phynai/runtime/`)

`PhynaiToolRuntime.dispatch()` is the single execution path for every tool
call. The pipeline is:

```
registry lookup → policy eval → PRE middleware → execute → POST middleware → journal
```

- **`registry.py`** — `ToolRegistry`: scoped per agent instance. Stores
  `ToolMetadata` + callable for each registered tool. No global registry.
- **`policy.py`** — `PolicyPipeline`: runs each registered `PolicyCheck` in
  order. A DENY decision short-circuits the pipeline — the tool is not
  executed.
- **`middleware.py`** — Async PRE/POST/ERROR middleware chain. Middleware
  receives a `MiddlewareContext` and calls `next()` to proceed.
- **`events.py`** — `EventBus` for in-process event dispatch and
  `ExecutionJournal` for durable audit logging to
  `~/.phynai/journal.db` (SQLite).
- **`tool_runtime.py`** — Composes registry, policy, middleware, event bus,
  and journal into the dispatch pipeline.

### L1 — Tools (`src/phynai/tools/`)

- **`decorator.py`** — `@tool` decorator that attaches `ToolMetadata` to a
  function. `discover_tools()` scans a module and returns all decorated
  callables.
- **`terminal.py`** — Shell command execution via `shlex.split()` +
  `create_subprocess_exec()`. No shell injection possible.
- **`file_tools.py`** — `read_file`, `write_file`, `search_files`, `patch`.
  All paths go through `Path.resolve()` with size caps.
  `search_files` uses `create_subprocess_exec()` with argv lists for
  rg/find/grep — no shell interpolation.
- **`web_tools.py`** — `web_search` (DuckDuckGo) and `web_extract` (fetch +
  extract). `web_extract` calls `_is_safe_url()` before every request,
  blocking RFC 1918 ranges, loopback, and 169.254.x.
- **`ms365.py`** — 9 Microsoft Graph API tools (mail, calendar, Teams,
  OneDrive, SharePoint). Client credentials OAuth2, in-process token cache.
- **`core.py`** — `register_core_tools()` wires all built-in tools into a
  `ToolRegistry`. Also loads skills via the skills loader.

### Skills (`src/phynai/skills/`)

Skills are user-defined tools stored in `~/.phynai/skills/<name>/` as
`skill.py` + `skill.json`. They are auto-discovered and registered at boot.

- **`registry.py`** — Manages the on-disk skill library.
- **`models.py`** — `SkillMeta` and `SkillUsageEvent` types.
- **`builder.py`** — LLM-powered skill generator (used by `phynai skills create`).
- **`loader.py`** — Auto-loads all installed skills at startup.

Skills execute as trusted Python. See security notes below.

### Contracts (`src/phynai/contracts/`)

Seven typed modules that form the shared vocabulary. No layer imports another
layer's internals — all cross-layer communication goes through these types.

| Module | Key types |
|---|---|
| `work.py` | `WorkItem`, `WorkResult`, `CostRecord` |
| `tools.py` | `ToolMetadata`, `ToolCall`, `ToolResult`, `Risk` |
| `events.py` | `EventType`, `Event`, `ToolEvent` |
| `middleware.py` | `Middleware`, `MiddlewareContext`, `MiddlewarePhase` |
| `policy.py` | `PolicyCheck`, `PolicyDecision` |
| `runtime.py` | `ToolRuntime` protocol |
| `agent.py` | `AgentCore`, `ClientManager`, `SessionStore` |
| `interfaces.py` | `Interface`, `CLIInterface`, `GatewayInterface` |

## Extension Model

### Middleware

Middleware wraps tool execution at L2. Each middleware is an async callable
registered with the `MiddlewareChain`:

```python
async def my_middleware(ctx: MiddlewareContext, next: NextFn) -> ToolResult:
    # PRE phase
    result = await next(ctx)
    # POST phase
    return result
```

Phases: `PRE`, `POST`, `ERROR`.

### Policies

Policies run before tool execution in `PolicyPipeline`. Each `PolicyCheck`
receives a `ToolCall` and returns `PolicyDecision` (ALLOW or DENY). A DENY
short-circuits the pipeline. Use policies for: file path restrictions, network
allowlists, budget caps, per-user tool restrictions.

### Skills

A skill is a Python file with a `@tool`-decorated function. The skills builder
generates the code via LLM. Once installed, the skill is automatically loaded
and available as a tool in every subsequent session.

## Security Architecture

| Control | Enforced in | Detail |
|---|---|---|
| No shell injection | L1 tools | `create_subprocess_exec()` + `shlex.split()` only |
| Path traversal blocked | L1 file tools | `Path.resolve()` + size caps |
| SSRF blocked | L1 `web_extract` | RFC 1918, loopback, 169.254.x blocklist |
| Gateway default-deny | L5 Slack | Hard fail if `SLACK_ALLOWED_USERS` unset |
| Per-user rate limiting | L5 gateway base | Sliding window: 5 req / 10 sec |
| Session file permissions | L3 session | `chmod 0o600` on write |
| Auth token permissions | `auth.py` | `chmod 0o600` on all credential files |
| Audit journal persistence | L2 journal | SQLite, survives restarts |
| User identity propagation | All layers | `user_id` on `WorkItem` |
| Structured logging | `cli_main.py` | JSON formatter, no raw exception leakage to users |

Skills execute as trusted Python in-process. There is no sandbox. Only install
skills from sources you trust.

## Directory Tree

```
src/phynai/
├── contracts/          # Shared vocabulary — crosses all layers
│   ├── work.py         #   WorkItem, WorkResult, CostRecord
│   ├── tools.py        #   ToolMetadata, ToolCall, ToolResult, Risk
│   ├── events.py       #   EventType, Event, ToolEvent
│   ├── middleware.py   #   Middleware, MiddlewareContext, MiddlewarePhase
│   ├── policy.py       #   PolicyCheck, PolicyDecision
│   ├── runtime.py      #   ToolRuntime protocol
│   ├── agent.py        #   AgentCore, ClientManager, SessionStore
│   └── interfaces.py   #   Interface, CLIInterface, GatewayInterface
├── runtime/            # L2 — tool execution engine
│   ├── registry.py     #   Scoped per-instance ToolRegistry
│   ├── policy.py       #   PolicyPipeline (DENY short-circuits)
│   ├── middleware.py   #   Async PRE/POST/ERROR chain
│   ├── events.py       #   EventBus + ExecutionJournal (SQLite)
│   └── tool_runtime.py #   Composes all into dispatch pipeline
├── agent/              # L3 — agent core
│   ├── loop.py         #   PhynaiAgent — the conversation driver
│   ├── client.py       #   httpx LLM client (Anthropic + OpenAI-compat)
│   ├── context.py      #   System prompt + token-based compression
│   ├── session.py      #   File-based JSON session persistence (0600)
│   └── cost.py         #   In-memory cost tracking
├── interfaces/         # L5 — thin protocol translators
│   ├── cli.py          #   Interactive REPL with spinner + color
│   └── gateway.py      #   PhynaiGateway base + SlackGateway (Socket Mode)
├── tools/              # L1 — tool implementations
│   ├── decorator.py    #   @tool decorator + auto-discovery
│   ├── terminal.py     #   Shell execution (shlex + exec, no shell injection)
│   ├── file_tools.py   #   read, write, search, patch (path-safe)
│   ├── web_tools.py    #   web_search, web_extract (SSRF-protected)
│   ├── ms365.py        #   9 Microsoft Graph API tools
│   └── core.py         #   register_core_tools() + skills loader
├── skills/             # Self-growing skill library
│   ├── registry.py     #   Manages ~/.phynai/skills/ on disk
│   ├── models.py       #   SkillMeta + SkillUsageEvent
│   ├── builder.py      #   LLM-powered skill generator
│   └── loader.py       #   Auto-loads skills at boot
├── prompts/
│   └── system.py       #   System prompt templates
├── auth.py             # Multi-provider OAuth + API-key auth
├── setup.py            # Interactive setup wizard
└── cli_main.py         # `phynai` CLI entry point
```

## Key Invariants

- `PhynaiAgent` (L3) never imports from `interfaces/` (L5).
- `contracts/` has zero internal dependencies outside stdlib and pydantic.
- Every async function in the core is cancellation-safe (respects `CancelledError`).
- `ExecutionJournal` is append-only. No event is mutated after emission.
- `ToolRegistry` is immutable after agent startup. Skills are loaded at boot,
  not at runtime.
- `user_id` from `WorkItem` is propagated to every `ToolCall` and journal entry.
- Gateway error messages sent to users are always generic. Full exceptions are
  logged internally only.
