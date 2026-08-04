#!/usr/bin/env python3
"""Default-branch-controlled GitHub-only Pull Request supervisor.

The supervisor never checks out or executes Pull Request code. It consumes
bounded same-repository GitHub evidence, validates an owner-authored source
Issue and immutable authorization amendments, and performs only these mutations:

1. mark one exact Draft Pull Request ready;
2. after a complete fresh re-evaluation, merge that exact expected head.

Codex and Claude output is never a completion, review, readiness, or merge gate.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
CLOSES_RE = re.compile(r"(?i)\b(?:closes|fixes|resolves)\s+#([1-9][0-9]*)\b")
SAFE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\x00-\x1f\\])[^:]+$")
TASK_BLOCK_RE = re.compile(
    r"<!--\s*foundation-task-scope\s*\n(?P<body>.*?)\n\s*-->", re.DOTALL
)
PROTECTED_BLOCK_RE = re.compile(
    r"<!--\s*foundation-protected-authorization(?:-amendment)?\s*\n"
    r"(?P<body>.*?)\n\s*-->",
    re.DOTALL,
)
GENERAL_REVIEW_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):(clean|blocked) -->"
)
PASS_REVIEW_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):"
    r"(scope-security|correctness-race):(clean|blocked) -->"
)
ALLOWED_SCOPE_HEADINGS = frozenset(
    {
        "allowed paths",
        "allowed scope",
        "exact scope",
        "exact authorized scope",
        "exact allowed paths",
        "phase 1 allowed paths",
        "phase 2 allowed paths",
        "additional allowed paths",
    }
)
PROTECTED_EXACT = frozenset(
    {
        "SECURITY.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".github/ISSUE_TEMPLATE/ai-task.yml",
        ".github/pull_request_template.md",
        "docs/MINIMUM_SAFETY_PROFILE.md",
        "docs/OPERATING_RULES.md",
        "docs/PROJECT_STARTUP.md",
        "docs/PUBLIC_SECURITY_MODEL.md",
        "scripts/github_coordinator_supervisor.py",
        "scripts/github_optional_provider.py",
        "scripts/supervisor_policy.py",
        "scripts/supervisor_runtime.py",
        "scripts/supervisor_final_guard.py",
        "scripts/validate_repository.py",
    }
)
PROTECTED_PREFIXES = (".github/workflows/", "bootstrap/", "automation/")
PROTECTED_GLOBS = ("scripts/supervisor_*.py", "scripts/github_*.py")
CHECK_WORKFLOWS = {
    "CI": ".github/workflows/ci.yml",
    "Unit Tests": ".github/workflows/unit-tests.yml",
}
DEFAULT_REQUIRED_CHECKS = ("CI", "Unit Tests")
PASSING = frozenset({"success", "neutral", "skipped"})
MAX_PAGES = 20
MAX_FILES = 3000
MAX_COMMENTS = 3000
MAX_OPEN_PULLS = 500
MAX_RUNS = 3000


class SupervisorError(RuntimeError):
    """A fail-closed decision with no mutation."""


@dataclass(frozen=True)
class ImmutableComment:
    comment_id: int
    login: str
    created_at: str
    updated_at: str
    body: str


@dataclass(frozen=True)
class TaskScope:
    risk: str
    paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    checks: tuple[str, ...]
    operation: str
    prohibited: str
    authorization_comments: tuple[ImmutableComment, ...]


@dataclass(frozen=True)
class ReviewEvidence:
    state: str
    unresolved_threads: int
    marker_ids: tuple[int, ...]
    thread_snapshot: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    head_sha: str
    risk: str
    unresolved_threads: int


class Client(Protocol):
    def repository(self) -> Mapping[str, Any]: ...
    def default_branch_sha(self, branch: str) -> str: ...
    def pull(self, number: int) -> Mapping[str, Any]: ...
    def issue(self, number: int) -> Mapping[str, Any]: ...
    def issue_comments(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def pull_files(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def open_pulls(self) -> Sequence[Mapping[str, Any]]: ...
    def workflow_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]: ...
    def review_threads(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def file_blob(self, path: str, ref: str) -> str: ...
    def mark_ready(self, node_id: str) -> None: ...
    def merge(self, number: int, head_sha: str) -> None: ...


def _safe_text(value: Any, where: str, limit: int = 50000) -> str:
    if not isinstance(value, str):
        raise SupervisorError(f"{where} must be text")
    if len(value) > limit or "\x00" in value:
        raise SupervisorError(f"{where} exceeds the bounded text contract")
    return value


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SupervisorError(f"{where} must be a positive integer")
    return value


def _exact_sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise SupervisorError(f"{where} must be a lowercase 40-character SHA")
    return value


def _login(value: Any, where: str) -> str:
    if not isinstance(value, str) or not LOGIN_RE.fullmatch(value):
        raise SupervisorError(f"{where} is invalid")
    return value


def _parse_simple_block(body: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list: str | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current_list is None:
                raise SupervisorError("structured block list item appeared without a key")
            result.setdefault(current_list, []).append(line.lstrip()[2:].strip())
            continue
        if ":" not in line:
            raise SupervisorError("structured block contains an invalid line")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in result:
            raise SupervisorError("structured block contains a missing or duplicate key")
        if value:
            result[key] = value
            current_list = None
        else:
            result[key] = []
            current_list = key
    return result


def _validate_path_pattern(value: str) -> str:
    pattern = value.strip().strip("`")
    if not pattern or not SAFE_PATH_RE.fullmatch(pattern):
        raise SupervisorError("authorization contains an unsafe path")
    if any(character in pattern for character in "?[]"):
        raise SupervisorError("only exact paths and a bounded /** suffix are supported")
    if "*" in pattern and not pattern.endswith("/**"):
        raise SupervisorError("authorization wildcard must be a bounded /** suffix")
    if pattern.count("*") not in {0, 2}:
        raise SupervisorError("authorization wildcard is invalid")
    return pattern


def path_allowed(path: str, patterns: Sequence[str]) -> bool:
    if not SAFE_PATH_RE.fullmatch(path):
        return False
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if prefix and (path == prefix or path.startswith(prefix + "/")):
                return True
        elif path == pattern:
            return True
    return False


def is_protected_path(path: str) -> bool:
    return (
        path in PROTECTED_EXACT
        or path.startswith(PROTECTED_PREFIXES)
        or any(fnmatch.fnmatchcase(path, pattern) for pattern in PROTECTED_GLOBS)
    )


def _heading_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    active = False
    for raw in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", raw.strip())
        if heading:
            title = heading.group(1).strip().casefold()
            active = title in ALLOWED_SCOPE_HEADINGS
            continue
        if active and raw.strip().startswith("<!--"):
            active = False
            continue
        if active and raw.strip().startswith("- "):
            paths.append(_validate_path_pattern(raw.strip()[2:]))
    return tuple(paths)


def _immutable_trusted_comments(
    comments: Sequence[Mapping[str, Any]],
    trusted_logins: Iterable[str],
    *,
    authorization_only: bool,
) -> tuple[ImmutableComment, ...]:
    trusted = {login.casefold() for login in trusted_logins}
    result: list[ImmutableComment] = []
    seen: set[int] = set()
    for raw in comments:
        author = raw.get("user") or raw.get("author") or {}
        login = str(author.get("login") or "")
        if login.casefold() not in trusted:
            continue
        created = str(raw.get("created_at") or raw.get("createdAt") or "")
        updated = str(raw.get("updated_at") or raw.get("updatedAt") or "")
        body = str(raw.get("body") or "")
        comment_id = raw.get("id")
        if (
            not created
            or created != updated
            or isinstance(comment_id, bool)
            or not isinstance(comment_id, int)
            or comment_id <= 0
            or comment_id in seen
        ):
            continue
        if authorization_only and "foundation-protected-authorization-amendment" not in body:
            continue
        seen.add(comment_id)
        result.append(
            ImmutableComment(
                comment_id=comment_id,
                login=login,
                created_at=created,
                updated_at=updated,
                body=_safe_text(body, "trusted comment"),
            )
        )
    return tuple(sorted(result, key=lambda item: item.comment_id))


def _parse_task_block(issue_body: str) -> tuple[str | None, tuple[str, ...], tuple[str, ...], str | None, str | None]:
    blocks = list(TASK_BLOCK_RE.finditer(issue_body))
    if len(blocks) > 1:
        raise SupervisorError("Issue contains multiple task-scope blocks")
    if not blocks:
        return None, (), (), None, None
    parsed = _parse_simple_block(blocks[0].group("body"))
    allowed_keys = {"risk", "risk_tier", "paths", "checks", "operation", "prohibited"}
    if set(parsed) - allowed_keys:
        raise SupervisorError("task-scope block contains unsupported fields")
    risk = parsed.get("risk") or parsed.get("risk_tier")
    paths = parsed.get("paths")
    checks = parsed.get("checks") or list(DEFAULT_REQUIRED_CHECKS)
    operation = parsed.get("operation")
    prohibited = parsed.get("prohibited")
    if risk not in {"low", "standard", "protected"}:
        raise SupervisorError("task scope risk is invalid")
    if not isinstance(paths, list) or not paths:
        raise SupervisorError("task scope paths are required")
    if not isinstance(checks, list) or not checks:
        raise SupervisorError("task scope checks are required")
    if not isinstance(operation, str) or not operation.strip():
        raise SupervisorError("task scope operation is required")
    if not isinstance(prohibited, str) or not prohibited.strip():
        raise SupervisorError("task scope prohibited effects are required")
    return (
        risk,
        tuple(_validate_path_pattern(item) for item in paths),
        tuple(_safe_text(item, "required check", 120).strip() for item in checks),
        operation.strip(),
        prohibited.strip(),
    )


def parse_task_scope(
    issue_body: str,
    source_comments: Sequence[Mapping[str, Any]],
    trusted_logins: Iterable[str],
) -> TaskScope:
    task_risk, task_paths, task_checks, task_operation, task_prohibited = _parse_task_block(issue_body)
    ordinary_paths = set(task_paths or _heading_paths(issue_body))
    if not ordinary_paths:
        raise SupervisorError("Issue has no trusted ordinary path scope")

    authorization_comments = _immutable_trusted_comments(
        source_comments, trusted_logins, authorization_only=True
    )
    protected_paths: set[str] = set()
    operation = task_operation or "bounded owner-authorized Issue operation"
    prohibited = task_prohibited or "no effects outside the trusted Issue"
    blocks: list[tuple[str, bool]] = [(match.group("body"), False) for match in PROTECTED_BLOCK_RE.finditer(issue_body)]
    for comment in authorization_comments:
        blocks.extend((match.group("body"), True) for match in PROTECTED_BLOCK_RE.finditer(comment.body))

    for block_body, amendment in blocks:
        parsed = _parse_simple_block(block_body)
        required = {"category", "paths", "operation", "prohibited", "validation", "rollback"}
        if not required.issubset(parsed):
            raise SupervisorError("protected authorization is incomplete")
        paths = parsed.get("paths")
        if not isinstance(paths, list) or not paths:
            raise SupervisorError("protected authorization paths are required")
        normalized = {_validate_path_pattern(item) for item in paths}
        protected_paths.update(normalized)
        if amendment:
            ordinary_paths.update(normalized)

    risk = task_risk or ("protected" if blocks or any(is_protected_path(path.rstrip("/**")) for path in ordinary_paths) else "standard")
    checks = task_checks or DEFAULT_REQUIRED_CHECKS
    if len(ordinary_paths) != len(set(ordinary_paths)) or len(checks) != len(set(checks)):
        raise SupervisorError("scope contains duplicate paths or checks")
    if risk == "protected":
        if not protected_paths:
            raise SupervisorError("protected risk requires explicit protected authorization")
        initial_protected = {
            path for path in ordinary_paths if is_protected_path(path.rstrip("/**"))
        }
        if not all(
            any(path_allowed(path.rstrip("/**"), (pattern,)) for pattern in protected_paths)
            for path in initial_protected
        ):
            raise SupervisorError("ordinary protected paths are not independently authorized")
    return TaskScope(
        risk=risk,
        paths=tuple(sorted(ordinary_paths)),
        protected_paths=tuple(sorted(protected_paths)),
        checks=tuple(checks),
        operation=operation,
        prohibited=prohibited,
        authorization_comments=authorization_comments,
    )


def source_issue_number(pr_body: str) -> int:
    numbers = {int(value) for value in CLOSES_RE.findall(pr_body)}
    if len(numbers) != 1:
        raise SupervisorError("Pull Request body must bind exactly one source Issue")
    return next(iter(numbers))


def changed_paths(files: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    paths: list[str] = []
    for file in files:
        filename = _safe_text(file.get("filename"), "changed filename", 500)
        if not SAFE_PATH_RE.fullmatch(filename):
            raise SupervisorError("Pull Request contains an unsafe changed path")
        paths.append(filename)
        previous = file.get("previous_filename")
        if previous is not None:
            previous_text = _safe_text(previous, "previous filename", 500)
            if not SAFE_PATH_RE.fullmatch(previous_text):
                raise SupervisorError("Pull Request contains an unsafe previous path")
            paths.append(previous_text)
    unique = tuple(sorted(set(paths)))
    if not unique or len(unique) > MAX_FILES:
        raise SupervisorError("Pull Request changed path set is empty or excessive")
    return unique


def _pr_identity(pr: Mapping[str, Any], repo: str, default_branch: str) -> tuple[int, str]:
    number = _positive_int(pr.get("number"), "Pull Request number")
    if pr.get("state") != "open":
        raise SupervisorError("Pull Request is not open")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    if str(head_repo or "").casefold() != repo.casefold() or str(base_repo or "").casefold() != repo.casefold():
        raise SupervisorError("Pull Request is not same-repository")
    if str(base.get("ref") or "") != default_branch:
        raise SupervisorError("Pull Request does not target the current default branch")
    return number, _exact_sha(head.get("sha"), "Pull Request head")


def _check_snapshot(
    runs: Sequence[Mapping[str, Any]],
    required: Sequence[str],
    head_sha: str,
    repo: str,
    pr_number: int,
) -> tuple[tuple[str, int, str, str], ...]:
    if len(runs) > MAX_RUNS:
        raise SupervisorError("workflow run count exceeds the bound")
    latest: dict[str, tuple[str, int, str]] = {}
    for run in runs:
        if run.get("event") != "pull_request" or run.get("head_sha") != head_sha:
            continue
        run_repo = run.get("repository") or {}
        if str(run_repo.get("full_name") or "").casefold() != repo.casefold():
            continue
        pulls = run.get("pull_requests")
        if not isinstance(pulls, list) or not any(
            isinstance(item, dict) and item.get("number") == pr_number for item in pulls
        ):
            continue
        name = str(run.get("name") or "")
        if name not in required:
            continue
        updated = str(run.get("updated_at") or run.get("created_at") or "")
        run_id = run.get("id")
        if not updated or isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise SupervisorError("workflow evidence is malformed")
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "") if status == "completed" else "pending"
        candidate = (updated, run_id, conclusion)
        if name not in latest or candidate[:2] > latest[name][:2]:
            latest[name] = candidate
    snapshot = tuple(
        (name, latest.get(name, ("", 0, "missing"))[1], latest.get(name, ("", 0, "missing"))[2], latest.get(name, ("", 0, "missing"))[0])
        for name in required
    )
    return snapshot


def review_evidence(
    comments: Sequence[Mapping[str, Any]],
    threads: Sequence[Mapping[str, Any]],
    head_sha: str,
    risk: str,
    trusted_logins: Iterable[str],
) -> ReviewEvidence:
    trusted = {value.casefold() for value in trusted_logins}
    thread_snapshot: list[tuple[str, bool]] = []
    seen_threads: set[str] = set()
    for thread in threads:
        thread_id = str(thread.get("id") or "")
        resolved = thread.get("isResolved")
        if not thread_id or thread_id in seen_threads or not isinstance(resolved, bool):
            raise SupervisorError("review-thread evidence is malformed or duplicate")
        seen_threads.add(thread_id)
        thread_snapshot.append((thread_id, resolved))
    thread_snapshot.sort()
    unresolved = sum(not resolved for _, resolved in thread_snapshot)

    general = {"clean": 0, "blocked": 0}
    passes = {
        "scope-security": {"clean": 0, "blocked": 0},
        "correctness-race": {"clean": 0, "blocked": 0},
    }
    marker_ids: list[int] = []
    ambiguous = False
    seen_comment_ids: set[int] = set()
    for comment in comments:
        author = comment.get("user") or comment.get("author") or {}
        login = str(author.get("login") or "")
        if login.casefold() not in trusted:
            continue
        created = str(comment.get("created_at") or comment.get("createdAt") or "")
        updated = str(comment.get("updated_at") or comment.get("updatedAt") or "")
        comment_id = comment.get("id")
        if (
            not created
            or created != updated
            or isinstance(comment_id, bool)
            or not isinstance(comment_id, int)
            or comment_id <= 0
            or comment_id in seen_comment_ids
        ):
            continue
        seen_comment_ids.add(comment_id)
        body = _safe_text(comment.get("body"), "review comment")
        spans: list[tuple[int, int]] = []
        for match in GENERAL_REVIEW_RE.finditer(body):
            if match.group(1) == head_sha:
                general[match.group(2)] += 1
                spans.append(match.span())
        for match in PASS_REVIEW_RE.finditer(body):
            if match.group(1) == head_sha:
                passes[match.group(2)][match.group(3)] += 1
                spans.append(match.span())
        if f"foundation-coordinator-review:{head_sha}:" in body and not spans:
            ambiguous = True
        if spans:
            remainder = body
            for start, end in reversed(sorted(spans)):
                remainder = remainder[:start] + remainder[end:]
            if len(remainder.strip()) < 20:
                ambiguous = True
            marker_ids.append(comment_id)

    if unresolved:
        state = "pending"
    elif general["blocked"] or any(values["blocked"] for values in passes.values()):
        state = "blocked"
    elif ambiguous:
        state = "pending"
    elif risk == "protected":
        state = (
            "clean"
            if general["clean"] == 0
            and passes["scope-security"]["clean"] == 1
            and passes["correctness-race"]["clean"] == 1
            else ("pending" if marker_ids else "required")
        )
    else:
        state = (
            "clean"
            if general["clean"] == 1
            and not any(values["clean"] for values in passes.values())
            else ("pending" if marker_ids else "required")
        )
    return ReviewEvidence(
        state=state,
        unresolved_threads=unresolved,
        marker_ids=tuple(sorted(marker_ids)),
        thread_snapshot=tuple(thread_snapshot),
    )


def _comment_snapshot(comments: Sequence[ImmutableComment]) -> tuple[tuple[int, str, str, str, str], ...]:
    return tuple(
        (item.comment_id, item.login, item.created_at, item.updated_at, item.body)
        for item in comments
    )


def _overlap_snapshot(
    client: Client,
    repo: str,
    pr_number: int,
    candidate_paths: Sequence[str],
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    result: list[tuple[int, tuple[str, ...]]] = []
    pulls = client.open_pulls()
    if len(pulls) > MAX_OPEN_PULLS:
        raise SupervisorError("open Pull Request count exceeds the bound")
    for other in pulls:
        other_number = _positive_int(other.get("number"), "open Pull Request number")
        if other_number == pr_number:
            continue
        other_repo = ((other.get("head") or {}).get("repo") or {}).get("full_name")
        if str(other_repo or "").casefold() != repo.casefold():
            continue
        overlap = tuple(sorted(set(candidate_paths) & set(changed_paths(client.pull_files(other_number)))))
        if overlap:
            result.append((other_number, overlap))
    return tuple(sorted(result))


def evaluate(
    client: Client,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    if not REPO_RE.fullmatch(repo):
        raise SupervisorError("repository identity is invalid")
    repository = client.repository()
    if str(repository.get("full_name") or repo).casefold() != repo.casefold():
        raise SupervisorError("repository metadata identity mismatch")
    default_branch = _safe_text(repository.get("default_branch"), "default branch", 200)
    owner_login = _login(
        str((repository.get("owner") or {}).get("login") or repo.split("/", 1)[0]),
        "repository owner",
    )
    trusted_logins = {owner_login}
    if automation_owner:
        trusted_logins.add(_login(automation_owner, "automation owner"))
    default_sha = client.default_branch_sha(default_branch)

    pr = client.pull(pr_number)
    _, head_sha = _pr_identity(pr, repo, default_branch)
    pr_body = _safe_text(pr.get("body"), "Pull Request body")
    source_number = source_issue_number(pr_body)
    issue = client.issue(source_number)
    issue_body = _safe_text(issue.get("body"), "Issue body")
    issue_updated = str(issue.get("updated_at") or "")
    issue_author = str((issue.get("user") or {}).get("login") or "")
    if issue.get("state") != "open" or issue_author.casefold() not in {item.casefold() for item in trusted_logins}:
        raise SupervisorError("source Issue is not open and trusted")
    source_comments_raw = client.issue_comments(source_number)
    scope = parse_task_scope(issue_body, source_comments_raw, trusted_logins)
    source_comment_state = _comment_snapshot(scope.authorization_comments)

    files_snapshot = changed_paths(client.pull_files(pr_number))
    unauthorized = [path for path in files_snapshot if not path_allowed(path, scope.paths)]
    if unauthorized:
        raise SupervisorError("Pull Request changed paths exceed the source Issue")
    for path in files_snapshot:
        if is_protected_path(path) and not path_allowed(path, scope.protected_paths):
            raise SupervisorError("protected changed path lacks explicit protected authorization")
    if any(is_protected_path(path) for path in files_snapshot) and scope.risk != "protected":
        raise SupervisorError("protected paths require protected risk")

    overlaps = _overlap_snapshot(client, repo, pr_number, files_snapshot)
    if overlaps:
        raise SupervisorError(f"live Pull Request #{overlaps[0][0]} overlaps the candidate path set")

    for check_name, workflow_path in CHECK_WORKFLOWS.items():
        if check_name in scope.checks:
            if client.file_blob(workflow_path, head_sha) != client.file_blob(workflow_path, default_sha):
                raise SupervisorError(f"{check_name} workflow differs from the default-branch definition")

    checks = _check_snapshot(client.workflow_runs(head_sha), scope.checks, head_sha, repo, pr_number)
    if any(state not in PASSING for _, _, state, _ in checks):
        raise SupervisorError("required exact-head checks are not all successful")

    pr_comments = client.issue_comments(pr_number)
    threads = client.review_threads(pr_number)
    review = review_evidence(pr_comments, threads, head_sha, scope.risk, trusted_logins)
    if review.state != "clean":
        raise SupervisorError(f"coordinator review is {review.state}")

    final_issue = client.issue(source_number)
    if (
        final_issue.get("state") != "open"
        or str(final_issue.get("body") or "") != issue_body
        or str(final_issue.get("updated_at") or "") != issue_updated
        or str((final_issue.get("user") or {}).get("login") or "").casefold() != issue_author.casefold()
    ):
        raise SupervisorError("source Issue changed during evaluation")
    final_source_comments = parse_task_scope(
        issue_body, client.issue_comments(source_number), trusted_logins
    )
    if _comment_snapshot(final_source_comments.authorization_comments) != source_comment_state:
        raise SupervisorError("source Issue authorization comments changed during evaluation")
    if client.default_branch_sha(default_branch) != default_sha:
        raise SupervisorError("default branch changed during evaluation")
    if changed_paths(client.pull_files(pr_number)) != files_snapshot:
        raise SupervisorError("Pull Request changed paths moved during evaluation")
    if _overlap_snapshot(client, repo, pr_number, files_snapshot) != overlaps:
        raise SupervisorError("Pull Request collision state changed during evaluation")
    final_checks = _check_snapshot(
        client.workflow_runs(head_sha), scope.checks, head_sha, repo, pr_number
    )
    if final_checks != checks:
        raise SupervisorError("exact-head check evidence changed during evaluation")
    final_review = review_evidence(
        client.issue_comments(pr_number),
        client.review_threads(pr_number),
        head_sha,
        scope.risk,
        trusted_logins,
    )
    if final_review != review:
        raise SupervisorError("coordinator review evidence changed during evaluation")

    final_pr = client.pull(pr_number)
    _, final_head = _pr_identity(final_pr, repo, default_branch)
    if (
        final_head != head_sha
        or str(final_pr.get("body") or "") != pr_body
        or final_pr.get("mergeable") is not True
    ):
        raise SupervisorError("Pull Request identity, body, head, or mergeability changed")
    labels = {str(item.get("name") or "") for item in final_pr.get("labels") or []}
    if "ai-no-merge" in labels:
        raise SupervisorError("Pull Request has ai-no-merge hold")
    action = "mark_ready" if final_pr.get("draft") is True else "merge"
    return Decision(
        action=action,
        reason="all exact-head GitHub coordinator gates are clean",
        head_sha=head_sha,
        risk=scope.risk,
        unresolved_threads=0,
    )


def supervise(
    client: Client,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    decision = evaluate(client, repo, pr_number, automation_owner)
    if decision.action == "mark_ready":
        pr = client.pull(pr_number)
        if _exact_sha((pr.get("head") or {}).get("sha"), "ready head") != decision.head_sha:
            raise SupervisorError("Pull Request head moved before Ready mutation")
        client.mark_ready(_safe_text(pr.get("node_id"), "Pull Request node id", 200))
        decision = evaluate(client, repo, pr_number, automation_owner)
    if decision.action != "merge":
        raise SupervisorError("fresh post-Ready evaluation did not produce a merge decision")
    client.merge(pr_number, decision.head_sha)
    return decision


class GhClient:
    """Fixed same-repository GitHub CLI adapter."""

    REVIEW_QUERY = """
