"""
Multi-provider authentication system for PhynAI Agent.

Ported from prowler-agent (Hermes) auth system. Supports OAuth device code
flows (Nous Portal, OpenAI Codex), OAuth PKCE (Anthropic/Claude), GitHub
Copilot device code flow, and traditional API key providers.

Auth state is persisted in ~/.phynai/auth.json with cross-process file locking.
When the ``keyring`` package is installed, sensitive tokens (access_token,
refresh_token, api_key, etc.) are stored in the OS keyring (Linux Secret
Service / macOS Keychain / Windows Credential Manager) instead of the JSON
file. Install with: ``pip install phynai-agent[keyring]``

Architecture:
- ProviderConfig registry defines known OAuth/API-key providers
- Auth store (auth.json) holds per-provider credential state (secrets in keyring when available)
- resolve_provider() picks the active provider via priority chain
- resolve_*_runtime_credentials() handles token refresh and key minting
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shlex
import shutil
import stat
import subprocess
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

try:
    import fcntl
except Exception:
    fcntl = None
try:
    import msvcrt
except Exception:
    msvcrt = None
try:
    import keyring as _keyring_mod
except Exception:
    _keyring_mod = None

# ---------------------------------------------------------------------------
# Keyring helpers — store/retrieve sensitive tokens via OS keyring
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "phynai-agent"
# Provider state fields that contain secrets and should go into the keyring
_SECRET_FIELDS = frozenset({
    "access_token", "refresh_token", "api_key", "token",
    "client_secret", "id_token",
})


def _keyring_available() -> bool:
    """Return True if the keyring backend is usable (not a no-op chainer)."""
    if _keyring_mod is None:
        return False
    try:
        backend = _keyring_mod.get_keyring()
        name = type(backend).__name__
        # Reject backends that silently fail (chainer with no real backend)
        if "fail" in name.lower() or "null" in name.lower():
            return False
        return True
    except Exception:
        return False


def _keyring_set(provider_id: str, field: str, value: str) -> bool:
    """Store a secret in the OS keyring. Returns True on success."""
    try:
        _keyring_mod.set_password(_KEYRING_SERVICE, f"{provider_id}/{field}", value)
        return True
    except Exception:
        logger.debug("keyring set failed for %s/%s", provider_id, field)
        return False


def _keyring_get(provider_id: str, field: str) -> Optional[str]:
    """Retrieve a secret from the OS keyring."""
    try:
        return _keyring_mod.get_password(_KEYRING_SERVICE, f"{provider_id}/{field}")
    except Exception:
        return None


def _keyring_delete(provider_id: str, field: str) -> None:
    """Remove a secret from the OS keyring (best-effort)."""
    try:
        _keyring_mod.delete_password(_KEYRING_SERVICE, f"{provider_id}/{field}")
    except Exception:
        pass


def _strip_secrets_to_keyring(provider_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Move secret fields from a provider state dict into the keyring.

    Returns a copy of the state with secrets replaced by a sentinel so
    the JSON file only holds non-sensitive metadata.
    """
    if not _keyring_available():
        return state
    cleaned = dict(state)
    for key in _SECRET_FIELDS:
        val = cleaned.get(key)
        if isinstance(val, str) and val:
            if _keyring_set(provider_id, key, val):
                cleaned[key] = "__keyring__"
    return cleaned


