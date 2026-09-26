from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / ".adlc"))

from adlc_config import AdlcConfig, load_config  # noqa: E402
from gitops import (  # noqa: E402
    GitOpsError,
    bootstrap_project,
    ensure_origin,
    publication_hazards,
    publish_feature,
)
from preflight import run_preflight  # noqa: E402


def command(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def configure_identity(repo: Path) -> None:
    command("git", "config", "user.name", "ADLC Test", cwd=repo)
    command("git", "config", "user.email", "adlc@example.invalid", cwd=repo)


def fake_gh(directory: Path, monkeypatch: pytest.MonkeyPatch, remote: Path) -> None:
    binary = directory / "gh"
    binary.write_text(
        f"""#!{sys.executable}
import json
import os
import subprocess
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["repo", "create"]:
    subprocess.run(["git", "init", "--bare", {str(remote)!r}], check=True)
    subprocess.run(["git", "remote", "add", "origin", {str(remote)!r}], check=True)
    print("https://example.invalid/example/project")
    raise SystemExit(0)
if args[:2] == ["pr", "create"]:
    marker = os.environ.get("ADLC_TEST_PUSH_MARKER")
    if marker and not os.path.exists(marker):
        raise SystemExit(3)
    print("https://example.invalid/example/project/pull/3")
    raise SystemExit(0)
if args[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 3, "url": "https://example.invalid/example/project/pull/3"}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


def test_configuration_defaults_to_public() -> None:
    assert load_config(PROJECT_ROOT / ".adlc") == AdlcConfig("master", "public")


def test_evidence_is_local_only() -> None:
    ignored = command(
        "git", "check-ignore", ".adlc/evidence/example.json", cwd=PROJECT_ROOT
    )
    tracked = command("git", "ls-files", ".adlc/evidence", cwd=PROJECT_ROOT)

    assert ignored.stdout.strip() == ".adlc/evidence/example.json"
    assert tracked.stdout == ""


def test_bootstrap_initializes_project_and_missing_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "new-project"
    project.mkdir()
    (project / "README.md").write_text("# Example\n", encoding="utf-8")
    (project / ".gitignore").write_text(".adlc/evidence/\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    command("git", "config", "--global", "user.name", "ADLC Test", cwd=project)
    command(
        "git", "config", "--global", "user.email", "adlc@example.invalid", cwd=project
    )
    remote = tmp_path / "new-project.git"
    fake_gh(tmp_path, monkeypatch, remote)

    result = bootstrap_project(project, AdlcConfig("master", "public"))

    assert result["repository_created"] is True
    assert command("git", "branch", "--show-current", cwd=project).stdout.strip() == "master"
    assert command("git", "remote", "get-url", "origin", cwd=project).stdout.strip() == str(remote)
    assert command("git", "rev-parse", "origin/master", cwd=project).stdout.strip()


def test_existing_repository_and_origin_are_preserved(tmp_path: Path) -> None:
    repo = tmp_path / "existing"
    repo.mkdir()
    command("git", "init", "-b", "master", cwd=repo)
    configure_identity(repo)
    (repo / "README.md").write_text("existing\n", encoding="utf-8")
    command("git", "add", ".", cwd=repo)
    command("git", "commit", "-m", "existing", cwd=repo)
    remote = tmp_path / "existing.git"
    command("git", "init", "--bare", str(remote), cwd=tmp_path)
    command("git", "remote", "add", "origin", str(remote), cwd=repo)
    command("git", "push", "-u", "origin", "master", cwd=repo)
    before = command("git", "rev-parse", "HEAD", cwd=repo).stdout

    result = bootstrap_project(repo, AdlcConfig("master", "private"))

    assert result == {"repository_created": False, "origin": str(remote)}
    assert command("git", "rev-parse", "HEAD", cwd=repo).stdout == before
    assert command("git", "remote", "get-url", "origin", cwd=repo).stdout.strip() == str(remote)


def test_missing_identity_blocks_before_repository_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "no-identity"
    project.mkdir()
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(GitOpsError, match="user.name and user.email"):
        bootstrap_project(project, AdlcConfig("master", "private"))

    assert not (project / ".git").exists()


def test_preflight_blocks_existing_repository_without_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    command("git", "init", "-b", "master", cwd=repo)
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    adlc = repo / ".adlc"
    shutil.copytree(PROJECT_ROOT / ".adlc", adlc, ignore=shutil.ignore_patterns("evidence"))

    report = run_preflight(repo, adlc)

    assert "missing-git-identity" in {item["code"] for item in report["issues"]}
    assert report["ok"] is False


def test_publication_hazards_report_paths_without_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SAFE_NAME=value\n", encoding="utf-8")
    secret_assignment = "api_" + "key=" + "do-not-print-this\n"
    (tmp_path / "settings.txt").write_text(secret_assignment, encoding="utf-8")
    private_key_header = "-----BEGIN " + "PRIVATE KEY-----\nmaterial\n"
    (tmp_path / "key.pem").write_text(
        private_key_header, encoding="utf-8"
    )
    protected_token = "ghp_" + "A" * 20
    (tmp_path / "notes.txt").write_text(protected_token, encoding="utf-8")

    hazards = publication_hazards(tmp_path)
    rendered = str(hazards)

    assert {item["hazard"] for item in hazards} == {
        "sensitive-name",
        "credential-assignment",
        "private-key",
        "credential-token",
    }
    assert "do-not-print-this" not in rendered


def test_publish_pushes_feature_and_creates_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "publish"
    repo.mkdir()
    command("git", "init", "-b", "master", cwd=repo)
    configure_identity(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    command("git", "add", ".", cwd=repo)
    command("git", "commit", "-m", "base", cwd=repo)
    remote = tmp_path / "publish.git"
    command("git", "init", "--bare", str(remote), cwd=tmp_path)
    command("git", "remote", "add", "origin", str(remote), cwd=repo)
    command("git", "push", "-u", "origin", "master", cwd=repo)
    command("git", "switch", "-c", "feature/example", cwd=repo)
    (repo / "README.md").write_text("feature\n", encoding="utf-8")
    command("git", "add", ".", cwd=repo)
    command("git", "commit", "-m", "Add feature", cwd=repo)
    fake_gh(tmp_path, monkeypatch, tmp_path / "unused.git")
    marker = tmp_path / "push-recorded"
    monkeypatch.setenv("ADLC_TEST_PUSH_MARKER", str(marker))

    pushed: list[tuple[str, str, str]] = []

    def record_push(remote_url: str, branch: str, commit: str) -> None:
        pushed.append((remote_url, branch, commit))
        marker.touch()

    result = publish_feature(
        repo, AdlcConfig("master", "private"), on_pushed=record_push
    )

    assert result.branch == "feature/example"
    assert result.pull_request_number == 3
    assert result.pull_request_url.endswith("/pull/3")
    assert pushed == [(str(remote), "feature/example", result.commit)]
    assert command("git", "rev-parse", "@{upstream}", cwd=repo).stdout.strip() == result.commit


def test_github_auth_failure_does_not_configure_origin(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command_args: object, cwd: Path) -> subprocess.CompletedProcess[str]:
        command = tuple(command_args)  # type: ignore[arg-type]
        calls.append(command)
        if command[:3] == ("git", "remote", "get-url"):
            raise GitOpsError("origin absent")
        if command[:2] == ("gh", "auth"):
            raise GitOpsError("gh authentication failed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(GitOpsError, match="authentication failed"):
        ensure_origin(tmp_path, AdlcConfig("master", "private"), runner)

    assert not any(command[:2] == ("gh", "repo") for command in calls)
