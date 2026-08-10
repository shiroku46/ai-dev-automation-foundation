#!/usr/bin/env python3
"""Plan the safest supported authentication setup without touching credentials."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Mapping

SCHEMA_VERSION = 1
STATES = frozenset({"automatic", "interactive_once", "manual_required"})
PROVIDERS = frozenset({"github", "vercel", "cloudflare"})
CLOUDFLARE_ROUTES = frozenset({"local", "github_actions"})

_ALLOWED_CAPABILITIES: dict[str, frozenset[str]] = {
    "github": frozenset({"github_app_connected", "github_cli_authenticated"}),
    "vercel": frozenset({"git_integration_connected", "oidc_available", "cli_authenticated"}),
    "cloudflare": frozenset(
        {
            "wrangler_oauth_authenticated",
            "api_token_configured",
            "account_id_configured",
        }
    ),
}


class CapabilityError(ValueError):
    """Raised when a capability snapshot is unsafe or unsupported."""


@dataclass(frozen=True)
class AuthPlan:
    schema_version: int
    provider: str
    route: str
    state: str
    human_action_required: bool
    next_action: str
    rationale_code: str


def _validate_capabilities(provider: str, capabilities: Mapping[str, object]) -> dict[str, bool]:
    if provider not in PROVIDERS:
        raise CapabilityError(f"unsupported provider: {provider}")
    if not isinstance(capabilities, Mapping):
        raise CapabilityError("capabilities must be a JSON object")

    allowed = _ALLOWED_CAPABILITIES[provider]
    unknown = sorted(set(capabilities) - allowed)
    if unknown:
        raise CapabilityError("unsupported capability keys: " + ", ".join(unknown))

    normalized: dict[str, bool] = {}
    for key in sorted(allowed):
        value = capabilities.get(key, False)
        if not isinstance(value, bool):
            raise CapabilityError(f"capability {key} must be boolean")
        normalized[key] = value
    return normalized


def plan_authentication(
    provider: str,
    *,
    route: str = "default",
    capabilities: Mapping[str, object] | None = None,
) -> AuthPlan:
    """Return a deterministic non-secret authentication setup plan."""
    provider = provider.strip().lower()
    capabilities = capabilities or {}
    caps = _validate_capabilities(provider, capabilities)

    if provider == "github":
        if route != "default":
            raise CapabilityError("github supports only route=default")
        if caps["github_app_connected"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_connected_github_app",
                "github_app_connected",
            )
        if caps["github_cli_authenticated"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_existing_github_cli_session",
                "github_cli_authenticated",
            )
        return AuthPlan(
            SCHEMA_VERSION,
            provider,
            route,
            "interactive_once",
            True,
            "connect_github_app",
            "github_app_connection_required",
        )

    if provider == "vercel":
        if route != "default":
            raise CapabilityError("vercel supports only route=default")
        if caps["git_integration_connected"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_vercel_git_integration",
                "vercel_git_integration_connected",
            )
        if caps["oidc_available"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_vercel_oidc",
                "vercel_oidc_available",
            )
        if caps["cli_authenticated"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_existing_vercel_cli_session",
                "vercel_cli_authenticated",
            )
        return AuthPlan(
            SCHEMA_VERSION,
            provider,
            route,
            "interactive_once",
            True,
            "authenticate_vercel_interactively",
            "vercel_interactive_auth_required",
        )

    if route not in CLOUDFLARE_ROUTES:
        raise CapabilityError("cloudflare route must be local or github_actions")
    if route == "local":
        if caps["wrangler_oauth_authenticated"]:
            return AuthPlan(
                SCHEMA_VERSION,
                provider,
                route,
                "automatic",
                False,
                "use_existing_wrangler_oauth_session",
                "wrangler_oauth_authenticated",
            )
        return AuthPlan(
            SCHEMA_VERSION,
            provider,
            route,
            "interactive_once",
            True,
            "run_wrangler_login_interactively",
            "wrangler_oauth_login_required",
        )

    if caps["api_token_configured"] and caps["account_id_configured"]:
        return AuthPlan(
            SCHEMA_VERSION,
            provider,
            route,
            "automatic",
            False,
            "use_existing_scoped_cloudflare_ci_credentials",
            "cloudflare_ci_credentials_configured",
        )
    return AuthPlan(
        SCHEMA_VERSION,
        provider,
        route,
        "manual_required",
        True,
        "create_and_store_scoped_cloudflare_ci_credentials_once",
        "cloudflare_ci_token_prerequisite_missing",
    )


def _parse_capabilities(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CapabilityError("capabilities JSON is malformed") from exc
    if not isinstance(value, dict):
        raise CapabilityError("capabilities must be a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--route", default="default")
    parser.add_argument("--capabilities-json", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        capabilities = _parse_capabilities(args.capabilities_json)
        plan = plan_authentication(args.provider, route=args.route, capabilities=capabilities)
    except CapabilityError as exc:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "error",
                    "error": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    payload = asdict(plan)
    if payload["state"] not in STATES:
        raise AssertionError("invalid authentication state")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
