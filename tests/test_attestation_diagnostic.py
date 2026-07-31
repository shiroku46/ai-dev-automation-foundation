import json
import unittest
from urllib.request import Request, urlopen


class PublicAttestationDiagnosticTest(unittest.TestCase):
    def test_print_recent_public_trusted_check_runs(self):
        url = (
            "https://api.github.com/repos/shiroku46/ai-dev-automation-foundation/"
            "actions/workflows/trusted-checks.yml/runs?event=workflow_dispatch&per_page=20"
        )
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "foundation-read-only-attestation-diagnostic",
            },
        )
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
        rows = []
        for run in payload.get("workflow_runs", []):
            rows.append(
                {
                    "id": run.get("id"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "display_title": run.get("display_title"),
                    "path": run.get("path"),
                    "event": run.get("event"),
                    "head_branch": run.get("head_branch"),
                    "head_sha": run.get("head_sha"),
                    "actor": (run.get("actor") or {}).get("login"),
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                }
            )
        print("PUBLIC_TRUSTED_CHECK_RUNS=" + json.dumps(rows, sort_keys=True))
        self.assertLessEqual(len(rows), 20)


if __name__ == "__main__":
    unittest.main()
