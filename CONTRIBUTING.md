# Contributing to phynai-agent

## Quick Start

```bash
git clone https://github.com/sbauwow/phynai-agent.git
cd phynai-agent
make dev        # install all dependencies (requires uv)
make test       # run the test suite
```

You need [uv](https://docs.astral.sh/uv/) installed. If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Project Structure

```
phynai-agent/
├── src/phynai/
│   ├── contracts/       # Shared types crossing all layers
│   ├── runtime/         # L2: tool execution engine
│   ├── agent/           # L3: agent core (loop, client, context, session)
│   ├── interfaces/      # L5: CLI REPL and Slack gateway
│   ├── tools/           # L1: built-in tool implementations
│   ├── skills/          # Self-growing skill library
│   ├── prompts/         # System prompt templates
│   ├── auth.py          # Multi-provider OAuth + API-key auth
│   ├── setup.py         # Interactive setup wizard
│   └── cli_main.py      # `phynai` CLI entry point
├── tests/               # All tests live here
├── Makefile             # Developer commands
├── pyproject.toml       # Project config, deps, tool settings
├── ARCHITECTURE.md      # Detailed design documentation
└── SECURITY_CONCERNS.md # Security audit and remediation tracking
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full layer-by-layer breakdown
and directory tree.

## Code Style

- **Formatter / Linter**: [ruff](https://docs.astral.sh/ruff/) — configured in `pyproject.toml`
- **Type hints**: Required on all public functions and methods
- **Docstrings**: Google-style docstrings on all public APIs
- Run `make lint` to check, `make format` to auto-fix

## Testing

- Framework: **pytest** with **pytest-asyncio** (async mode = auto)
- All async tests run automatically — no `@pytest.mark.asyncio` decorator needed
- Test files mirror the source layout under `tests/`
- Run `make test` (quiet) or `make test-verbose` (with full output)

Current stats: 3,627 lines of tests across the test suite.

## Adding a Tool

Tools are plain async functions decorated with `@tool`. They are
auto-discovered by `discover_tools()` and registered in `ToolRegistry`.

```python
from phynai.tools.decorator import tool
from phynai.contracts.tools import Risk

@tool(
    name="my_tool",
    description="One-sentence description shown to the LLM.",
    risk=Risk.LOW,
    mutates=False,
    requires_confirmation=False,
    parameters={
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "What to process"}
        },
        "required": ["input"],
    },
)
async def my_tool(input: str) -> str:
    """Full docstring here."""
    return result
```

Security requirements:
- Never pass user input to `shell=True` or `create_subprocess_shell()`. Use
  `create_subprocess_exec()` with an argv list.
- Resolve and validate file paths with `Path(p).resolve()` before use.
- Validate URLs with `_is_safe_url()` before any network fetch.

Place the tool in `src/phynai/tools/` and add it to `register_core_tools()`
in `tools/core.py`.

## Adding a Skill

Skills are user-defined tools stored on disk in `~/.phynai/skills/<name>/`.
The easiest way to add one is:

```bash
phynai skills create my_skill -d "What the skill does"
```

This generates `skill.py` and `skill.json` via the LLM builder. Skills are
auto-loaded at boot. To write one manually, create a `skill.py` with a
`@tool`-decorated async function and a `skill.json` with the `SkillMeta`
fields.

Skills execute as trusted Python in-process with no sandbox. Only install
skills you trust.

## Makefile Targets

| Target | Description |
|---|---|
| `make dev` | Install all dependencies with uv |
| `make test` | Run test suite (quiet) |
| `make test-verbose` | Run test suite with full output |
| `make lint` | Check code style with ruff |
| `make format` | Auto-fix formatting with ruff |
| `make help` | List all targets |

## Pull Request Process

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```
2. Write code and tests. All new public functions need type hints and docstrings.
3. Ensure everything passes:
   ```bash
   make lint
   make test
   ```
4. Open a PR against `main` on [GitHub](https://github.com/sbauwow/phynai-agent)
   with a clear description of what changed and why.
5. Address review feedback.

For security-sensitive changes (new tools, gateway modifications, auth changes),
call out the security implications explicitly in the PR description.
