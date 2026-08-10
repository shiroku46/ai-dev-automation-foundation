"""Contract tests for provider-neutral isolated coding-agent workspaces."""
from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/AGENT_WORKSPACE_CAPABILITY.schema.json"
DOC_PATH = ROOT / "docs/AGENT_WORKSPACE_CONTRACT.md"

TOP_LEVEL = {
    "schema_version",
    "adapter",
    "isolation",
    "workspace",
    "execution",
    "network",
    "credentials",
    "lifecycle",
    "security",
}
ISOLATION_PRIMITIVES = {
    "container",
    "microvm",
    "process-isolated",
    "vm-isolated-container",
}
PERSISTENCE = {"ephemeral", "persistent", "session-bounded"}
COMMAND_TRANSPORTS = {"argv", "shell-string"}
NETWORK_MODES = {"allowlist", "deny-all", "unrestricted"}
CREDENTIAL_MODES = {"brokered-egress", "none"}
CLEANUP_MODES = {"destroy", "reset", "stop"}
SECURITY_KEYS = {
    "host_credentials_visible",
    "repository_write_credentials_visible",
    "secrets_visible",
    "oidc_visible",
    "provider_control_plane_mutation_available",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ADAPTER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


def no_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_schema() -> dict[str, Any]:
    return json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8"),
        object_pairs_hook=no_duplicate_pairs,
    )


def sorted_unique_strings(value: object, allowed: set[str], *, minimum: int = 1) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError("capability list is empty or invalid")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise ValueError("capability list contains unsupported value")
    if value != sorted(value) or len(value) != len(set(value)):
        raise ValueError("capability list must be sorted and unique")
    return tuple(value)


