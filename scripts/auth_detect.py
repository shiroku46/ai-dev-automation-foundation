#!/usr/bin/env python3
"""Detect usable local CLI authentication without reading credential or identity output."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable, Sequence

PROVIDERS = frozenset({"github", "vercel", "cloudflare"})
DEFAULT_TIMEOUT_SECONDS = 15

_COMMANDS: dict[str, tuple[str, ...]] = {
    "github": ("auth", "status", "--active", "--hostname", "github.com"),
    "vercel": ("whoami",),
    "cloudflare": ("whoami", "--json"),
}
_EXECUTABLES: dict[str, str] = {
    "github": "gh",
    "vercel": "vercel",
    "cloudflare": "wrangler",
}


@dataclass(frozen=True)
class DetectionResult:
    provider: str
    executable_present: bool
    authenticated: bool


def detect_authentication(
    provider: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> DetectionResult:
    """Return only presence/authentication booleans from fixed read-only CLI checks."""
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if not isinstance(timeout_seconds, int) or not (1 <= timeout_seconds <= 60):
        raise ValueError("timeout_seconds must be an integer from 1 to 60")

    executable = which(_EXECUTABLES[provider])
    if not executable:
        return DetectionResult(provider, False, False)

    command: Sequence[str] = (executable, *_COMMANDS[provider])
    try:
        completed = runner(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return DetectionResult(provider, True, False)

    return DetectionResult(provider, True, completed.returncode == 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=sorted(PROVIDERS))
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = detect_authentication(args.provider, timeout_seconds=args.timeout_seconds)
    except ValueError:
        print(
            json.dumps(
                {"status": "error", "error": "invalid_detector_configuration"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(asdict(result), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
