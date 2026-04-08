"""Admin CLI for fleet deployment — provision, deploy, and manage PhynAI across machines.

Usage:
    phynai admin init                          Create manifest scaffold
    phynai admin validate                      Validate manifest
    phynai admin provision --role <name>       Generate bundle for a role
    phynai admin provision --all               Generate all role bundles
    phynai admin deploy --bundle <dir> --host <host>  Push bundle to host
    phynai admin deploy --all                  Push all bundles per manifest
    phynai admin audit                         Show who has access to what
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── ANSI helpers ──────────────────────────────────────────────────────────

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


def _ok(text: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {text}")


def _warn(text: str) -> None:
    print(f"  {_YELLOW}⚠{_RESET} {text}")


def _err(text: str) -> None:
    print(f"  {_RED}✗{_RESET} {text}")


def _info(text: str) -> None:
    print(f"  {_DIM}{text}{_RESET}")


# ── Credential validation ─────────────────────────────────────────────────

import re

_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _validate_env_entry(key: str, value: str) -> None:
    """Reject .env keys/values that could inject additional variables.

    Prevents:
      - Key injection: keys must be uppercase + underscores only
      - Value injection: values cannot contain newlines (would create extra env vars)
      - Dangerous overrides: block PATH, LD_PRELOAD, PYTHONPATH, etc.
    """
    _BLOCKED_KEYS = {
        "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH",
        "PYTHONSTARTUP", "PYTHONHOME", "HOME", "SHELL", "USER",
    }

    if not _ENV_KEY_RE.match(key):
        _err(f"Invalid credential key: {key!r} — must match [A-Z_][A-Z0-9_]*")
        sys.exit(1)

    if key in _BLOCKED_KEYS:
        _err(f"Blocked credential key: {key!r} — overriding system vars is not allowed")
        sys.exit(1)

    if "\n" in value or "\r" in value:
        _err(f"Credential value for {key!r} contains newlines — possible injection")
        sys.exit(1)


# ── YAML helpers ──────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file. Uses PyYAML if available, else a minimal parser."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Minimal fallback — enough for our structured manifest
        _err("PyYAML not installed. Run: uv pip install pyyaml")
        sys.exit(1)


def _dump_yaml(data: dict[str, Any], path: Path) -> None:
    """Write YAML file."""
    try:
        import yaml
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    except ImportError:
        _err("PyYAML not installed. Run: uv pip install pyyaml")
        sys.exit(1)


# ── Manifest scaffold ────────────────────────────────────────────────────

MANIFEST_SCAFFOLD = """\
# phynai-manifest.yaml — Admin eyes only. Never commit this file.
# Docs: docs/admin-deployment.md
version: 1

# ── Organization defaults ───────────────────────────────
defaults:
  provider: anthropic
  model: claude-sonnet-4-6
  max_iterations: 50
  log_level: warning

# ── Credentials ─────────────────────────────────────────
# All org-wide API keys and tokens. Sliced per-role during provisioning.
credentials:
  provider:
    ANTHROPIC_API_KEY: ""           # or OPENAI_API_KEY, etc.
  # google:
  #   GOOGLE_CLIENT_ID: ""
  #   GOOGLE_CLIENT_SECRET: ""
  #   GOOGLE_REFRESH_TOKEN: ""
  # github:
  #   GITHUB_TOKEN: ""
  # jira:
  #   JIRA_URL: ""
  #   JIRA_EMAIL: ""
  #   JIRA_API_TOKEN: ""
  # slack:
  #   SLACK_BOT_TOKEN: ""
  #   SLACK_APP_TOKEN: ""
  # ms365:
  #   MICROSOFT_TENANT_ID: ""
  #   MICROSOFT_CLIENT_ID: ""
  #   MICROSOFT_CLIENT_SECRET: ""
  # okta:
  #   OKTA_ORG_URL: ""
  #   OKTA_API_TOKEN: ""

# ── Roles ───────────────────────────────────────────────
roles:
  default:
    description: "Default role — basic access"
    integrations:
      - provider
    tools:
      allow: [terminal, file_tools, web_tools]
    risk_ceiling: medium
    require_confirmation: [terminal.execute]

