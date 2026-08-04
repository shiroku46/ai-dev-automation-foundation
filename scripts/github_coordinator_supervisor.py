#!/usr/bin/env python3
"""Default-branch-controlled GitHub-only Pull Request supervisor.

The supervisor never checks out or executes Pull Request code. It reads fixed
same-repository GitHub evidence, evaluates the owner-authored task scope and
current-head coordinator review, and performs at most one bounded mutation:
mark the exact Draft Pull Request ready, or merge the exact expected head.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CLOSES_RE = re.compile(r"(?im)^\s*(?:closes|fixes|resolves)\s+#([1-9][0-9]*)\s*[.]?\s*$")
TASK_BLOCK_RE = re.compile(
    r"<!--\s*foundation-task-scope\s*\n(?P<body>.*?)\n\s*-->", re.DOTALL
)
PROTECTED_BLOCK_RE = re.compile(
    r"<!--\s*foundation-protected-authorization\s*\n(?P<body>.*?)\n\s*-->",
    re.DOTALL,
)
GENERAL_REVIEW_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):(clean|blocked) -->"
)
PASS_REVIEW_RE = re.compile(
    r"<!-- foundation-coordinator-review:([0-9a-f]{40}):"
    r"(scope-security|correctness-race):(clean|blocked) -->"
)
SAFE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*[\x00-\x1f\\])[^:]+$")
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
DEFAULT_REQUIRED_CHECKS = ("CI", "Unit Tests")
PASSING = frozenset({"success", "neutral", "skipped"})
MAX_PAGES = 20
MAX_FILES = 3000
MAX_COMMENTS = 3000
MAX_OPEN_PULLS = 500


class SupervisorError(RuntimeError):
    """A fail-closed supervisor decision."""


@dataclass(frozen=True)
class TaskScope:
    risk: str
    paths: tuple[str, ...]
    checks: tuple[str, ...]
    operation: str
    prohibited: str


@dataclass(frozen=True)
class ReviewEvidence:
    state: str
    unresolved_threads: int
    marker_ids: tuple[int, ...]


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
    def pull_files(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def open_pulls(self) -> Sequence[Mapping[str, Any]]: ...
    def workflow_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]: ...
    def issue_comments(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def review_threads(self, number: int) -> Sequence[Mapping[str, Any]]: ...
    def mark_ready(self, node_id: str) -> None: ...
    def merge(self, number: int, head_sha: str) -> None: ...


def _safe_text(value: Any, where: str, limit: int = 20000) -> str:
    if not isinstance(value, str):
        raise SupervisorError(f"{where} must be text")
    if len(value) > limit or "\x00" in value:
        raise SupervisorError(f"{where} exceeds the bounded text contract")
    return value


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SupervisorError(f"{where} must be a positive integer")
    return value


def _exact_sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise SupervisorError(f"{where} must be a lowercase 40-character SHA")
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
                raise SupervisorError("list item appeared without a key")
            result.setdefault(current_list, []).append(line.lstrip()[2:].strip())
            continue
        if ":" not in line:
            raise SupervisorError("structured scope contains an invalid line")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in result:
            raise SupervisorError(f"structured scope repeats {key}")
        if value:
            result[key] = value
            current_list = None
        else:
            result[key] = []
            current_list = key
    return result


def _parse_heading_paths(issue_body: str) -> tuple[str, ...]:
    heading = re.search(
        r"(?im)^##\s+(?:Exact\s+)?Allowed paths\s*$", issue_body
    )
    if not heading:
        return ()
    lines: list[str] = []
    for raw in issue_body[heading.end() :].splitlines():
        if raw.startswith("## ") or raw.startswith("<!--"):
            break
        stripped = raw.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip().strip("`"))
    return tuple(lines)


def is_protected_path(path: str) -> bool:
    return (
        path in PROTECTED_EXACT
        or path.startswith(PROTECTED_PREFIXES)
        or any(fnmatch.fnmatchcase(path, pattern) for pattern in PROTECTED_GLOBS)
    )


def _validate_path_pattern(pattern: str) -> str:
    pattern = pattern.strip().strip("`")
    if not pattern or not SAFE_PATH_RE.fullmatch(pattern):
        raise SupervisorError("task scope contains an unsafe path")
    if any(char in pattern for char in "?[]"):
        raise SupervisorError("task scope supports only exact paths and bounded /** suffixes")
    if "*" in pattern and not pattern.endswith("/**"):
        raise SupervisorError("task scope wildcard must be a bounded /** suffix")
    if pattern.count("*") not in {0, 2}:
        raise SupervisorError("task scope wildcard is invalid")
    return pattern


def path_allowed(path: str, patterns: Sequence[str]) -> bool:
    if not SAFE_PATH_RE.fullmatch(path):
        return False
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        elif path == pattern:
            return True
    return False


def parse_task_scope(issue_body: str) -> TaskScope:
    blocks = list(TASK_BLOCK_RE.finditer(issue_body))
    if len(blocks) > 1:
        raise SupervisorError("Issue contains multiple task-scope blocks")
    if blocks:
        parsed = _parse_simple_block(blocks[0].group("body"))
        allowed_keys = {"risk", "paths", "operation", "prohibited", "checks"}
        extra = set(parsed) - allowed_keys
        if extra:
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
        normalized_paths = tuple(_validate_path_pattern(item) for item in paths)
        normalized_checks = tuple(_safe_text(item, "check", 120).strip() for item in checks)
        if len(set(normalized_paths)) != len(normalized_paths):
            raise SupervisorError("task scope paths contain duplicates")
        if len(set(normalized_checks)) != len(normalized_checks):
            raise SupervisorError("task scope checks contain duplicates")
        return TaskScope(
            risk=risk,
            paths=normalized_paths,
            checks=normalized_checks,
            operation=operation.strip(),
            prohibited=prohibited.strip(),
        )

    paths = _parse_heading_paths(issue_body)
    if not paths:
        raise SupervisorError("Issue has no trusted path scope")
    normalized_paths = tuple(_validate_path_pattern(item) for item in paths)
    protected = list(PROTECTED_BLOCK_RE.finditer(issue_body))
    if len(protected) > 1:
        raise SupervisorError("Issue contains multiple protected authorization blocks")
    risk = "protected" if protected or any(is_protected_path(path.rstrip("/**")) for path in normalized_paths) else "standard"
    operation = "bounded legacy Issue operation"
    prohibited = "no effects outside the trusted Issue"
    if protected:
        parsed = _parse_simple_block(protected[0].group("body"))
        operation = str(parsed.get("operation") or "").strip()
        prohibited = str(parsed.get("prohibited") or "").strip()
        protected_paths = parsed.get("paths")
        if not operation or not prohibited or not isinstance(protected_paths, list):
            raise SupervisorError("protected authorization is incomplete")
        for path in protected_paths:
            _validate_path_pattern(path)
    return TaskScope(
        risk=risk,
        paths=normalized_paths,
        checks=DEFAULT_REQUIRED_CHECKS,
        operation=operation,
        prohibited=prohibited,
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
    if not unique:
        raise SupervisorError("Pull Request contains no changed paths")
    if len(unique) > MAX_FILES:
        raise SupervisorError("Pull Request changed path count exceeds the bound")
    return unique


def _latest_checks(
    runs: Sequence[Mapping[str, Any]],
    required: Sequence[str],
    head_sha: str,
    repo: str,
) -> dict[str, str]:
    latest: dict[str, tuple[str, int, str]] = {}
    for run in runs:
        if run.get("event") != "pull_request" or run.get("head_sha") != head_sha:
            continue
        run_repo = run.get("repository") or {}
        if str(run_repo.get("full_name") or "").casefold() != repo.casefold():
            continue
        name = str(run.get("name") or "")
        if name not in required:
            continue
        updated = str(run.get("updated_at") or run.get("created_at") or "")
        run_id = run.get("id")
        if not updated or isinstance(run_id, bool) or not isinstance(run_id, int):
            raise SupervisorError("workflow evidence is malformed")
        status = str(run.get("status") or "")
        conclusion = run.get("conclusion")
        state = "pending" if status != "completed" else str(conclusion or "missing")
        candidate = (updated, run_id, state)
        if name not in latest or candidate[:2] > latest[name][:2]:
            latest[name] = candidate
    result = {name: latest.get(name, ("", 0, "missing"))[2] for name in required}
    return result


def review_evidence(
    comments: Sequence[Mapping[str, Any]],
    threads: Sequence[Mapping[str, Any]],
    head_sha: str,
    risk: str,
    trusted_logins: Iterable[str],
) -> ReviewEvidence:
    unresolved = sum(not bool(thread.get("isResolved")) for thread in threads)
    trusted = {value.casefold() for value in trusted_logins}
    general_clean = general_blocked = 0
    pass_clean = {"scope-security": 0, "correctness-race": 0}
    pass_blocked = {"scope-security": 0, "correctness-race": 0}
    marker_ids: list[int] = []
    ambiguous = False
    for comment in comments:
        author = comment.get("user") or comment.get("author") or {}
        login = str(author.get("login") or "")
        if login.casefold() not in trusted:
            continue
        created = str(comment.get("created_at") or comment.get("createdAt") or "")
        updated = str(comment.get("updated_at") or comment.get("updatedAt") or "")
        if not created or created != updated:
            continue
        body = _safe_text(comment.get("body"), "review comment")
        spans: list[tuple[int, int]] = []
        for match in GENERAL_REVIEW_RE.finditer(body):
            if match.group(1) != head_sha:
                continue
            spans.append(match.span())
            if match.group(2) == "clean":
                general_clean += 1
            else:
                general_blocked += 1
        for match in PASS_REVIEW_RE.finditer(body):
            if match.group(1) != head_sha:
                continue
            spans.append(match.span())
            if match.group(3) == "clean":
                pass_clean[match.group(2)] += 1
            else:
                pass_blocked[match.group(2)] += 1
        if f"foundation-coordinator-review:{head_sha}:" in body and not spans:
            ambiguous = True
        if spans:
            remainder = body
            for start, end in reversed(sorted(spans)):
                remainder = remainder[:start] + remainder[end:]
            if len(remainder.strip()) < 20:
                ambiguous = True
            comment_id = comment.get("id")
            if isinstance(comment_id, int) and not isinstance(comment_id, bool):
                marker_ids.append(comment_id)
    if unresolved:
        return ReviewEvidence("pending", unresolved, tuple(sorted(marker_ids)))
    if general_blocked or any(pass_blocked.values()):
        return ReviewEvidence("blocked", 0, tuple(sorted(marker_ids)))
    if ambiguous:
        return ReviewEvidence("pending", 0, tuple(sorted(marker_ids)))
    if risk == "protected":
        clean = (
            general_clean == 0
            and pass_clean["scope-security"] == 1
            and pass_clean["correctness-race"] == 1
        )
    else:
        clean = general_clean == 1 and not any(pass_clean.values())
    state = "clean" if clean else ("pending" if marker_ids else "required")
    return ReviewEvidence(state, 0, tuple(sorted(marker_ids)))


def _validate_pr(pr: Mapping[str, Any], repo: str, default_branch: str) -> tuple[int, str]:
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


def evaluate(
    client: Client,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    if not REPO_RE.fullmatch(repo):
        raise SupervisorError("repository identity is invalid")
    repository = client.repository()
    default_branch = _safe_text(repository.get("default_branch"), "default branch", 200)
    owner_login = str((repository.get("owner") or {}).get("login") or repo.split("/", 1)[0])
    trusted_logins = {owner_login}
    if automation_owner:
        trusted_logins.add(automation_owner)
    default_sha = client.default_branch_sha(default_branch)

    pr = client.pull(pr_number)
    _, head_sha = _validate_pr(pr, repo, default_branch)
    source_number = source_issue_number(_safe_text(pr.get("body"), "Pull Request body"))
    issue = client.issue(source_number)
    issue_body = _safe_text(issue.get("body"), "Issue body")
    issue_updated = str(issue.get("updated_at") or "")
    issue_author = str((issue.get("user") or {}).get("login") or "")
    if issue.get("state") != "open" or issue_author.casefold() not in {value.casefold() for value in trusted_logins}:
        raise SupervisorError("source Issue is not open and trusted")
    scope = parse_task_scope(issue_body)

    files = client.pull_files(pr_number)
    paths = changed_paths(files)
    unauthorized = [path for path in paths if not path_allowed(path, scope.paths)]
    if unauthorized:
        raise SupervisorError("Pull Request changed paths exceed the source Issue")
    if any(is_protected_path(path) for path in paths) and scope.risk != "protected":
        raise SupervisorError("protected paths require protected risk")

    for other in client.open_pulls():
        other_number = _positive_int(other.get("number"), "open Pull Request number")
        if other_number == pr_number:
            continue
        other_head_repo = ((other.get("head") or {}).get("repo") or {}).get("full_name")
        if str(other_head_repo or "").casefold() != repo.casefold():
            continue
        other_paths = changed_paths(client.pull_files(other_number))
        overlap = sorted(set(paths) & set(other_paths))
        if overlap:
            raise SupervisorError(
                f"live Pull Request #{other_number} overlaps the candidate path set"
            )

    checks = _latest_checks(client.workflow_runs(head_sha), scope.checks, head_sha, repo)
    if any(state not in PASSING for state in checks.values()):
        raise SupervisorError("required exact-head checks are not all successful")

    review = review_evidence(
        client.issue_comments(pr_number),
        client.review_threads(pr_number),
        head_sha,
        scope.risk,
        trusted_logins,
    )
    if review.state != "clean":
        raise SupervisorError(f"coordinator review is {review.state}")

    # Re-fetch mutable authority before any mutation.
    final_issue = client.issue(source_number)
    if (
        final_issue.get("state") != "open"
        or str(final_issue.get("body") or "") != issue_body
        or str(final_issue.get("updated_at") or "") != issue_updated
    ):
        raise SupervisorError("source Issue changed during evaluation")
    if client.default_branch_sha(default_branch) != default_sha:
        raise SupervisorError("default branch changed during evaluation")

    final_pr = client.pull(pr_number)
    _, final_head = _validate_pr(final_pr, repo, default_branch)
    if final_head != head_sha or str(final_pr.get("body") or "") != str(pr.get("body") or ""):
        raise SupervisorError("Pull Request changed during evaluation")
    labels = {str(item.get("name") or "") for item in final_pr.get("labels") or []}
    if "ai-no-merge" in labels:
        raise SupervisorError("Pull Request has ai-no-merge hold")
    mergeable = final_pr.get("mergeable")
    if mergeable is not True:
        raise SupervisorError("Pull Request is not currently mergeable")
    if final_pr.get("draft") is True:
        return Decision("mark_ready", "all exact-head gates are clean", head_sha, scope.risk, 0)
    return Decision("merge", "all final live gates are clean", head_sha, scope.risk, 0)


def supervise(
    client: Client,
    repo: str,
    pr_number: int,
    automation_owner: str | None = None,
) -> Decision:
    decision = evaluate(client, repo, pr_number, automation_owner)
    if decision.action == "mark_ready":
        pr = client.pull(pr_number)
        if _exact_sha((pr.get("head") or {}).get("sha"), "final ready head") != decision.head_sha:
            raise SupervisorError("Pull Request head moved before Ready mutation")
        node_id = _safe_text(pr.get("node_id"), "Pull Request node id", 200)
        client.mark_ready(node_id)
    elif decision.action == "merge":
        # The merge call must be the next connected operation after evaluate's final PR fetch.
        client.merge(pr_number, decision.head_sha)
    else:
        raise SupervisorError("unknown supervisor action")
    return decision


class GhClient:
    """Fixed same-repository GitHub CLI adapter."""

    REVIEW_QUERY = """
