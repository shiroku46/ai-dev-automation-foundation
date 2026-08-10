#!/usr/bin/env python3
"""Parse immutable provider-neutral isolated-workspace capability evidence."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 16_384

ISOLATION_PRIMITIVES = frozenset({
    "container",
    "microvm",
    "process-isolated",
    "vm-isolated-container",
})
PERSISTENCE_MODES = frozenset({"ephemeral", "persistent", "session-bounded"})
COMMAND_TRANSPORTS = frozenset({"argv", "shell-string"})
NETWORK_MODES = frozenset({"allowlist", "deny-all", "unrestricted"})
CREDENTIAL_MODES = frozenset({"brokered-egress", "none"})
CLEANUP_MODES = frozenset({"destroy", "reset", "stop"})

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ADAPTER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class WorkspaceCapabilityError(ValueError):
    """Workspace capability evidence is malformed, ambiguous, or unsafe."""


@dataclass(frozen=True)
class AdapterIdentity:
    adapter_id: str
    provider: str
    revision_sha: str


@dataclass(frozen=True)
class IsolationCapability:
    primitive: str


@dataclass(frozen=True)
class WorkspaceBoundary:
    root: str
    persistence: str
    host_filesystem_visible: bool
    git_metadata_visibility: str


@dataclass(frozen=True)
class ExecutionCapability:
    command_transport: str
    max_execution_seconds: int


@dataclass(frozen=True)
class NetworkCapability:
    supported_modes: tuple[str, ...]


@dataclass(frozen=True)
class CredentialCapability:
    supported_modes: tuple[str, ...]


@dataclass(frozen=True)
class LifecycleCapability:
    cleanup_modes: tuple[str, ...]
    controller_enforceable_cleanup: bool


@dataclass(frozen=True)
class SecurityCapability:
    host_credentials_visible: bool
    repository_write_credentials_visible: bool
    secrets_visible: bool
    oidc_visible: bool
    provider_control_plane_mutation_available: bool


@dataclass(frozen=True)
class WorkspaceCapabilityManifest:
    schema_version: int
    adapter: AdapterIdentity
    isolation: IsolationCapability
    workspace: WorkspaceBoundary
    execution: ExecutionCapability
    network: NetworkCapability
    credentials: CredentialCapability
    lifecycle: LifecycleCapability
    security: SecurityCapability
    manifest_sha256: str


def _duplicate_rejector(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceCapabilityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise WorkspaceCapabilityError(f"non-finite JSON constant is forbidden: {value}")


def _object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorkspaceCapabilityError(f"{label} is incomplete or contains unknown fields")
    return value


def _text(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise WorkspaceCapabilityError(f"{label} is invalid")
    return value


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise WorkspaceCapabilityError(f"{label} is unsupported")
    return value


def _sorted_tuple(value: object, allowed: frozenset[str], label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise WorkspaceCapabilityError(f"{label} must be a non-empty array")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise WorkspaceCapabilityError(f"{label} contains an unsupported value")
    if value != sorted(value) or len(value) != len(set(value)):
        raise WorkspaceCapabilityError(f"{label} must be sorted and unique")
    return tuple(value)


def _workspace_root(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise WorkspaceCapabilityError("workspace root is invalid")
    if not value.startswith("/") or "\\" in value or ":" in value or "\x00" in value:
        raise WorkspaceCapabilityError("workspace root is unsafe")
    if any(part == ".." for part in value.split("/")):
        raise WorkspaceCapabilityError("workspace root is unsafe")
    if any(ord(character) < 32 for character in value):
        raise WorkspaceCapabilityError("workspace root contains a control character")
    return value


def _strict_false(value: object, label: str) -> bool:
    if value is not False:
        raise WorkspaceCapabilityError(f"{label} must be false at the agent boundary")
    return False


def _profile_object(manifest: WorkspaceCapabilityManifest) -> dict[str, object]:
    return {
        "adapter": {
            "adapter_id": manifest.adapter.adapter_id,
            "provider": manifest.adapter.provider,
            "revision_sha": manifest.adapter.revision_sha,
        },
        "credentials": {"supported_modes": list(manifest.credentials.supported_modes)},
        "execution": {
            "command_transport": manifest.execution.command_transport,
            "max_execution_seconds": manifest.execution.max_execution_seconds,
        },
        "isolation": {"primitive": manifest.isolation.primitive},
        "lifecycle": {
            "cleanup_modes": list(manifest.lifecycle.cleanup_modes),
            "controller_enforceable_cleanup": manifest.lifecycle.controller_enforceable_cleanup,
        },
        "network": {"supported_modes": list(manifest.network.supported_modes)},
        "schema_version": manifest.schema_version,
        "security": {
            "host_credentials_visible": manifest.security.host_credentials_visible,
            "oidc_visible": manifest.security.oidc_visible,
            "provider_control_plane_mutation_available": manifest.security.provider_control_plane_mutation_available,
            "repository_write_credentials_visible": manifest.security.repository_write_credentials_visible,
            "secrets_visible": manifest.security.secrets_visible,
        },
        "workspace": {
            "git_metadata_visibility": manifest.workspace.git_metadata_visibility,
            "host_filesystem_visible": manifest.workspace.host_filesystem_visible,
            "persistence": manifest.workspace.persistence,
            "root": manifest.workspace.root,
        },
    }


def _canonical_bytes(value: dict[str, object]) -> bytes:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise WorkspaceCapabilityError("workspace capability manifest exceeds bounded size")
    return raw


def _build(value: dict[str, object]) -> WorkspaceCapabilityManifest:
    top = _object(
        value,
        {
            "schema_version",
            "adapter",
            "isolation",
            "workspace",
            "execution",
            "network",
            "credentials",
            "lifecycle",
            "security",
        },
        "workspace capability manifest",
    )
    if top["schema_version"] != SCHEMA_VERSION:
        raise WorkspaceCapabilityError("workspace capability schema version is unsupported")

    adapter_value = _object(
        top["adapter"],
        {"adapter_id", "provider", "revision_sha"},
        "adapter identity",
    )
    adapter = AdapterIdentity(
        adapter_id=_text(adapter_value["adapter_id"], _ADAPTER_RE, "adapter ID"),
        provider=_text(adapter_value["provider"], _PROVIDER_RE, "provider label"),
        revision_sha=_text(adapter_value["revision_sha"], _SHA_RE, "adapter revision SHA"),
    )

    isolation_value = _object(top["isolation"], {"primitive"}, "isolation capability")
    isolation = IsolationCapability(
        primitive=_enum(isolation_value["primitive"], ISOLATION_PRIMITIVES, "isolation primitive")
    )

    workspace_value = _object(
        top["workspace"],
        {"root", "persistence", "host_filesystem_visible", "git_metadata_visibility"},
        "workspace boundary",
    )
    workspace = WorkspaceBoundary(
        root=_workspace_root(workspace_value["root"]),
        persistence=_enum(workspace_value["persistence"], PERSISTENCE_MODES, "workspace persistence"),
        host_filesystem_visible=_strict_false(
            workspace_value["host_filesystem_visible"],
            "host filesystem visibility",
        ),
        git_metadata_visibility=_enum(
            workspace_value["git_metadata_visibility"],
            frozenset({"external"}),
            "Git metadata visibility",
        ),
    )

    execution_value = _object(
        top["execution"],
        {"command_transport", "max_execution_seconds"},
        "execution capability",
    )
    timeout = execution_value["max_execution_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        raise WorkspaceCapabilityError("execution timeout must be an integer from 1 to 3600 seconds")
    execution = ExecutionCapability(
        command_transport=_enum(
            execution_value["command_transport"],
            COMMAND_TRANSPORTS,
            "command transport",
        ),
        max_execution_seconds=timeout,
    )

    network_value = _object(top["network"], {"supported_modes"}, "network capability")
    network_modes = _sorted_tuple(network_value["supported_modes"], NETWORK_MODES, "network modes")
    if "deny-all" not in network_modes:
        raise WorkspaceCapabilityError("deny-all network mode is required")
    network = NetworkCapability(network_modes)

    credential_value = _object(top["credentials"], {"supported_modes"}, "credential capability")
    credentials = CredentialCapability(
        _sorted_tuple(credential_value["supported_modes"], CREDENTIAL_MODES, "credential modes")
    )

    lifecycle_value = _object(
        top["lifecycle"],
        {"cleanup_modes", "controller_enforceable_cleanup"},
        "lifecycle capability",
    )
    cleanup = lifecycle_value["controller_enforceable_cleanup"]
    if cleanup is not True:
        raise WorkspaceCapabilityError("controller-enforceable cleanup is required")
    lifecycle = LifecycleCapability(
        cleanup_modes=_sorted_tuple(lifecycle_value["cleanup_modes"], CLEANUP_MODES, "cleanup modes"),
        controller_enforceable_cleanup=True,
    )

    security_value = _object(
        top["security"],
        {
            "host_credentials_visible",
            "repository_write_credentials_visible",
            "secrets_visible",
            "oidc_visible",
            "provider_control_plane_mutation_available",
        },
        "security capability",
    )
    security = SecurityCapability(
        host_credentials_visible=_strict_false(
            security_value["host_credentials_visible"],
            "host credential visibility",
        ),
        repository_write_credentials_visible=_strict_false(
            security_value["repository_write_credentials_visible"],
            "repository write credential visibility",
        ),
        secrets_visible=_strict_false(security_value["secrets_visible"], "Secret visibility"),
        oidc_visible=_strict_false(security_value["oidc_visible"], "OIDC visibility"),
        provider_control_plane_mutation_available=_strict_false(
            security_value["provider_control_plane_mutation_available"],
            "provider control-plane mutation capability",
        ),
    )

    provisional = WorkspaceCapabilityManifest(
        schema_version=SCHEMA_VERSION,
        adapter=adapter,
        isolation=isolation,
        workspace=workspace,
        execution=execution,
        network=network,
        credentials=credentials,
        lifecycle=lifecycle,
        security=security,
        manifest_sha256="0" * 64,
    )
    raw = _canonical_bytes(_profile_object(provisional))
    return WorkspaceCapabilityManifest(
        schema_version=provisional.schema_version,
        adapter=provisional.adapter,
        isolation=provisional.isolation,
        workspace=provisional.workspace,
        execution=provisional.execution,
        network=provisional.network,
        credentials=provisional.credentials,
        lifecycle=provisional.lifecycle,
        security=provisional.security,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_workspace_capability(content: bytes) -> WorkspaceCapabilityManifest:
    """Parse exactly one canonical bounded F1 capability manifest."""
    if not isinstance(content, bytes) or not content or len(content) > MAX_MANIFEST_BYTES:
        raise WorkspaceCapabilityError("workspace capability input is empty or exceeds bounded size")
    if content.startswith(b"\xef\xbb\xbf"):
        raise WorkspaceCapabilityError("workspace capability input must not contain a UTF-8 BOM")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceCapabilityError("workspace capability input is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejector,
            parse_constant=_reject_constant,
        )
    except WorkspaceCapabilityError:
        raise
    except json.JSONDecodeError as exc:
        raise WorkspaceCapabilityError("workspace capability input is malformed JSON") from exc
    if not isinstance(value, dict):
        raise WorkspaceCapabilityError("workspace capability input must be a JSON object")
    manifest = _build(value)
    canonical = serialize_workspace_capability(manifest)
    if canonical != content:
        raise WorkspaceCapabilityError("workspace capability input is not canonical JSON")
    return manifest


def serialize_workspace_capability(manifest: WorkspaceCapabilityManifest) -> bytes:
    """Return canonical bytes after revalidating immutable capability evidence."""
    if not isinstance(manifest, WorkspaceCapabilityManifest):
        raise WorkspaceCapabilityError("workspace capability evidence type is invalid")
    value = _profile_object(manifest)
    rebuilt = _build(value)
    raw = _canonical_bytes(value)
    if rebuilt.manifest_sha256 != manifest.manifest_sha256:
        raise WorkspaceCapabilityError("workspace capability digest does not match payload")
    return raw
