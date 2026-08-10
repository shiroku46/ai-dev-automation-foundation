#!/usr/bin/env python3
"""Guard and launch one-time interactive provider login without handling credentials."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable

from scripts.auth_detect import DetectionResult, detect_authentication

PROVIDERS = frozenset({"github", "vercel", "cloudflare"})
DEFAULT_TIMEOUT_SECONDS = 600

_EXECUTABLES: dict[str, str] = {
    "github": "gh",
    "vercel": "vercel",
    "cloudflare": "wrangler",
}

_LOGIN_ARGS: dict[str, tuple[str, ...]] = {
    "github": (
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--web",
        "--git-protocol",
        "https",
        "--skip-ssh-key",
    ),
    "vercel": ("login",),
    "cloudflare": ("login", "--use-keyring"),
}

_ACTIONS: dict[str, str] = {
    "github": "authenticate_github_in_browser",
    "vercel": "authenticate_vercel_device_flow",
    "cloudflare": "authenticate_cloudflare_oauth_with_keyring",
}


@dataclass(frozen=True)
class SetupResult:
    provider: str
    status: str
    human_action_required: bool
    action: str


def _result(provider: str, status: str, human: bool, action: str) -> SetupResult:
    return SetupResult(
        provider=provider,
        status=status,
        human_action_required=human,
        action=action,
    )


def setup_authentication(
    provider: str,
    *,
    interactive: bool = False,
    detector: Callable[[str], DetectionResult] = detect_authentication,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    is_tty: Callable[[], bool] = lambda: sys.stdin.isatty() and sys.stdout.isatty(),
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> SetupResult:
    """Detect authentication and, only when authorized, launch the fixed login flow."""
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("unsupported provider")
    if not isinstance(timeout_seconds, int) or not (30 <= timeout_seconds <= 1800):
        raise ValueError("invalid timeout")

    before = detector(provider)
    if before.authenticated:
        return _result(provider, "ready", False, "none")
    if not before.executable_present:
        return _result(provider, "install_required", True, "install_provider_cli")

    if not interactive:
        return _result(provider, "interaction_required", True, _ACTIONS[provider])
    if not is_tty():
        return _result(provider, "interactive_terminal_required", True, _ACTIONS[provider])

    executable = which(_EXECUTABLES[provider])
    if not executable:
        return _result(provider, "install_required", True, "install_provider_cli")

    command = [executable, *_LOGIN_ARGS[provider]]
    try:
        completed = runner(
            command,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _result(provider, "login_timeout", True, _ACTIONS[provider])
    except (OSError, subprocess.SubprocessError):
        return _result(provider, "login_failed", True, _ACTIONS[provider])

    if completed.returncode != 0:
        return _result(provider, "login_failed", True, _ACTIONS[provider])

    after = detector(provider)
    if after.authenticated:
        return _result(provider, "ready", False, "none")
    return _result(provider, "verification_failed", True, _ACTIONS[provider])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = setup_authentication(
            args.provider,
            interactive=args.interactive,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError:
        print(
            json.dumps(
                {
                    "status": "error",
                    "human_action_required": False,
                    "action": "fix_invocation",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