query ReviewThreads($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviewThreads(first:100,after:$cursor){
        nodes{id isResolved}
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}
""".strip()

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

    def _api(self, path: str, method: str = "GET", payload: Mapping[str, Any] | None = None) -> Any:
        if not path.startswith(f"repos/{self.repo}/") and path != f"repos/{self.repo}":
            raise SupervisorError("API path escaped the fixed repository")
        args = ["api", "--method", method, path]
        stdin = None
        if payload is not None:
            args += ["--input", "-"]
            stdin = json.dumps(payload, separators=(",", ":"))
        return self._run(args, stdin)

    def _pages(self, path: str) -> list[Mapping[str, Any]]:
        pages = self._run(["api", "--paginate", "--slurp", path])
        if not isinstance(pages, list) or len(pages) > MAX_PAGES:
            raise SupervisorError("GitHub pagination exceeded the bound")
        flattened: list[Mapping[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise SupervisorError("GitHub paginated response is malformed")
            for item in page:
                if not isinstance(item, dict):
                    raise SupervisorError("GitHub paginated item is malformed")
                flattened.append(item)
        return flattened

    def repository(self) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}")

    def default_branch_sha(self, branch: str) -> str:
        value = self._api(f"repos/{self.repo}/commits/{branch}")
        return _exact_sha(value.get("sha"), "default branch SHA")

    def pull(self, number: int) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}/pulls/{number}")

    def issue(self, number: int) -> Mapping[str, Any]:
        return self._api(f"repos/{self.repo}/issues/{number}")

    def pull_files(self, number: int) -> Sequence[Mapping[str, Any]]:
        return self._pages(f"repos/{self.repo}/pulls/{number}/files?per_page=100")

    def open_pulls(self) -> Sequence[Mapping[str, Any]]:
        values = self._pages(f"repos/{self.repo}/pulls?state=open&per_page=100")
        if len(values) > MAX_OPEN_PULLS:
            raise SupervisorError("open Pull Request count exceeds the bound")
        return values

    def workflow_runs(self, head_sha: str) -> Sequence[Mapping[str, Any]]:
        encoded = f"event=pull_request&head_sha={head_sha}&per_page=100"
        values = self._pages(f"repos/{self.repo}/actions/runs?{encoded}")
        # --slurp for this endpoint yields dictionaries rather than page arrays on some gh versions.
        if values and "workflow_runs" in values[0]:
            runs: list[Mapping[str, Any]] = []
            for page in values:
                page_runs = page.get("workflow_runs")
                if not isinstance(page_runs, list):
                    raise SupervisorError("workflow run pagination is malformed")
                runs.extend(item for item in page_runs if isinstance(item, dict))
            return runs
        return values

    def issue_comments(self, number: int) -> Sequence[Mapping[str, Any]]:
        values = self._pages(f"repos/{self.repo}/issues/{number}/comments?per_page=100")
        if len(values) > MAX_COMMENTS:
            raise SupervisorError("comment count exceeds the bound")
        return values

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
            connection = (((response.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}
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

    def mark_ready(self, node_id: str) -> None:
        query = "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{id}}}"
        self._run(["api", "graphql", "-f", f"query={query}", "-F", f"id={node_id}"])

    def merge(self, number: int, head_sha: str) -> None:
        result = self._api(
            f"repos/{self.repo}/pulls/{number}/merge",
            method="PUT",
            payload={"sha": head_sha, "merge_method": "merge"},
        )
        if result.get("merged") is not True:
            raise SupervisorError("expected-head merge was rejected")


def pr_number_from_event(event: Mapping[str, Any], event_name: str) -> int:
    if event_name == "pull_request_target":
        return _positive_int((event.get("pull_request") or {}).get("number"), "event Pull Request")
    if event_name == "workflow_run":
        pull_requests = (event.get("workflow_run") or {}).get("pull_requests")
        if not isinstance(pull_requests, list) or len(pull_requests) != 1:
            raise SupervisorError("workflow_run must identify exactly one Pull Request")
        return _positive_int(pull_requests[0].get("number"), "workflow Pull Request")
    if event_name == "workflow_dispatch":
        return _positive_int((event.get("inputs") or {}).get("pr_number"), "dispatch Pull Request")
    raise SupervisorError("unsupported supervisor event")


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        event_path = Path(os.environ.get("GITHUB_EVENT_PATH", ""))
        event = json.loads(event_path.read_text(encoding="utf-8"))
        pr_number = pr_number_from_event(event, event_name)
        decision = supervise(
            GhClient(repo),
            repo,
            pr_number,
            os.environ.get("AUTOMATION_OWNER") or None,
        )
        print(
            json.dumps(
                {
                    "action": decision.action,
                    "reason": decision.reason,
                    "head_sha": decision.head_sha,
                    "risk_tier": decision.risk,
                    "review_route": "github-coordinator",
                    "human_action_required": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (SupervisorError, OSError, json.JSONDecodeError) as exc:
        print(f"github-coordinator-supervisor: blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
