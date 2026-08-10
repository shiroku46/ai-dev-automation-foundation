"""Guarded interactive authentication setup regression tests."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from types import SimpleNamespace

from scripts.auth_detect import DetectionResult
from scripts.auth_setup import setup_authentication


class AuthSetupTest(unittest.TestCase):
    @staticmethod
    def detector(provider: str, *, executable: bool = True, authenticated: bool = False):
        return DetectionResult(provider, executable, authenticated)

    def test_already_authenticated_never_runs_login(self):
        def runner(*args: object, **kwargs: object):
            raise AssertionError("login must not run")

        result = setup_authentication(
            "github",
            detector=lambda provider: self.detector(provider, authenticated=True),
            runner=runner,
        )
        self.assertEqual(result.status, "ready")
        self.assertFalse(result.human_action_required)

    def test_missing_executable_never_runs_installer_or_login(self):
        def runner(*args: object, **kwargs: object):
            raise AssertionError("runner must not run")

        result = setup_authentication(
            "vercel",
            detector=lambda provider: self.detector(provider, executable=False),
            runner=runner,
        )
        self.assertEqual(result.status, "install_required")
        self.assertEqual(result.action, "install_provider_cli")

    def test_planning_mode_never_runs_login(self):
        def runner(*args: object, **kwargs: object):
            raise AssertionError("login must not run")

        result = setup_authentication(
            "cloudflare",
            detector=lambda provider: self.detector(provider),
            runner=runner,
        )
        self.assertEqual(result.status, "interaction_required")
        self.assertTrue(result.human_action_required)

    def test_non_tty_interactive_request_fails_closed(self):
        def runner(*args: object, **kwargs: object):
            raise AssertionError("login must not run")

        result = setup_authentication(
            "github",
            interactive=True,
            detector=lambda provider: self.detector(provider),
            is_tty=lambda: False,
            runner=runner,
        )
        self.assertEqual(result.status, "interactive_terminal_required")

    def test_exact_login_vectors_inherit_terminal_output(self):
        expected = {
            "github": [
                "/bin/gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--web",
                "--git-protocol",
                "https",
                "--skip-ssh-key",
            ],
            "vercel": ["/bin/vercel", "login"],
            "cloudflare": ["/bin/wrangler", "login", "--use-keyring"],
        }
        executable = {"github": "gh", "vercel": "vercel", "cloudflare": "wrangler"}

        for provider, expected_command in expected.items():
            with self.subTest(provider=provider):
                detections = iter(
                    [
                        self.detector(provider),
                        self.detector(provider, authenticated=True),
                    ]
                )
                calls: list[tuple[list[str], dict[str, object]]] = []

                def runner(command: list[str], **kwargs: object):
                    calls.append((command, kwargs))
                    return SimpleNamespace(returncode=0)

                result = setup_authentication(
                    provider,
                    interactive=True,
                    detector=lambda _: next(detections),
                    which=lambda name: f"/bin/{name}" if name == executable[provider] else None,
                    runner=runner,
                    is_tty=lambda: True,
                )
                self.assertEqual(result.status, "ready")
                self.assertEqual(len(calls), 1)
                command, kwargs = calls[0]
                self.assertEqual(command, expected_command)
                self.assertFalse(kwargs["check"])
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["timeout"], 600)
                self.assertNotIn("stdin", kwargs)
                self.assertNotIn("stdout", kwargs)
                self.assertNotIn("stderr", kwargs)
                self.assertNotIn("capture_output", kwargs)

    def test_forbidden_token_and_plaintext_flags_are_absent(self):
        providers = ("github", "vercel", "cloudflare")
        for provider in providers:
            with self.subTest(provider=provider):
                detections = iter([self.detector(provider), self.detector(provider, authenticated=True)])
                commands: list[list[str]] = []

                def runner(command: list[str], **kwargs: object):
                    commands.append(command)
                    return SimpleNamespace(returncode=0)

                setup_authentication(
                    provider,
                    interactive=True,
                    detector=lambda _: next(detections),
                    which=lambda name: f"/bin/{name}",
                    runner=runner,
                    is_tty=lambda: True,
                )
                joined = " ".join(commands[0][1:]).lower()
                self.assertNotIn("auth token", joined)
                self.assertNotIn("show-token", joined)
                self.assertNotIn("with-token", joined)
                self.assertNotIn("--token", joined)
                self.assertNotIn("insecure-storage", joined)
                self.assertNotIn("--github", joined)
                self.assertNotIn("--gitlab", joined)
                self.assertNotIn("--bitbucket", joined)
                if provider == "cloudflare":
                    self.assertIn("--use-keyring", commands[0])

    def test_post_login_detection_is_required(self):
        detections = iter([self.detector("vercel"), self.detector("vercel")])
        result = setup_authentication(
            "vercel",
            interactive=True,
            detector=lambda _: next(detections),
            which=lambda _: "/bin/vercel",
            runner=lambda *args, **kwargs: SimpleNamespace(returncode=0),
            is_tty=lambda: True,
        )
        self.assertEqual(result.status, "verification_failed")
        self.assertTrue(result.human_action_required)

    def test_login_failures_return_only_bounded_status(self):
        cases = (
            (lambda *args, **kwargs: SimpleNamespace(returncode=3), "login_failed"),
            (lambda *args, **kwargs: (_ for _ in ()).throw(OSError("secret path detail")), "login_failed"),
            (
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    subprocess.TimeoutExpired(["provider", "sensitive"], 600)
                ),
                "login_timeout",
            ),
        )
        for runner, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                result = setup_authentication(
                    "github",
                    interactive=True,
                    detector=lambda provider: self.detector(provider),
                    which=lambda _: "/bin/gh",
                    runner=runner,
                    is_tty=lambda: True,
                )
                self.assertEqual(result.status, expected_status)
                serialized = json.dumps(result.__dict__)
                self.assertNotIn("secret path detail", serialized)
                self.assertNotIn("sensitive", serialized)
                self.assertNotIn("/bin/gh", serialized)

    def test_invalid_provider_and_timeout_fail_closed(self):
        with self.assertRaises(ValueError):
            setup_authentication("unknown")
        with self.assertRaises(ValueError):
            setup_authentication("github", timeout_seconds=10)
        with self.assertRaises(ValueError):
            setup_authentication("github", timeout_seconds=1801)

    def test_cli_plan_output_is_non_secret_and_machine_readable(self):
        completed = subprocess.run(
            [sys.executable, "scripts/auth_setup.py", "github"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(completed.returncode, (0, 1))
        payload = json.loads(completed.stdout)
        self.assertEqual(
            set(payload),
            {"provider", "status", "human_action_required", "action"},
        )
        self.assertEqual(payload["provider"], "github")
        self.assertNotIn("@", completed.stdout)
        self.assertNotIn("\\", completed.stdout)


if __name__ == "__main__":
    unittest.main()