# ── Host assignments ────────────────────────────────────
# hostname-or-IP: role  (or user@host: role)
hosts: {}
"""


def cmd_init(args: list[str]) -> None:
    """Create a manifest scaffold."""
    manifest_path = Path("phynai-manifest.yaml")
    if manifest_path.exists():
        _err(f"{manifest_path} already exists. Delete it first or edit directly.")
        sys.exit(1)

    manifest_path.write_text(MANIFEST_SCAFFOLD)
    manifest_path.chmod(0o600)
    _ok(f"Created {manifest_path}")
    _info("Edit it with your credentials, roles, and host assignments.")
    _info("Then run: phynai admin provision --all")

    # Suggest adding to gitignore
    gitignore = Path(".gitignore")
    if gitignore.exists():
        content = gitignore.read_text()
        if "phynai-manifest" not in content:
            _warn("Add 'phynai-manifest.yaml' to .gitignore — it contains secrets.")
    else:
        _warn("No .gitignore found. Create one and add 'phynai-manifest.yaml'.")


def _find_manifest() -> Path:
    """Find the manifest file."""
    candidates = [
        Path("phynai-manifest.yaml"),
        Path("phynai-manifest.yml"),
        Path.home() / ".phynai" / "manifest.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    _err("No manifest found. Run: phynai admin init")
    sys.exit(1)


def _validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate manifest structure. Returns list of errors."""
    errors: list[str] = []

    if manifest.get("version") != 1:
        errors.append("Missing or unsupported 'version' (expected: 1)")

    credentials = manifest.get("credentials", {})
    if not isinstance(credentials, dict):
        errors.append("'credentials' must be a dict")

    roles = manifest.get("roles", {})
    if not isinstance(roles, dict):
        errors.append("'roles' must be a dict")
    else:
        for role_name, role_def in roles.items():
            if not isinstance(role_def, dict):
                errors.append(f"Role '{role_name}' must be a dict")
                continue

            integrations = role_def.get("integrations", [])
            if not isinstance(integrations, list):
                errors.append(f"Role '{role_name}': 'integrations' must be a list")
            else:
                for integ in integrations:
                    if integ not in credentials and integ != "provider":
                        # provider is always available from defaults
                        if integ not in credentials:
                            errors.append(
                                f"Role '{role_name}' references integration "
                                f"'{integ}' but no credentials defined for it"
                            )

            tools = role_def.get("tools", {})
            if not isinstance(tools, dict):
                errors.append(f"Role '{role_name}': 'tools' must be a dict with 'allow'")

            ceiling = role_def.get("risk_ceiling", "medium")
            if ceiling not in ("low", "medium", "high", "critical"):
                errors.append(
                    f"Role '{role_name}': invalid risk_ceiling '{ceiling}' "
                    f"(must be low/medium/high/critical)"
                )

    hosts = manifest.get("hosts", {})
    if not isinstance(hosts, dict):
        errors.append("'hosts' must be a dict mapping host → role")
    else:
        for host, role in hosts.items():
            if role not in roles:
                errors.append(f"Host '{host}' assigned role '{role}' which is not defined")

    return errors


def cmd_validate(args: list[str]) -> None:
    """Validate the manifest."""
    manifest_path = _find_manifest()
    manifest = _load_yaml(manifest_path)

    errors = _validate_manifest(manifest)
    if errors:
        _err(f"Manifest has {len(errors)} error(s):")
        for e in errors:
            _err(f"  {e}")
        sys.exit(1)
    else:
        _ok(f"Manifest is valid ({manifest_path})")
        roles = manifest.get("roles", {})
        hosts = manifest.get("hosts", {})
        _info(f"{len(roles)} role(s), {len(hosts)} host assignment(s)")


