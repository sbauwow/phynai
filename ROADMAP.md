# Roadmap — PhynAI Agent

Last updated: 2026-04-05

## Vision

PhynAI is a compliance-first AI agent runtime for ops teams. One binary, any LLM, real tools, full audit trail. The roadmap is organized in horizons: what we're shipping now, what's next, and where we're headed.

---

## H1 — Now (Q2 2026): Stabilize & Ship

The foundation is solid. This horizon is about closing gaps, hardening what exists, and making phynai installable by strangers.

### Fleet deployment (NEW — `phynai admin`)
- [x] Admin manifest format (`phynai-manifest.yaml`) — roles, credentials, host assignments
- [x] `phynai admin init` — scaffold manifest
- [x] `phynai admin validate` — check manifest for errors
- [x] `phynai admin provision` — generate scoped bundles per role
- [x] `phynai admin deploy` — push bundles to target machines via SSH/SCP
- [x] `phynai admin audit` — show who has access to what
- [x] Policy enforcement at runtime (`~/.phynai/policy.yaml`)
- [x] `phynai admin rotate` — update a credential and redeploy affected hosts
- [ ] Ansible playbook export (`phynai admin deploy --format ansible`)
- [ ] Bundle encryption (age/sops) for transit security
- [ ] `phynai admin doctor --host <host>` — remote health check

### Security (3 LOW issues remaining)
- [ ] Circuit breaker on external calls (client.py, web_tools, ms365)
- [ ] Dependency pinning with hash verification
- [ ] Session token rotation documentation

### Multi-agent orchestration (just landed)
- [ ] Integration tests for pool, scheduler, queue, team
- [ ] Shared memory persistence (currently in-memory only)
- [ ] Task retry / dead-letter handling
- [ ] Document multi-agent API in ARCHITECTURE.md

### Corporate integrations — high priority
- [ ] **GitLab** — MRs, issues, pipelines, project management
- [ ] **PagerDuty** — Trigger/ack/resolve incidents, on-call schedule
- [ ] **Confluence** — Search/read/create pages, space navigation
- [ ] **Notion** — Search/read/update pages and databases
- [ ] **SQL** — Query Postgres, MySQL, MSSQL (read-only default, parameterized)

### Developer experience
- [ ] `phynai doctor` — diagnose env, credentials, connectivity
- [ ] `phynai replay <session-id>` — replay journal entries for debugging
- [ ] Improve error messages for missing credentials (currently silent)
- [ ] PyPI package (`pip install phynai-agent`)

### Testing
- [ ] Coverage target: 80%+ on agent core and tool runtime
- [ ] E2E test harness (mock LLM, real tools, assert on journal)
- [ ] CI pipeline (GitHub Actions: lint, typecheck, test, security scan)

---

## H2 — Next (Q3–Q4 2026): Expand the Surface

With the core stable, expand where phynai runs and how it connects.

### New interfaces
- [ ] **Discord gateway** — bot with slash commands (mirrors Slack pattern)
- [ ] **Telegram gateway** — bot with inline keyboard support
- [ ] **HTTP API** — REST endpoint for programmatic access (webhook-driven)
- [ ] **MCP server mode** — expose phynai tools as an MCP server so other agents can call them

### Persistent memory
- [ ] Cross-session memory store (SQLite-backed, scoped per user)
- [ ] Skill learning — agent can save and recall procedures between sessions
- [ ] Context compression for long conversations (summary + key facts)

### Cloud & infrastructure integrations
- [ ] **AWS** — EC2/ECS/Lambda/RDS describe, CloudWatch logs, S3 list/read
- [ ] **GCP** — Compute/GKE/Cloud Run, Cloud Logging, GCS
- [ ] **Azure** — VM/AKS, Monitor queries, Blob Storage (extends MS365 auth)
- [ ] **Datadog** — Metrics, monitors, log search
- [ ] **Grafana** — Data sources, alerts, dashboard panels

### Security & compliance
- [ ] **LDAP / Active Directory** — User lookup, group membership
- [ ] **Vault / Secrets Manager** — Scoped KV read (token/AppRole auth)
- [ ] RBAC — role-based tool access (admin, operator, viewer)
- [ ] Audit log export (JSON Lines, Splunk/ELK-friendly)
- [ ] SOC 2 Type I documentation prep

### Agent capabilities
- [ ] Streaming responses (SSE for HTTP, chunked for CLI)
- [ ] Tool chaining — agent plans multi-step workflows before executing
- [ ] Confirmation workflows — escalate MEDIUM/HIGH risk actions to Slack/email
- [ ] File upload/download through gateways
- [ ] Image/vision input (pass screenshots to multimodal LLMs)

---

## H3 — Horizon (2027): Platform

PhynAI becomes a platform that teams deploy, not just a CLI they install.

### Multi-tenant
- [ ] Team workspaces with isolated journals, credentials, and policies
- [ ] SSO integration (SAML/OIDC) for enterprise auth
- [ ] Per-team cost budgets and usage dashboards
- [ ] Admin console (web UI) for managing agents, policies, and audit trails

### Plugin ecosystem
- [ ] Plugin spec — third-party tool packages installable via `phynai install <plugin>`
- [ ] Plugin registry (hosted or self-hosted)
- [ ] Sandboxed plugin execution (subprocess isolation, capability-based permissions)

### Advanced orchestration
- [ ] DAG-based workflow engine (define multi-agent pipelines as YAML)
- [ ] Event-driven triggers (webhook → agent task, schedule → agent task)
- [ ] Human-in-the-loop approval gates for critical workflows
- [ ] Cross-agent communication protocol (agents can delegate to each other)

### Hosted offering
- [ ] Managed PhynAI Cloud — zero-infra deployment for teams
- [ ] Bring-your-own-key model (customers supply LLM API keys)
- [ ] Usage-based billing tied to the cost ledger
- [ ] SLA and uptime guarantees

### Ops-specific capabilities
- [ ] Runbook execution — parse and execute runbooks from Confluence/Notion
- [ ] Incident response automation — triage, page, remediate, postmortem
- [ ] Change management — RFC creation, approval tracking, rollback triggers
- [ ] Capacity planning — pull metrics, forecast, recommend scaling actions

---

## Nice to have (unprioritized)

Useful for specific teams or advanced use cases. Pull into a horizon when demand is clear.

- [ ] **OpsGenie** — Incident lifecycle, on-call routing
- [ ] **Terraform** — State inspection, plan parsing, drift detection (read-only)
- [ ] **Redis** — Key inspection, TTL checks, INFO stats, slow log
- [ ] **DNS (Cloudflare / Route53)** — Record lookup, zone listing, CRUD
- [ ] **S3 / GCS** — Dedicated object storage tools (separate from full cloud)
- [ ] Voice interface (Whisper STT → agent → TTS)
- [ ] VS Code extension (agent in sidebar)
- [ ] Jupyter kernel (agent as notebook assistant)
- [ ] Mobile companion app (iOS/Android, push notifications for approvals)

---

## Design principles

These apply to everything on the roadmap:

1. **No SDKs** — every integration uses httpx against the vendor's REST API directly
2. **Conditional loading** — tools only register when credentials are present in env
3. **Read-first** — default to read-only; mutating tools get `Risk.MEDIUM` or higher
4. **Setup wizard** — each integration gets a `phynai setup <name>` section
5. **Zero core bloat** — no integration adds to the base install's dependency list
6. **Journal everything** — every tool call, every LLM call, every cost event
7. **Contracts at boundaries** — typed dataclasses for all cross-layer communication
