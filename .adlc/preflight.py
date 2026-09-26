"""Deterministic repository and ADLC preflight checks."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from adlc_config import ConfigError, load_config
from gitops import GitOpsError, git_identity, is_project_repository

EXPECTED_ADLC_VERSION = "V0.5"
REQUIRED_SKILLS = ("spec-it", "implement-it", "test-it", "retrospect-it")


def command_output(project_root: Path, *command: str) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def remote_name(remote: str | None) -> str | None:
    if not remote:
        return None
    if "://" in remote:
        path = urlsplit(remote).path
    elif ":" in remote:
        path = remote.split(":", 1)[1]
    else:
        path = remote
    name = Path(path).name
    return name.removesuffix(".git") or None


def sanitized_remote(remote: str | None) -> str | None:
    if not remote or "://" not in remote:
        return remote
    parsed = urlsplit(remote)
    if not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host, query="", fragment="").geturl()


def issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    human_approval: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "requires_human_approval": human_approval,
    }


def quality_gate_issues(
    project_root: Path, adlc_root: Path, base_branch: str
) -> list[dict[str, Any]]:
    baseline_path = adlc_root / "quality-gates.json"
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return [issue("missing-quality-gate-baseline", "Quality-gate baseline missing")]

    problems: list[dict[str, Any]] = []
    try:
        relative_baseline = baseline_path.relative_to(project_root).as_posix()
    except ValueError:
        relative_baseline = ""
    base_text = (
        command_output(
            project_root, "git", "show", f"{base_branch}:{relative_baseline}"
        )
        if relative_baseline
        else None
    )
    if base_text:
        try:
            base_baseline = json.loads(base_text)
        except json.JSONDecodeError:
            base_baseline = {}
        removed_commands = sorted(
            set(base_baseline.get("required_commands", []))
            - set(baseline.get("required_commands", []))
        )
        removed_fragments = any(
            not set(fragments).issubset(
                set(baseline.get("required_fragments", {}).get(relative, []))
            )
            for relative, fragments in base_baseline.get(
                "required_fragments", {}
            ).items()
        )
        if removed_commands or removed_fragments:
            problems.append(
                issue(
                    "quality-gate-baseline-weakened",
                    "Quality-gate baseline is less strict than the base branch",
                    severity="warning",
                    human_approval=True,
                )
            )
    workflow = project_root / baseline.get("workflow", "")
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.is_file() else ""
    for command in baseline.get("required_commands", []):
        if command not in workflow_text:
            problems.append(
                issue(
                    "quality-gate-weakened",
                    f"Required CI command is absent: {command}",
                    severity="warning",
                    human_approval=True,
                )
            )

    for relative, fragments in baseline.get("required_fragments", {}).items():
        target = project_root / relative
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
        for fragment in fragments:
            if fragment not in text:
                problems.append(
                    issue(
                        "quality-gate-configuration-changed",
                        f"Protected quality-gate setting is absent from {relative}",
                        severity="warning",
                        human_approval=True,
                    )
                )
    return problems


def run_preflight(
    project_root: Path, adlc_root: Path, base_branch: str | None = None
) -> dict[str, Any]:
    project_root = project_root.resolve()
    adlc_root = adlc_root.resolve()
    problems: list[dict[str, Any]] = []
    try:
        config = load_config(adlc_root)
    except ConfigError as error:
        config = None
        problems.append(issue("invalid-adlc-config", str(error)))
    remote = command_output(project_root, "git", "config", "--get", "remote.origin.url")
    branch = command_output(project_root, "git", "branch", "--show-current")
    base = base_branch or (config.base_branch if config else "main")
    status = command_output(project_root, "git", "status", "--porcelain")
    version_path = adlc_root / "VERSION"
    version = (
        version_path.read_text(encoding="utf-8").strip()
        if version_path.is_file()
        else None
    )
    ci_files = sorted((project_root / ".github" / "workflows").glob("*.y*ml"))
    if not is_project_repository(project_root):
        problems.append(
            issue(
                "missing-project-git-repository",
                "Project root is not the top level of a Git repository",
            )
        )
    try:
        git_identity(project_root)
    except GitOpsError as error:
        problems.append(issue("missing-git-identity", str(error)))

    repository_name = remote_name(remote)
    if repository_name and repository_name.casefold() != project_root.name.casefold():
        problems.append(
            issue(
                "repository-identity-mismatch",
                f"Directory {project_root.name!r} does not match remote {repository_name!r}",
                severity="warning",
            )
        )
    if not remote:
        problems.append(issue("missing-remote", "Git remote origin is not configured"))
    if not branch:
        problems.append(
            issue("missing-current-branch", "Current Git branch is unknown")
        )
    if branch == base:
        problems.append(
            issue(
                "on-base-branch",
                "A feature branch is required before implementation",
                severity="warning",
            )
        )
    if status:
        problems.append(
            issue(
                "dirty-working-tree",
                "Working tree contains uncommitted changes",
                severity="warning",
            )
        )
    if version != EXPECTED_ADLC_VERSION:
        problems.append(
            issue(
                "unknown-adlc-version",
                f"Expected {EXPECTED_ADLC_VERSION}, found {version!r}",
            )
        )

    missing_skills = [
        name
        for name in REQUIRED_SKILLS
        if not (project_root / ".codex" / "skills" / name / "SKILL.md").is_file()
    ]
    for name in missing_skills:
        problems.append(
            issue("missing-required-skill", f"Missing required skill: {name}")
        )
    if not ci_files:
        problems.append(issue("missing-ci", "No CI workflow is present"))
    for required in ("evidence.py", "lifecycle.py", "preflight.py"):
        if not (adlc_root / required).is_file():
            problems.append(
                issue("missing-evidence-subsystem", f"Missing .adlc/{required}")
            )

    expected_tools = ["git", "python3"]
    if (project_root / "uv.lock").is_file():
        expected_tools.append("uv")
    for tool in expected_tools:
        if shutil.which(tool) is None:
            problems.append(
                issue("missing-tool", f"Required tool is unavailable: {tool}")
            )
    problems.extend(quality_gate_issues(project_root, adlc_root, base))

    return {
        "schema_version": "1.0",
        "repository": {
            "project_name": project_root.name,
            "remote": sanitized_remote(remote),
            "remote_name": repository_name,
            "base_branch": base,
            "current_branch": branch,
            "working_tree_clean": not bool(status),
        },
        "adlc": {
            "version": version,
            "required_skills": list(REQUIRED_SKILLS),
            "ci_configuration_present": bool(ci_files),
            "evidence_subsystem_present": (adlc_root / "evidence.py").is_file(),
            "repository_visibility": (
                config.repository_visibility if config is not None else None
            ),
        },
        "tooling": {
            "python": platform.python_version(),
            "git": command_output(project_root, "git", "--version"),
            "uv": command_output(project_root, "uv", "--version"),
        },
        "issues": problems,
        "ok": not any(problem["severity"] == "error" for problem in problems),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--adlc-root", default=".adlc")
    parser.add_argument("--base-branch")
    args = parser.parse_args()
    report = run_preflight(
        Path(args.project_root), Path(args.adlc_root), args.base_branch
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
