"""Regression tests for strict isolated-workspace capability parsing."""
from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from scripts.agent_workspace_contract import (
    WorkspaceCapabilityError,
    parse_workspace_capability,
    serialize_workspace_capability,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "scripts/agent_workspace_contract.py"


def profile(*, provider: str, primitive: str, root: str, command: str, cleanup: str):
    return {
        "adapter": {
            "adapter_id": provider.casefold().replace(" ", "-") + "-sandbox",
            "provider": provider,
            "revision_sha": "a" * 40,
        },
        "credentials": {"supported_modes": ["brokered-egress", "none"]},
        "execution": {
            "command_transport": command,
            "max_execution_seconds": 900,
        },
        "isolation": {"primitive": primitive},
        "lifecycle": {
            "cleanup_modes": [cleanup],
            "controller_enforceable_cleanup": True,
        },
        "network": {"supported_modes": ["allowlist", "deny-all", "unrestricted"]},
        "schema_version": 1,
        "security": {
            "host_credentials_visible": False,
            "oidc_visible": False,
            "provider_control_plane_mutation_available": False,
            "repository_write_credentials_visible": False,
            "secrets_visible": False,
        },
        "workspace": {
            "git_metadata_visibility": "external",
            "host_filesystem_visible": False,
            "persistence": "ephemeral",
            "root": root,
        },
    }


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class WorkspaceCapabilityParserTest(unittest.TestCase):
    def test_vercel_and_cloudflare_profiles_parse_to_distinct_immutable_evidence(self):
        cases = (
            profile(
                provider="Vercel",
                primitive="microvm",
                root="/vercel/sandbox",
                command="argv",
                cleanup="stop",
            ),
            profile(
                provider="Cloudflare",
                primitive="vm-isolated-container",
                root="/workspace",
                command="shell-string",
                cleanup="destroy",
            ),
        )
        parsed = [parse_workspace_capability(canonical(item)) for item in cases]
        self.assertEqual(parsed[0].isolation.primitive, "microvm")
        self.assertEqual(parsed[1].isolation.primitive, "vm-isolated-container")
        self.assertEqual(parsed[0].execution.command_transport, "argv")
        self.assertEqual(parsed[1].execution.command_transport, "shell-string")
        self.assertNotEqual(parsed[0].manifest_sha256, parsed[1].manifest_sha256)
        for item, evidence in zip(cases, parsed, strict=True):
            self.assertEqual(serialize_workspace_capability(evidence), canonical(item))
            self.assertEqual(evidence.network.supported_modes, ("allowlist", "deny-all", "unrestricted"))
            self.assertEqual(evidence.credentials.supported_modes, ("brokered-egress", "none"))
            with self.assertRaises(FrozenInstanceError):
                evidence.schema_version = 2  # type: ignore[misc]

    def test_parse_serialize_roundtrip_and_digest_are_deterministic(self):
        raw = canonical(
            profile(
                provider="Example",
                primitive="container",
                root="/workspace",
                command="shell-string",
                cleanup="reset",
            )
        )
        first = parse_workspace_capability(raw)
        second = parse_workspace_capability(raw)
        self.assertEqual(first, second)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(serialize_workspace_capability(first), raw)
        self.assertEqual(parse_workspace_capability(serialize_workspace_capability(first)), first)

    def test_semantically_equivalent_noncanonical_json_is_rejected(self):
        value = profile(
            provider="Example",
            primitive="microvm",
            root="/workspace",
            command="argv",
            cleanup="destroy",
        )
        noncanonical = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(noncanonical)
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(canonical(value) + b"\n")
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(b"\xef\xbb\xbf" + canonical(value))

    def test_duplicate_keys_nonfinite_values_and_trailing_data_fail_closed(self):
        valid = canonical(
            profile(
                provider="Example",
                primitive="microvm",
                root="/workspace",
                command="argv",
                cleanup="destroy",
            )
        )
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(b'{"value":NaN}')
        with self.assertRaises(WorkspaceCapabilityError):
            parse_workspace_capability(valid + b"{}")

    def test_required_unsafe_profiles_fail_closed(self):
        base = profile(
            provider="Example",
            primitive="microvm",
            root="/workspace",
            command="argv",
            cleanup="destroy",
        )
        invalids = []

        for key, value in (
            ("adapter_id", "Example Sandbox"),
            ("provider", "Bad/Provider"),
            ("revision_sha", "A" * 40),
        ):
            item = copy.deepcopy(base)
            item["adapter"][key] = value
            invalids.append(item)

        item = copy.deepcopy(base)
        item["isolation"]["primitive"] = "marketing-secure"
        invalids.append(item)

        for root in ("workspace", "/workspace/../secret", "/workspace\\secret", "C:/workspace"):
            item = copy.deepcopy(base)
            item["workspace"]["root"] = root
            invalids.append(item)

        item = copy.deepcopy(base)
        item["workspace"]["host_filesystem_visible"] = True
        invalids.append(item)

        item = copy.deepcopy(base)
        item["workspace"]["git_metadata_visibility"] = "workspace-visible"
        invalids.append(item)

        item = copy.deepcopy(base)
        item["execution"]["command_transport"] = "command-line"
        invalids.append(item)

        for timeout in (True, 0, 3601):
            item = copy.deepcopy(base)
            item["execution"]["max_execution_seconds"] = timeout
            invalids.append(item)

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["allowlist", "unrestricted"]
        invalids.append(item)

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["deny-all", "allowlist"]
        invalids.append(item)

        item = copy.deepcopy(base)
        item["network"]["supported_modes"] = ["deny-all", "deny-all"]
        invalids.append(item)

        item = copy.deepcopy(base)
        item["credentials"]["supported_modes"] = ["workspace-environment"]
        invalids.append(item)

        item = copy.deepcopy(base)
        item["credentials"]["supported_modes"] = ["none", "brokered-egress"]
        invalids.append(item)

        item = copy.deepcopy(base)
        item["lifecycle"]["controller_enforceable_cleanup"] = False
        invalids.append(item)

        item = copy.deepcopy(base)
        item["lifecycle"]["cleanup_modes"] = ["stop", "destroy"]
        invalids.append(item)

        for key in base["security"]:
            item = copy.deepcopy(base)
            item["security"][key] = True
            invalids.append(item)

        item = copy.deepcopy(base)
        item["unexpected"] = True
        invalids.append(item)

        item = copy.deepcopy(base)
        item["security"]["unexpected"] = False
        invalids.append(item)

        for index, value in enumerate(invalids):
            with self.subTest(index=index), self.assertRaises(WorkspaceCapabilityError):
                parse_workspace_capability(canonical(value))

    def test_tampered_immutable_evidence_fails_serialization(self):
        evidence = parse_workspace_capability(
            canonical(
                profile(
                    provider="Example",
                    primitive="microvm",
                    root="/workspace",
                    command="argv",
                    cleanup="destroy",
                )
            )
        )
        with self.assertRaises(WorkspaceCapabilityError):
            serialize_workspace_capability(replace(evidence, manifest_sha256="0" * 64))
        with self.assertRaises(WorkspaceCapabilityError):
            serialize_workspace_capability(
                replace(
                    evidence,
                    security=replace(evidence.security, secrets_visible=True),
                )
            )

    def test_parser_import_surface_has_no_runtime_provider_or_host_interfaces(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertEqual(imports, {"__future__", "dataclasses", "hashlib", "json", "re", "typing"})
        for forbidden in (
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "pathlib",
            "os.environ",
            "getenv(",
            "VERCEL_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "GITHUB_TOKEN",
            "OIDC",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