query ReviewThreads($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){
    nameWithOwner
    pullRequest(number:$number){
      number
      headRefOid
      reviewThreads(first:100,after:$cursor){
        nodes{id isResolved}
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}
""".strip()

    READY_MUTATION = (
        "mutation MarkReady($id:ID!){"
        "markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{id}}}"
    )

    def __init__(self, repo: str) -> None:
        if not REPO_RE.fullmatch(repo):
            raise SupervisorError("repository identity is invalid")
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)

    def _run(self, args: Sequence[str], stdin: str | None = None) -> Any:
        completed = subprocess.run(
            ["gh", *args],
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise SupervisorError("GitHub API request failed")
        try:
            return json.loads(completed.stdout or "null")
        except json.JSONDecodeError as exc:
            raise SupervisorError("GitHub API returned invalid JSON") from exc

    def _api(
        self,
        path: str,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        if not path.startswith(f"repos/{self.repo}/") and path != f"repos/{self.repo}":
            raise SupervisorError("API path escaped the fixed repository")
        args = ["api", "--method", method, path]
        stdin = None
        if payload is not None:
            args += ["--input", "-"]
            stdin = json.dumps(payload, separators=(",", ":"))
        return self._run(args, stdin)

    def _slurp(self, path: str) -> list[Any]:
        pages = self._run(["api", "--paginate", "--slurp", path])
        if not isinstance(pages, list) or len(pages) > MAX_PAGES:
            raise SupervisorError("GitHub pagination exceeded the bound")
        return pages

    def _array_pages(self, path: str) -> list[Mapping[str, Any]]:
        values: list[Mapping[str, Any]] = []
        for page in self._slurp(path):
            if not isinstance(page, list):
                raise SupervisorError("GitHub array pagination is malformed")
            for item in page:
                if not isinstance(item, dict):
                    raise SupervisorError("GitHub paginated item is malformed")
                values.append(item)
        return values

    def repository(self) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}")

    def default_branch_sha(self, branch: str) -> str:
        return _exact_sha(
            self._api(f"repos/{self.repo}/commits/{urllib.parse.quote(branch, safe='')}").get("sha"),
            "default branch SHA",
        )

    def pull(self, number: int) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}/pulls/{number}")

    def issue(self, number: int) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}/issues/{number}")

    def issue_comments(self, number: int) -> Sequence[Mapping[str, Any]]:
        values = self._array_pages(f"repos/{self.repo}/issues/{number}/comments?per_page=100")
        if len(values) > MAX_COMMENTS:
            raise SupervisorError("comment count exceeds the bound")
        return values

    def pull_files(self, number: int) -> Sequence[Mapping[str, Any]]:
        return self._array_pages(f"repos/{self.repo}/pulls/{number}/files?per_page=100")

    def open_pulls(self) -> Sequence[Mapping[str, Any]]:
        values = self._array_pages(f"repos/{self.repo}/pulls?state=open&per_page=100")
        if len(values) > MAX_OPEN_PULLS:
            raise SupervisorError("open Pull Request count exceeds the bound")
        return values

    def workflow_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]:
        runs: list[Mapping[str, Any]] = []
        path = (
            f"repos/{self.repo}/actions/runs?event=pull_request"
            f"&head_sha={head_sha}&per_page=100"
        )
        for page in self._slurp(path):
            if not isinstance(page, dict):
                raise SupervisorError("workflow pagination is malformed")
            page_runs = page.get("workflow_runs")
            if not isinstance(page_runs, list):
                raise SupervisorError("workflow page omitted workflow_runs")
            for item in page_runs:
                if not isinstance(item, dict):
                    raise SupervisorError("workflow run is malformed")
                runs.append(item)
        return runs

    def review_threads(self, number: int) -> Sequence[Mapping[str, Any]]:
        cursor: str | None = None
        nodes: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            args = [
                "api",
                "graphql",
                "-f",
                f"query={self.REVIEW_QUERY}",
                "-F",
                f"owner={self.owner}",
                "-F",
                f"name={self.name}",
                "-F",
                f"number={number}",
            ]
            if cursor:
                args += ["-F", f"cursor={cursor}"]
            response = self._run(args)
            repository = (response.get("data") or {}).get("repository") or {}
            if str(repository.get("nameWithOwner") or "").casefold() != self.repo.casefold():
                raise SupervisorError("GraphQL repository identity mismatch")
            pull = repository.get("pullRequest") or {}
            if pull.get("number") != number:
                raise SupervisorError("GraphQL Pull Request identity mismatch")
            connection = pull.get("reviewThreads") or {}
            page_nodes = connection.get("nodes")
            page_info = connection.get("pageInfo") or {}
            if not isinstance(page_nodes, list) or not isinstance(page_info.get("hasNextPage"), bool):
                raise SupervisorError("review-thread response is malformed")
            for node in page_nodes:
                if not isinstance(node, dict):
                    raise SupervisorError("review-thread node is malformed")
                node_id = str(node.get("id") or "")
                if not node_id or node_id in seen:
                    raise SupervisorError("review-thread pagination is duplicate or invalid")
                seen.add(node_id)
                nodes.append(node)
            if not page_info["hasNextPage"]:
                return nodes
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise SupervisorError("review-thread cursor is invalid")
            cursor = next_cursor
        raise SupervisorError("review-thread pagination exceeded the bound")

    def file_blob(self, path: str, ref: str) -> str:
        quoted = urllib.parse.quote(path, safe="/")
        value = self._api(f"repos/{self.repo}/contents/{quoted}?ref={ref}")
        if value.get("type") != "file":
            raise SupervisorError("required workflow blob is not a file")
        return _exact_sha(value.get("sha"), "workflow blob SHA")

    def mark_ready(self, node_id: str) -> None:
        self._run(
            [
                "api",
                "graphql",
                "-f",
                f"query={self.READY_MUTATION}",
                "-F",
                f"id={node_id}",
            ]
        )

    def merge(self, number: int, head_sha: str) -> None:
        result = self._api(
            f"repos/{self.repo}/pulls/{number}/merge",
            method="PUT",
            payload={"sha": head_sha, "merge_method": "merge"},
        )
        if result.get("merged") is not True:
            raise SupervisorError("expected-head merge was rejected")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-number", action="append", type=int)
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    owner = os.environ.get("AUTOMATION_OWNER") or None
    try:
        client = GhClient(repo)
        numbers = args.pr_number or [
            _positive_int(item.get("number"), "open Pull Request number")
            for item in client.open_pulls()
        ]
        results: list[dict[str, Any]] = []
        for number in numbers:
            try:
                decision = supervise(client, repo, number, owner)
                results.append(
                    {
                        "pr_number": number,
                        "action": decision.action,
                        "head_sha": decision.head_sha,
                        "risk_tier": decision.risk,
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
                        "review_route": "github-coordinator",
                        "human_action_required": False,
                    }
                )
        print(json.dumps(results, sort_keys=True))
        return 0
    except (SupervisorError, OSError, json.JSONDecodeError) as exc:
        print(f"github-coordinator-supervisor: blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
