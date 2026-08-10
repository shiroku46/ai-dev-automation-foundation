#!/usr/bin/env python3
"""Parse target-owned execution validation contracts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CONFIG_PATH = ".github/foundation-product-checks.json"
LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2
MAX_CONFIG_BYTES = 16_384
MAX_CHECKS = 20
MAX_NAME_LENGTH = 100
MAX_PATH_LENGTH = 240
MAX_PROVIDER_LENGTH = 80
MAX_APP_SLUG_LENGTH = 100
RESERVED_CHECK_NAMES = frozenset({"CI", "Unit Tests"})
RESERVED_WORKFLOW_PATHS = frozenset({
    ".github/workflows/ci.yml",
    ".github/workflows/unit-tests.yml",
    ".github/workflows/trusted-checks.yml",
    ".github/workflows/claude-queue.yml",
    ".github/workflows/claude-queue-comment-bridge.yml",
    ".github/workflows/ci-reconcile.yml",
    ".github/workflows/supervisor.yml",
})
EXECUTION_PROFILES = frozenset({"github-actions", "free-only"})
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_WORKFLOW_RE = re.compile(
    r"^\.github/workflows/(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\\:\x00-\x1f])[^/][^:]*\.(?:yml|yaml)$"
)
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_APP_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


class ProductCheckConfigError(ValueError):
    """The execution-validation configuration is malformed or unsafe."""


@dataclass(frozen=True, order=True)
class ProductCheck:
    name: str
    workflow: str


@dataclass(frozen=True, order=True)
class ExternalCheck:
    name: str
    provider: str
    check_name: str
    app_slug: str
    app_id: int | None = None


@dataclass(frozen=True)
class ProductValidationConfig:
    schema_version: int
    execution_profile: str
    workflow_checks: tuple[ProductCheck, ...]
    external_checks: tuple[ExternalCheck, ...]


def _text(value: Any, *, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ProductCheckConfigError(f"{label} is invalid")
    if value != value.strip() or _NAME_RE.fullmatch(value) is None:
        raise ProductCheckConfigError(f"{label} is invalid")
    return value


def _workflow(item: dict[str, Any]) -> ProductCheck:
    if set(item) != {"kind", "name", "workflow"} or item.get("kind") != "workflow":
        raise ProductCheckConfigError("workflow check entry is invalid")
    name = _text(item.get("name"), label="product check name", limit=MAX_NAME_LENGTH)
    workflow = _text(
        item.get("workflow"), label="product workflow path", limit=MAX_PATH_LENGTH
    )
    if (
        name.casefold() in {value.casefold() for value in RESERVED_CHECK_NAMES}
        or _WORKFLOW_RE.fullmatch(workflow) is None
        or ".." in workflow.split("/")
        or "//" in workflow
        or workflow in RESERVED_WORKFLOW_PATHS
    ):
        raise ProductCheckConfigError("product workflow identity is unsafe")
    return ProductCheck(name, workflow)


def _legacy_workflow(item: dict[str, Any]) -> ProductCheck:
    if set(item) != {"name", "workflow"}:
        raise ProductCheckConfigError("product check entry is invalid")
    return _workflow({"kind": "workflow", **item})


def _external(item: dict[str, Any]) -> ExternalCheck:
    required = {"kind", "name", "provider", "check_name", "app_slug"}
    if item.get("kind") != "external" or set(item) not in {
        frozenset(required),
        frozenset(required | {"app_id"}),
    }:
        raise ProductCheckConfigError("external check entry is invalid")
    name = _text(item.get("name"), label="external check name", limit=MAX_NAME_LENGTH)
    provider = _text(
        item.get("provider"), label="external provider", limit=MAX_PROVIDER_LENGTH
    ).casefold()
    check_name = _text(
        item.get("check_name"), label="external check-run name", limit=MAX_NAME_LENGTH
    )
    app_slug = _text(
        item.get("app_slug"), label="external app slug", limit=MAX_APP_SLUG_LENGTH
    ).casefold()
    if _PROVIDER_RE.fullmatch(provider) is None or _APP_SLUG_RE.fullmatch(app_slug) is None:
        raise ProductCheckConfigError("external provider identity is unsafe")
    app_id = item.get("app_id")
    if app_id is not None and (
        isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0
    ):
        raise ProductCheckConfigError("external app id is invalid")
    return ExternalCheck(name, provider, check_name, app_slug, app_id)


def parse_validation_config(content: bytes | str) -> ProductValidationConfig:
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        raise ProductCheckConfigError("configuration content type is invalid")
    if not raw or len(raw) > MAX_CONFIG_BYTES:
        raise ProductCheckConfigError("configuration size is invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductCheckConfigError("configuration is malformed") from exc
    if not isinstance(value, dict):
        raise ProductCheckConfigError("configuration object is invalid")

    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
    }:
        raise ProductCheckConfigError("configuration schema version is unsupported")

    if schema_version == LEGACY_SCHEMA_VERSION:
        if set(value) != {"schema_version", "checks"}:
            raise ProductCheckConfigError("configuration object keys are invalid")
        execution_profile = "github-actions"
    else:
        if set(value) != {"schema_version", "execution_profile", "checks"}:
            raise ProductCheckConfigError("configuration object keys are invalid")
        execution_profile = value.get("execution_profile")
        if execution_profile not in EXECUTION_PROFILES:
            raise ProductCheckConfigError("execution profile is unsupported")

    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
        raise ProductCheckConfigError("configuration check list is invalid")

    workflow_checks: list[ProductCheck] = []
    external_checks: list[ExternalCheck] = []
    names: set[str] = set()
    workflows: set[str] = set()
    external_identities: set[tuple[str, int | None, str]] = set()

    for item in checks:
        if not isinstance(item, dict):
            raise ProductCheckConfigError("check entry is invalid")
        if schema_version == LEGACY_SCHEMA_VERSION:
            parsed = _legacy_workflow(item)
        elif item.get("kind") == "workflow":
            parsed = _workflow(item)
        elif item.get("kind") == "external":
            parsed = _external(item)
        else:
            raise ProductCheckConfigError("check kind is unsupported")

        folded = parsed.name.casefold()
        if folded in names:
            raise ProductCheckConfigError("checks contain a duplicate name")
        names.add(folded)
        if isinstance(parsed, ProductCheck):
            if parsed.workflow in workflows:
                raise ProductCheckConfigError("checks contain a duplicate workflow")
            workflows.add(parsed.workflow)
            workflow_checks.append(parsed)
        else:
            identity = (parsed.app_slug, parsed.app_id, parsed.check_name)
            if identity in external_identities:
                raise ProductCheckConfigError("checks contain a duplicate external identity")
            external_identities.add(identity)
            external_checks.append(parsed)

    if execution_profile == "free-only":
        if workflow_checks:
            raise ProductCheckConfigError(
                "free-only configuration cannot require GitHub workflow checks"
            )
        if not external_checks:
            raise ProductCheckConfigError(
                "free-only configuration requires external validation"
            )

    return ProductValidationConfig(
        schema_version,
        execution_profile,
        tuple(sorted(workflow_checks)),
        tuple(sorted(external_checks)),
    )


def parse_product_checks(content: bytes | str) -> tuple[ProductCheck, ...]:
    """Backward-compatible workflow-check view used by existing consumers."""
    return parse_validation_config(content).workflow_checks


def parse_external_checks(content: bytes | str) -> tuple[ExternalCheck, ...]:
    return parse_validation_config(content).external_checks
