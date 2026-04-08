"""Interactive setup wizard for PhynAI Agent.

Modular wizard with independently-runnable sections:
  1. Provider & Model — choose your AI provider, model, and API key
  2. Gateway — configure Telegram, Discord
  3. Tools — check available tools, optional deps
  4. Agent Settings — iterations, context window

Config is stored in ~/.phynai/.env and the project .env.
Run with: phynai setup [section]
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── ANSI helpers ──────────────────────────────────────────────────────────

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"

def _header(title: str) -> None:
    print(f"\n{_CYAN}{_BOLD}◆ {title}{_RESET}")

def _ok(text: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {text}")

def _warn(text: str) -> None:
    print(f"  {_YELLOW}⚠{_RESET} {text}")

def _err(text: str) -> None:
    print(f"  {_RED}✗{_RESET} {text}")

def _info(text: str) -> None:
    print(f"  {_DIM}{text}{_RESET}")

def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {_YELLOW}{question}{suffix}: {_RESET}").strip()
        return value or default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

def _prompt_secret(question: str, default: str = "") -> str:
    suffix = " [***]" if default else ""
    try:
        value = getpass.getpass(f"  {_YELLOW}{question}{suffix}: {_RESET}").strip()
        return value or default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

def _prompt_choice(question: str, choices: list[str], default: int = 0) -> int:
    """Arrow-key menu using curses, fallback to numbered list."""
    idx = _curses_menu(question, choices, default)
    if idx >= 0:
        return idx

    # Fallback: numbered list
    print(f"  {_YELLOW}{question}{_RESET}")
    for i, c in enumerate(choices):
        marker = f"{_GREEN}●{_RESET}" if i == default else "○"
        print(f"    {marker} {i + 1}. {c}")
    _info(f"Enter for default ({default + 1})")
    while True:
        try:
            v = input(f"  {_DIM}Select [1-{len(choices)}] ({default + 1}): {_RESET}").strip()
            if not v:
                return default
            n = int(v) - 1
            if 0 <= n < len(choices):
                return n
        except (ValueError, KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)


def _curses_menu(question: str, choices: list[str], default: int = 0) -> int:
    """Single-select menu with arrow keys via curses."""
    try:
        import curses
        result = [default]

        def _run(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = default
            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()
                try:
                    stdscr.addnstr(0, 0, question, max_x - 1,
                                   curses.A_BOLD | (curses.color_pair(2) if curses.has_colors() else 0))
                except curses.error:
                    pass
                for i, choice in enumerate(choices):
                    y = i + 2
                    if y >= max_y - 1:
                        break
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow}  {choice}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass
                stdscr.refresh()
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(choices)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(choices)
                elif key in (curses.KEY_ENTER, 10, 13):
                    result[0] = cursor
                    return
                elif key in (27, ord("q")):
                    return

        curses.wrapper(_run)
        return result[0]
    except Exception:
        return -1


# ── .env file helpers ─────────────────────────────────────────────────────

def _env_path() -> Path:
    """Find the .env file — project root or ~/.phynai/.env."""
    project = Path(__file__).resolve().parent.parent.parent / ".env"
    if project.is_file():
        return project
    home = Path.home() / ".phynai" / ".env"
    home.parent.mkdir(parents=True, exist_ok=True)
    return home if home.is_file() else project

def _load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip("\"'")
    return env

def _save_env(path: Path, env: dict[str, str]) -> None:
    """Write env dict back to .env file, preserving comments."""
    lines: list[str] = []
    existing_keys: set[str] = set()

    if path.is_file():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                existing_keys.add(key)
                if key in env:
                    lines.append(f"{key}={env[key]}")
                else:
                    lines.append(line)
            else:
                lines.append(line)

    # Append new keys not in original file
    for k, v in env.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}")

    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


# ── Section: Provider & Model ─────────────────────────────────────────────

PROVIDERS = [
    ("anthropic", "Anthropic (Claude)", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    ("anthropic-oauth", "Anthropic (OAuth — Claude Pro/Max)", "", ""),
    ("openai", "OpenAI", "https://api.openai.com", "OPENAI_API_KEY"),
    ("openai-codex", "OpenAI Codex (OAuth)", "", ""),
    ("openrouter", "OpenRouter (many models)", "https://openrouter.ai/api", "OPENROUTER_API_KEY"),
    ("nous", "Nous Portal (OAuth)", "", ""),
    ("copilot", "GitHub Copilot (OAuth)", "", ""),
    ("zai", "Z.AI / GLM", "https://api.z.ai/api/paas/v4", "GLM_API_KEY"),
    ("kimi-coding", "Kimi / Moonshot", "https://api.moonshot.ai/v1", "KIMI_API_KEY"),
    ("minimax", "MiniMax", "https://api.minimax.io/anthropic", "MINIMAX_API_KEY"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ("huggingface", "Hugging Face", "https://router.huggingface.co/v1", "HF_TOKEN"),
    ("local", "Local (Ollama / vLLM)", "http://localhost:11434", ""),
    ("custom", "Custom OpenAI-compatible endpoint", "", "PHYNAI_API_KEY"),
]

DEFAULT_MODELS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini"],
    "openrouter": ["anthropic/claude-opus-4-6", "anthropic/claude-sonnet-4-6", "openai/gpt-4o", "google/gemini-2.5-flash"],
    "anthropic": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "anthropic-oauth": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "nous": ["claude-opus-4-6", "claude-sonnet-4-6"],
    "zai": ["glm-5", "glm-4.7"],
    "kimi-coding": ["kimi-k2.5"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "local": ["llama3", "mistral", "codellama", "phi3"],
}


def setup_provider(env: dict[str, str]) -> dict[str, str]:
    """Configure AI provider, model, and API key."""
    _header("Provider & Model")

    current_provider = env.get("PHYNAI_PROVIDER", "anthropic")

    # Provider selection
    provider_names = [p[1] for p in PROVIDERS]
    current_idx = next((i for i, p in enumerate(PROVIDERS) if p[0] == current_provider), 0)
    idx = _prompt_choice("Select AI provider:", provider_names, current_idx)

    provider_id, provider_name, base_url, key_var = PROVIDERS[idx]

    # Handle OAuth providers
    oauth_providers = {"anthropic-oauth", "openai-codex", "nous", "copilot"}
    if provider_id in oauth_providers:
        _info(f"Starting {provider_name} OAuth login...")
        try:
            from phynai.auth import login_provider
            # Map to actual provider for config
            actual_provider = "anthropic" if provider_id == "anthropic-oauth" else provider_id
            login_provider(actual_provider if provider_id != "anthropic-oauth" else "anthropic")
            env["PHYNAI_PROVIDER"] = actual_provider
        except Exception as exc:
            _err(f"OAuth login failed: {exc}")
            return env
    else:
        env["PHYNAI_PROVIDER"] = provider_id

    # Base URL
    if provider_id == "custom":
        base_url = _prompt("Base URL (OpenAI-compatible /v1 endpoint)", env.get("PHYNAI_BASE_URL", "http://localhost:8080/v1"))
        env["PHYNAI_BASE_URL"] = base_url
    elif base_url:
        env["PHYNAI_BASE_URL"] = base_url

    # Model selection
    effective_id = "anthropic" if provider_id == "anthropic-oauth" else provider_id
    models = DEFAULT_MODELS.get(effective_id, [])
    current_model = env.get("PHYNAI_MODEL", "")
    if models:
        model_choices = models + ["Custom model"]
        if current_model:
            model_choices.append(f"Keep current ({current_model})")

        current_model_idx = len(model_choices) - 1 if current_model else 0
        model_idx = _prompt_choice("Select default model:", model_choices, current_model_idx)

        if model_idx < len(models):
            env["PHYNAI_MODEL"] = models[model_idx]
        elif model_idx == len(models):
            custom = _prompt("Enter model name")
            if custom:
                env["PHYNAI_MODEL"] = custom
        # else: keep current
    else:
        model = _prompt("Model name", current_model or "claude-opus-4-6")
        env["PHYNAI_MODEL"] = model

    # API key (for non-OAuth providers)
    if key_var and provider_id not in oauth_providers:
        env_key_already = os.environ.get(key_var, "")
        current_key = env.get(key_var, "") or env_key_already

        key_source_choices = [
            "Enter API key now",
            f"Read from environment / .bashrc  (${key_var})",
        ]
        if current_key:
            key_source_choices.append("Keep current key")

        key_src_idx = _prompt_choice("How should PhynAI get the API key?", key_source_choices, 0)

        if key_src_idx == 0:
            # Enter key manually
            masked = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) > 12 else ""
            if masked:
                _info(f"Current key: {masked}")
            new_key = _prompt_secret("API key (Enter to keep current)" if current_key else "API key")
            if new_key:
                env[key_var] = new_key
            env.pop("PHYNAI_API_KEY_SOURCE", None)

        elif key_src_idx == 1:
            # Use environment / .bashrc
            if env_key_already:
                _ok(f"${key_var} detected in current environment ({env_key_already[:8]}...)")
            else:
                _warn(f"${key_var} is not set in the current environment.")
                _info(f"Add this to your ~/.bashrc:")
                _info(f"    export {key_var}=sk-ant-...")
                _info("Then run: source ~/.bashrc")
            # Remove any stored key and mark source as env
            env.pop(key_var, None)
            env["PHYNAI_API_KEY_SOURCE"] = "env"
            _ok(f"PhynAI will read {key_var} from the environment at runtime")

        # else: keep current — do nothing

    _ok(f"Provider: {provider_name}")
    _ok(f"Model: {env.get('PHYNAI_MODEL', '?')}")
    if provider_id in oauth_providers:
        _ok("Auth: OAuth configured")
    elif env.get("PHYNAI_API_KEY_SOURCE") == "env":
        _ok(f"API key: from environment (${key_var})")
    elif env.get(key_var or "PHYNAI_API_KEY"):
        _ok("API key: set")
    return env


# ── Section: Gateway ──────────────────────────────────────────────────────

def setup_gateway(env: dict[str, str]) -> dict[str, str]:
    """Configure messaging platform tokens."""
    _header("Messaging Gateway")

    platforms = ["Slack", "Discord", "Skip"]
    idx = _prompt_choice("Configure a messaging platform:", platforms, len(platforms) - 1)

    if idx == 0:  # Slack
        _info("Create a Slack app at api.slack.com/apps")
        _info("Enable Socket Mode — no public URL required")
        _info("Bot scopes needed: chat:write, im:history, app_mentions:read, channels:history")
        _info("Bot token (xoxb-): OAuth & Permissions > Bot User OAuth Token")
        _info("App token (xapp-): Basic Information > App-Level Tokens (connections:write scope)")
        current_bot = env.get("SLACK_BOT_TOKEN", "")
        bot_token = _prompt_secret("Slack bot token (xoxb-)" + (" (Enter to keep)" if current_bot else ""))
        if bot_token:
            env["SLACK_BOT_TOKEN"] = bot_token
        current_app = env.get("SLACK_APP_TOKEN", "")
        app_token = _prompt_secret("Slack app token (xapp-)" + (" (Enter to keep)" if current_app else ""))
        if app_token:
            env["SLACK_APP_TOKEN"] = app_token
        allowed = _prompt("Allowed Slack user IDs (comma-separated, blank = all)", env.get("SLACK_ALLOWED_USERS", ""))
        if allowed:
            env["SLACK_ALLOWED_USERS"] = allowed
        if env.get("SLACK_BOT_TOKEN") and env.get("SLACK_APP_TOKEN"):
            _ok("Slack tokens saved — run: phynai gateway slack")
        try:
            import slack_bolt  # noqa: F401
            _ok("slack-bolt is installed")
        except ImportError:
            _warn("slack-bolt not installed — run: pip install 'phynai-agent[slack]'")

    elif idx == 1:  # Discord
        current = env.get("DISCORD_BOT_TOKEN", "")
        token = _prompt_secret("Discord bot token" + (" (Enter to keep)" if current else ""))
        if token:
            env["DISCORD_BOT_TOKEN"] = token
            _ok("Discord token saved")
        elif current:
            _ok("Discord token: already set")

    else:
        _info("Skipped gateway setup")

    return env


# ── Section: Microsoft 365 ───────────────────────────────────────────────────

def setup_ms365(env: dict[str, str]) -> dict[str, str]:
    """Configure Microsoft 365 / Graph API credentials."""
    _header("Microsoft 365 Integration")
    _info("Requires an Azure AD app registration with application permissions:")
    _info("  Mail.ReadWrite · Mail.Send · Calendars.ReadWrite")
    _info("  Files.ReadWrite.All · ChannelMessage.Send · Sites.Read.All")
    _info("Create at: portal.azure.com > Azure Active Directory > App registrations")

    current_tenant = env.get("MICROSOFT_TENANT_ID", "")
    if _prompt_choice(
        "Configure Microsoft 365?",
        ["Yes — enter credentials", "Skip"],
        1 if not current_tenant else 0,
    ) == 1:
        _info("Skipped Microsoft 365 setup")
        return env

    tenant = _prompt("Tenant ID (Directory ID)", current_tenant)
    if tenant:
        env["MICROSOFT_TENANT_ID"] = tenant

    client_id = _prompt("Client (Application) ID", env.get("MICROSOFT_CLIENT_ID", ""))
    if client_id:
        env["MICROSOFT_CLIENT_ID"] = client_id

    secret = _prompt_secret("Client secret" + (" (Enter to keep)" if env.get("MICROSOFT_CLIENT_SECRET") else ""))
    if secret:
        env["MICROSOFT_CLIENT_SECRET"] = secret

    if env.get("MICROSOFT_TENANT_ID") and env.get("MICROSOFT_CLIENT_ID") and env.get("MICROSOFT_CLIENT_SECRET"):
        _info("Testing Graph API auth...")
        try:
            import httpx
            resp = httpx.post(
                f"https://login.microsoftonline.com/{env['MICROSOFT_TENANT_ID']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": env["MICROSOFT_CLIENT_ID"],
                    "client_secret": env["MICROSOFT_CLIENT_SECRET"],
                    "scope": "https://graph.microsoft.com/.default",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                _ok("Microsoft Graph API auth successful")
                _ok("MS365 tools will load automatically on next start")
            else:
                _warn(f"Auth failed: {resp.json().get('error_description', resp.text[:80])}")
        except Exception as exc:
            _warn(f"Could not verify credentials: {exc}")

    return env


# ── Section: Google Workspace ────────────────────────────────────────────────

def setup_google(env: dict[str, str]) -> dict[str, str]:
    """Configure Google Workspace / Gmail / Calendar / Drive credentials."""
    _header("Google Workspace Integration")
    _info("Requires a Google Cloud OAuth2 client (Desktop type).")
    _info("  1. Create at: console.cloud.google.com > APIs & Services > Credentials")
    _info("  2. Enable: Gmail API, Google Calendar API, Google Drive API")
    _info("  3. Create OAuth consent screen, then OAuth 2.0 Client ID (Desktop)")
    _info("  4. Use the OAuth Playground or a local flow to obtain a refresh token")

    current_id = env.get("GOOGLE_CLIENT_ID", "")
    if _prompt_choice(
        "Configure Google Workspace?",
        ["Yes — enter credentials", "Skip"],
        1 if not current_id else 0,
    ) == 1:
        _info("Skipped Google Workspace setup")
        return env

    client_id = _prompt("OAuth2 Client ID", current_id)
    if client_id:
        env["GOOGLE_CLIENT_ID"] = client_id

    client_secret = _prompt_secret(
        "OAuth2 Client secret" + (" (Enter to keep)" if env.get("GOOGLE_CLIENT_SECRET") else "")
    )
    if client_secret:
        env["GOOGLE_CLIENT_SECRET"] = client_secret

    refresh_token = _prompt_secret(
        "Refresh token" + (" (Enter to keep)" if env.get("GOOGLE_REFRESH_TOKEN") else "")
    )
    if refresh_token:
        env["GOOGLE_REFRESH_TOKEN"] = refresh_token

    if env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET") and env.get("GOOGLE_REFRESH_TOKEN"):
        _info("Testing Google OAuth2 token refresh...")
        try:
            import httpx
            resp = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": env["GOOGLE_CLIENT_ID"],
                    "client_secret": env["GOOGLE_CLIENT_SECRET"],
                    "refresh_token": env["GOOGLE_REFRESH_TOKEN"],
                },
                timeout=15,
            )
            if resp.status_code == 200:
                _ok("Google OAuth2 auth successful")
                _ok("Google Workspace tools will load automatically on next start")
            else:
                _warn(f"Auth failed: {resp.json().get('error_description', resp.text[:80])}")
        except Exception as exc:
            _warn(f"Could not verify credentials: {exc}")

    return env


# ── Section: GitHub ──────────────────────────────────────────────────────────

def setup_github(env: dict[str, str]) -> dict[str, str]:
    """Configure GitHub API credentials."""
    _header("GitHub Integration")
    _info("Requires a personal access token (classic or fine-grained).")
    _info("  Classic: github.com > Settings > Developer settings > Personal access tokens")
    _info("  Scopes needed: repo, read:org")
    _info("  Fine-grained: grant repo access with Contents, Issues, Pull requests, Actions read")

    current = env.get("GITHUB_TOKEN", "")
    if _prompt_choice(
        "Configure GitHub?",
        ["Yes — enter token", "Skip"],
        1 if not current else 0,
    ) == 1:
        _info("Skipped GitHub setup")
        return env

    token = _prompt_secret("GitHub personal access token" + (" (Enter to keep)" if current else ""))
    if token:
        env["GITHUB_TOKEN"] = token

    if env.get("GITHUB_TOKEN"):
        _info("Testing GitHub API access...")
        try:
            import httpx
            resp = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {env['GITHUB_TOKEN']}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                user = resp.json()
                _ok(f"Authenticated as {user.get('login', '?')}")
                _ok("GitHub tools will load automatically on next start")
            else:
                _warn(f"Auth failed: {resp.status_code} {resp.text[:80]}")
        except Exception as exc:
            _warn(f"Could not verify token: {exc}")

    return env


# ── Section: Jira ───────────────────────────────────────────────────────────

def setup_jira(env: dict[str, str]) -> dict[str, str]:
    """Configure Jira API credentials."""
    _header("Jira Integration")
    _info("Supports Jira Cloud (email + API token) and Data Center (PAT).")
    _info("  Cloud: id.atlassian.com/manage-profile/security/api-tokens")
    _info("  Data Center: Jira > Profile > Personal Access Tokens")

    current_url = env.get("JIRA_URL", "")
    if _prompt_choice(
        "Configure Jira?",
        ["Yes — enter credentials", "Skip"],
        1 if not current_url else 0,
    ) == 1:
        _info("Skipped Jira setup")
        return env

    url = _prompt("Jira instance URL (e.g. https://yourcompany.atlassian.net)", current_url)
    if url:
        env["JIRA_URL"] = url.rstrip("/")

    auth_method = _prompt_choice("Authentication method:", [
        "Email + API token (Jira Cloud)",
        "Personal access token (Data Center / Server)",
    ], 0)

    if auth_method == 0:
        email = _prompt("Jira account email", env.get("JIRA_EMAIL", ""))
        if email:
            env["JIRA_EMAIL"] = email
        token = _prompt_secret("API token" + (" (Enter to keep)" if env.get("JIRA_API_TOKEN") else ""))
        if token:
            env["JIRA_API_TOKEN"] = token
        env.pop("JIRA_PAT", None)
    else:
        pat = _prompt_secret("Personal access token" + (" (Enter to keep)" if env.get("JIRA_PAT") else ""))
        if pat:
            env["JIRA_PAT"] = pat
        env.pop("JIRA_EMAIL", None)
        env.pop("JIRA_API_TOKEN", None)

    # Verify
    jira_url = env.get("JIRA_URL", "")
    if jira_url:
        _info("Testing Jira API access...")
        try:
            import httpx
            import base64
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if env.get("JIRA_PAT"):
                headers["Authorization"] = f"Bearer {env['JIRA_PAT']}"
            elif env.get("JIRA_EMAIL") and env.get("JIRA_API_TOKEN"):
                basic = base64.b64encode(
                    f"{env['JIRA_EMAIL']}:{env['JIRA_API_TOKEN']}".encode()
                ).decode()
                headers["Authorization"] = f"Basic {basic}"
            resp = httpx.get(f"{jira_url}/rest/api/3/myself", headers=headers, timeout=15)
            if resp.status_code == 200:
                user = resp.json()
                _ok(f"Authenticated as {user.get('displayName', '?')} ({user.get('emailAddress', '')})")
                _ok("Jira tools will load automatically on next start")
            else:
                _warn(f"Auth failed: {resp.status_code} {resp.text[:80]}")
        except Exception as exc:
            _warn(f"Could not verify credentials: {exc}")

    return env


# ── Section: Okta ──────────────���─────────────────────────────────────────

def setup_okta(env: dict[str, str]) -> dict[str, str]:
    """Configure Okta API credentials."""
    _header("Okta Integration")
    _info("Requires an Okta API token with admin privileges.")
    _info("  Create at: Okta Admin > Security > API > Tokens")
    _info("  Needs Super Admin or appropriate admin role for user/group/app management")

    current_url = env.get("OKTA_ORG_URL", "")
    if _prompt_choice(
        "Configure Okta?",
        ["Yes — enter credentials", "Skip"],
        1 if not current_url else 0,
    ) == 1:
        _info("Skipped Okta setup")
        return env

    url = _prompt("Okta org URL (e.g. https://yourcompany.okta.com)", current_url)
    if url:
        env["OKTA_ORG_URL"] = url.rstrip("/")

    token = _prompt_secret("API token" + (" (Enter to keep)" if env.get("OKTA_API_TOKEN") else ""))
    if token:
        env["OKTA_API_TOKEN"] = token

    if env.get("OKTA_ORG_URL") and env.get("OKTA_API_TOKEN"):
        _info("Testing Okta API access...")
        try:
            import httpx
            resp = httpx.get(
                f"{env['OKTA_ORG_URL']}/api/v1/users/me",
                headers={
                    "Authorization": f"SSWS {env['OKTA_API_TOKEN']}",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                user = resp.json()
                profile = user.get("profile", {})
                _ok(f"Authenticated as {profile.get('firstName', '')} {profile.get('lastName', '')} ({profile.get('email', '')})")
                _ok("Okta tools will load automatically on next start")
            else:
                _warn(f"Auth failed: {resp.status_code} {resp.text[:80]}")
        except Exception as exc:
            _warn(f"Could not verify token: {exc}")

    return env


# ── Section: Tools ──────────────────────────────────────���─────────────────

def setup_tools(env: dict[str, str]) -> dict[str, str]:
    """Check available tools and optional dependencies."""
    _header("Tool Check")

    # ripgrep
    if shutil.which("rg"):
        _ok("ripgrep found (fast file search)")
    else:
        _warn("ripgrep not found — search_files will use grep fallback")
        _info("Install: sudo apt install ripgrep")

    # adb
    if shutil.which("adb"):
        _ok("ADB found (55 Android tools available)")
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
            devices = [l for l in r.stdout.strip().splitlines()[1:] if l.strip()]
            if devices:
                _ok(f"{len(devices)} Android device(s) connected")
            else:
                _warn("No devices connected — Android tools will be unavailable at runtime")
        except Exception:
            pass
    else:
        _info("ADB not found — Android tools disabled")
        _info("Install: sudo apt install android-tools-adb")

    # httpx (should always be there)
    try:
        import httpx
        _ok(f"httpx {httpx.__version__} (HTTP client)")
    except ImportError:
        _err("httpx not found — run: pip install httpx")

    return env


# ── Section: Agent Settings ───────────────────────────────────────────────

def setup_agent(env: dict[str, str]) -> dict[str, str]:
    """Configure agent behavior."""
    _header("Agent Settings")

    # Max iterations
    current = env.get("PHYNAI_MAX_ITERATIONS", "50")
    val = _prompt("Max tool iterations per task", current)
    env["PHYNAI_MAX_ITERATIONS"] = val

    # Log level
    levels = ["warning", "info", "debug"]
    current_level = env.get("PHYNAI_LOG_LEVEL", "warning")
    current_idx = levels.index(current_level) if current_level in levels else 0
    idx = _prompt_choice("Log level:", levels, current_idx)
    env["PHYNAI_LOG_LEVEL"] = levels[idx]

    _ok(f"Max iterations: {env['PHYNAI_MAX_ITERATIONS']}")
    _ok(f"Log level: {env['PHYNAI_LOG_LEVEL']}")
    return env


# ── Main Wizard ───────────────────────────────────────────────────────────

SECTIONS = [
    ("provider", "Provider & Model", setup_provider),
    ("gateway", "Messaging Gateway", setup_gateway),
    ("github", "GitHub", setup_github),
    ("jira", "Jira", setup_jira),
    ("ms365", "Microsoft 365", setup_ms365),
    ("google", "Google Workspace", setup_google),
    ("okta", "Okta", setup_okta),
    ("tools", "Tools", setup_tools),
    ("agent", "Agent Settings", setup_agent),
]


def run_setup_wizard(section: str | None = None) -> None:
    """Run the interactive setup wizard.

    Args:
        section: Run a specific section only (provider, gateway, tools, agent).
                 None runs all sections.
    """
    print()
    print(f"{_CYAN}{_BOLD}⚡ PhynAI Setup Wizard{_RESET}")

    env_file = _env_path()
    env = _load_env(env_file)

    if section:
        # Run single section
        for key, name, fn in SECTIONS:
            if key == section:
                env = fn(env)
                break
        else:
            _err(f"Unknown section: {section}")
            _info(f"Available: {', '.join(s[0] for s in SECTIONS)}")
            return
    else:
        # Run all sections
        for key, name, fn in SECTIONS:
            env = fn(env)

    # Save
    _save_env(env_file, env)
    _ok(f"Config saved to {env_file}")

    print()
    print(f"{_GREEN}{_BOLD}✓ Setup complete!{_RESET}")
    print()
    print("  Start chatting:")
    print(f"    {_BOLD}phynai chat{_RESET}")
    print()
