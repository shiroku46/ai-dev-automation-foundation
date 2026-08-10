"""Authentication capability planner regression tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from scripts.auth_bootstrap import CapabilityError, plan_authentication


class AuthBootstrapTest(unittest.TestCase):
    def test_github_prefers_connected_app_then_existing_cli(self):
        app_plan = plan_authentication(
            "github",
            capabilities={"github_app_connected": True, "github_cli_authenticated": True},
        )
        self.assertEqual(app_plan.state, "automatic")
        self.assertFalse(app_plan.human_action_required)
        self.assertEqual(app_plan.next_action, "use_connected_github_app")

        cli_plan = plan_authentication(
            "github",
            capabilities={"github_cli_authenticated": True},
        )
        self.assertEqual(cli_plan.state, "automatic")
        self.assertFalse(cli_plan.human_action_required)
        self.assertEqual(cli_plan.next_action, "use_existing_github_cli_session")

    def test_github_missing_connection_is_interactive_once(self):
        plan = plan_authentication("github")
        self.assertEqual(plan.state, "interactive_once")
        self.assertTrue(plan.human_action_required)

    def test_vercel_prefers_git_then_oidc_then_existing_cli(self):
        git_plan = plan_authentication(
            "vercel",
            capabilities={
                "git_integration_connected": True,
                "oidc_available": True,
                "cli_authenticated": True,
            },
        )
        self.assertEqual(git_plan.next_action, "use_vercel_git_integration")

        oidc_plan = plan_authentication("vercel", capabilities={"oidc_available": True})
        self.assertEqual(oidc_plan.state, "automatic")
        self.assertEqual(oidc_plan.next_action, "use_vercel_oidc")

        cli_plan = plan_authentication("vercel", capabilities={"cli_authenticated": True})
        self.assertEqual(cli_plan.next_action, "use_existing_vercel_cli_session")

    def test_vercel_without_existing_route_is_interactive_once(self):
        plan = plan_authentication("vercel")
        self.assertEqual(plan.state, "interactive_once")
        self.assertTrue(plan.human_action_required)

    def test_cloudflare_local_uses_oauth_or_one_interactive_login(self):
        ready = plan_authentication(
            "cloudflare",
            route="local",
            capabilities={"wrangler_oauth_authenticated": True},
        )
        self.assertEqual(ready.state, "automatic")
        self.assertFalse(ready.human_action_required)

        login = plan_authentication("cloudflare", route="local")
        self.assertEqual(login.state, "interactive_once")
        self.assertEqual(login.next_action, "run_wrangler_login_interactively")

    def test_cloudflare_deployment_prefers_workers_builds_without_user_created_token(self):
        missing = plan_authentication("cloudflare", route="deployment")
        self.assertEqual(missing.state, "interactive_once")
        self.assertTrue(missing.human_action_required)
        self.assertEqual(
            missing.next_action,
            "connect_cloudflare_workers_builds_git_integration",
        )
        self.assertNotEqual(
            missing.next_action,
            "create_and_store_scoped_cloudflare_ci_credentials_once",
        )

        connected = plan_authentication(
            "cloudflare",
            route="deployment",
            capabilities={"workers_builds_git_connected": True},
        )
        self.assertEqual(connected.state, "automatic")
        self.assertFalse(connected.human_action_required)
        self.assertEqual(
            connected.next_action,
            "use_cloudflare_workers_builds_git_integration",
        )
        self.assertNotEqual(
            connected.next_action,
            "create_and_store_scoped_cloudflare_ci_credentials_once",
        )

    def test_cloudflare_ci_requires_scoped_prerequisites_when_missing(self):
        missing = plan_authentication("cloudflare", route="github_actions")
        self.assertEqual(missing.state, "manual_required")
        self.assertTrue(missing.human_action_required)
        self.assertEqual(
            missing.next_action,
            "create_and_store_scoped_cloudflare_ci_credentials_once",
        )

        configured = plan_authentication(
            "cloudflare",
            route="github_actions",
            capabilities={"api_token_configured": True, "account_id_configured": True},
        )
        self.assertEqual(configured.state, "automatic")
        self.assertFalse(configured.human_action_required)

    def test_unknown_keys_and_non_boolean_values_fail_closed(self):
        with self.assertRaises(CapabilityError):
            plan_authentication("github", capabilities={"token": True})
        with self.assertRaises(CapabilityError):
            plan_authentication("vercel", capabilities={"oidc_available": "yes"})
        with self.assertRaises(CapabilityError):
            plan_authentication("cloudflare", route="deployment", capabilities={"build_token": True})
        with self.assertRaises(CapabilityError):
            plan_authentication("cloudflare", route="unknown")

    def test_planner_never_shells_out(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("must not execute")):
            plan = plan_authentication("github", capabilities={"github_app_connected": True})
        self.assertEqual(plan.state, "automatic")

    def test_cli_output_is_deterministic_and_ignores_secret_environment_values(self):
        secret = "SENTINEL_SECRET_VALUE_MUST_NOT_APPEAR"
        env = os.environ.copy()
        env.update(
            {
                "GITHUB_TOKEN": secret,
                "VERCEL_TOKEN": secret,
                "CLOUDFLARE_API_TOKEN": secret,
            }
        )
        command = [
            sys.executable,
            "scripts/auth_bootstrap.py",
            "cloudflare",
            "--route",
            "github_actions",
            "--capabilities-json",
            json.dumps({"api_token_configured": False, "account_id_configured": False}),
        ]
        first = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
        second = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn(secret, first.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["state"], "manual_required")
        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "provider",
                "route",
                "state",
                "human_action_required",
                "next_action",
                "rationale_code",
            },
        )

    def test_cli_malformed_snapshot_returns_machine_readable_error(self):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/auth_bootstrap.py",
                "github",
                "--capabilities-json",
                "[]",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "error")


if __name__ == "__main__":
    unittest.main()