def _provision_role(
    role_name: str,
    role_def: dict[str, Any],
    credentials: dict[str, dict[str, str]],
    defaults: dict[str, Any],
    output_dir: Path,
) -> None:
    """Generate a deployment bundle for a single role."""
    bundle_dir = output_dir / f"bundle-{role_name}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # ── .env — scoped credentials only ──────────────────────────
    env_lines: list[str] = [
        f"# PhynAI deployment bundle — role: {role_name}",
        f"# Generated by: phynai admin provision",
        f"# DO NOT EDIT — regenerate with: phynai admin provision --role {role_name}",
        "",
    ]

    # Provider config
    provider = defaults.get("provider", "anthropic")
    model = role_def.get("overrides", {}).get("model", defaults.get("model", "claude-sonnet-4-6"))
    env_lines.append(f"PHYNAI_PROVIDER={provider}")
    env_lines.append(f"PHYNAI_MODEL={model}")
    env_lines.append("")

    # Credentials for allowed integrations only
    integrations = role_def.get("integrations", [])
    for integ_name in integrations:
        integ_creds = credentials.get(integ_name, {})
        if integ_creds:
            env_lines.append(f"# {integ_name}")
            for key, value in integ_creds.items():
                if key.startswith("_"):
                    continue  # skip meta keys like _vault
                _validate_env_entry(key, str(value))
                env_lines.append(f"{key}={value}")
            env_lines.append("")

    env_path = bundle_dir / ".env"
    env_path.write_text("\n".join(env_lines) + "\n")
    env_path.chmod(0o600)

    # ── config.yaml — agent settings ────────────────────────────
    overrides = role_def.get("overrides", {})
    config = {
        "provider": provider,
        "model": model,
        "max_iterations": overrides.get(
            "max_iterations", defaults.get("max_iterations", 50)
        ),
        "log_level": overrides.get(
            "log_level", defaults.get("log_level", "warning")
        ),
        "role": role_name,
        "managed": True,  # signals this is an admin-managed deployment
    }
    _dump_yaml(config, bundle_dir / "config.yaml")

    # ── policy.yaml — tool access control ───────────────────────
    tools = role_def.get("tools", {})
    policy = {
        "tools": {
            "allow": tools.get("allow", []),
            "deny": tools.get("deny", []),
        },
        "risk_ceiling": role_def.get("risk_ceiling", "medium"),
        "require_confirmation": role_def.get("require_confirmation", []),
        "credential_source": "bundle",
    }
    _dump_yaml(policy, bundle_dir / "policy.yaml")

    _ok(f"Bundle for '{role_name}' → {bundle_dir}/")
    _info(f"  Integrations: {', '.join(integrations)}")
    _info(f"  Tools: {', '.join(tools.get('allow', []))}")
    _info(f"  Risk ceiling: {role_def.get('risk_ceiling', 'medium')}")
    _info(f"  Model: {model}")


def cmd_provision(args: list[str]) -> None:
    """Generate deployment bundles."""
    manifest_path = _find_manifest()
    manifest = _load_yaml(manifest_path)

    errors = _validate_manifest(manifest)
    if errors:
        _err(f"Manifest has {len(errors)} error(s) — fix them first.")
        for e in errors:
            _err(f"  {e}")
        sys.exit(1)

    credentials = manifest.get("credentials", {})
    defaults = manifest.get("defaults", {})
    roles = manifest.get("roles", {})

    # Parse args
    output_dir = Path("./bundles")
    role_filter = None
    provision_all = False

    i = 0
    while i < len(args):
        if args[i] == "--role" and i + 1 < len(args):
            role_filter = args[i + 1]
            i += 2
        elif args[i] == "--all":
            provision_all = True
            i += 1
        elif args[i] == "--output" and i + 1 < len(args):
            output_dir = Path(args[i + 1])
            i += 2
        else:
            i += 1

    if not role_filter and not provision_all:
        _err("Specify --role <name> or --all")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    if role_filter:
        if role_filter not in roles:
            _err(f"Role '{role_filter}' not found in manifest")
            _info(f"Available: {', '.join(roles.keys())}")
            sys.exit(1)
        _provision_role(role_filter, roles[role_filter], credentials, defaults, output_dir)
    else:
        for name, role_def in roles.items():
            _provision_role(name, role_def, credentials, defaults, output_dir)

    print()
    _ok(f"Bundles ready in {output_dir}/")
    _info("Deploy with: phynai admin deploy --bundle <dir> --host <host>")


