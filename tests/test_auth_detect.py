"""Read-only authentication detector regression tests."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from types import SimpleNamespace

from scripts.auth_detect import detect_authentication


class AuthDetectTest(unittest.TestCase):
    def _detect_and_capture(self, provider: str, returncode: int = 0):
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_which(name: str) -> str:
            return f"/detector-bin/{name}"

        def fake_runner(command: list[str], **kwargs: object):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=returncode)

        result = detect_authentication(provider, which=fake_which, runner=fake_runner)
        self.assertEqual(len(calls), 1)
        return result, calls[0]

    def test_exact_read_only_commands_and_suppressed_output(self):
        expected = {
            "github": [
                "/detector-bin/gh",
                "auth",
                "status",
                "--active",
                "--hostname",
                "github.com",
            ],
            "vercel": ["/detector-bin/vercel", "whoami"],
            "cloudflare": ["/detector-bin/wrangler", "whoami", "--json"],
        }
        for provider, expected_command in expected.items():
            with self.subTest(provider=provider):
                result, (command, kwargs) = self._detect_and_capture(provider)
                self.assertEqual(command, expected_command)
                self.assertTrue(result.executable_present)
                self.assertTrue(result.authenticated)
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
                self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
                self.assertFalse(kwargs["check"])
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["timeout"], 15)
                self.assertNotIn("capture_output", kwargs)

    def test_no_provider_path_can_select_token_retrieval(self):
        for provider in ("github", "vercel", "cloudflare"):
            with self.subTest(provider=provider):
                _, (command, _) = self._detect_and_capture(provider)
                joined = " ".join(command[1:]).lower()
                self.assertNotIn("show-token", joined)
                self.assertNotIn("auth token", joined)
                self.assertNotIn("--token", joined)

    def test_missing_executable_does_not_run_command(self):
        ran = False

        def runner(*args: object, **kwargs: object):
            nonlocal ran
            ran = True
            raise AssertionError("runner must not be called")

        result = detect_authentication("github", which=lambda _: None, runner=runner)
        self.assertFalse(result.executable_present)
        self.assertFalse(result.authenticated)
        self.assertFalse(ran)

    def test_nonzero_timeout_and_os_errors_collapse_to_false(self):
        failed, _ = self._detect_and_capture("vercel", returncode=1)
        self.assertTrue(failed.executable_present)
        self.assertFalse(failed.authenticated)

        for error in (subprocess.TimeoutExpired(["wrangler"], 1), OSError("sensitive detail")):
            with self.subTest(error=type(error).__name__):
                def raising_runner(*args: object, **kwargs: object):
                    raise error

                result = detect_authentication(
                    "cloudflare",
                    which=lambda _: "/detector-bin/wrangler",
                    runner=raising_runner,
                )
                self.assertTrue(result.executable_present)
                self.assertFalse(result.authenticated)

    def test_invalid_provider_and_timeout_fail_closed(self):
        with self.assertRaises(ValueError):
            detect_authentication("unknown")
        with self.assertRaises(ValueError):
            detect_authentication("github", timeout_seconds=0)
        with self.assertRaises(ValueError):
            detect_authentication("github", timeout_seconds=61)

    def test_cli_output_contains_only_boolean_detection_result(self):
        completed = subprocess.run(
            [sys.executable, "scripts/auth_detect.py", "github"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload), {"provider", "executable_present", "authenticated"})
        self.assertEqual(payload["provider"], "github")
        self.assertIsInstance(payload["executable_present"], bool)
        self.assertIsInstance(payload["authenticated"], bool)
        self.assertNotIn("/", completed.stdout)
        self.assertNotIn("@", completed.stdout)


if __name__ == "__main__":
    unittest.main()
