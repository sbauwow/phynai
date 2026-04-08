# PhynAI Agent

An AI-powered sysadmin agent. One binary, any LLM, real tools, full audit trail.

PhynAI drops into your terminal and gets work done — runs commands, writes code, reads and edits files, searches the web — with every action policy-checked, journaled to SQLite, and costed to the cent.

> *phynai* (φῦναι) — Greek, "to grow; to come into being by nature"

The best agent runtimes in the wild — Claude Code, OpenClaw, Devin, the emerging wave of open agentic harnesses — share a pattern: a tight loop of reasoning, action, and observation that compounds on itself. Each cycle makes the next one sharper. The agent doesn't just execute instructions; it *grows into* the problem.

PhynAI brings that pattern to environments where growth needs guardrails. Ops teams can't afford a loop that learns by breaking things. Every tool call flows through a typed policy pipeline. Every action is journaled to SQLite. Every session is costed to the cent. The loop still compounds — but it compounds inside a compliance boundary.

The architecture is deliberately minimal. No orchestration layer, no dependency graph, no scheduler. Just an interface, an agent core, a tool runtime, and the contracts that bind them. When the loop is clean, you don't need scaffolding around it — you need the loop to be trustworthy enough to run unsupervised. That's what PhynAI is: a loop you can hand to your ops team and walk away.

```bash
./setup.sh && phynai setup && phynai chat
```

---

## Install

**Requirements:** Linux, macOS, or Windows (via WSL). Everything else is handled for you.

### Linux / macOS

```bash
git clone https://github.com/sbauwow/phynai-agent
cd phynai-agent
./setup.sh
```

