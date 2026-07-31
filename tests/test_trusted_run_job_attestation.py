import importlib
import os
import sys
import unittest
from unittest.mock import patch

CANDIDATE_SHA = "a" * 40
WORKFLOW_SHA = "c" * 40


def runtime_module():
    environment = {
        "REPOSITORY": "example/foundation",
        "DEFAULT_BRANCH": "main",
        "AUTOMATION_OWNER": "owner",
    }
    with patch.dict(os.environ, environment, clear=False):
        sys.modules.pop("scripts.supervisor_runtime", None)
        return importlib.import_module("scripts.supervisor_runtime")


def make_run(status="completed", conclusion="success"):
    return {
        "id": 7,
        "head_sha": WORKFLOW_SHA,
        "status": status,
        "conclusion": conclusion,
    }


def make_job(
    name,
    run_id=7,
    workflow_sha=WORKFLOW_SHA,
    status="completed",
    conclusion="success",
):
    return {
        "name": name,
        "run_id": run_id,
        "head_sha": workflow_sha,
        "status": status,
        "conclusion": conclusion,
    }


class RunJobEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_module()

    def classify(self, run, jobs):
        with patch.object(self.runtime, "trusted_runs_for_sha", return_value=[run]), patch.object(
            self.runtime, "trusted_run_jobs", return_value=jobs
        ):
            return self.runtime.attestation_attempts(CANDIDATE_SHA)[0]

    def test_unique_successful_required_jobs_authorize(self):
        result = self.classify(
            make_run(),
            [make_job("CI / validate"), make_job("Unit Tests / test")],
        )
        self.assertEqual(
            result,
            {
                "run_id": 7,
                "active": False,
                "success": True,
                "complete": True,
                "updated_at": None,
            },
        )

    def test_missing_or_duplicate_job_fails_closed(self):
        missing = self.classify(make_run(), [make_job("CI / validate")])
        duplicate = self.classify(
            make_run(),
            [
                make_job("CI / validate"),
                make_job("CI / validate"),
                make_job("Unit Tests / test"),
            ],
        )
        self.assertFalse(missing["success"])
        self.assertFalse(duplicate["success"])

    def test_wrong_run_sha_or_failed_job_fails_closed(self):
        wrong_sha = self.classify(
            make_run(),
            [
                make_job("CI / validate", workflow_sha="b" * 40),
                make_job("Unit Tests / test"),
            ],
        )
        failed = self.classify(
            make_run(),
            [
                make_job("CI / validate", conclusion="failure"),
                make_job("Unit Tests / test"),
            ],
        )
        self.assertFalse(wrong_sha["success"])
        self.assertFalse(failed["success"])

    def test_active_run_does_not_fetch_jobs(self):
        with patch.object(
            self.runtime,
            "trusted_runs_for_sha",
            return_value=[make_run(status="in_progress", conclusion=None)],
        ), patch.object(self.runtime, "trusted_run_jobs") as fetch_jobs:
            result = self.runtime.attestation_attempts(CANDIDATE_SHA)[0]
        fetch_jobs.assert_not_called()
        self.assertTrue(result["active"])
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
