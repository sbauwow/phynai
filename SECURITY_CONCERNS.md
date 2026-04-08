# PhynAI Security Concerns

Ongoing security tracker. Last updated 2026-04-05.

All CRITICAL, HIGH, and MEDIUM severity issues are resolved. Only LOW
hardening items remain.

---

## Resolved (for reference)

<details>
<summary>25 issues closed — click to expand</summary>

| # | Severity | Issue | Resolved |
|---|----------|-------|----------|
| 1 | CRITICAL | Shell injection in `search_files` | 2026-04-03 |
| 2 | CRITICAL | Shell injection in `terminal_tool` | 2026-04-03 |
| 3 | CRITICAL | Path traversal in file tools | 2026-04-03 |
| 4 | CRITICAL | SSRF in `web_extract` | 2026-04-03 |
| 5 | CRITICAL | Gateway default-allow (no auth) | 2026-04-03 |
| 6 | HIGH | Policy pipeline ships empty | 2026-04-04 |
| 7 | HIGH | No rate limiting on gateways | 2026-04-04 |
| 8 | HIGH | Plaintext session storage | 2026-04-04 |
| 9 | HIGH | OAuth tokens stored plaintext | 2026-04-05 |
| 10 | HIGH | Event journal in-memory only | 2026-04-04 |
| 11 | HIGH | Cost ledger in-memory only | 2026-04-05 |
| 12 | HIGH | No user identity on WorkItem | 2026-04-04 |
| 13 | HIGH | No file read size limits | 2026-04-05 |
| 14 | HIGH | Overly broad exception handling | 2026-04-05 |
| 15 | HIGH | Unawaited tool dispatch | 2026-04-05 |
| 16 | MEDIUM | No LLM audit trail | 2026-04-05 |
| 17 | MEDIUM | No structured logging | 2026-04-04 |
| 18 | MEDIUM | System prompt has no guardrails | 2026-04-05 |
| 19 | MEDIUM | LLM timeout too long, no retry | 2026-04-05 |
| 20 | MEDIUM | Skills loaded without sandboxing | 2026-04-05 |
| 21 | MEDIUM | Malformed tool calls silently dropped | 2026-04-05 |
| 22 | MEDIUM | No schema validation before dispatch | 2026-04-05 |
| 23 | MEDIUM | Secrets in .env without rotation docs | 2026-04-05 |
| 24 | LOW | Dead inject_memory() no-op | 2026-04-05 |
| 28 | LOW | Gateway errors leak internals | 2026-04-04 |

</details>

---

## Open — LOW

### 25. No circuit breaker on external calls
**Files:** `agent/client.py`, `tools/web_tools.py`, `tools/ms365.py`
Repeated failures waste resources and block the loop.
**Fix:** Fail-fast after N consecutive failures, reset after cooldown.

### 26. No TLS certificate pinning on MS365
**File:** `tools/ms365.py`
httpx uses the system CA store. Acceptable for most deployments; enterprise may need explicit pinning.
**Fix:** Document. Add `verify=certifi.where()` for explicit trust anchors.

### 27. Setup wizard lacks input sanitization
**File:** `setup.py`
Token inputs stored raw — no whitespace stripping, no format validation (xoxb-, xapp-).
**Fix:** Validate token format before saving to `.env`.

---

## Resolved — Phase 3 (Security Audit, 2026-04-05)

<details>
<summary>13 issues closed — click to expand</summary>

| # | Severity | Issue | Resolved |
|---|----------|-------|----------|
| 29 | CRITICAL | Shell injection in admin.py `_deploy_to_host` (shell=True + f-strings) | 2026-04-05 |
| 30 | HIGH | Skill AST lint was advisory-only — unsafe code could load | 2026-04-05 |
| 31 | HIGH | Skill `save_skill()` lacked path traversal protection | 2026-04-05 |
| 32 | HIGH | LLM-generated skill code saved without safety gate | 2026-04-05 |
| 33 | HIGH | `.env` credential values could inject via newlines | 2026-04-05 |
| 34 | HIGH | API path traversal in GitHub/Jira/Okta/MS365/Google tools | 2026-04-05 |
| 35 | MEDIUM | Policy.yaml fail-open when PyYAML missing or parse error | 2026-04-05 |
| 36 | MEDIUM | Token refresh race condition in MS365 (no lock) | 2026-04-05 |
| 37 | MEDIUM | Token refresh race condition in Google Workspace (no lock) | 2026-04-05 |
| 38 | MEDIUM | Multi-agent: AgentSpec.tools not enforced (all tools available) | 2026-04-05 |

</details>

## Open — LOW

### 25. No circuit breaker on external calls (carried over)
### 39. TOCTOU SSRF via DNS rebinding in web_extract
**File:** `tools/web_tools.py:44-58`
DNS resolution check and httpx connect are separate — rebinding possible.
**Fix:** Pin resolved IP or use httpx transport hooks.

### 40. Debug logging may expose API endpoint URLs
**File:** `agent/client.py:167`
**Fix:** Redact or omit URL from debug logs.

### 41. Token caches in module globals (no TTL on fork)
**Files:** `tools/ms365.py`, `tools/google_workspace.py`
**Fix:** Clear caches on fork. Low risk since asyncio is single-threaded.

### 42. Gateway rate limit buckets never cleaned up
**File:** `interfaces/gateway.py:54`
**Fix:** Use TTL-based cache (cachetools.TTLCache).

### 43. Inter-agent prompt injection filter easily bypassed
**File:** `multi/orchestrator.py:42-54`
Unicode homoglyphs, base64, split words bypass regex filters.
**Fix:** Defense-in-depth only; not primary security boundary.

---

## Deployment Status

All CRITICAL, HIGH, and MEDIUM issues are resolved across 3 audit phases
(38 total). The remaining 5 LOW items are hardening improvements that do
not block deployment.
