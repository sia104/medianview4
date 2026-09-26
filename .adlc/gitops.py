"""Deterministic Git and GitHub operations for ADLC V0.4."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from adlc_config import AdlcConfig

RunCommand = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b"
    r"[ \t]*[:=][ \t]*[^\s]+"
)
TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
SECRET_NAME = re.compile(
    r"(^|[._-])(\.env|credentials?|secrets?|tokens?|id_rsa|private[-_]?key)"
    r"($|[._-])",
    re.IGNORECASE,
)
SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


class GitOpsError(RuntimeError):
    """A deterministic Git/GitHub operation could not complete safely."""


@dataclass(frozen=True)
class PublicationResult:
    remote: str
    branch: str
    commit: str
    pull_request_number: int
    pull_request_url: str


def run_command(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as error:
        raise GitOpsError(f"required command is unavailable: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "command failed").strip()
        raise GitOpsError(f"{command[0]} failed: {detail}") from error
    except subprocess.TimeoutExpired as error:
        raise GitOpsError(f"{command[0]} timed out") from error


def output(
    runner: RunCommand, project_root: Path, *command: str, required: bool = True
) -> str | None:
    try:
        value = runner(command, project_root).stdout.strip()
    except GitOpsError:
        if required:
            raise
        return None
    return value or None


def git_identity(project_root: Path, runner: RunCommand = run_command) -> tuple[str, str]:
    name = output(runner, project_root, "git", "config", "--get", "user.name", required=False)
    email = output(
        runner, project_root, "git", "config", "--get", "user.email", required=False
    )
    if not name or not email:
        raise GitOpsError(
            "Git user.name and user.email must be configured before the ADLC can "
            "create commits"
        )
    return name, email


def is_project_repository(project_root: Path, runner: RunCommand = run_command) -> bool:
    top = output(
        runner,
        project_root,
        "git",
        "rev-parse",
        "--show-toplevel",
        required=False,
    )
    return top is not None and Path(top).resolve() == project_root.resolve()


def publication_hazards(project_root: Path) -> list[dict[str, str]]:
    hazards: list[dict[str, str]] = []
    for path in sorted(project_root.rglob("*")):
        relative = path.relative_to(project_root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if relative.parts[:2] == (".adlc", "evidence"):
            continue
        if path.is_dir():
            continue
        if SECRET_NAME.search(path.name):
            hazards.append({"path": relative.as_posix(), "hazard": "sensitive-name"})
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if PRIVATE_KEY.search(text):
            hazards.append({"path": relative.as_posix(), "hazard": "private-key"})
        elif SECRET_ASSIGNMENT.search(text):
            hazards.append(
                {"path": relative.as_posix(), "hazard": "credential-assignment"}
            )
        elif any(pattern.search(text) for pattern in TOKEN_PATTERNS):
            hazards.append({"path": relative.as_posix(), "hazard": "credential-token"})
    return hazards


def ensure_publication_safe(project_root: Path) -> None:
    hazards = publication_hazards(project_root)
    if hazards:
        summary = ", ".join(
            f"{item['path']} ({item['hazard']})" for item in hazards
        )
        raise GitOpsError(f"public publication blocked: {summary}")


def ensure_github(runner: RunCommand, project_root: Path) -> None:
    if shutil.which("gh") is None and runner is run_command:
        raise GitOpsError("GitHub CLI is unavailable")
    output(runner, project_root, "gh", "auth", "status", "--hostname", "github.com")


def ensure_origin(
    project_root: Path,
    config: AdlcConfig,
    runner: RunCommand = run_command,
) -> str:
    existing = output(
        runner,
        project_root,
        "git",
        "remote",
        "get-url",
        "origin",
        required=False,
    )
    if existing:
        return existing
    ensure_github(runner, project_root)
    if config.repository_visibility == "public":
        ensure_publication_safe(project_root)
    flag = f"--{config.repository_visibility}"
    output(
        runner,
        project_root,
        "gh",
        "repo",
        "create",
        project_root.name,
        flag,
        "--source=.",
        "--remote=origin",
    )
    created = output(
        runner, project_root, "git", "remote", "get-url", "origin", required=False
    )
    if not created:
        raise GitOpsError("GitHub repository creation did not configure origin")
    return created


def bootstrap_project(
    project_root: Path,
    config: AdlcConfig,
    runner: RunCommand = run_command,
) -> dict[str, str | bool]:
    project_root = project_root.resolve()
    git_identity(project_root, runner)
    created_repository = False
    if not is_project_repository(project_root, runner):
        output(runner, project_root, "git", "init", "-b", config.base_branch)
        created_repository = True

    head = output(runner, project_root, "git", "rev-parse", "HEAD", required=False)
    if head is None:
        if config.repository_visibility == "public":
            ensure_publication_safe(project_root)
        output(
            runner,
            project_root,
            "git",
            "add",
            "-A",
            "--",
            ".",
            ":(exclude).adlc/evidence/**",
        )
        output(runner, project_root, "git", "commit", "-m", "Initialize ADLC repository")

    origin = ensure_origin(project_root, config, runner)
    remote_base = output(
        runner,
        project_root,
        "git",
        "rev-parse",
        f"origin/{config.base_branch}",
        required=False,
    )
    if remote_base is None:
        if config.repository_visibility == "public":
            ensure_publication_safe(project_root)
        output(
            runner,
            project_root,
            "git",
            "push",
            "-u",
            "origin",
            config.base_branch,
        )
    return {"repository_created": created_repository, "origin": origin}


def publish_feature(
    project_root: Path,
    config: AdlcConfig,
    runner: RunCommand = run_command,
    verification_summary: str = "deterministic verification passed",
    on_pushed: Callable[[str, str, str], None] | None = None,
) -> PublicationResult:
    if not is_project_repository(project_root, runner):
        raise GitOpsError("project root is not a Git repository")
    git_identity(project_root, runner)
    branch = output(runner, project_root, "git", "branch", "--show-current")
    if not branch:
        raise GitOpsError("current Git branch is unavailable")
    if branch == config.base_branch:
        raise GitOpsError("refusing to publish implementation from the base branch")
    status = output(
        runner, project_root, "git", "status", "--porcelain", required=False
    )
    if status:
        raise GitOpsError("feature branch must be clean and committed before publication")
    commit = output(runner, project_root, "git", "rev-parse", "HEAD")
    if not commit:
        raise GitOpsError("feature branch has no commit")
    count = output(
        runner,
        project_root,
        "git",
        "rev-list",
        "--count",
        f"{config.base_branch}..HEAD",
    )
    if count == "0":
        raise GitOpsError("feature branch has no commits beyond the base branch")
    origin = ensure_origin(project_root, config, runner)
    if config.repository_visibility == "public":
        ensure_publication_safe(project_root)
    output(runner, project_root, "git", "push", "-u", "origin", branch)
    if on_pushed is not None:
        on_pushed(origin, branch, commit)
    ensure_github(runner, project_root)
    title = output(runner, project_root, "git", "log", "-1", "--format=%s")
    commits = output(
        runner,
        project_root,
        "git",
        "log",
        "--format=- %s",
        f"{config.base_branch}..HEAD",
    )
    body = (
        f"## Changes\n{commits}\n\n## Verification\n{verification_summary}"
        "\n\nHuman review and merge are required."
    )
    pr_url = output(
        runner,
        project_root,
        "gh",
        "pr",
        "create",
        "--base",
        config.base_branch,
        "--head",
        branch,
        "--title",
        title or "ADLC feature",
        "--body",
        body,
    )
    details_text = output(
        runner,
        project_root,
        "gh",
        "pr",
        "view",
        pr_url or "",
        "--json",
        "number,url",
    )
    try:
        details = json.loads(details_text or "")
        number = details["number"]
        url = details["url"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise GitOpsError("GitHub CLI returned malformed pull-request context") from error
    if not isinstance(number, int) or not isinstance(url, str):
        raise GitOpsError("GitHub CLI returned malformed pull-request context")
    return PublicationResult(origin, branch or "", commit or "", number, url)