The setup script installs Python 3.11 (via [uv](https://docs.astral.sh/uv/)), creates a virtualenv, installs core dependencies, and puts `phynai` on your PATH. Takes about 30 seconds.

### Windows (WSL)

PhynAI runs on Windows through the Windows Subsystem for Linux. WSL gives you a full Linux environment without a VM — `setup.sh` and all tools work as-is.

**1. Install WSL** (run in PowerShell as Administrator):

```powershell
wsl --install
```

This installs WSL 2 with Ubuntu by default. Restart when prompted.

**2. Launch WSL and install PhynAI:**

```bash
# Open the Ubuntu terminal from Start, then:
sudo apt update && sudo apt install -y git curl
git clone https://github.com/sbauwow/phynai-agent
cd phynai-agent
./setup.sh
```

**3. Run PhynAI:**

```bash
phynai setup
phynai chat
```

**Tips for WSL users:**

- Access Windows files from WSL at `/mnt/c/Users/<YourName>/`
- Access WSL files from Windows Explorer at `\\wsl$\Ubuntu\home\<user>\`
- For the best terminal experience, use [Windows Terminal](https://aka.ms/terminal)
- If you use VS Code, install the [WSL extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl) to develop directly inside WSL
- WSL 2 is required (WSL 1 may work but is untested). Check your version with `wsl -l -v`

### Configure your provider

```bash
phynai setup
```

The wizard walks you through choosing a provider, picking a model, and entering your API key. Supports **Anthropic**, **OpenAI**, **OpenRouter**, **GitHub Copilot**, and local models via Ollama/vLLM.

Or skip the wizard and set it directly:

```bash
echo 'PHYNAI_API_KEY=sk-ant-...' >> .env
echo 'PHYNAI_PROVIDER=anthropic' >> .env
echo 'PHYNAI_MODEL=claude-sonnet-4-6' >> .env
```

---

## Usage

### Interactive chat

```bash
phynai chat
```

A full REPL with tool dispatch, session persistence, token tracking, and cost reporting. Type `/help` inside the REPL for available commands, `/cost` to see your running total.

### One-shot execution

```bash
phynai run "find all TODO comments in src/ and summarize them"
```

Runs the prompt, prints the result, shows token usage and cost, then exits.

### CLI options

```bash
phynai -m claude-opus-4-6 chat          # override the model
phynai -r high run "prove this theorem"  # enable extended thinking (Anthropic)
phynai -m gpt-4o run "write tests"       # use OpenAI
phynai -v chat                           # debug logging
```

| Flag | Description |
|------|-------------|
| `-m, --model` | Override the configured model |
| `-r, --reasoning` | Extended thinking budget: `none`, `low`, `medium`, `high` |
| `-v, --verbose` | Enable debug logging |

---

## Tools

PhynAI ships with 7 core tools. The agent picks the right tool for each step automatically.

| Category | Tools |
|----------|-------|
| **Terminal** | `terminal` — run shell commands with full output capture |
| **Files** | `read_file`, `write_file`, `patch`, `search_files` |
| **Web** | `web_search`, `web_extract` |

Every tool call is policy-checked before execution and logged to an audit journal.

### Skills — your own tools

Generate custom tools from a description and they're available in every future session:

```bash
phynai skills create changelog_writer -d "Generate a changelog from recent git commits"
phynai skills list
```

Skills live in `~/.phynai/skills/`, persist across updates, and load automatically at startup.

---

## Optional integrations

The core install has zero optional dependencies. The integrations below can be added individually when needed.

### Slack bot

Connect PhynAI to Slack so your team can interact with the agent via DM or @-mention.

**Install the dependency:**

```bash
pip install 'phynai-agent[slack]'
# or: uv pip install slack-bolt
```

**Configure:**

```bash
phynai setup gateway    # interactive wizard
```

Or set the environment variables directly:

```bash
SLACK_BOT_TOKEN=xoxb-...        # OAuth & Permissions > Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...        # Basic Information > App-Level Tokens (connections:write)
SLACK_ALLOWED_USERS=U012AB,U098ZY  # mandatory — comma-separated Slack user IDs
```

**Run:**

```bash
phynai gateway slack
```

Runs via Socket Mode — no public URL or ingress required. The bot refuses to start with an empty allowlist.

### GitHub

Manage repos, issues, pull requests, CI status, and releases. No extra packages required.

**Configure:**

```bash
phynai setup github     # interactive wizard with auth test
```

Or set the environment variable directly:

```bash
GITHUB_TOKEN=ghp_...            # Personal access token (classic or fine-grained)
```

The 9 GitHub tools load automatically when the token is present:

| Tools |
|-------|
| Repo search, Issues (list/create/comment), PRs (list/get/create), Actions status, Releases list |

Token scopes needed (classic): `repo`, `read:org`. Fine-grained: Contents, Issues, Pull requests, Actions (read), Metadata (read).

### Jira

Search, create, update, and transition issues. Supports Jira Cloud (email + API token) and Data Center/Server (PAT). No extra packages required.

**Configure:**

```bash
phynai setup jira       # interactive wizard with auth test
```

Or set the environment variables directly:

```bash
# Jira Cloud
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=...              # id.atlassian.com > Security > API tokens

# OR Jira Data Center / Server
JIRA_URL=https://jira.internal.company.com
JIRA_PAT=...                    # Personal access token
```

The 7 Jira tools load automatically when credentials are present:

| Tools |
|-------|
| JQL search, Issue (get/create/update/transition), Comments (list/add) |

### Okta

Manage users, groups, app assignments, MFA status, and audit logs. No extra packages required.

**Configure:**

```bash
phynai setup okta       # interactive wizard with auth test
```

Or set the environment variables directly:

```bash
OKTA_ORG_URL=https://yourcompany.okta.com
OKTA_API_TOKEN=...              # Security > API > Tokens in Okta admin
```

The 12 Okta tools load automatically when credentials are present:

| Category | Tools |
|----------|-------|
| **Users** | Search, get details, lifecycle actions (activate/deactivate/suspend/unlock/reset password) |
| **Groups** | List, members, add/remove user |
| **Apps** | List applications, list app users |
| **MFA** | List enrolled factors for a user |
| **Audit** | Query system log events |

Token requires Super Admin or appropriate admin role.

### Google Workspace

Connect to Gmail, Google Calendar, and Google Drive. No extra packages required — uses httpx (already a core dependency).

**Configure:**

```bash
phynai setup google     # interactive wizard with auth test
```

Or set the environment variables directly:

```bash
GOOGLE_CLIENT_ID=...            # OAuth2 client ID (Desktop type)
GOOGLE_CLIENT_SECRET=...        # OAuth2 client secret
GOOGLE_REFRESH_TOKEN=...        # Offline refresh token
```

The 6 Google Workspace tools load automatically when credentials are present:

| Tools |
|-------|
| Gmail (send/read), Calendar (list/create), Drive (list/read) |

**Setup steps:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) > APIs & Services > Credentials
2. Enable the **Gmail API**, **Google Calendar API**, and **Google Drive API**
3. Create an OAuth consent screen, then an **OAuth 2.0 Client ID** (Desktop type)
4. Obtain a refresh token via the [OAuth Playground](https://developers.google.com/oauthplayground/) or a local OAuth flow

### Microsoft 365

Connect to your organization's Microsoft Graph API for email, calendar, Teams, OneDrive, and SharePoint. No extra packages required — uses httpx (already a core dependency).

**Configure:**

```bash
phynai setup ms365      # interactive wizard with auth test
```

Or set the environment variables directly:

```bash
MICROSOFT_TENANT_ID=...         # Azure AD > Directory (tenant) ID
MICROSOFT_CLIENT_ID=...         # App registration > Application (client) ID
MICROSOFT_CLIENT_SECRET=...     # App registration > Certificates & secrets
```

The 9 MS365 tools load automatically when credentials are present:

| Tools |
|-------|
| Mail (send/read), Calendar (list/create), Teams (send/list channels), OneDrive (list/read), SharePoint (search) |

Azure AD app permissions needed: `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All`, `ChannelMessage.Send`, `Sites.Read.All`.

---

## Security

PhynAI is built for environments where audit and control matter.

- **No shell injection** — all subprocess calls use `exec()` with argument lists, never shell strings
- **No path traversal** — every file path is canonicalized before access
- **SSRF protection** — web tools block private IPs, loopback, and cloud metadata endpoints
- **Default-deny gateways** — Slack requires an explicit user allowlist; empty = hard fail
- **Rate limiting** — per-user throttling on all gateway interfaces
- **Audit journal** — every tool call persisted to SQLite (`~/.phynai/journal.db`), survives restarts
- **Credential isolation** — `.env` files enforced to `0600`, `~/.phynai/` directory set to `0700`. The setup script and wizard both enforce these permissions automatically
- **Zero-load integrations** — unconfigured integrations are never imported, add no attack surface, and make no network calls. Only integrations with credentials present in the environment are loaded
- **Structured logging** — JSON log lines with trace IDs for correlation

### Credential management

All integration secrets (API tokens, client secrets, refresh tokens) are stored in `.env` with `0600` permissions. For stronger isolation:

- **Environment variables** — store secrets in your shell profile or a secrets manager instead of `.env`. Each integration reads directly from env vars at runtime
- **Keyring** — install `pip install 'phynai-agent[keyring]'` to use your OS keychain (macOS Keychain, GNOME Keyring, Windows Credential Locker) instead of plaintext files
- **Rotate regularly** — each integration's setup wizard (`phynai setup <name>`) re-verifies credentials on update, making rotation straightforward

---

## Cost tracking

Token usage and estimated cost are displayed after every interaction:

```
  1,847in/423out · $0.0348
```

Use `/cost` in the REPL for cumulative session totals. Pricing is computed from built-in per-model rate tables covering Anthropic, OpenAI, and OpenRouter models.

---

## Development

```bash
make dev              # install with dev dependencies
make test             # run the test suite
make lint             # ruff check
make format           # ruff format
```

### Adding a tool

```python
from phynai.tools.decorator import tool
from phynai.contracts.tools import Risk, ToolResult

@tool(
    name="my_tool",
    description="Does something useful",
    risk=Risk.LOW,
    parameters={
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "The input"},
        },
        "required": ["input"],
    },
)
async def my_tool(arguments: dict) -> ToolResult:
    result = arguments["input"].upper()
    return ToolResult(tool_name="my_tool", success=True, output=result)
```

Register in `src/phynai/tools/core.py` or ship it as a skill.

### Docker

```bash
make docker           # build image
make docker-run       # run with .env
```

---

## License

MIT