def safe_workspace_root(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        return False
    if not value.startswith("/") or "\\" in value or ":" in value or "\x00" in value:
        return False
    return all(part not in {".."} for part in value.split("/"))


def validate_profile(value: object) -> None:
    if not isinstance(value, dict) or set(value) != TOP_LEVEL:
        raise ValueError("profile top level is incomplete or contains unknown fields")
    if value["schema_version"] != 1:
        raise ValueError("schema version")

    adapter = value["adapter"]
    if not isinstance(adapter, dict) or set(adapter) != {"adapter_id", "provider", "revision_sha"}:
        raise ValueError("adapter identity")
    if not isinstance(adapter["adapter_id"], str) or ADAPTER_RE.fullmatch(adapter["adapter_id"]) is None:
        raise ValueError("adapter ID")
    if not isinstance(adapter["provider"], str) or PROVIDER_RE.fullmatch(adapter["provider"]) is None:
        raise ValueError("provider label")
    if not isinstance(adapter["revision_sha"], str) or SHA_RE.fullmatch(adapter["revision_sha"]) is None:
        raise ValueError("adapter revision")

    isolation = value["isolation"]
    if (
        not isinstance(isolation, dict)
        or set(isolation) != {"primitive"}
        or isolation["primitive"] not in ISOLATION_PRIMITIVES
    ):
        raise ValueError("isolation primitive")

    workspace = value["workspace"]
    if not isinstance(workspace, dict) or set(workspace) != {
        "root",
        "persistence",
        "host_filesystem_visible",
        "git_metadata_visibility",
    }:
        raise ValueError("workspace contract")
    if not safe_workspace_root(workspace["root"]):
        raise ValueError("workspace root")
    if workspace["persistence"] not in PERSISTENCE:
        raise ValueError("workspace persistence")
    if workspace["host_filesystem_visible"] is not False:
        raise ValueError("host filesystem must remain outside the agent boundary")
    if workspace["git_metadata_visibility"] != "external":
        raise ValueError("Git metadata must remain external")

    execution = value["execution"]
    if not isinstance(execution, dict) or set(execution) != {"command_transport", "max_execution_seconds"}:
        raise ValueError("execution contract")
    if execution["command_transport"] not in COMMAND_TRANSPORTS:
        raise ValueError("command transport")
    timeout = execution["max_execution_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        raise ValueError("execution timeout")

    network = value["network"]
    if not isinstance(network, dict) or set(network) != {"supported_modes"}:
        raise ValueError("network contract")
    network_modes = sorted_unique_strings(network["supported_modes"], NETWORK_MODES)
    if "deny-all" not in network_modes:
        raise ValueError("deny-all network isolation is required")

    credentials = value["credentials"]
    if not isinstance(credentials, dict) or set(credentials) != {"supported_modes"}:
        raise ValueError("credential contract")
    sorted_unique_strings(credentials["supported_modes"], CREDENTIAL_MODES)

    lifecycle = value["lifecycle"]
    if not isinstance(lifecycle, dict) or set(lifecycle) != {
        "cleanup_modes",
        "controller_enforceable_cleanup",
    }:
        raise ValueError("lifecycle contract")
    sorted_unique_strings(lifecycle["cleanup_modes"], CLEANUP_MODES)
    if lifecycle["controller_enforceable_cleanup"] is not True:
        raise ValueError("controller-enforceable cleanup is required")

    security = value["security"]
    if not isinstance(security, dict) or set(security) != SECURITY_KEYS:
        raise ValueError("security contract")
    if any(security[key] is not False for key in SECURITY_KEYS):
        raise ValueError("agent workspace exposes a forbidden control-plane capability")


def profile(*, provider: str, primitive: str, root: str, command: str, cleanup: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "adapter": {
            "adapter_id": provider.casefold().replace(" ", "-") + "-sandbox",
            "provider": provider,
            "revision_sha": "a" * 40,
        },
        "isolation": {"primitive": primitive},
        "workspace": {
            "root": root,
            "persistence": "ephemeral",
            "host_filesystem_visible": False,
            "git_metadata_visibility": "external",
        },
        "execution": {
            "command_transport": command,
            "max_execution_seconds": 900,
        },
        "network": {
            "supported_modes": ["allowlist", "deny-all", "unrestricted"],
        },
        "credentials": {
            "supported_modes": ["brokered-egress", "none"],
        },
        "lifecycle": {
            "cleanup_modes": [cleanup],
            "controller_enforceable_cleanup": True,
        },
        "security": {
            "host_credentials_visible": False,
            "repository_write_credentials_visible": False,
            "secrets_visible": False,
            "oidc_visible": False,
            "provider_control_plane_mutation_available": False,
        },
    }


class WorkspaceCapabilityContractTest(unittest.TestCase):
    def test_schema_is_strict_draft_2020_12(self):
        schema = load_schema()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), TOP_LEVEL)
        for key in TOP_LEVEL - {"schema_version"}:
            self.assertIs(schema["properties"][key]["additionalProperties"], False)
        self.assertEqual(
            set(schema["properties"]["isolation"]["properties"]["primitive"]["enum"]),
            ISOLATION_PRIMITIVES,
        )
        self.assertEqual(
            set(schema["properties"]["network"]["properties"]["supported_modes"]["items"]["enum"]),
            NETWORK_MODES,
        )
        self.assertEqual(
            set(schema["properties"]["credentials"]["properties"]["supported_modes"]["items"]["enum"]),
            CREDENTIAL_MODES,
        )
        self.assertTrue(schema["allOf"])

    def test_representative_vercel_and_cloudflare_profiles_are_distinct_and_valid(self):
        vercel = profile(
            provider="Vercel",
            primitive="microvm",
            root="/vercel/sandbox",
            command="argv",
            cleanup="stop",
        )
        cloudflare = profile(
            provider="Cloudflare",
            primitive="vm-isolated-container",
            root="/workspace",
            command="shell-string",
            cleanup="destroy",
        )
        validate_profile(vercel)
        validate_profile(cloudflare)
        self.assertNotEqual(vercel["isolation"]["primitive"], cloudflare["isolation"]["primitive"])
        self.assertNotEqual(vercel["execution"]["command_transport"], cloudflare["execution"]["command_transport"])

    def test_required_fail_closed_cases(self):
        base = profile(
            provider="Example",
            primitive="microvm",
            root="/workspace",
            command="argv",
            cleanup="destroy",
        )
        mutations = []

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["allowlist", "unrestricted"]
        mutations.append(item)

        item = copy.deepcopy(base)
        item["workspace"]["git_metadata_visibility"] = "workspace-visible"
        mutations.append(item)

        item = copy.deepcopy(base)
        item["workspace"]["host_filesystem_visible"] = True
        mutations.append(item)

        for key in SECURITY_KEYS:
            item = copy.deepcopy(base)
            item["security"][key] = True
            mutations.append(item)

        item = copy.deepcopy(base)
        item["execution"]["max_execution_seconds"] = 0
        mutations.append(item)

        item = copy.deepcopy(base)
        item["execution"]["max_execution_seconds"] = 3601
        mutations.append(item)

        item = copy.deepcopy(base)
        item["lifecycle"]["controller_enforceable_cleanup"] = False
        mutations.append(item)

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["deny-all", "allowlist"]
        mutations.append(item)

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["deny-all", "deny-all"]
        mutations.append(item)

        item = copy.deepcopy(base)
        item["adapter"]["revision_sha"] = "A" * 40
        mutations.append(item)

        item = copy.deepcopy(base)
        item["unexpected"] = True
        mutations.append(item)

        item = copy.deepcopy(base)
        item["security"]["unexpected"] = False
        mutations.append(item)

        for index, invalid in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_profile(invalid)

    def test_workspace_root_and_capability_lists_are_canonical(self):
        base = profile(
            provider="Example",
            primitive="container",
            root="/workspace",
            command="shell-string",
            cleanup="reset",
        )
        validate_profile(base)
        for invalid_root in ("workspace", "/workspace/../secret", "/workspace\\secret", "C:/workspace"):
            invalid = copy.deepcopy(base)
            invalid["workspace"]["root"] = invalid_root
            with self.subTest(root=invalid_root), self.assertRaises(ValueError):
                validate_profile(invalid)
        invalid = copy.deepcopy(base)
        invalid["credentials"]["supported_modes"] = ["none", "brokered-egress"]
        with self.assertRaises(ValueError):
            validate_profile(invalid)

    def test_document_states_current_platform_and_safety_boundaries(self):
        document = DOC_PATH.read_text(encoding="utf-8")
        for required in (
            "Firecracker microVM",
            "vm-isolated-container",
            "enableInternet = false",
            "brokered-egress",
            "Cloudflare also published a June 2026 Sandbox SDK migration guide",
            "git_metadata_visibility: external",
            "provider control-plane mutation available",
            "unrestricted",
            "not the Foundation candidate-execution default",
            "billing",
            "F2/F3",
        ):
            self.assertIn(required, document)
        self.assertIn("https://vercel.com/docs/sandbox", document)
        self.assertIn("https://developers.cloudflare.com/sandbox/concepts/security/", document)
        self.assertIn("https://developers.cloudflare.com/sandbox/guides/outbound-traffic/", document)
        self.assertIn("https://developers.cloudflare.com/sandbox/guides/2026-deprecation/", document)


if __name__ == "__main__":
    unittest.main()