def cmd_deploy(args: list[str]) -> None:
    """Deploy a bundle to target machines via SSH/SCP."""
    manifest_path = _find_manifest()
    manifest = _load_yaml(manifest_path)

    hosts = manifest.get("hosts", {})
    roles = manifest.get("roles", {})

    # Parse args
    bundle_dir = None
    target_host = None
    target_user = None
    deploy_all = False
    dry_run = False

    i = 0
    while i < len(args):
        if args[i] == "--bundle" and i + 1 < len(args):
            bundle_dir = Path(args[i + 1])
            i += 2
        elif args[i] == "--host" and i + 1 < len(args):
            target_host = args[i + 1]
            i += 2
        elif args[i] == "--user" and i + 1 < len(args):
            target_user = args[i + 1]
            i += 2
        elif args[i] == "--all":
            deploy_all = True
            i += 1
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            i += 1

    if deploy_all:
        _deploy_all(manifest, dry_run)
        return

    if not bundle_dir or not target_host:
        _err("Specify --bundle <dir> --host <host>  or  --all")
        sys.exit(1)

    if not bundle_dir.is_dir():
        _err(f"Bundle directory not found: {bundle_dir}")
        sys.exit(1)

    _deploy_to_host(bundle_dir, target_host, target_user, dry_run)


_HOST_RE = __import__("re").compile(r"^[a-zA-Z0-9._:-]+$")


def _validate_host(host: str) -> str:
    """Validate host/user strings against a strict allowlist to prevent injection."""
    if not _HOST_RE.match(host):
        _err(f"Invalid hostname: {host!r} — must match [a-zA-Z0-9._:-]")
        sys.exit(1)
    return host


def _deploy_to_host(
    bundle_dir: Path,
    host: str,
    user: str | None,
    dry_run: bool,
) -> None:
    """SCP a bundle to a remote host.

    All subprocess calls use shell=False with argv lists to prevent
    shell injection via manifest-controlled hostnames.
    """
    # Parse user@host format
    if "@" in host and not user:
        user, host = host.rsplit("@", 1)

    # Validate against injection
    _validate_host(host)
    if user:
        _validate_host(user)

    target = f"{user}@{host}" if user else host
    remote_path = "~/.phynai/"

    # Resolve bundle_dir to prevent path traversal
    bundle_dir = bundle_dir.resolve()
    if not bundle_dir.is_dir():
        _err(f"Bundle directory not found: {bundle_dir}")
        return

    # Collect files to transfer
    bundle_files = list(bundle_dir.iterdir())
    if not bundle_files:
        _err(f"Bundle directory is empty: {bundle_dir}")
        return

    # All commands use argv lists (shell=False) — no injection possible
    steps: list[tuple[str, list[str]]] = [
        ("mkdir", ["ssh", target, "mkdir", "-p", remote_path]),
        ("scp", ["scp", "-r"] + [str(f) for f in bundle_files] + [f"{target}:{remote_path}"]),
        ("chmod", ["ssh", target, "chmod", "600", f"{remote_path}.env"]),
        ("chmod-config", ["ssh", target, "chmod", "644",
                          f"{remote_path}config.yaml", f"{remote_path}policy.yaml"]),
    ]

    if dry_run:
        for label, argv in steps:
            _info(f"[dry-run] {' '.join(argv)}")
        return

    print(f"  Deploying to {target}...")
    for label, argv in steps:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            # Don't log full command to avoid leaking paths in error output
            _err(f"Step '{label}' failed for {target}")
            stderr = result.stderr.strip()
            if stderr:
                _err(f"  {stderr[:200]}")
            return

    _ok(f"Deployed to {target}:{remote_path}")


def _deploy_all(manifest: dict[str, Any], dry_run: bool) -> None:
    """Deploy all bundles to all assigned hosts."""
    hosts = manifest.get("hosts", {})
    if not hosts:
        _err("No hosts defined in manifest")
        sys.exit(1)

    bundles_dir = Path("./bundles")
    if not bundles_dir.is_dir():
        _err("No bundles directory. Run: phynai admin provision --all")
        sys.exit(1)

    for host_spec, role_name in hosts.items():
        bundle_dir = bundles_dir / f"bundle-{role_name}"
        if not bundle_dir.is_dir():
            _warn(f"Bundle for role '{role_name}' not found — skipping {host_spec}")
            continue

        user = None
        host = host_spec
        if "@" in host_spec:
            user, host = host_spec.rsplit("@", 1)

        _deploy_to_host(bundle_dir, host, user, dry_run)

    print()
    _ok(f"Deployed to {len(hosts)} host(s)")


