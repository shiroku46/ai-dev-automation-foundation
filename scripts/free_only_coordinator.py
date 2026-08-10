#!/usr/bin/env python3
"""Free-only Pull Request coordinator using trusted external CI evidence."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from scripts.external_validation import (
    ExternalCheckRequirement,
    ExternalValidationError,
    external_checks_satisfied,
    snapshot_external_checks,
)
from scripts.foundation_product_checks import (
    CONFIG_PATH as VALIDATION_CONFIG_PATH,
    ProductCheckConfigError,
    ProductValidationConfig,
    parse_validation_config,
)
from scripts.github_coordinator_supervisor import (
    Decision,
    GhClient,
    REPO_RE,
    SupervisorError,
    _authorization_snapshot,
    _login,
    _overlaps,
    _pr_identity,
    _sha,
    _text,
    changed_paths,
    is_protected_path,
    parse_task_scope,
    path_allowed,
    review_evidence,
    source_issue_number,
)

MAX_PAGES = 20
MAX_ITEMS = 3000


class FreeOnlyClient(Protocol):
    def repository(self) -> Mapping[str, Any]: ...
    def default_branch_sha(self, branch: str) -> str: ...
    def pull(self, number: int) -> Mapping[str, Any]: ...
    def issue(self, number: int) -> Mapping[str, Any]: ...
    def issue_comments(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def pull_files(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def open_pulls(self) -> Sequence[Mapping[str, Any]]: ...
    def review_threads(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def file_content(self, path: str, ref: str) -> bytes: ...
    def check_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]: ...
    def mark_ready(self, node_id: str) -> None: ...
    def merge(self, number: int, head_sha: str) -> None: ...


@dataclass(frozen=True)
class FreeOnlyEvidence:
    default_config: bytes
    candidate_config: bytes
    external_snapshot: tuple[tuple[str, str, int], ...]


def _config(client: FreeOnlyClient, ref: str, where: str) -> tuple[bytes, ProductValidationConfig]:
    raw = client.file_content(VALIDATION_CONFIG_PATH, ref)
    try:
        return raw, parse_validation_config(raw)
    except ProductCheckConfigError as exc:
        raise SupervisorError(f"{where} validation configuration is invalid") from exc


def _requirements(config: ProductValidationConfig) -> tuple[ExternalCheckRequirement, ...]:
    return tuple(
        ExternalCheckRequirement(
            provider=item.provider,
            name=item.check_name,
            app_slug=item.app_slug,
            app_id=item.app_id,
        )
        for item in config.external_checks
    )


def _external_snapshot(
    client: FreeOnlyClient,
    *,
    head: str,
    pr_number: int,
    config: ProductValidationConfig,
) -> tuple[tuple[str, str, int], ...]:
    try:
        evidence = snapshot_external_checks(
            client.check_runs(head),
            head_sha=head,
            pr_number=pr_number,
            requirements=_requirements(config),
        )
    except ExternalValidationError as exc:
        raise SupervisorError("external validation evidence is malformed") from exc
    snapshot = tuple((item.name, item.state, item.run_id) for item in evidence)
    if not external_checks_satisfied(evidence):
        raise SupervisorError("required free-only external checks are not all successful")
    return snapshot


def evaluate_free_only(
    client: FreeOnlyClient,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    """Evaluate one exact PR using free-only external execution evidence.

    The current default-branch validation configuration judges the candidate.
    A candidate configuration is parsed and snapshotted only for future validity;
    it cannot select its own validators.
    """
    if not REPO_RE.fullmatch(repo):
        raise SupervisorError("repository identity is invalid")
    repository = client.repository()
    default_branch = _text(repository.get("default_branch"), "default branch", 200)
    owner = _login(str(((repository.get("owner") or {}).get("login")) or repo.split("/", 1)[0]))
    trusted = {owner}
    if automation_owner:
        trusted.add(_login(automation_owner))

    default_sha = client.default_branch_sha(default_branch)
    pr = client.pull(pr_number)
    number, head = _pr_identity(pr, repo, default_branch)
    default_raw, default_config = _config(client, default_sha, "default")
    candidate_raw, candidate_config = _config(client, head, "candidate")
    if default_config.execution_profile != "free-only":
        raise SupervisorError("default branch has not opted into free-only validation")
    if candidate_config.execution_profile != "free-only":
        raise SupervisorError("candidate would leave the free-only execution profile")

    pr_body = _text(pr.get("body"), "PR body")
    source_number = source_issue_number(pr_body)
    issue = client.issue(source_number)
    issue_body = _text(issue.get("body"), "Issue body")
    issue_updated = str(issue.get("updated_at") or "")
    issue_author = str(((issue.get("user") or {}).get("login")) or "")
    if issue.get("state") != "open" or issue_author.casefold() not in {
        value.casefold() for value in trusted
    }:
        raise SupervisorError("source Issue is not open and trusted")

    scope = parse_task_scope(issue_body, client.issue_comments(source_number), trusted)
    auth_snapshot = _authorization_snapshot(scope)
    files = changed_paths(client.pull_files(number))
    if any(not path_allowed(path, scope.paths) for path in files):
        raise SupervisorError("PR paths exceed the source Issue")
    if any(
        is_protected_path(path) and not path_allowed(path, scope.protected_paths)
        for path in files
    ):
        raise SupervisorError("protected path lacks authorization")
    overlap = _overlaps(client, repo, number, files)
    if overlap:
        raise SupervisorError(f"live Pull Request #{overlap[0][0]} overlaps the candidate path set")

    external = _external_snapshot(
        client,
        head=head,
        pr_number=number,
        config=default_config,
    )
    review = review_evidence(
        client.issue_comments(number),
        client.review_threads(number),
        head,
        scope.risk,
        trusted,
    )
    if review.state != "clean":
        raise SupervisorError(f"coordinator review is {review.state}")

    final_issue = client.issue(source_number)
    if (
        final_issue.get("state") != "open"
        or str(final_issue.get("body") or "") != issue_body
        or str(final_issue.get("updated_at") or "") != issue_updated
    ):
        raise SupervisorError("source Issue changed during evaluation")
    if _authorization_snapshot(
        parse_task_scope(issue_body, client.issue_comments(source_number), trusted)
    ) != auth_snapshot:
        raise SupervisorError("source authorization comments changed during evaluation")
    if client.default_branch_sha(default_branch) != default_sha:
        raise SupervisorError("default branch changed during evaluation")
    if client.file_content(VALIDATION_CONFIG_PATH, default_sha) != default_raw:
        raise SupervisorError("default validation configuration changed during evaluation")
    if client.file_content(VALIDATION_CONFIG_PATH, head) != candidate_raw:
        raise SupervisorError("candidate validation configuration changed during evaluation")
    if changed_paths(client.pull_files(number)) != files or _overlaps(
        client, repo, number, files
    ) != overlap:
        raise SupervisorError("path or collision state changed during evaluation")
    if _external_snapshot(
        client,
        head=head,
        pr_number=number,
        config=default_config,
    ) != external:
        raise SupervisorError("external validation evidence changed during evaluation")
    if review_evidence(
        client.issue_comments(number),
        client.review_threads(number),
        head,
        scope.risk,
        trusted,
    ) != review:
        raise SupervisorError("coordinator review evidence changed during evaluation")

    final_pr = client.pull(number)
    _, final_head = _pr_identity(final_pr, repo, default_branch)
    if (
        final_head != head
        or str(final_pr.get("body") or "") != pr_body
        or final_pr.get("mergeable") is not True
    ):
        raise SupervisorError("Pull Request identity, body, head, or mergeability changed")
    if "ai-no-merge" in {
        str(item.get("name") or "") for item in final_pr.get("labels") or []
    }:
        raise SupervisorError("Pull Request has ai-no-merge hold")
    return Decision(
        "mark_ready" if final_pr.get("draft") is True else "merge",
        "free-only external gates clean",
        head,
        scope.risk,
        0,
    )


def supervise_free_only(
    client: FreeOnlyClient,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    decision = evaluate_free_only(client, repo, pr_number, automation_owner)
    if decision.action == "mark_ready":
        pr = client.pull(pr_number)
        if _sha((pr.get("head") or {}).get("sha"), "ready head") != decision.head_sha:
            raise SupervisorError("head moved before Ready")
        client.mark_ready(_text(pr.get("node_id"), "node id", 200))
        decision = evaluate_free_only(client, repo, pr_number, automation_owner)
    if decision.action != "merge":
        raise SupervisorError("post-Ready evaluation is not merge-ready")
    client.merge(pr_number, decision.head_sha)
    return decision


class FreeOnlyGhClient(GhClient):
    """GitHub transport for free-only external check-run evidence."""

    def check_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]:
        head_sha = _sha(head_sha, "external check head")
        path = f"repos/{self.repo}/commits/{head_sha}/check-runs?per_page=100"
        pages = self._run(["api", "--paginate", "--slurp", path])
        if not isinstance(pages, list) or len(pages) > MAX_PAGES:
            raise SupervisorError("external check pagination exceeded")
        values: list[Mapping[str, Any]] = []
        for page in pages:
            if not isinstance(page, Mapping) or not isinstance(page.get("check_runs"), list):
                raise SupervisorError("external check pagination response malformed")
            values.extend(item for item in page["check_runs"] if isinstance(item, Mapping))
            if len(values) > MAX_ITEMS:
                raise SupervisorError("external check evidence is excessive")
        return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-number", action="append", type=int, required=True)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    owner = os.environ.get("AUTOMATION_OWNER") or None
    try:
        client = FreeOnlyGhClient(repo)
        results = []
        for number in args.pr_number:
            try:
                decision = supervise_free_only(client, repo, number, owner)
                results.append(
                    {
                        "pr_number": number,
                        "action": decision.action,
                        "head_sha": decision.head_sha,
                        "execution_profile": "free-only",
                        "review_route": "github-coordinator",
                        "review_state": "clean",
                        "human_action_required": False,
                    }
                )
            except SupervisorError as exc:
                results.append(
                    {
                        "pr_number": number,
                        "action": "blocked",
                        "reason": str(exc),
                        "execution_profile": "free-only",
                        "review_route": "github-coordinator",
                        "human_action_required": False,
                    }
                )
        print(json.dumps(results, sort_keys=True))
        return 0
    except (SupervisorError, OSError, json.JSONDecodeError) as exc:
        print(f"free-only-coordinator: blocked: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
