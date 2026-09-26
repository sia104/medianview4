from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def command(
    *args: str, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def adlc(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return command(sys.executable, ".adlc/evidence.py", *args, cwd=repo, check=check)


def make_repository(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "portable-adlc"
    repo.mkdir()
    shutil.copytree(
        PROJECT_ROOT / ".adlc",
        repo / ".adlc",
        ignore=shutil.ignore_patterns("evidence", ".DS_Store", "__pycache__"),
    )
    shutil.copytree(PROJECT_ROOT / ".codex", repo / ".codex")
    shutil.copytree(PROJECT_ROOT / ".github", repo / ".github")
    shutil.copy2(PROJECT_ROOT / "AGENTS.md", repo / "AGENTS.md")
    shutil.copy2(PROJECT_ROOT / ".gitignore", repo / ".gitignore")
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", repo / "pyproject.toml")

    command("git", "init", "-b", "master", cwd=repo)
    command("git", "config", "user.email", "adlc@example.invalid", cwd=repo)
    command("git", "config", "user.name", "ADLC Test", cwd=repo)
    command("git", "add", ".", cwd=repo)
    command("git", "commit", "-m", "test fixture", cwd=repo)

    remote = tmp_path / f"{repo.name}.git"
    command("git", "init", "--bare", str(remote), cwd=tmp_path)
    command("git", "remote", "add", "origin", str(remote), cwd=repo)
    command("git", "push", "-u", "origin", "master", cwd=repo)
    return repo, remote


def create_run(repo: Path, tmp_path: Path, run_id: str = "test-run") -> str:
    request = tmp_path / f"{run_id}-request.txt"
    request.write_text("Build a deterministic example.\n", encoding="utf-8")
    result = adlc(
        repo,
        "create",
        "--run-id",
        run_id,
        "--base-branch",
        "master",
        "--request-file",
        str(request),
    )
    return result.stdout.strip()


def manifest(repo: Path, run_id: str) -> dict[str, object]:
    path = repo / ".adlc" / "evidence" / "runs" / run_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def install_fake_gh(
    directory: Path, monkeypatch: pytest.MonkeyPatch, remote: Path
) -> None:
    binary = directory / "gh"
    binary.write_text(
        f"""#!{sys.executable}
import json
import subprocess
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["repo", "create"]:
    subprocess.run(["git", "init", "--bare", {str(remote)!r}], check=True)
    subprocess.run(["git", "remote", "add", "origin", {str(remote)!r}], check=True)
    print("https://example.invalid/example/repository")
    raise SystemExit(0)
if args[:2] == ["pr", "create"]:
    print("https://example.invalid/example/repository/pull/7")
    raise SystemExit(0)
if args[:2] == ["pr", "view"]:
    print(json.dumps({{"number": 7, "url": "https://example.invalid/example/repository/pull/7"}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


def test_create_uses_canonical_version_preflight_and_event_journal(
    tmp_path: Path,
) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)
    value = manifest(repo, run_id)
    run_dir = repo / ".adlc" / "evidence" / "runs" / run_id

    assert value["adlc"]["version"] == "V0.5"  # type: ignore[index]
    assert value["lifecycle"]["current_stage"] == "specification"  # type: ignore[index]
    assert value["governance"]["controls"]["human_merge"] == {  # type: ignore[index]
        "enforcement": "procedural"
    }
    assert (run_dir / "raw" / "observations" / "preflight.json").is_file()
    preflight = json.loads(
        (run_dir / "raw" / "observations" / "preflight.json").read_text()
    )
    assert preflight["adlc"]["required_skills"] == [
        "spec-it",
        "implement-it",
        "test-it",
        "retrospect-it",
    ]
    assert "on-base-branch" in {item["code"] for item in preflight["issues"]}
    assert (run_dir / "events" / "event-0001.json").is_file()
    assert not (run_dir / "raw" / "manifest-history").exists()
    assert adlc(repo, "validate", run_id).returncode == 0


def test_attachment_deduplication_avoids_redundant_copy(tmp_path: Path) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)
    evidence = tmp_path / "result.txt"
    evidence.write_text("same evidence\n", encoding="utf-8")

    def attach(index: int) -> str:
        return adlc(
            repo,
            "attach",
            run_id,
            "tests",
            str(evidence),
            "--name",
            f"{index}.txt",
        ).stdout

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(attach, range(6)))

    assert len(set(results)) == 1
    directory = repo / ".adlc" / "evidence" / "runs" / run_id / "raw" / "tests"
    assert len(list(directory.iterdir())) == 1
    assert manifest(repo, run_id)["revision"] == 2


def test_repository_evidence_uses_path_commit_and_hash_without_copy(
    tmp_path: Path,
) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)

    first = adlc(repo, "identify", run_id, "outputs", "AGENTS.md").stdout.strip()
    second = adlc(repo, "identify", run_id, "outputs", "AGENTS.md").stdout.strip()

    assert first == second
    artifacts = cast(list[dict[str, Any]], manifest(repo, run_id)["artifacts"])
    identities = [
        item
        for item in artifacts
        if item["type"] == "repository"
    ]
    assert len(identities) == 1
    assert identities[0]["repository_path"] == "AGENTS.md"
    assert len(identities[0]["commit"]) == 40
    assert len(identities[0]["sha256"]) == 64
    output_dir = repo / ".adlc" / "evidence" / "runs" / run_id / "raw" / "outputs"
    assert list(output_dir.iterdir()) == []


def test_concurrent_updates_are_serialized_without_lost_records(tmp_path: Path) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)

    def record(index: int) -> None:
        adlc(
            repo,
            "unverified",
            run_id,
            f"criterion-{index}",
            "--reason",
            "not part of this test",
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(record, range(12)))

    value = manifest(repo, run_id)
    criteria = value["verification"]["unverified_acceptance_criteria"]  # type: ignore[index]
    assert {item["criterion"] for item in criteria} == {
        f"criterion-{index}" for index in range(12)
    }
    assert value["revision"] == 13
    assert adlc(repo, "validate", run_id).returncode == 0


def test_controller_blocks_invalid_transition_and_supports_remediation(
    tmp_path: Path,
) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)
    early_merge = adlc(repo, "merge-context", run_id, "merged", check=False)
    assert early_merge.returncode == 2
    assert "merge can only be recorded" in early_merge.stderr
    early_close = adlc(repo, "close", run_id, check=False)
    assert early_close.returncode == 2
    assert "cannot transition" in early_close.stderr
    invalid = adlc(repo, "transition", run_id, "implementation", "pass", check=False)
    assert invalid.returncode == 2
    assert "cannot transition" in invalid.stderr

    spec = tmp_path / "spec.md"
    spec.write_text("# Approved specification\n", encoding="utf-8")
    spec_ref = adlc(repo, "attach", run_id, "outputs", str(spec)).stdout.strip()
    adlc(
        repo,
        "transition",
        run_id,
        "human-specification-approval",
        "pass",
        "--evidence",
        spec_ref,
    )
    adlc(repo, "specification", run_id, spec_ref)
    adlc(repo, "decision", run_id, "specification", "approved")
    command("git", "checkout", "-b", "feature/example", cwd=repo)
    adlc(repo, "transition", run_id, "feature-branch", "pass")
    adlc(repo, "transition", run_id, "implementation", "pass")
    adlc(repo, "transition", run_id, "independent-testing", "pass")
    adlc(repo, "transition", run_id, "remediation", "fail")

    blocked = adlc(repo, "transition", run_id, "implementation", "pass", check=False)
    assert blocked.returncode == 2
    assert "approved human decision" in blocked.stderr
    adlc(repo, "decision", run_id, "implementation", "approved")
    adlc(repo, "transition", run_id, "implementation", "pass")
    assert manifest(repo, run_id)["lifecycle"]["current_stage"] == "implementation"  # type: ignore[index]


def test_closed_run_can_enter_retrospective_and_propose_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = make_repository(tmp_path)
    run_id = create_run(repo, tmp_path)
    open_lesson = adlc(
        repo,
        "lesson-create",
        "too-early",
        "--observation",
        "An observation",
        "--support",
        f"{run_id}:raw/inputs/original-request.txt",
        "--why",
        "It matters",
        "--target",
        "no-action",
        check=False,
    )
    assert open_lesson.returncode == 2
    assert "closed runs" in open_lesson.stderr

    spec = tmp_path / "spec.md"
    spec.write_text("# Approved specification\n", encoding="utf-8")
    spec_ref = adlc(repo, "attach", run_id, "outputs", str(spec)).stdout.strip()
    adlc(
        repo,
        "transition",
        run_id,
        "human-specification-approval",
        "pass",
        "--evidence",
        spec_ref,
    )
    adlc(repo, "specification", run_id, spec_ref)
    adlc(repo, "decision", run_id, "specification", "approved")
    command("git", "checkout", "-b", "feature/complete", cwd=repo)
    adlc(repo, "transition", run_id, "feature-branch", "pass")
    adlc(repo, "transition", run_id, "implementation", "pass")
    adlc(repo, "transition", run_id, "independent-testing", "pass")
    premature = adlc(
        repo, "transition", run_id, "pull-request", "pass", check=False
    )
    assert premature.returncode == 2
    assert "recorded feature publication" in premature.stderr
    adlc(repo, "stage", run_id, "test-it", "pass")
    (repo / ".adlc/config.json").write_text(
        json.dumps(
            {"base_branch": "master", "repository_visibility": "private"}
        ),
        encoding="utf-8",
    )
    command("git", "add", ".adlc/config.json", cwd=repo)
    command("git", "commit", "-m", "Use private test publication", cwd=repo)
    install_fake_gh(tmp_path, monkeypatch, tmp_path / "unused.git")
    publication = adlc(repo, "publish", run_id, check=False)
    assert publication.returncode == 0, publication.stderr
    published = manifest(repo, run_id)
    assert published["git"]["pull_request"]["number"] == 7  # type: ignore[index]
    assert published["git"]["publication"]["branch"] == "feature/complete"  # type: ignore[index]
    adlc(repo, "transition", run_id, "ci", "pass")
    adlc(repo, "ci", run_id, "pass", "--workflow-run", "ci-1")
    adlc(repo, "transition", run_id, "human-review", "pass")
    adlc(repo, "decision", run_id, "merge", "approved")
    adlc(repo, "transition", run_id, "merge", "pass")
    head = command("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    adlc(repo, "merge-context", run_id, "merged", "--final-commit", head)
    adlc(repo, "close", run_id)
    assert manifest(repo, run_id)["stages"]["merge"]["status"] == "pass"  # type: ignore[index]
    manifest_path = repo / ".adlc/evidence/runs" / run_id / "manifest.json"
    closed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inconsistent = json.loads(json.dumps(closed_manifest))
    inconsistent["stages"]["merge"]["status"] = "not_run"
    manifest_path.write_text(json.dumps(inconsistent), encoding="utf-8")
    rejected = adlc(repo, "validate", run_id, check=False)
    assert rejected.returncode == 2
    assert "passing merge stage" in rejected.stderr
    manifest_path.write_text(json.dumps(closed_manifest), encoding="utf-8")
    adlc(repo, "transition", run_id, "retrospective", "pass")
    adlc(
        repo,
        "lesson-create",
        "closed-observation",
        "--observation",
        "An evidence-backed observation",
        "--support",
        f"{run_id}:raw/inputs/original-request.txt",
        "--why",
        "It may improve future runs",
        "--target",
        "no-action",
    )

    candidate = (
        repo
        / ".adlc"
        / "evidence"
        / "lessons"
        / "closed-observation"
        / "candidate.json"
    )
    assert (
        json.loads(candidate.read_text(encoding="utf-8"))["automatically_applied"]
        is False
    )
    assert manifest(repo, run_id)["lifecycle"]["open"] is False  # type: ignore[index]


def test_preflight_flags_quality_gate_weakening_for_human_approval(
    tmp_path: Path,
) -> None:
    repo, _ = make_repository(tmp_path)
    command("git", "checkout", "-b", "feature/weaken", cwd=repo)
    baseline_path = repo / ".adlc" / "quality-gates.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["required_commands"].remove(
        "uv run --project .adlc --frozen pytest .adlc/tests"
    )
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    result = command(
        sys.executable,
        ".adlc/preflight.py",
        "--base-branch",
        "master",
        cwd=repo,
    )
    report = json.loads(result.stdout)
    issue = next(
        item
        for item in report["issues"]
        if item["code"] == "quality-gate-baseline-weakened"
    )
    assert issue["requires_human_approval"] is True


def test_skill_contracts_preserve_provenance_and_retrospective_boundaries() -> None:
    spec_skill = (PROJECT_ROOT / ".codex/skills/spec-it/SKILL.md").read_text()
    retrospect_skill = (
        PROJECT_ROOT / ".codex/skills/retrospect-it/SKILL.md"
    ).read_text()

    assert "provenance" in spec_skill
    assert "Optional" in spec_skill
    assert "OBSERVE / PROPOSE ONLY" in retrospect_skill
    assert "closed" in retrospect_skill
    assert "Never directly" in retrospect_skill
    assert "Human approval" in retrospect_skill


@pytest.mark.parametrize(
    "argument",
    ["V0.2", "V9.9"],
)
def test_create_rejects_caller_selected_version(tmp_path: Path, argument: str) -> None:
    repo, _ = make_repository(tmp_path)
    result = adlc(
        repo,
        "create",
        "--run-id",
        "wrong-version",
        "--adlc-version",
        argument,
        check=False,
    )
    assert result.returncode == 2
    assert "conflicts with canonical version" in result.stderr