def cmd_rotate(args: list[str]) -> None:
    """Rotate a credential, re-provision affected bundles, and redeploy.

    Usage:
        phynai admin rotate --credential github
        phynai admin rotate --credential github --deploy
        phynai admin rotate --credential github --deploy --dry-run
        phynai admin rotate --credential github --value ghp_newtoken123
    """
    manifest_path = _find_manifest()
    manifest = _load_yaml(manifest_path)

    errors = _validate_manifest(manifest)
    if errors:
        _err(f"Manifest has {len(errors)} error(s) — fix them first.")
        sys.exit(1)

    credentials = manifest.get("credentials", {})
    roles = manifest.get("roles", {})
    defaults = manifest.get("defaults", {})
    hosts = manifest.get("hosts", {})

    # Parse args
    cred_name = None
    new_values: dict[str, str] = {}
    do_deploy = False
    dry_run = False
    output_dir = Path("./bundles")

    i = 0
    while i < len(args):
        if args[i] == "--credential" and i + 1 < len(args):
            cred_name = args[i + 1]
            i += 2
        elif args[i] == "--value" and i + 1 < len(args):
            # Format: KEY=VALUE or just VALUE (for single-key credentials)
            raw = args[i + 1]
            if "=" in raw:
                k, _, v = raw.partition("=")
                new_values[k.strip()] = v.strip()
            else:
                new_values["_single"] = raw
            i += 2
        elif args[i] == "--deploy":
            do_deploy = True
            i += 1
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif args[i] == "--output" and i + 1 < len(args):
            output_dir = Path(args[i + 1])
            i += 2
        else:
            i += 1

    if not cred_name:
        _err("Specify --credential <name>")
        _info(f"Available credentials: {', '.join(credentials.keys())}")
        sys.exit(1)

    if cred_name not in credentials:
        _err(f"Credential group '{cred_name}' not found in manifest")
        _info(f"Available: {', '.join(credentials.keys())}")
        sys.exit(1)

    cred_group = credentials[cred_name]
    cred_keys = [k for k in cred_group if not k.startswith("_")]

    print(f"\n{_CYAN}{_BOLD}◆ Rotate: {cred_name}{_RESET}\n")

    # ── Step 1: Collect new values ──────────────────────────────
    if new_values.get("_single") and len(cred_keys) == 1:
        # Single-key credential, value provided inline
        cred_group[cred_keys[0]] = new_values["_single"]
        _ok(f"Updated {cred_keys[0]}")
    elif new_values:
        # Explicit KEY=VALUE pairs
        for k, v in new_values.items():
            if k == "_single":
                continue
            if k in cred_group:
                cred_group[k] = v
                _ok(f"Updated {k}")
            else:
                _warn(f"Key '{k}' not in {cred_name} — skipping")
    else:
        # Interactive: prompt for each key
        _info(f"Enter new values (Enter to keep current):")
        for key in cred_keys:
            current = cred_group[key]
            masked = _mask_value(current)
            import getpass
            try:
                new_val = getpass.getpass(f"  {_YELLOW}{key} [{masked}]: {_RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                sys.exit(0)
            if new_val:
                cred_group[key] = new_val
                _ok(f"Updated {key}")
            else:
                _info(f"Kept {key}")

    # ── Step 2: Save manifest ───────────────────────────────────
    manifest["credentials"][cred_name] = cred_group
    _dump_yaml(manifest, manifest_path)
    manifest_path.chmod(0o600)
    _ok(f"Manifest updated: {manifest_path}")

    # ── Step 3: Find affected roles ─────────────────────────────
    affected_roles: list[str] = []
    for role_name, role_def in roles.items():
        if cred_name in role_def.get("integrations", []):
            affected_roles.append(role_name)

    if not affected_roles:
        _warn(f"No roles reference '{cred_name}' — nothing to re-provision")
        return

    _info(f"Affected roles: {', '.join(affected_roles)}")

    # ── Step 4: Re-provision affected bundles ───────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    for role_name in affected_roles:
        _provision_role(role_name, roles[role_name], credentials, defaults, output_dir)

    # ── Step 5: Find affected hosts ─────────────────────────────
    affected_hosts: dict[str, str] = {
        host: role for host, role in hosts.items() if role in affected_roles
    }

    if not affected_hosts:
        _info("No hosts assigned to affected roles — bundles updated locally only")
        return

    _info(f"Affected hosts: {len(affected_hosts)}")
    for host, role in affected_hosts.items():
        _info(f"  {host} ({role})")

    # ── Step 6: Deploy if requested ─────────────────────────────
    if do_deploy:
        print()
        for host_spec, role_name in affected_hosts.items():
            bundle_dir = output_dir / f"bundle-{role_name}"
            user = None
            host = host_spec
            if "@" in host_spec:
                user, host = host_spec.rsplit("@", 1)
            _deploy_to_host(bundle_dir, host, user, dry_run)

        print()
        verb = "would deploy" if dry_run else "deployed"
        _ok(f"Rotation complete — {verb} to {len(affected_hosts)} host(s)")
    else:
        print()
        _ok("Bundles re-provisioned. Deploy with:")
        _info(f"  phynai admin deploy --all")
        _info(f"  # or: phynai admin rotate --credential {cred_name} --deploy")


def _mask_value(value: str) -> str:
    """Mask a credential value for display."""
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def cmd_audit(args: list[str]) -> None:
    """Show who has access to what."""
    manifest_path = _find_manifest()
    manifest = _load_yaml(manifest_path)

    roles = manifest.get("roles", {})
    hosts = manifest.get("hosts", {})

    print(f"\n{_CYAN}{_BOLD}PhynAI Fleet Audit{_RESET}\n")

    # Roles summary
    print(f"{_BOLD}Roles:{_RESET}")
    for name, role_def in roles.items():
        desc = role_def.get("description", "")
        integrations = role_def.get("integrations", [])
        ceiling = role_def.get("risk_ceiling", "medium")
        tools = role_def.get("tools", {}).get("allow", [])
        host_count = sum(1 for r in hosts.values() if r == name)

        print(f"\n  {_GREEN}{name}{_RESET} — {desc}")
        print(f"    Integrations: {', '.join(integrations)}")
        print(f"    Tools:        {', '.join(tools)}")
        print(f"    Risk ceiling: {ceiling}")
        print(f"    Hosts:        {host_count}")

    # Host assignments
    if hosts:
        print(f"\n{_BOLD}Host Assignments:{_RESET}")
        # Group by role
        by_role: dict[str, list[str]] = {}
        for host, role in hosts.items():
            by_role.setdefault(role, []).append(host)

        for role, host_list in by_role.items():
            print(f"\n  {_GREEN}{role}{_RESET}:")
            for h in sorted(host_list):
                print(f"    {h}")
    else:
        _warn("No hosts assigned yet")

    print()


# ── CLI entry point ───────────────────────────────────────────────────────

COMMANDS = {
    "init": cmd_init,
    "validate": cmd_validate,
    "provision": cmd_provision,
    "deploy": cmd_deploy,
    "rotate": cmd_rotate,
    "audit": cmd_audit,
}


def run_admin(args: list[str]) -> None:
    """Entry point for `phynai admin <subcommand>`."""
    if not args:
        print(f"\n{_CYAN}{_BOLD}PhynAI Admin — Fleet Deployment{_RESET}\n")
        print("  Commands:")
        print(f"    {_BOLD}init{_RESET}        Create manifest scaffold")
        print(f"    {_BOLD}validate{_RESET}    Check manifest for errors")
        print(f"    {_BOLD}provision{_RESET}   Generate deployment bundles")
        print(f"    {_BOLD}deploy{_RESET}      Push bundles to target machines")
        print(f"    {_BOLD}rotate{_RESET}      Rotate a credential and redeploy")
        print(f"    {_BOLD}audit{_RESET}       Show who has access to what")
        print()
        print("  Workflow:")
        print("    1. phynai admin init")
        print("    2. Edit phynai-manifest.yaml with credentials + roles")
        print("    3. phynai admin provision --all")
        print("    4. phynai admin deploy --all")
        print()
        print("  Docs: docs/admin-deployment.md")
        print()
        return

    subcommand = args[0]
    if subcommand in COMMANDS:
        COMMANDS[subcommand](args[1:])
    else:
        _err(f"Unknown command: {subcommand}")
        _info(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)