def _restore_secrets_from_keyring(provider_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Replace keyring sentinel values with actual secrets from the OS keyring."""
    if not _keyring_available():
        return state
    restored = dict(state)
    for key in _SECRET_FIELDS:
        if restored.get(key) == "__keyring__":
            val = _keyring_get(provider_id, key)
            if val is not None:
                restored[key] = val
            else:
                # Keyring entry missing — clear the sentinel so callers
                # know the secret is unavailable
                restored[key] = ""
    return restored


def _clear_keyring_secrets(provider_id: str) -> None:
    """Remove all keyring entries for a provider."""
    if not _keyring_available():
        return
    for field in _SECRET_FIELDS:
        _keyring_delete(provider_id, field)

# =============================================================================
# Constants
# =============================================================================

AUTH_STORE_VERSION = 1
AUTH_LOCK_TIMEOUT_SECONDS = 15.0

# Nous Portal defaults
DEFAULT_NOUS_PORTAL_URL = "https://portal.nousresearch.com"
DEFAULT_NOUS_INFERENCE_URL = "https://inference-api.nousresearch.com/v1"
DEFAULT_NOUS_CLIENT_ID = "hermes-cli"
DEFAULT_NOUS_SCOPE = "inference:mint_agent_key"
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 30 * 60  # 30 minutes
ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120       # refresh 2 min before expiry
DEVICE_AUTH_POLL_INTERVAL_CAP_SECONDS = 1     # poll at most every 1s

# OpenAI Codex
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120

# GitHub Copilot
DEFAULT_GITHUB_MODELS_BASE_URL = "https://api.githubcopilot.com"
COPILOT_OAUTH_CLIENT_ID = "Ov23li8tweQw6odWQebz"

# Anthropic OAuth PKCE
ANTHROPIC_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
ANTHROPIC_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
ANTHROPIC_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"

# OpenRouter
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_phynai_home() -> Path:
    """Return the PhynAI config home directory (~/.phynai/)."""
    home = Path(os.environ.get("PHYNAI_HOME", Path.home() / ".phynai"))
    home.mkdir(parents=True, exist_ok=True)
    return home


# =============================================================================
# Provider Registry
# =============================================================================

@dataclass
class ProviderConfig:
    """Describes a known inference provider."""
    id: str
    name: str
    auth_type: str  # "oauth_device_code", "oauth_external", "oauth_pkce", or "api_key"
    portal_base_url: str = ""
    inference_base_url: str = ""
    client_id: str = ""
    scope: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    api_key_env_vars: tuple = ()
    base_url_env_var: str = ""


PROVIDER_REGISTRY: Dict[str, ProviderConfig] = {
    "nous": ProviderConfig(
        id="nous",
        name="Nous Portal",
        auth_type="oauth_device_code",
        portal_base_url=DEFAULT_NOUS_PORTAL_URL,
        inference_base_url=DEFAULT_NOUS_INFERENCE_URL,
        client_id=DEFAULT_NOUS_CLIENT_ID,
        scope=DEFAULT_NOUS_SCOPE,
    ),
    "openai-codex": ProviderConfig(
        id="openai-codex",
        name="OpenAI Codex",
        auth_type="oauth_external",
        inference_base_url=DEFAULT_CODEX_BASE_URL,
    ),
    "copilot": ProviderConfig(
        id="copilot",
        name="GitHub Copilot",
        auth_type="api_key",
        inference_base_url=DEFAULT_GITHUB_MODELS_BASE_URL,
        api_key_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
    ),
    "anthropic": ProviderConfig(
        id="anthropic",
        name="Anthropic",
        auth_type="api_key",
        inference_base_url="https://api.anthropic.com",
        api_key_env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ),
    "openrouter": ProviderConfig(
        id="openrouter",
        name="OpenRouter",
        auth_type="api_key",
        inference_base_url=OPENROUTER_BASE_URL,
        api_key_env_vars=("OPENROUTER_API_KEY",),
    ),
    "openai": ProviderConfig(
        id="openai",
        name="OpenAI",
        auth_type="api_key",
        inference_base_url="https://api.openai.com",
        api_key_env_vars=("OPENAI_API_KEY",),
    ),
    "zai": ProviderConfig(
        id="zai",
        name="Z.AI / GLM",
        auth_type="api_key",
        inference_base_url="https://api.z.ai/api/paas/v4",
        api_key_env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        base_url_env_var="GLM_BASE_URL",
    ),
    "kimi-coding": ProviderConfig(
        id="kimi-coding",
        name="Kimi / Moonshot",
        auth_type="api_key",
        inference_base_url="https://api.moonshot.ai/v1",
        api_key_env_vars=("KIMI_API_KEY",),
        base_url_env_var="KIMI_BASE_URL",
    ),
    "minimax": ProviderConfig(
        id="minimax",
        name="MiniMax",
        auth_type="api_key",
        inference_base_url="https://api.minimax.io/anthropic",
        api_key_env_vars=("MINIMAX_API_KEY",),
        base_url_env_var="MINIMAX_BASE_URL",
    ),
    "minimax-cn": ProviderConfig(
        id="minimax-cn",
        name="MiniMax (China)",
        auth_type="api_key",
        inference_base_url="https://api.minimaxi.com/anthropic",
        api_key_env_vars=("MINIMAX_CN_API_KEY",),
        base_url_env_var="MINIMAX_CN_BASE_URL",
    ),
    "deepseek": ProviderConfig(
        id="deepseek",
        name="DeepSeek",
        auth_type="api_key",
        inference_base_url="https://api.deepseek.com/v1",
        api_key_env_vars=("DEEPSEEK_API_KEY",),
        base_url_env_var="DEEPSEEK_BASE_URL",
    ),
    "huggingface": ProviderConfig(
        id="huggingface",
        name="Hugging Face",
        auth_type="api_key",
        inference_base_url="https://router.huggingface.co/v1",
        api_key_env_vars=("HF_TOKEN",),
        base_url_env_var="HF_BASE_URL",
    ),
    "alibaba": ProviderConfig(
        id="alibaba",
        name="Alibaba Cloud (DashScope)",
        auth_type="api_key",
        inference_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key_env_vars=("DASHSCOPE_API_KEY",),
        base_url_env_var="DASHSCOPE_BASE_URL",
    ),
}

# Provider aliases for user convenience
PROVIDER_ALIASES: Dict[str, str] = {
    "glm": "zai", "z-ai": "zai", "z.ai": "zai", "zhipu": "zai",
    "kimi": "kimi-coding", "moonshot": "kimi-coding",
    "minimax-china": "minimax-cn", "minimax_cn": "minimax-cn",
    "claude": "anthropic", "claude-code": "anthropic",
    "github": "copilot", "github-copilot": "copilot",
    "or": "openrouter", "open-router": "openrouter",
    "hf": "huggingface", "hugging-face": "huggingface",
    "ollama": "local", "lmstudio": "local", "vllm": "local",
}

# Providers that support OAuth login
OAUTH_CAPABLE_PROVIDERS = {"anthropic", "nous", "openai-codex"}


# =============================================================================
# Kimi Code Endpoint Detection
# =============================================================================

KIMI_CODE_BASE_URL = "https://api.kimi.com/coding/v1"


def _resolve_kimi_base_url(api_key: str, default_url: str, env_override: str) -> str:
    if env_override:
        return env_override
    if api_key.startswith("sk-kimi-"):
        return KIMI_CODE_BASE_URL
    return default_url


# =============================================================================
# Utility Helpers
# =============================================================================

_PLACEHOLDER_SECRET_VALUES = {
    "*", "**", "***", "changeme", "your_api_key", "your-api-key",
    "placeholder", "example", "dummy", "null", "none",
}


def has_usable_secret(value: Any, *, min_length: int = 4) -> bool:
    """Return True when a configured secret looks usable, not empty/placeholder."""
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if len(cleaned) < min_length:
        return False
    if cleaned.lower() in _PLACEHOLDER_SECRET_VALUES:
        return False
    return True


def _gh_cli_candidates() -> list[str]:
    """Return candidate ``gh`` binary paths."""
    candidates: list[str] = []
    resolved = shutil.which("gh")
    if resolved:
        candidates.append(resolved)
    for candidate in (
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        str(Path.home() / ".local" / "bin" / "gh"),
    ):
        if candidate in candidates:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            candidates.append(candidate)
    return candidates


def _try_gh_cli_token() -> Optional[str]:
    """Return a token from ``gh auth token`` when the GitHub CLI is available."""
    for gh_path in _gh_cli_candidates():
        try:
            result = subprocess.run(
                [gh_path, "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _is_remote_session() -> bool:
    """Detect if running in an SSH session where webbrowser.open() won't work."""
    return bool(os.getenv("SSH_CLIENT") or os.getenv("SSH_TTY"))


def _decode_jwt_claims(token: Any) -> Dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        claims = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _parse_iso_timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _is_expiring(expires_at_iso: Any, skew_seconds: int) -> bool:
    expires_epoch = _parse_iso_timestamp(expires_at_iso)
    if expires_epoch is None:
        return True
    return expires_epoch <= (time.time() + skew_seconds)


def _coerce_ttl_seconds(expires_in: Any) -> int:
    try:
        ttl = int(expires_in)
    except Exception:
        ttl = 0
    return max(0, ttl)


def _optional_base_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip("/")
    return cleaned if cleaned else None


# =============================================================================
# Error Types
# =============================================================================

class AuthError(RuntimeError):
    """Structured auth error with UX mapping hints."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        code: Optional[str] = None,
        relogin_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.relogin_required = relogin_required


def format_auth_error(error: Exception) -> str:
    """Map auth failures to concise user-facing guidance."""
    if not isinstance(error, AuthError):
        return str(error)
    if error.relogin_required:
        return f"{error} Run `phynai setup provider` to re-authenticate."
    return str(error)


# =============================================================================
# Auth Store — persistence layer for ~/.phynai/auth.json
# =============================================================================

def _auth_file_path() -> Path:
    return get_phynai_home() / "auth.json"


def _auth_lock_path() -> Path:
    return _auth_file_path().with_suffix(".lock")


_auth_lock_holder = threading.local()


@contextmanager
def _auth_store_lock(timeout_seconds: float = AUTH_LOCK_TIMEOUT_SECONDS):
    """Cross-process advisory lock for auth.json reads+writes. Reentrant."""
    if getattr(_auth_lock_holder, "depth", 0) > 0:
        _auth_lock_holder.depth += 1
        try:
            yield
        finally:
            _auth_lock_holder.depth -= 1
        return

    lock_path = _auth_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is None and msvcrt is None:
        _auth_lock_holder.depth = 1
        try:
            yield
        finally:
            _auth_lock_holder.depth = 0
        return

    if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
        lock_path.write_text(" ", encoding="utf-8")

    with lock_path.open("r+" if msvcrt else "a+") as lock_file:
        deadline = time.time() + max(1.0, timeout_seconds)
        while True:
            try:
                if fcntl:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except (BlockingIOError, OSError, PermissionError):
                if time.time() >= deadline:
                    raise TimeoutError("Timed out waiting for auth store lock")
                time.sleep(0.05)

        _auth_lock_holder.depth = 1
        try:
            yield
        finally:
            _auth_lock_holder.depth = 0
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass


def _load_auth_store(auth_file: Optional[Path] = None) -> Dict[str, Any]:
    auth_file = auth_file or _auth_file_path()
    if not auth_file.exists():
        return {"version": AUTH_STORE_VERSION, "providers": {}}
    try:
        raw = json.loads(auth_file.read_text())
    except Exception:
        return {"version": AUTH_STORE_VERSION, "providers": {}}
    if isinstance(raw, dict) and isinstance(raw.get("providers"), dict):
        return raw
    return {"version": AUTH_STORE_VERSION, "providers": {}}


def _save_auth_store(auth_store: Dict[str, Any]) -> Path:
    auth_file = _auth_file_path()
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_store["version"] = AUTH_STORE_VERSION
    auth_store["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(auth_store, indent=2) + "\n"
    tmp_path = auth_file.with_name(f"{auth_file.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, auth_file)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    try:
        auth_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return auth_file


def _load_provider_state(auth_store: Dict[str, Any], provider_id: str) -> Optional[Dict[str, Any]]:
    providers = auth_store.get("providers")
    if not isinstance(providers, dict):
        return None
    state = providers.get(provider_id)
    if not isinstance(state, dict):
        return None
    return _restore_secrets_from_keyring(provider_id, state)


def _save_provider_state(auth_store: Dict[str, Any], provider_id: str, state: Dict[str, Any]) -> None:
    providers = auth_store.setdefault("providers", {})
    if not isinstance(providers, dict):
        auth_store["providers"] = {}
        providers = auth_store["providers"]
    providers[provider_id] = _strip_secrets_to_keyring(provider_id, state)
    auth_store["active_provider"] = provider_id


def get_provider_auth_state(provider_id: str) -> Optional[Dict[str, Any]]:
    """Return persisted auth state for a provider, or None."""
    auth_store = _load_auth_store()
    return _load_provider_state(auth_store, provider_id)


def get_active_provider() -> Optional[str]:
    """Return the currently active provider ID from auth store."""
    auth_store = _load_auth_store()
    return auth_store.get("active_provider")


def clear_provider_auth(provider_id: Optional[str] = None) -> bool:
    """Clear auth state for a provider. Returns True if something was cleared."""
    with _auth_store_lock():
        auth_store = _load_auth_store()
        target = provider_id or auth_store.get("active_provider")
        if not target:
            return False
        providers = auth_store.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            auth_store["providers"] = providers
        cleared = False
        if target in providers:
            del providers[target]
            _clear_keyring_secrets(target)
            cleared = True
        if not cleared:
            return False
        if auth_store.get("active_provider") == target:
            auth_store["active_provider"] = None
        _save_auth_store(auth_store)
    return True


# =============================================================================
# API Key Provider Resolution
# =============================================================================

def _resolve_api_key_provider_secret(
    provider_id: str, pconfig: ProviderConfig
) -> tuple[str, str]:
    """Resolve an API-key provider's token and indicate where it came from."""
    if provider_id == "copilot":
        token, source = _resolve_copilot_token()
        if token:
            return token, source
        return "", ""

    for env_var in pconfig.api_key_env_vars:
        val = os.getenv(env_var, "").strip()
        if has_usable_secret(val):
            return val, env_var

    return "", ""


def _resolve_copilot_token() -> tuple[str, str]:
    """Resolve a GitHub token suitable for Copilot API use."""
    copilot_env_vars = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
    for env_var in copilot_env_vars:
        val = os.getenv(env_var, "").strip()
        if val and not val.startswith("ghp_"):  # Classic PATs not supported
            return val, env_var
    token = _try_gh_cli_token()
    if token and not token.startswith("ghp_"):
        return token, "gh auth token"
    return "", ""


def resolve_api_key_provider_credentials(provider_id: str) -> Dict[str, Any]:
    """Resolve API key and base URL for an API-key provider."""
    pconfig = PROVIDER_REGISTRY.get(provider_id)
    if not pconfig or pconfig.auth_type not in ("api_key",):
        raise AuthError(
            f"Provider '{provider_id}' is not an API-key provider.",
            provider=provider_id, code="invalid_provider",
        )

    api_key, key_source = _resolve_api_key_provider_secret(provider_id, pconfig)

    env_url = ""
    if pconfig.base_url_env_var:
        env_url = os.getenv(pconfig.base_url_env_var, "").strip()

    if provider_id == "kimi-coding":
        base_url = _resolve_kimi_base_url(api_key, pconfig.inference_base_url, env_url)
    elif env_url:
        base_url = env_url.rstrip("/")
    else:
        base_url = pconfig.inference_base_url

    return {
        "provider": provider_id,
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "source": key_source or "default",
    }


# =============================================================================
# Provider Resolution — picks which provider to use
# =============================================================================

def resolve_provider(
    requested: Optional[str] = None,
    *,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> str:
    """
    Determine which inference provider to use.

    Priority (when requested="auto" or None):
    1. active_provider in auth.json with valid credentials
    2. Explicit CLI api_key/base_url -> "openrouter"
    3. ANTHROPIC_API_KEY env var -> "anthropic"
    4. OPENAI_API_KEY or OPENROUTER_API_KEY env vars -> respective provider
    5. Provider-specific API keys -> that provider
    6. Fallback: raise AuthError
    """
    normalized = (requested or "auto").strip().lower()
    normalized = PROVIDER_ALIASES.get(normalized, normalized)

    if normalized in ("openrouter", "custom", "local"):
        return normalized
    if normalized in PROVIDER_REGISTRY:
        return normalized
    if normalized != "auto":
        raise AuthError(f"Unknown provider '{normalized}'.", code="invalid_provider")

    if explicit_api_key or explicit_base_url:
        return "openrouter"

    # Check auth store for an active OAuth provider
    try:
        auth_store = _load_auth_store()
        active = auth_store.get("active_provider")
        if active and active in PROVIDER_REGISTRY:
            status = get_auth_status(active)
            if status.get("logged_in"):
                return active
    except Exception as e:
        logger.debug("Could not detect active auth provider: %s", e)

    # Check env vars
    if has_usable_secret(os.getenv("ANTHROPIC_API_KEY")):
        return "anthropic"
    if has_usable_secret(os.getenv("OPENAI_API_KEY")):
        return "openai"
    if has_usable_secret(os.getenv("OPENROUTER_API_KEY")):
        return "openrouter"

    # Auto-detect API-key providers
    for pid, pconfig in PROVIDER_REGISTRY.items():
        if pconfig.auth_type != "api_key" or pid in ("copilot", "anthropic", "openai", "openrouter"):
            continue
        for env_var in pconfig.api_key_env_vars:
            if has_usable_secret(os.getenv(env_var, "")):
                return pid

    raise AuthError(
        "No inference provider configured. Run 'phynai setup provider' to choose a "
        "provider and model, or set an API key (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) in .env.",
        code="no_provider_configured",
    )


# =============================================================================
# Auth Status
# =============================================================================

def get_auth_status(provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Generic auth status dispatcher."""
    target = provider_id or get_active_provider()
    if target == "nous":
        state = get_provider_auth_state("nous")
        if not state:
            return {"logged_in": False}
        return {
            "logged_in": bool(state.get("access_token")),
            "portal_base_url": state.get("portal_base_url"),
            "inference_base_url": state.get("inference_base_url"),
            "has_refresh_token": bool(state.get("refresh_token")),
        }
    if target == "openai-codex":
        try:
            creds = resolve_codex_runtime_credentials()
            return {"logged_in": True, "source": creds.get("source")}
        except AuthError:
            return {"logged_in": False}
    # API-key providers
    pconfig = PROVIDER_REGISTRY.get(target)
    if pconfig and pconfig.auth_type == "api_key":
        api_key, _ = _resolve_api_key_provider_secret(target, pconfig)
        return {"configured": bool(api_key), "logged_in": bool(api_key)}
    return {"logged_in": False}


# =============================================================================
# OAuth Device Code Flow — generic, parameterized by provider
# =============================================================================

def _request_device_code(
    client: httpx.Client,
    portal_base_url: str,
    client_id: str,
    scope: Optional[str],
) -> Dict[str, Any]:
    """POST to the device code endpoint."""
    response = client.post(
        f"{portal_base_url}/api/oauth/device/code",
        data={
            "client_id": client_id,
            **({"scope": scope} if scope else {}),
        },
    )
    response.raise_for_status()
    data = response.json()
    required_fields = [
        "device_code", "user_code", "verification_uri",
        "verification_uri_complete", "expires_in", "interval",
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Device code response missing fields: {', '.join(missing)}")
    return data


def _poll_for_token(
    client: httpx.Client,
    portal_base_url: str,
    client_id: str,
    device_code: str,
    expires_in: int,
    poll_interval: int,
) -> Dict[str, Any]:
    """Poll the token endpoint until the user approves or the code expires."""
    deadline = time.time() + max(1, expires_in)
    current_interval = max(1, min(poll_interval, DEVICE_AUTH_POLL_INTERVAL_CAP_SECONDS))

    while time.time() < deadline:
        response = client.post(
            f"{portal_base_url}/api/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
        )

        if response.status_code == 200:
            payload = response.json()
            if "access_token" not in payload:
                raise ValueError("Token response did not include access_token")
            return payload

        try:
            error_payload = response.json()
        except Exception:
            response.raise_for_status()
            raise RuntimeError("Token endpoint returned a non-JSON error response")

        error_code = error_payload.get("error", "")
        if error_code == "authorization_pending":
            time.sleep(current_interval)
            continue
        if error_code == "slow_down":
            current_interval = min(current_interval + 1, 30)
            time.sleep(current_interval)
            continue

        description = error_payload.get("error_description") or "Unknown authentication error"
        raise RuntimeError(f"{error_code}: {description}")

    raise TimeoutError("Timed out waiting for device authorization")


# =============================================================================
# Nous Portal — token refresh, agent key minting
# =============================================================================

def _refresh_access_token(
    *, client: httpx.Client, portal_base_url: str,
    client_id: str, refresh_token: str,
) -> Dict[str, Any]:
    response = client.post(
        f"{portal_base_url}/api/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
    )
    if response.status_code == 200:
        payload = response.json()
        if "access_token" not in payload:
            raise AuthError("Refresh response missing access_token",
                            provider="nous", code="invalid_token", relogin_required=True)
        return payload
    try:
        error_payload = response.json()
    except Exception as exc:
        raise AuthError("Refresh token exchange failed",
                        provider="nous", relogin_required=True) from exc
    code = str(error_payload.get("error", "invalid_grant"))
    description = str(error_payload.get("error_description") or "Refresh token exchange failed")
    relogin = code in {"invalid_grant", "invalid_token"}
    raise AuthError(description, provider="nous", code=code, relogin_required=relogin)


def _mint_agent_key(
    *, client: httpx.Client, portal_base_url: str,
    access_token: str, min_ttl_seconds: int,
) -> Dict[str, Any]:
    """Mint (or reuse) a short-lived inference API key."""
    response = client.post(
        f"{portal_base_url}/api/oauth/agent-key",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"min_ttl_seconds": max(60, int(min_ttl_seconds))},
    )
    if response.status_code == 200:
        payload = response.json()
        if "api_key" not in payload:
            raise AuthError("Mint response missing api_key", provider="nous", code="server_error")
        return payload
    try:
        error_payload = response.json()
    except Exception as exc:
        raise AuthError("Agent key mint request failed", provider="nous", code="server_error") from exc
    code = str(error_payload.get("error", "server_error"))
    description = str(error_payload.get("error_description") or "Agent key mint request failed")
    relogin = code in {"invalid_token", "invalid_grant"}
    raise AuthError(description, provider="nous", code=code, relogin_required=relogin)


def _agent_key_is_usable(state: Dict[str, Any], min_ttl_seconds: int) -> bool:
    key = state.get("agent_key")
    if not isinstance(key, str) or not key.strip():
        return False
    return not _is_expiring(state.get("agent_key_expires_at"), min_ttl_seconds)


def resolve_nous_runtime_credentials(
    *, min_key_ttl_seconds: int = DEFAULT_AGENT_KEY_MIN_TTL_SECONDS,
    timeout_seconds: float = 15.0,
    force_mint: bool = False,
) -> Dict[str, Any]:
    """Resolve Nous inference credentials for runtime use."""
    min_key_ttl_seconds = max(60, int(min_key_ttl_seconds))

    with _auth_store_lock():
        auth_store = _load_auth_store()
        state = _load_provider_state(auth_store, "nous")
        if not state:
            raise AuthError("Not logged into Nous Portal.",
                            provider="nous", relogin_required=True)

        portal_base_url = (
            _optional_base_url(state.get("portal_base_url"))
            or DEFAULT_NOUS_PORTAL_URL
        ).rstrip("/")
        inference_base_url = (
            _optional_base_url(state.get("inference_base_url"))
            or DEFAULT_NOUS_INFERENCE_URL
        ).rstrip("/")
        client_id = str(state.get("client_id") or DEFAULT_NOUS_CLIENT_ID)
        timeout = httpx.Timeout(timeout_seconds)

        with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
            access_token = state.get("access_token")
            refresh_token = state.get("refresh_token")

            if not isinstance(access_token, str) or not access_token:
                raise AuthError("No access token found.",
                                provider="nous", relogin_required=True)

            # Refresh access token if expiring
            if _is_expiring(state.get("expires_at"), ACCESS_TOKEN_REFRESH_SKEW_SECONDS):
                if not isinstance(refresh_token, str) or not refresh_token:
                    raise AuthError("Session expired and no refresh token.",
                                    provider="nous", relogin_required=True)
                refreshed = _refresh_access_token(
                    client=client, portal_base_url=portal_base_url,
                    client_id=client_id, refresh_token=refresh_token,
                )
                now = datetime.now(timezone.utc)
                access_ttl = _coerce_ttl_seconds(refreshed.get("expires_in"))
                state["access_token"] = refreshed["access_token"]
                state["refresh_token"] = refreshed.get("refresh_token") or refresh_token
                state["obtained_at"] = now.isoformat()
                state["expires_at"] = datetime.fromtimestamp(
                    now.timestamp() + access_ttl, tz=timezone.utc
                ).isoformat()
                access_token = state["access_token"]

            # Mint agent key if missing/expiring
            used_cached_key = False
            if not force_mint and _agent_key_is_usable(state, min_key_ttl_seconds):
                used_cached_key = True
            else:
                mint_payload = _mint_agent_key(
                    client=client, portal_base_url=portal_base_url,
                    access_token=access_token, min_ttl_seconds=min_key_ttl_seconds,
                )
                now = datetime.now(timezone.utc)
                state["agent_key"] = mint_payload.get("api_key")
                state["agent_key_expires_at"] = mint_payload.get("expires_at")
                minted_url = _optional_base_url(mint_payload.get("inference_base_url"))
                if minted_url:
                    inference_base_url = minted_url

        state["portal_base_url"] = portal_base_url
        state["inference_base_url"] = inference_base_url
        _save_provider_state(auth_store, "nous", state)
        _save_auth_store(auth_store)

    api_key = state.get("agent_key")
    if not isinstance(api_key, str) or not api_key:
        raise AuthError("Failed to resolve a Nous inference API key",
                        provider="nous", code="server_error")

    return {
        "provider": "nous",
        "base_url": inference_base_url,
        "api_key": api_key,
        "source": "cache" if used_cached_key else "portal",
    }


def nous_device_code_login(
    *, portal_base_url: Optional[str] = None,
    inference_base_url: Optional[str] = None,
    open_browser: bool = True,
    timeout_seconds: float = 15.0,
) -> Dict[str, Any]:
    """Run the Nous device-code flow and return full OAuth state."""
    pconfig = PROVIDER_REGISTRY["nous"]
    portal_base_url = (
        portal_base_url
        or os.getenv("NOUS_PORTAL_BASE_URL")
        or pconfig.portal_base_url
    ).rstrip("/")
    requested_inference_url = (
        inference_base_url
        or os.getenv("NOUS_INFERENCE_BASE_URL")
        or pconfig.inference_base_url
    ).rstrip("/")
    client_id = pconfig.client_id
    scope = pconfig.scope
    timeout = httpx.Timeout(timeout_seconds)

    if _is_remote_session():
        open_browser = False

    print(f"Starting login via {pconfig.name}...")
    print(f"Portal: {portal_base_url}")

    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        device_data = _request_device_code(
            client=client, portal_base_url=portal_base_url,
            client_id=client_id, scope=scope,
        )

        verification_url = str(device_data["verification_uri_complete"])
        user_code = str(device_data["user_code"])
        expires_in = int(device_data["expires_in"])
        interval = int(device_data["interval"])

        print()
        print("To continue:")
        print(f"  1. Open: {verification_url}")
        print(f"  2. If prompted, enter code: {user_code}")

        if open_browser:
            opened = webbrowser.open(verification_url)
            if opened:
                print("  (Opened browser for verification)")

        token_data = _poll_for_token(
            client=client, portal_base_url=portal_base_url,
            client_id=client_id, device_code=str(device_data["device_code"]),
            expires_in=expires_in, poll_interval=interval,
        )

    now = datetime.now(timezone.utc)
    token_expires_in = _coerce_ttl_seconds(token_data.get("expires_in", 0))
    expires_at = now.timestamp() + token_expires_in
    resolved_inference_url = (
        _optional_base_url(token_data.get("inference_base_url"))
        or requested_inference_url
    )

    return {
        "portal_base_url": portal_base_url,
        "inference_base_url": resolved_inference_url,
        "client_id": client_id,
        "scope": token_data.get("scope") or scope,
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "obtained_at": now.isoformat(),
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "expires_in": token_expires_in,
        "agent_key": None,
    }


# =============================================================================
# OpenAI Codex — OAuth device code flow
# =============================================================================

def _codex_access_token_is_expiring(access_token: Any, skew_seconds: int) -> bool:
    claims = _decode_jwt_claims(access_token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return float(exp) <= (time.time() + max(0, int(skew_seconds)))


def _read_codex_tokens(*, _lock: bool = True) -> Dict[str, Any]:
    """Read Codex OAuth tokens from auth store."""
    if _lock:
        with _auth_store_lock():
            auth_store = _load_auth_store()
    else:
        auth_store = _load_auth_store()
    state = _load_provider_state(auth_store, "openai-codex")
    if not state:
        raise AuthError("No Codex credentials stored. Run login to authenticate.",
                        provider="openai-codex", code="codex_auth_missing", relogin_required=True)
    tokens = state.get("tokens")
    if not isinstance(tokens, dict):
        raise AuthError("Codex auth state is missing tokens.",
                        provider="openai-codex", code="codex_auth_invalid_shape", relogin_required=True)
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError("Codex auth is missing access_token.",
                        provider="openai-codex", relogin_required=True)
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AuthError("Codex auth is missing refresh_token.",
                        provider="openai-codex", relogin_required=True)
    return {"tokens": tokens, "last_refresh": state.get("last_refresh")}


def _save_codex_tokens(tokens: Dict[str, str], last_refresh: str = None) -> None:
    """Save Codex OAuth tokens to auth store."""
    if last_refresh is None:
        last_refresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _auth_store_lock():
        auth_store = _load_auth_store()
        state = _load_provider_state(auth_store, "openai-codex") or {}
        state["tokens"] = tokens
        state["last_refresh"] = last_refresh
        state["auth_mode"] = "chatgpt"
        _save_provider_state(auth_store, "openai-codex", state)
        _save_auth_store(auth_store)


def refresh_codex_oauth_pure(
    access_token: str, refresh_token: str,
    *, timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Refresh Codex OAuth tokens."""
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AuthError("Codex auth is missing refresh_token.",
                        provider="openai-codex", relogin_required=True)
    timeout = httpx.Timeout(max(5.0, float(timeout_seconds)))
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        response = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )
    if response.status_code != 200:
        relogin_required = False
        try:
            err = response.json()
            err_code = err.get("error", "")
            if err_code in {"invalid_grant", "invalid_token"}:
                relogin_required = True
        except Exception:
            pass
        raise AuthError(f"Codex token refresh failed [{response.status_code}].",
                        provider="openai-codex", relogin_required=relogin_required)
    refresh_payload = response.json()
    refreshed_access = refresh_payload.get("access_token")
    if not isinstance(refreshed_access, str) or not refreshed_access.strip():
        raise AuthError("Codex refresh missing access_token.",
                        provider="openai-codex", relogin_required=True)
    updated = {
        "access_token": refreshed_access.strip(),
        "refresh_token": refresh_token.strip(),
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    next_refresh = refresh_payload.get("refresh_token")
    if isinstance(next_refresh, str) and next_refresh.strip():
        updated["refresh_token"] = next_refresh.strip()
    return updated


def resolve_codex_runtime_credentials(
    *, force_refresh: bool = False,
    refresh_skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
) -> Dict[str, Any]:
    """Resolve runtime credentials from Codex token store."""
    data = _read_codex_tokens()
    tokens = dict(data["tokens"])
    access_token = str(tokens.get("access_token", "") or "").strip()

    should_refresh = bool(force_refresh)
    if not should_refresh:
        should_refresh = _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
    if should_refresh:
        refreshed = refresh_codex_oauth_pure(access_token, str(tokens.get("refresh_token", "")))
        tokens["access_token"] = refreshed["access_token"]
        tokens["refresh_token"] = refreshed["refresh_token"]
        _save_codex_tokens(tokens)
        access_token = tokens["access_token"]

    base_url = os.getenv("PHYNAI_CODEX_BASE_URL", "").strip().rstrip("/") or DEFAULT_CODEX_BASE_URL
    return {
        "provider": "openai-codex",
        "base_url": base_url,
        "api_key": access_token,
        "source": "phynai-auth-store",
    }


def codex_device_code_login() -> Dict[str, Any]:
    """Run the OpenAI device code login flow and return credentials dict."""
    issuer = "https://auth.openai.com"
    client_id = CODEX_OAUTH_CLIENT_ID

    # Step 1: Request device code
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": client_id},
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        raise AuthError(f"Failed to request device code: {exc}",
                        provider="openai-codex", code="device_code_request_failed")
    if resp.status_code != 200:
        raise AuthError(f"Device code request returned status {resp.status_code}.",
                        provider="openai-codex")

    device_data = resp.json()
    user_code = device_data.get("user_code", "")
    device_auth_id = device_data.get("device_auth_id", "")
    poll_interval = max(3, int(device_data.get("interval", "5")))

    if not user_code or not device_auth_id:
        raise AuthError("Device code response missing required fields.",
                        provider="openai-codex")

    # Step 2: Show instructions
    print("To continue, follow these steps:\n")
    print("  1. Open this URL in your browser:")
    print(f"     \033[94m{issuer}/codex/device\033[0m\n")
    print("  2. Enter this code:")
    print(f"     \033[94m{user_code}\033[0m\n")
    print("Waiting for sign-in... (press Ctrl+C to cancel)")

    # Step 3: Poll for authorization code
    max_wait = 15 * 60
    start = time.monotonic()
    code_resp = None

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.monotonic() - start < max_wait:
                time.sleep(poll_interval)
                poll_resp = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll_resp.status_code == 200:
                    code_resp = poll_resp.json()
                    break
                elif poll_resp.status_code in (403, 404):
                    continue
                else:
                    raise AuthError(f"Poll returned status {poll_resp.status_code}.",
                                    provider="openai-codex")
    except KeyboardInterrupt:
        print("\nLogin cancelled.")
        raise SystemExit(130)

    if code_resp is None:
        raise AuthError("Login timed out.", provider="openai-codex")

    # Step 4: Exchange authorization code for tokens
    authorization_code = code_resp.get("authorization_code", "")
    code_verifier = code_resp.get("code_verifier", "")
    redirect_uri = f"{issuer}/deviceauth/callback"

    if not authorization_code or not code_verifier:
        raise AuthError("Device auth response incomplete.", provider="openai-codex")

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_resp = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:
        raise AuthError(f"Token exchange failed: {exc}", provider="openai-codex")

    if token_resp.status_code != 200:
        raise AuthError(f"Token exchange returned status {token_resp.status_code}.",
                        provider="openai-codex")

    tokens = token_resp.json()
    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    if not access_token:
        raise AuthError("Token exchange did not return an access_token.",
                        provider="openai-codex")

    base_url = os.getenv("PHYNAI_CODEX_BASE_URL", "").strip().rstrip("/") or DEFAULT_CODEX_BASE_URL

    return {
        "tokens": {"access_token": access_token, "refresh_token": refresh_token},
        "base_url": base_url,
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "auth_mode": "chatgpt",
        "source": "device-code",
    }


# =============================================================================
# GitHub Copilot — OAuth device code flow
# =============================================================================

def copilot_device_code_login(
    *, host: str = "github.com",
    timeout_seconds: float = 300,
) -> Optional[str]:
    """Run the GitHub OAuth device code flow for Copilot.

    Returns the OAuth access token on success, or None on failure.
    """
    import urllib.request
    import urllib.parse

    domain = host.rstrip("/")
    device_code_url = f"https://{domain}/login/device/code"
    access_token_url = f"https://{domain}/login/oauth/access_token"

    data = urllib.parse.urlencode({
        "client_id": COPILOT_OAUTH_CLIENT_ID,
        "scope": "read:user",
    }).encode()

    req = urllib.request.Request(
        device_code_url, data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "PhynAI/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_data = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"  Failed to start device authorization: {exc}")
        return None

    verification_uri = device_data.get("verification_uri", f"https://{domain}/login/device")
    user_code = device_data.get("user_code", "")
    device_code = device_data.get("device_code", "")
    interval = max(device_data.get("interval", 5), 1)

    if not device_code or not user_code:
        print("  GitHub did not return a device code.")
        return None

    print()
    print(f"  Open this URL in your browser: {verification_uri}")
    print(f"  Enter this code: {user_code}")
    print()
    print("  Waiting for authorization...", end="", flush=True)

    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        time.sleep(interval + 3)

        poll_data = urllib.parse.urlencode({
            "client_id": COPILOT_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()

        poll_req = urllib.request.Request(
            access_token_url, data=poll_data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "PhynAI/1.0",
            },
        )

        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception:
            print(".", end="", flush=True)
            continue

        if result.get("access_token"):
            print(" done")
            return result["access_token"]

        error = result.get("error", "")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            interval += 5
            continue
        elif error in ("expired_token", "access_denied"):
            print(f"\n  Authorization failed: {error}")
            return None

    print("\n  Timed out waiting for authorization.")
    return None


# =============================================================================
# Anthropic OAuth PKCE Flow — Claude Pro/Max subscription
# =============================================================================

def _generate_pkce() -> tuple:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    import secrets

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _detect_claude_code_version() -> str:
    """Detect the installed Claude Code version for user-agent."""
    for cmd in ("claude", "claude-code"):
        try:
            result = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    return version
        except Exception:
            pass
    return "2.1.74"


def anthropic_oauth_pkce_login() -> Optional[Dict[str, Any]]:
    """Run Anthropic OAuth PKCE flow and return credential state.

    Opens browser to claude.ai for authorization, prompts for the code,
    exchanges it for tokens.

    Returns dict with access_token, refresh_token, expires_at_ms or None.
    """
    from urllib.parse import urlencode

    verifier, challenge = _generate_pkce()

    params = {
        "code": "true",
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTHROPIC_OAUTH_REDIRECT_URI,
        "scope": ANTHROPIC_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    auth_url = f"https://claude.ai/oauth/authorize?{urlencode(params)}"

    print()
    print("Authorize with your Claude Pro/Max subscription.")
    print()
    print("  Open this link in your browser:")
    print(f"  {auth_url}")
    print()

    try:
        webbrowser.open(auth_url)
        print("  (Browser opened automatically)")
    except Exception:
        pass

    print()
    print("After authorizing, you'll see a code. Paste it below.")
    print()
    try:
        auth_code = input("Authorization code: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None

    if not auth_code:
        print("No code entered.")
        return None

    splits = auth_code.split("#")
    code = splits[0]
    state = splits[1] if len(splits) > 1 else ""

    claude_version = _detect_claude_code_version()

    try:
        import urllib.request

        exchange_data = json.dumps({
            "grant_type": "authorization_code",
            "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
            "code": code,
            "state": state,
            "redirect_uri": ANTHROPIC_OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        }).encode()

        req = urllib.request.Request(
            ANTHROPIC_OAUTH_TOKEN_URL,
            data=exchange_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"claude-cli/{claude_version} (external, cli)",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Token exchange failed: {e}")
        return None

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = result.get("expires_in", 3600)

    if not access_token:
        print("No access token in response.")
        return None

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at_ms": expires_at_ms,
    }


def refresh_anthropic_oauth_pure(refresh_token: str) -> Dict[str, Any]:
    """Refresh an Anthropic OAuth token."""
    import urllib.parse
    import urllib.request

    if not refresh_token:
        raise ValueError("refresh_token is required")

    claude_version = _detect_claude_code_version()
    data = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
    }).encode()

    token_endpoints = [
        "https://platform.claude.com/v1/oauth/token",
        "https://console.anthropic.com/v1/oauth/token",
    ]
    last_error = None
    for endpoint in token_endpoints:
        req = urllib.request.Request(
            endpoint, data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"claude-cli/{claude_version} (external, cli)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            continue

        access_token = result.get("access_token", "")
        if not access_token:
            raise ValueError("Anthropic refresh response missing access_token")
        next_refresh = result.get("refresh_token", refresh_token)
        expires_in = result.get("expires_in", 3600)
        return {
            "access_token": access_token,
            "refresh_token": next_refresh,
            "expires_at_ms": int(time.time() * 1000) + (expires_in * 1000),
        }

    if last_error is not None:
        raise last_error
    raise ValueError("Anthropic token refresh failed")


def read_claude_code_credentials() -> Optional[Dict[str, Any]]:
    """Read refreshable Claude Code OAuth credentials from ~/.claude/.credentials.json."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            oauth_data = data.get("claudeAiOauth")
            if oauth_data and isinstance(oauth_data, dict):
                access_token = oauth_data.get("accessToken", "")
                if access_token:
                    return {
                        "accessToken": access_token,
                        "refreshToken": oauth_data.get("refreshToken", ""),
                        "expiresAt": oauth_data.get("expiresAt", 0),
                        "source": "claude_code_credentials_file",
                    }
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_claude_code_credentials(
    access_token: str, refresh_token: str, expires_at_ms: int,
) -> None:
    """Write refreshed credentials back to ~/.claude/.credentials.json."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        existing = {}
        if cred_path.exists():
            existing = json.loads(cred_path.read_text(encoding="utf-8"))
        oauth_data: Dict[str, Any] = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
        if "claudeAiOauth" in existing and "scopes" in existing["claudeAiOauth"]:
            oauth_data["scopes"] = existing["claudeAiOauth"]["scopes"]
        existing["claudeAiOauth"] = oauth_data
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        cred_path.chmod(0o600)
    except (OSError, IOError) as e:
        logger.debug("Failed to write refreshed credentials: %s", e)


def _save_phynai_oauth_credentials(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """Save OAuth credentials to ~/.phynai/.anthropic_oauth.json."""
    oauth_file = get_phynai_home() / ".anthropic_oauth.json"
    data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    try:
        oauth_file.parent.mkdir(parents=True, exist_ok=True)
        oauth_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        oauth_file.chmod(0o600)
    except (OSError, IOError) as e:
        logger.debug("Failed to save PhynAI OAuth credentials: %s", e)


def read_phynai_oauth_credentials() -> Optional[Dict[str, Any]]:
    """Read PhynAI-managed OAuth credentials from ~/.phynai/.anthropic_oauth.json."""
    oauth_file = get_phynai_home() / ".anthropic_oauth.json"
    if oauth_file.exists():
        try:
            data = json.loads(oauth_file.read_text(encoding="utf-8"))
            if data.get("accessToken"):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def resolve_anthropic_token() -> Optional[str]:
    """Resolve an Anthropic token from all available sources.

    Priority:
      1. ANTHROPIC_TOKEN env var
      2. CLAUDE_CODE_OAUTH_TOKEN env var
      3. Claude Code credentials (~/.claude/.credentials.json) with refresh
      4. PhynAI OAuth credentials (~/.phynai/.anthropic_oauth.json) with refresh
      5. ANTHROPIC_API_KEY env var
    """
    # 1. ANTHROPIC_TOKEN env var
    token = os.getenv("ANTHROPIC_TOKEN", "").strip()
    if token:
        return token

    # 2. CLAUDE_CODE_OAUTH_TOKEN
    cc_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if cc_token:
        return cc_token

    # 3. Claude Code credential file
    creds = read_claude_code_credentials()
    if creds:
        expires_at = creds.get("expiresAt", 0)
        now_ms = int(time.time() * 1000)
        if now_ms < (expires_at - 60_000):
            return creds["accessToken"]
        # Try refresh
        refresh_token = creds.get("refreshToken", "")
        if refresh_token:
            try:
                refreshed = refresh_anthropic_oauth_pure(refresh_token)
                _write_claude_code_credentials(
                    refreshed["access_token"],
                    refreshed["refresh_token"],
                    refreshed["expires_at_ms"],
                )
                return refreshed["access_token"]
            except Exception:
                pass

    # 4. PhynAI OAuth credentials
    phynai_creds = read_phynai_oauth_credentials()
    if phynai_creds:
        expires_at = phynai_creds.get("expiresAt", 0)
        now_ms = int(time.time() * 1000)
        if now_ms < (expires_at - 60_000):
            return phynai_creds["accessToken"]
        refresh_token = phynai_creds.get("refreshToken", "")
        if refresh_token:
            try:
                refreshed = refresh_anthropic_oauth_pure(refresh_token)
                _save_phynai_oauth_credentials(
                    refreshed["access_token"],
                    refreshed["refresh_token"],
                    refreshed["expires_at_ms"],
                )
                return refreshed["access_token"]
            except Exception:
                pass

    # 5. Regular API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return api_key

    return None


# =============================================================================
# MCP OAuth Token Storage
# =============================================================================

_TOKEN_DIR_NAME = "mcp-tokens"


def _sanitize_server_name(name: str) -> str:
    import re
    clean = re.sub(r"[^\w\-]", "-", name.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean[:60] or "unnamed"


class MCPTokenStorage:
    """File-backed token storage for MCP OAuth."""

    def __init__(self, server_name: str):
        self._server_name = _sanitize_server_name(server_name)

    def _base_dir(self) -> Path:
        d = get_phynai_home() / _TOKEN_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _tokens_path(self) -> Path:
        return self._base_dir() / f"{self._server_name}.json"

    def _client_path(self) -> Path:
        return self._base_dir() / f"{self._server_name}.client.json"

    async def get_tokens(self):
        data = self._read_json(self._tokens_path())
        if not data:
            return None
        try:
            from mcp.shared.auth import OAuthToken
            return OAuthToken(**data)
        except Exception:
            return None

    async def set_tokens(self, tokens) -> None:
        self._write_json(self._tokens_path(), tokens.model_dump(exclude_none=True))

    async def get_client_info(self):
        data = self._read_json(self._client_path())
        if not data:
            return None
        try:
            from mcp.shared.auth import OAuthClientInformationFull
            return OAuthClientInformationFull(**data)
        except Exception:
            return None

    async def set_client_info(self, client_info) -> None:
        self._write_json(self._client_path(), client_info.model_dump(exclude_none=True))

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def remove(self) -> None:
        """Delete stored tokens and client info for this server."""
        for p in (self._tokens_path(), self._client_path()):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


# =============================================================================
# CLI Commands — login / logout / auth management
# =============================================================================

def login_provider(provider_id: str) -> None:
    """Interactive login for a provider."""
    if provider_id == "nous":
        _login_nous()
    elif provider_id == "openai-codex":
        _login_codex()
    elif provider_id == "anthropic":
        _login_anthropic()
    elif provider_id == "copilot":
        _login_copilot()
    else:
        pconfig = PROVIDER_REGISTRY.get(provider_id)
        if pconfig and pconfig.auth_type == "api_key":
            _login_api_key(provider_id, pconfig)
        else:
            print(f"Unknown provider: {provider_id}")


def _login_api_key(provider_id: str, pconfig: ProviderConfig) -> None:
    """Interactive API key entry."""
    from getpass import getpass
    print(f"\n{pconfig.name} uses API keys for authentication.")
    if pconfig.api_key_env_vars:
        print(f"Set one of these env vars: {', '.join(pconfig.api_key_env_vars)}")
    key = getpass("Paste your API key: ").strip()
    if key:
        env_var = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else "PHYNAI_API_KEY"
        print(f"\nAdd this to your .env file:")
        print(f"  {env_var}={key}")
    else:
        print("No key entered.")


def _login_nous() -> None:
    """Nous Portal device authorization flow."""
    try:
        auth_state = nous_device_code_login()
        with _auth_store_lock():
            auth_store = _load_auth_store()
            _save_provider_state(auth_store, "nous", auth_state)
            saved_to = _save_auth_store(auth_store)
        print()
        print("Login successful!")
        print(f"  Auth state: {saved_to}")
    except KeyboardInterrupt:
        print("\nLogin cancelled.")
    except Exception as exc:
        print(f"Login failed: {exc}")


def _login_codex() -> None:
    """OpenAI Codex login via device code flow."""
    print("\nSigning in to OpenAI Codex...")
    try:
        creds = codex_device_code_login()
        _save_codex_tokens(creds["tokens"], creds.get("last_refresh"))
        print("\nLogin successful!")
    except KeyboardInterrupt:
        print("\nLogin cancelled.")
    except Exception as exc:
        print(f"Login failed: {exc}")


def _login_anthropic() -> None:
    """Anthropic OAuth PKCE login."""
    result = anthropic_oauth_pkce_login()
    if not result:
        return

    access_token = result["access_token"]
    refresh_token = result["refresh_token"]
    expires_at_ms = result["expires_at_ms"]

    _save_phynai_oauth_credentials(access_token, refresh_token, expires_at_ms)
    _write_claude_code_credentials(access_token, refresh_token, expires_at_ms)

    # Also persist in auth store
    with _auth_store_lock():
        auth_store = _load_auth_store()
        _save_provider_state(auth_store, "anthropic", {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at_ms": expires_at_ms,
            "auth_type": "oauth_pkce",
        })
        _save_auth_store(auth_store)

    print("\nAuthentication successful!")


def _login_copilot() -> None:
    """GitHub Copilot OAuth login."""
    print("\nSigning in to GitHub Copilot...")
    token = copilot_device_code_login()
    if token:
        with _auth_store_lock():
            auth_store = _load_auth_store()
            _save_provider_state(auth_store, "copilot", {
                "access_token": token,
                "auth_type": "oauth_device_code",
            })
            _save_auth_store(auth_store)
        print("\nLogin successful!")
    else:
        print("\nLogin failed or cancelled.")


def logout_provider(provider_id: Optional[str] = None) -> None:
    """Clear auth state for a provider."""
    active = get_active_provider()
    target = provider_id or active

    if not target:
        print("No provider is currently logged in.")
        return

    provider_name = PROVIDER_REGISTRY[target].name if target in PROVIDER_REGISTRY else target

    if clear_provider_auth(target):
        print(f"Logged out of {provider_name}.")
    else:
        print(f"No auth state found for {provider_name}.")


def list_auth_status() -> None:
    """Print auth status for all providers."""
    print("Auth Status")
    print("=" * 50)

    active = get_active_provider()

    for pid, pconfig in PROVIDER_REGISTRY.items():
        status = get_auth_status(pid)
        logged_in = status.get("logged_in", False)
        marker = " <- active" if pid == active else ""
        state = "configured" if logged_in else "not configured"
        print(f"  {pconfig.name:25s} {state}{marker}")

    print()
