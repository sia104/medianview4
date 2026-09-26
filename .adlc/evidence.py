"""Deterministic filesystem evidence capture for an ADLC run."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from adlc_config import ConfigError, load_config
from gitops import GitOpsError, bootstrap_project, publish_feature
from lifecycle import (
    LIFECYCLE_STAGES,
    LifecycleError,
    advance_lifecycle,
    lifecycle_from_dict,
    new_lifecycle,
)
from preflight import EXPECTED_ADLC_VERSION, run_preflight

SCHEMA_VERSION = "1.0"
EVENT_SCHEMA_VERSION = "1.0"
LIFECYCLE_VERSIONS = {"V0.3", "V0.4", "V0.5"}
RAW_CATEGORIES = (
    "inputs",
    "outputs",
    "tests",
    "ci",
    "decisions",
    "logs",
    "observations",
)
DERIVED_CATEGORIES = ("summaries", "traceability", "failures")
STAGES = (
    "spec-it",
    "specification-approval",
    "implement-it",
    "test-it",
    "ci",
    "human-review",
    "merge",
)
OUTCOMES = ("not_run", "running", "pass", "fail", "incomplete", "blocked")


def has_lifecycle(manifest: dict[str, Any]) -> bool:
    return manifest.get("adlc", {}).get("version") in LIFECYCLE_VERSIONS
LESSON_TARGETS = (
    "product-test",
    "deterministic-adlc-control",
    "skill-change",
    "agents-rule",
    "ci-change",
    "eval",
    "documentation",
    "no-action",
)
LESSON_STATUSES = ("proposed", "accepted", "rejected", "superseded")
MAX_ATTACHMENT_BYTES = 1_000_000
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
THREAD_LOCK = threading.Lock()
FILE_LOCK = importlib.import_module("msvcrt" if os.name == "nt" else "fcntl")
SENSITIVE_NAMES = re.compile(
    r"(^|[._-])(\.env|credentials?|secrets?|tokens?|id_rsa|private[-_]?key)"
    r"($|[._-])",
    re.IGNORECASE,
)
SENSITIVE_CONTENT = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b"
        r"\s*[:=]\s*[^\s]+"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class EvidenceError(Exception):
    """A predictable evidence-capture failure."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def json_sha256(value: object) -> str:
    return sha256_bytes(json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(*command: str, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def command_succeeds(*command: str, cwd: Path) -> bool:
    try:
        subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def validate_identifier(value: str, label: str) -> None:
    if not IDENTIFIER.fullmatch(value):
        raise EvidenceError(f"invalid {label}: {value!r}")


def reject_sensitive_text(value: str | None, label: str) -> None:
    if value and any(pattern.search(value) for pattern in SENSITIVE_CONTENT):
        raise EvidenceError(f"{label} may contain a secret; sanitize it first")


def sanitized_repository_url(value: str | None) -> str | None:
    if value is None or "://" not in value:
        return value
    parsed = urlsplit(value)
    if not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvidenceError(f"missing file: {path}") from error
    except json.JSONDecodeError as error:
        raise EvidenceError(f"invalid JSON in {path}: {error.msg}") from error
    if not isinstance(value, dict):
        raise EvidenceError(f"expected a JSON object in {path}")
    return value


def write_json_new(
    path: Path, value: dict[str, Any], *, readonly: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError as error:
        raise EvidenceError(
            f"refusing to overwrite existing evidence: {path}"
        ) from error
    if readonly:
        path.chmod(0o444)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as destination:
            json.dump(value, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_directory(root: Path, run_id: str) -> Path:
    validate_identifier(run_id, "run ID")
    return root / "evidence" / "runs" / run_id


def manifest_path(root: Path, run_id: str) -> Path:
    return run_directory(root, run_id) / "manifest.json"


@contextmanager
def manifest_lock(root: Path, run_id: str) -> Iterator[None]:
    """Serialize manifest updates across threads and local processes."""
    lock_key = sha256_bytes(str(manifest_path(root, run_id)).encode("utf-8"))
    lock_path = Path(tempfile.gettempdir()) / f"adlc-{lock_key}.lock"
    with THREAD_LOCK, lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            lock_file.seek(0)
            lock_file.write(b"\0")
            lock_file.flush()
            lock_file.seek(0)
            FILE_LOCK.locking(lock_file.fileno(), FILE_LOCK.LK_LOCK, 1)
        else:
            FILE_LOCK.flock(lock_file.fileno(), FILE_LOCK.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                FILE_LOCK.locking(lock_file.fileno(), FILE_LOCK.LK_UNLCK, 1)
            else:
                FILE_LOCK.flock(lock_file.fileno(), FILE_LOCK.LOCK_UN)


def canonical_adlc_version(root: Path) -> str:
    version_path = root / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise EvidenceError(
            f"missing canonical ADLC version: {version_path}"
        ) from error
    if not version:
        raise EvidenceError("canonical ADLC version is empty")
    return version


def changed_fields(
    before: object, after: object, prefix: str = ""
) -> list[dict[str, Any]]:
    """Return compact hashes for changed manifest paths."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(before.keys() | after.keys()):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before:
                changes.append(
                    {"path": path, "before": None, "after": json_sha256(after[key])}
                )
            elif key not in after:
                changes.append(
                    {"path": path, "before": json_sha256(before[key]), "after": None}
                )
            else:
                changes.extend(changed_fields(before[key], after[key], path))
        return changes
    return [
        {
            "path": prefix or "$",
            "before": json_sha256(before),
            "after": json_sha256(after),
        }
    ]


def event_path(root: Path, run_id: str, revision: int) -> Path:
    return run_directory(root, run_id) / "events" / f"event-{revision:04d}.json"


def append_event(
    root: Path,
    run_id: str,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    revision = after["revision"]
    previous = event_path(root, run_id, revision - 1)
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": revision,
        "event_type": event_type,
        "recorded_at": utc_now(),
        "previous_event_sha256": sha256_file(previous) if previous.is_file() else None,
        "manifest_sha256": json_sha256(after),
        "changes": changed_fields(before or {}, after),
    }
    write_json_new(event_path(root, run_id, revision), event, readonly=True)


def validate_timestamp(value: object, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise EvidenceError(f"{label} must be an RFC 3339 timestamp")
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceError(f"{label} must be an RFC 3339 timestamp") from error


def require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return value


def require_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be an array")
    return value


def validate_manifest(manifest: dict[str, Any], expected_run_id: str) -> None:
    required = {
        "schema_version",
        "run_id",
        "revision",
        "started_at",
        "ended_at",
        "project",
        "adlc",
        "context",
        "git",
        "stages",
        "verification",
        "human_decisions",
        "failures_and_interventions",
        "artifacts",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise EvidenceError(f"manifest missing required fields: {', '.join(missing)}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("unsupported manifest schema_version")
    if manifest["run_id"] != expected_run_id:
        raise EvidenceError("manifest run_id does not match its run directory")
    validate_identifier(expected_run_id, "run ID")
    if not isinstance(manifest["revision"], int) or manifest["revision"] < 1:
        raise EvidenceError("manifest revision must be a positive integer")
    validate_timestamp(manifest["started_at"], "started_at")
    validate_timestamp(manifest["ended_at"], "ended_at", nullable=True)

    project = require_mapping(manifest["project"], "project")
    if not isinstance(project.get("name"), str) or not project["name"]:
        raise EvidenceError("project.name must be a non-empty string")
    adlc = require_mapping(manifest["adlc"], "adlc")
    if not isinstance(adlc.get("version"), str) or not adlc["version"]:
        raise EvidenceError("adlc.version must be a non-empty string")
    require_list(adlc.get("skills"), "adlc.skills")
    require_mapping(manifest["context"], "context")
    git_context = require_mapping(manifest["git"], "git")

    if adlc["version"] in LIFECYCLE_VERSIONS:
        try:
            lifecycle = lifecycle_from_dict(
                require_mapping(manifest.get("lifecycle"), "lifecycle")
            )
        except LifecycleError as error:
            raise EvidenceError(str(error)) from error
        if lifecycle.active_run_id != expected_run_id:
            raise EvidenceError("lifecycle active_run_id does not match the run")
        if (manifest["ended_at"] is None) != lifecycle.open:
            raise EvidenceError("run closure timestamp does not match lifecycle state")
        governance = require_mapping(manifest.get("governance"), "governance")
        controls = require_mapping(governance.get("controls"), "governance.controls")
        for control in (
            "feature_branch",
            "ci_before_merge",
            "human_merge",
            "quality_gate_changes",
        ):
            value = require_mapping(
                controls.get(control), f"governance.controls.{control}"
            )
            if value.get("enforcement") not in {"procedural", "technical"}:
                raise EvidenceError(f"governance control {control} lacks enforcement")
        history = (*lifecycle.completed_stages, lifecycle.current_stage)
        if adlc["version"] == EXPECTED_ADLC_VERSION and "pull-request" in history:
            pull_request = require_mapping(
                git_context.get("pull_request"), "git.pull_request"
            )
            publication = require_mapping(
                git_context.get("publication"), "git.publication"
            )
            if not pull_request.get("url") or not pull_request.get("number"):
                raise EvidenceError("pull-request lifecycle lacks PR context")
            for field in ("remote", "branch", "commit"):
                if not publication.get(field):
                    raise EvidenceError(
                        f"pull-request lifecycle lacks publication {field}"
                    )
        if (
            adlc["version"] == EXPECTED_ADLC_VERSION
            and "merge" in lifecycle.completed_stages
        ):
            if stages := manifest.get("stages"):
                merge_stage = require_mapping(stages.get("merge"), "stages.merge")
                if merge_stage.get("status") != "pass":
                    raise EvidenceError(
                        "completed merge lifecycle requires a passing merge stage"
                    )

    stages = require_mapping(manifest["stages"], "stages")
    for stage in STAGES:
        stage_value = require_mapping(stages.get(stage), f"stages.{stage}")
        if stage_value.get("status") not in OUTCOMES:
            raise EvidenceError(f"stages.{stage}.status is invalid")
        require_list(stage_value.get("evidence"), f"stages.{stage}.evidence")

    verification = require_mapping(manifest["verification"], "verification")
    for field in (
        "tests",
        "quality_gates",
        "unverified_acceptance_criteria",
        "runtime_e2e",
    ):
        require_list(verification.get(field), f"verification.{field}")
    require_mapping(verification.get("ci"), "verification.ci")
    require_list(manifest["human_decisions"], "human_decisions")
    require_list(manifest["failures_and_interventions"], "failures_and_interventions")

    for index, artifact_value in enumerate(
        require_list(manifest["artifacts"], "artifacts")
    ):
        artifact = require_mapping(artifact_value, f"artifacts[{index}]")
        if artifact.get("type") not in {
            "file",
            "reference",
            "derived",
            "repository",
        }:
            raise EvidenceError(f"artifacts[{index}].type is invalid")
        artifact_type = artifact["type"]
        valid_categories = (
            DERIVED_CATEGORIES if artifact_type == "derived" else RAW_CATEGORIES
        )
        if artifact.get("category") not in valid_categories:
            raise EvidenceError(f"artifacts[{index}].category is invalid")
        if artifact_type in {"file", "derived", "repository"} and not SHA256.fullmatch(
            str(artifact.get("sha256", ""))
        ):
            raise EvidenceError(f"artifacts[{index}].sha256 is invalid")
        if artifact_type == "derived":
            require_list(
                artifact.get("supporting_evidence"),
                f"artifacts[{index}].supporting_evidence",
            )
        if artifact_type == "repository":
            repository_path = artifact.get("repository_path")
            if not isinstance(repository_path, str) or not repository_path:
                raise EvidenceError(f"artifacts[{index}].repository_path is invalid")
            commit = artifact.get("commit")
            if not isinstance(commit, str) or not commit:
                raise EvidenceError(f"artifacts[{index}].commit is invalid")


def load_manifest(root: Path, run_id: str) -> dict[str, Any]:
    manifest = load_json(manifest_path(root, run_id))
    validate_manifest(manifest, run_id)
    return manifest


def update_manifest(
    root: Path,
    run_id: str,
    update: Callable[[dict[str, Any]], bool | None],
    *,
    event_type: str = "manifest.updated",
) -> dict[str, Any]:
    path = manifest_path(root, run_id)
    with manifest_lock(root, run_id):
        manifest = load_manifest(root, run_id)
        before = copy.deepcopy(manifest)
        revision = manifest["revision"]
        changed = update(manifest)
        if changed is False:
            return manifest
        if not has_lifecycle(manifest):
            history = run_directory(root, run_id) / "raw" / "manifest-history"
            write_json_new(
                history / f"revision-{revision:04d}.json", before, readonly=True
            )
        manifest["revision"] = revision + 1
        manifest["updated_at"] = utc_now()
        validate_manifest(manifest, run_id)
        if has_lifecycle(manifest):
            append_event(root, run_id, event_type, before, manifest)
        atomic_write_json(path, manifest)
        return manifest


def skill_records(project_root: Path) -> list[dict[str, Any]]:
    skills_root = project_root / ".codex" / "skills"
    records: list[dict[str, Any]] = []
    if not skills_root.is_dir():
        return records
    for skill_file in sorted(skills_root.glob("*/SKILL.md")):
        records.append(
            {
                "name": skill_file.parent.name,
                "version": None,
                "sha256": sha256_file(skill_file),
            }
        )
    return records


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def detected_base_branch(project_root: Path) -> str | None:
    remote_head = run_command(
        "git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=project_root
    )
    if remote_head and "/" in remote_head:
        return remote_head.split("/", 1)[1]
    for candidate in ("main", "master"):
        if run_command(
            "git", "show-ref", "--verify", f"refs/heads/{candidate}", cwd=project_root
        ):
            return candidate
    return None


def create_run(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    project_root = Path.cwd().resolve()
    adlc_version = canonical_adlc_version(root)
    if args.adlc_version and args.adlc_version != adlc_version:
        raise EvidenceError(
            f"--adlc-version {args.adlc_version!r} conflicts with canonical "
            f"version {adlc_version!r}"
        )
    try:
        config = load_config(root)
        if args.base_branch and args.base_branch != config.base_branch:
            raise EvidenceError(
                "--base-branch conflicts with configured ADLC base_branch"
            )
        bootstrap_project(project_root, config)
    except (ConfigError, GitOpsError) as error:
        raise EvidenceError(str(error)) from error
    run_id = args.run_id or new_run_id()
    validate_identifier(run_id, "run ID")
    for label in ("project", "harness", "model"):
        reject_sensitive_text(getattr(args, label), label)
    directory = run_directory(root, run_id)
    if directory.exists():
        raise EvidenceError(f"run already exists: {run_id}")

    for category in RAW_CATEGORIES:
        (directory / "raw" / category).mkdir(parents=True, exist_ok=False)
    (directory / "events").mkdir()
    for category in DERIVED_CATEGORIES:
        (directory / "derived" / category).mkdir(parents=True, exist_ok=False)

    base_branch = config.base_branch
    preflight = run_preflight(project_root, root, base_branch)
    preflight_path = directory / "raw" / "observations" / "preflight.json"
    write_json_new(preflight_path, preflight, readonly=True)
    preflight_artifact = {
        "type": "file",
        "category": "observations",
        "path": "raw/observations/preflight.json",
        "sha256": sha256_file(preflight_path),
        "size": preflight_path.stat().st_size,
        "created_at": utc_now(),
    }
    artifacts: list[dict[str, Any]] = [preflight_artifact]
    original_request: str | None = None
    if args.request_file:
        request_source = Path(args.request_file).resolve()
        content = safe_attachment(request_source, "original-request.txt")
        request_path = directory / "raw" / "inputs" / "original-request.txt"
        request_path.write_bytes(content)
        request_path.chmod(0o444)
        original_request = "raw/inputs/original-request.txt"
        artifacts.append(
            {
                "type": "file",
                "category": "inputs",
                "path": original_request,
                "sha256": sha256_bytes(content),
                "size": len(content),
                "created_at": utc_now(),
            }
        )

    git_commit = run_command("git", "rev-parse", "HEAD", cwd=project_root)
    agents_path = project_root / "AGENTS.md"
    agents_hash = sha256_file(agents_path) if agents_path.is_file() else None
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "revision": 1,
        "started_at": utc_now(),
        "ended_at": None,
        "project": {
            "name": args.project or project_root.name,
            "repository": sanitized_repository_url(
                run_command(
                    "git",
                    "config",
                    "--get",
                    "remote.origin.url",
                    cwd=project_root,
                )
            ),
        },
        "adlc": {
            "version": adlc_version,
            "agents_md_sha256": agents_hash,
            "skills": skill_records(project_root),
        },
        "context": {
            "original_request": original_request,
            "approved_specification": None,
            "harness": args.harness,
            "model": args.model,
            "runtime_versions": {
                "python": platform.python_version(),
                "git": run_command("git", "--version", cwd=project_root),
                "uv": run_command("uv", "--version", cwd=project_root),
            },
        },
        "git": {
            "branch": run_command("git", "branch", "--show-current", cwd=project_root),
            "base_branch": base_branch,
            "commits": [git_commit] if git_commit else [],
            "origin": sanitized_repository_url(
                run_command(
                    "git", "config", "--get", "remote.origin.url", cwd=project_root
                )
            ),
            "publication": {"remote": None, "branch": None, "commit": None},
            "pull_request": {"number": None, "url": None},
            "merge_status": "not_merged",
        },
        "stages": {stage: {"status": "not_run", "evidence": []} for stage in STAGES},
        "lifecycle": new_lifecycle(run_id).as_dict(),
        "governance": {
            "controls": {
                "feature_branch": {"enforcement": "procedural"},
                "ci_before_merge": {"enforcement": "procedural"},
                "human_merge": {"enforcement": "procedural"},
                "quality_gate_changes": {"enforcement": "procedural"},
            },
            "preflight_evidence": preflight_artifact["path"],
        },
        "verification": {
            "tests": [],
            "ci": {"status": "not_run", "workflow_run": None, "evidence": []},
            "quality_gates": [],
            "unverified_acceptance_criteria": [],
            "runtime_e2e": [],
        },
        "human_decisions": [],
        "failures_and_interventions": [],
        "artifacts": artifacts,
    }
    manifest["stages"]["spec-it"] = {
        "status": "running",
        "evidence": [],
        "recorded_at": utc_now(),
    }
    validate_manifest(manifest, run_id)
    write_json_new(directory / "manifest.json", manifest)
    append_event(root, run_id, "run.created", None, manifest)
    print(run_id)


def safe_attachment(source: Path, name: str) -> bytes:
    if Path(name).name != name or name in {".", ".."}:
        raise EvidenceError("attachment name must be a plain file name")
    if SENSITIVE_NAMES.search(source.name) or SENSITIVE_NAMES.search(name):
        raise EvidenceError("attachment name may contain sensitive material")
    try:
        size = source.stat().st_size
    except FileNotFoundError as error:
        raise EvidenceError(f"attachment does not exist: {source}") from error
    if not source.is_file():
        raise EvidenceError(f"attachment is not a regular file: {source}")
    if size > MAX_ATTACHMENT_BYTES:
        raise EvidenceError("attachment is too large; store an external reference")
    content = source.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceError("binary evidence must be stored as a reference") from error
    if "\x00" in text:
        raise EvidenceError("binary evidence must be stored as a reference")
    reject_sensitive_text(text, "attachment")
    return content


def attach_file(
    root: Path, run_id: str, category: str, source: Path, name: str | None
) -> dict[str, Any]:
    target_name = name or source.name
    content = safe_attachment(source, target_name)
    digest = sha256_bytes(content)
    result: dict[str, Any] = {}

    def add_artifact(manifest: dict[str, Any]) -> bool:
        nonlocal result
        for existing in manifest["artifacts"]:
            if (
                existing["type"] == "file"
                and existing["category"] == category
                and existing.get("sha256") == digest
            ):
                result = existing
                return False
        destination = run_directory(root, run_id) / "raw" / category / target_name
        try:
            with destination.open("xb") as target:
                target.write(content)
        except FileExistsError as error:
            raise EvidenceError(
                f"refusing to overwrite existing evidence: {destination}"
            ) from error
        destination.chmod(0o444)
        relative = destination.relative_to(run_directory(root, run_id)).as_posix()
        result = {
            "type": "file",
            "category": category,
            "path": relative,
            "sha256": digest,
            "size": len(content),
            "created_at": utc_now(),
        }
        manifest["artifacts"].append(result)
        return True

    update_manifest(root, run_id, add_artifact, event_type="artifact.attached")
    if not result:
        raise EvidenceError("attachment was not registered")
    return result


def attach_command(args: argparse.Namespace) -> None:
    artifact = attach_file(
        Path(args.root).resolve(),
        args.run_id,
        args.category,
        Path(args.file).resolve(),
        args.name,
    )
    print(artifact["path"])


def derived_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    manifest = load_manifest(root, args.run_id)
    ensure_evidence_refs(manifest, args.support)
    if not args.support:
        raise EvidenceError("derived evidence requires raw supporting evidence")
    raw_paths = {
        artifact.get("path")
        for artifact in manifest["artifacts"]
        if artifact["type"] == "file"
    }
    if any(reference not in raw_paths for reference in args.support):
        raise EvidenceError("derived evidence must reference registered raw files")

    source = Path(args.file).resolve()
    target_name = args.name or source.name
    content = safe_attachment(source, target_name)
    digest = sha256_bytes(content)
    for existing in manifest["artifacts"]:
        if (
            existing["type"] == "derived"
            and existing["category"] == args.category
            and existing.get("sha256") == digest
            and existing.get("supporting_evidence") == args.support
        ):
            print(existing["path"])
            return
    destination = (
        run_directory(root, args.run_id) / "derived" / args.category / target_name
    )
    try:
        with destination.open("xb") as target:
            target.write(content)
    except FileExistsError as error:
        raise EvidenceError(
            f"refusing to overwrite existing evidence: {destination}"
        ) from error
    destination.chmod(0o444)
    relative = destination.relative_to(run_directory(root, args.run_id)).as_posix()
    artifact = {
        "type": "derived",
        "category": args.category,
        "path": relative,
        "sha256": digest,
        "size": len(content),
        "supporting_evidence": args.support,
        "created_at": utc_now(),
    }

    def add_derived(current: dict[str, Any]) -> None:
        current["artifacts"].append(artifact)

    update_manifest(root, args.run_id, add_derived, event_type="artifact.derived")
    print(relative)


def validate_reference_uri(uri: str) -> None:
    parsed = urlsplit(uri)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise EvidenceError(
            "references must not contain credentials, queries, or fragments"
        )
    if parsed.scheme not in {"https", "s3", "file", "ci"}:
        raise EvidenceError("reference scheme must be https, s3, file, or ci")


def reference_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    existing_manifest = load_manifest(root, args.run_id)
    validate_reference_uri(args.uri)
    reject_sensitive_text(args.description, "reference description")
    for existing in existing_manifest["artifacts"]:
        if (
            existing["type"] == "reference"
            and existing["category"] == args.category
            and existing.get("uri") == args.uri
        ):
            print(args.uri)
            return
    artifact = {
        "type": "reference",
        "category": args.category,
        "uri": args.uri,
        "description": args.description,
        "created_at": utc_now(),
    }

    def add_reference(manifest: dict[str, Any]) -> None:
        manifest["artifacts"].append(artifact)

    update_manifest(root, args.run_id, add_reference, event_type="artifact.referenced")
    print(args.uri)


def repository_identity(artifact: dict[str, Any]) -> str:
    return f"repo:{artifact['repository_path']}@{artifact['commit']}"


def identify_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    project_root = Path.cwd().resolve()
    source = Path(args.file).resolve()
    try:
        relative = source.relative_to(project_root).as_posix()
    except ValueError as error:
        raise EvidenceError("repository evidence must be inside the project") from error
    if (
        run_command("git", "ls-files", "--error-unmatch", relative, cwd=project_root)
        is None
    ):
        raise EvidenceError("repository evidence must be tracked by Git")
    if not command_succeeds(
        "git", "diff", "--quiet", "HEAD", "--", relative, cwd=project_root
    ):
        raise EvidenceError("repository evidence must match the recorded commit")
    commit = run_command("git", "rev-parse", "HEAD", cwd=project_root)
    if commit is None:
        raise EvidenceError("unable to identify the current Git commit")
    artifact = {
        "type": "repository",
        "category": args.category,
        "repository_path": relative,
        "commit": commit,
        "sha256": sha256_file(source),
        "created_at": utc_now(),
    }
    identity = repository_identity(artifact)
    manifest = load_manifest(root, args.run_id)
    for existing in manifest["artifacts"]:
        if (
            existing["type"] == "repository"
            and repository_identity(existing) == identity
        ):
            if existing["sha256"] != artifact["sha256"]:
                raise EvidenceError(
                    "repository evidence hash conflicts with its identity"
                )
            print(identity)
            return

    def add_identity(current: dict[str, Any]) -> None:
        current["artifacts"].append(artifact)

    update_manifest(
        root, args.run_id, add_identity, event_type="artifact.repository-identified"
    )
    print(identity)


def ensure_evidence_refs(manifest: dict[str, Any], references: list[str]) -> None:
    known = {
        repository_identity(artifact)
        if artifact["type"] == "repository"
        else artifact.get("path", artifact.get("uri"))
        for artifact in manifest["artifacts"]
    }
    unknown = sorted(set(references) - known)
    if unknown:
        raise EvidenceError(f"unknown evidence reference: {', '.join(unknown)}")


LIFECYCLE_STAGE_RECORDS = {
    "specification": "spec-it",
    "human-specification-approval": "specification-approval",
    "implementation": "implement-it",
    "independent-testing": "test-it",
    "ci": "ci",
    "human-review": "human-review",
    "merge": "merge",
}


def latest_decision(manifest: dict[str, Any], gate: str) -> str | None:
    decisions = [
        item["decision"] for item in manifest["human_decisions"] if item["gate"] == gate
    ]
    return decisions[-1] if decisions else None


def branch_is_pushed(project_root: Path) -> bool:
    local = run_command("git", "rev-parse", "HEAD", cwd=project_root)
    upstream = run_command("git", "rev-parse", "@{upstream}", cwd=project_root)
    return local is not None and local == upstream


def enforce_transition(
    manifest: dict[str, Any],
    current: str,
    target: str,
    outcome: str,
    project_root: Path,
) -> None:
    if current == "independent-testing" and target == "remediation":
        if outcome not in {"fail", "incomplete"}:
            raise EvidenceError(
                "remediation requires a failed or incomplete test outcome"
            )
    elif outcome != "pass":
        raise EvidenceError("advancing this lifecycle stage requires a pass outcome")

    if current == "human-specification-approval":
        if latest_decision(manifest, "specification") != "approved":
            raise EvidenceError("human specification approval has not been recorded")
        if not manifest["context"].get("approved_specification"):
            raise EvidenceError("the approved specification reference is missing")
    if target == "implementation":
        branch = run_command("git", "branch", "--show-current", cwd=project_root)
        base = manifest["git"].get("base_branch")
        if not branch or branch == base:
            raise EvidenceError("implementation requires a feature branch")
        if (
            current == "remediation"
            and latest_decision(manifest, "implementation") != "approved"
        ):
            raise EvidenceError("remediation requires an approved human decision")
        manifest["git"]["branch"] = branch
    if (
        current == "independent-testing"
        and target == "pull-request"
        and manifest["adlc"]["version"] == EXPECTED_ADLC_VERSION
    ):
        publication = manifest["git"].get("publication", {})
        pull_request = manifest["git"].get("pull_request", {})
        if not all(publication.get(field) for field in ("remote", "branch", "commit")):
            raise EvidenceError("pull request requires recorded feature publication")
        if not pull_request.get("url") or not pull_request.get("number"):
            raise EvidenceError("pull request requires recorded PR context")
    if current == "pull-request":
        if not manifest["git"]["pull_request"].get("url"):
            raise EvidenceError("CI requires a recorded pull request")
        if not branch_is_pushed(project_root):
            raise EvidenceError("the feature branch must be pushed before CI")
    if current == "ci" and manifest["verification"]["ci"]["status"] != "pass":
        raise EvidenceError("human review requires passing CI")
    if current == "human-review" and latest_decision(manifest, "merge") != "approved":
        raise EvidenceError("merge requires recorded human review approval")
    if current == "merge" and manifest["git"]["merge_status"] != "merged":
        raise EvidenceError("run closure requires a recorded merge")


def transition_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    project_root = Path.cwd().resolve()

    def transition(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        try:
            state = lifecycle_from_dict(manifest["lifecycle"])
            next_state = advance_lifecycle(state, args.target)
            enforce_transition(
                manifest,
                state.current_stage,
                args.target,
                args.outcome,
                project_root,
            )
        except LifecycleError as error:
            raise EvidenceError(str(error)) from error
        stage = LIFECYCLE_STAGE_RECORDS.get(state.current_stage)
        if stage:
            manifest["stages"][stage] = {
                "status": args.outcome,
                "evidence": args.evidence,
                "recorded_at": utc_now(),
            }
        manifest["lifecycle"] = next_state.as_dict()
        if args.target == "run-closure":
            manifest["ended_at"] = utc_now()

    update_manifest(root, args.run_id, transition, event_type="lifecycle.transitioned")


def lifecycle_command(args: argparse.Namespace) -> None:
    manifest = load_manifest(Path(args.root).resolve(), args.run_id)
    if "lifecycle" not in manifest:
        raise EvidenceError("run predates the V0.3 lifecycle controller")
    print(json.dumps(manifest["lifecycle"], indent=2, sort_keys=True))


def stage_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        manifest["stages"][args.stage] = {
            "status": args.status,
            "evidence": args.evidence,
            "recorded_at": utc_now(),
        }

    update_manifest(root, args.run_id, record, event_type="stage.recorded")


def specification_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, [args.evidence])
        if has_lifecycle(manifest):
            state = lifecycle_from_dict(manifest["lifecycle"])
            if state.current_stage != "human-specification-approval":
                raise EvidenceError(
                    "approved specification can only be recorded at its approval stage"
                )
        manifest["context"]["approved_specification"] = args.evidence

    update_manifest(
        root, args.run_id, record, event_type="specification.approved-reference"
    )


def gate_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        manifest["verification"]["quality_gates"].append(
            {
                "name": args.name,
                "status": args.status,
                "evidence": args.evidence,
                "recorded_at": utc_now(),
            }
        )

    update_manifest(root, args.run_id, record, event_type="quality-gate.recorded")


def verification_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    field = "tests" if args.kind == "test" else "runtime_e2e"

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        manifest["verification"][field].append(
            {
                "name": args.name,
                "status": args.status,
                "evidence": args.evidence,
                "recorded_at": utc_now(),
            }
        )

    update_manifest(root, args.run_id, record, event_type="verification.recorded")


def unverified_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    reject_sensitive_text(args.criterion, "criterion")
    reject_sensitive_text(args.reason, "reason")

    def record(manifest: dict[str, Any]) -> None:
        manifest["verification"]["unverified_acceptance_criteria"].append(
            {"criterion": args.criterion, "reason": args.reason}
        )

    update_manifest(root, args.run_id, record, event_type="verification.unverified")


def ci_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        manifest["verification"]["ci"] = {
            "status": args.status,
            "workflow_run": args.workflow_run,
            "evidence": args.evidence,
            "recorded_at": utc_now(),
        }

    update_manifest(root, args.run_id, record, event_type="ci.recorded")


def decision_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        if has_lifecycle(manifest):
            current = lifecycle_from_dict(manifest["lifecycle"]).current_stage
            expected = {
                "specification": "human-specification-approval",
                "implementation": "remediation",
                "merge": "human-review",
            }.get(args.gate)
            if expected and current != expected:
                raise EvidenceError(
                    f"{args.gate} decision is invalid during lifecycle stage {current}"
                )
        manifest["human_decisions"].append(
            {
                "gate": args.gate,
                "decision": args.decision,
                "actor": "human",
                "evidence": args.evidence,
                "recorded_at": utc_now(),
            }
        )

    update_manifest(root, args.run_id, record, event_type="human-decision.recorded")


def failure_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    reject_sensitive_text(args.summary, "failure summary")
    reject_sensitive_text(args.intervention, "intervention")

    def record(manifest: dict[str, Any]) -> None:
        ensure_evidence_refs(manifest, args.evidence)
        manifest["failures_and_interventions"].append(
            {
                "summary": args.summary,
                "intervention": args.intervention,
                "retry": args.retry,
                "deviation": args.deviation,
                "evidence": args.evidence,
                "recorded_at": utc_now(),
            }
        )

    update_manifest(root, args.run_id, record, event_type="failure.recorded")


def close_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()

    def close(manifest: dict[str, Any]) -> None:
        if manifest["ended_at"] is not None:
            raise EvidenceError("run is already closed")
        if has_lifecycle(manifest):
            try:
                state = lifecycle_from_dict(manifest["lifecycle"])
                enforce_transition(
                    manifest, state.current_stage, "run-closure", "pass", Path.cwd()
                )
                manifest["lifecycle"] = advance_lifecycle(
                    state, "run-closure"
                ).as_dict()
                manifest["stages"]["merge"] = {
                    "status": "pass",
                    "evidence": manifest["stages"]["merge"].get("evidence", []),
                    "recorded_at": utc_now(),
                }
            except LifecycleError as error:
                raise EvidenceError(str(error)) from error
        manifest["ended_at"] = utc_now()

    update_manifest(root, args.run_id, close, event_type="run.closed")


def publish_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    project_root = Path.cwd().resolve()
    manifest = load_manifest(root, args.run_id)
    if manifest["adlc"]["version"] != EXPECTED_ADLC_VERSION:
        raise EvidenceError("publication automation requires an ADLC V0.5 run")
    state = lifecycle_from_dict(manifest["lifecycle"])
    if state.current_stage != "independent-testing":
        raise EvidenceError("publication requires the independent-testing stage")
    if manifest["stages"]["test-it"]["status"] != "pass":
        raise EvidenceError("publication requires passing independent testing")
    passed_checks = [
        item["name"]
        for field in ("tests", "quality_gates", "runtime_e2e")
        for item in manifest["verification"][field]
        if item.get("status") == "pass"
    ]
    verification_summary = (
        "Passed: " + ", ".join(dict.fromkeys(passed_checks))
        if passed_checks
        else "Independent testing recorded as passed."
    )
    def record_pushed(remote: str, branch: str, commit: str) -> None:
        def record(value: dict[str, Any]) -> None:
            current = lifecycle_from_dict(value["lifecycle"])
            if current.current_stage != "independent-testing":
                raise EvidenceError("lifecycle changed during publication")
            value["git"]["origin"] = sanitized_repository_url(remote)
            value["git"]["publication"] = {
                "remote": "origin",
                "branch": branch,
                "commit": commit,
            }
            value["git"].pop("publication_blocker", None)
            value["project"]["repository"] = sanitized_repository_url(remote)

        update_manifest(root, args.run_id, record, event_type="publication.pushed")

    try:
        config = load_config(root)
        result = publish_feature(
            project_root,
            config,
            verification_summary=verification_summary,
            on_pushed=record_pushed,
        )
    except (ConfigError, GitOpsError) as error:
        error_message = str(error)

        def record_blocker(value: dict[str, Any]) -> None:
            value["git"]["publication_blocker"] = {
                "message": error_message,
                "recorded_at": utc_now(),
            }

        update_manifest(
            root, args.run_id, record_blocker, event_type="publication.blocked"
        )
        raise EvidenceError(str(error)) from error

    def record(value: dict[str, Any]) -> None:
        current = lifecycle_from_dict(value["lifecycle"])
        if current.current_stage != "independent-testing":
            raise EvidenceError("lifecycle changed during publication")
        value["git"].pop("publication_blocker", None)
        value["git"]["pull_request"] = {
            "number": result.pull_request_number,
            "url": result.pull_request_url,
        }
        value["project"]["repository"] = sanitized_repository_url(result.remote)
        value["stages"]["test-it"] = {
            "status": "pass",
            "evidence": value["stages"]["test-it"].get("evidence", []),
            "recorded_at": utc_now(),
        }
        value["lifecycle"] = advance_lifecycle(current, "pull-request").as_dict()

    update_manifest(root, args.run_id, record, event_type="publication.completed")
    print(
        json.dumps(
            {
                "remote": "origin",
                "branch": result.branch,
                "commit": result.commit,
                "pull_request_number": result.pull_request_number,
                "pull_request_url": result.pull_request_url,
            },
            sort_keys=True,
        )
    )


def merge_context_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    if args.pull_request_url:
        validate_reference_uri(args.pull_request_url)

    def record(manifest: dict[str, Any]) -> None:
        if has_lifecycle(manifest):
            current = lifecycle_from_dict(manifest["lifecycle"]).current_stage
            if args.pull_request_url and current != "pull-request":
                raise EvidenceError(
                    "pull request context requires the pull-request stage"
                )
            if args.status == "merged":
                if current != "merge":
                    raise EvidenceError(
                        "merge can only be recorded during the merge stage"
                    )
                if manifest["verification"]["ci"]["status"] != "pass":
                    raise EvidenceError("merge requires passing CI")
                if latest_decision(manifest, "merge") != "approved":
                    raise EvidenceError("merge requires human review approval")
        if args.pull_request_number is not None or args.pull_request_url is not None:
            manifest["git"]["pull_request"] = {
                "number": args.pull_request_number,
                "url": args.pull_request_url,
            }
        manifest["git"]["merge_status"] = args.status
        if args.final_commit and args.final_commit not in manifest["git"]["commits"]:
            manifest["git"]["commits"].append(args.final_commit)

    update_manifest(root, args.run_id, record, event_type="git-context.recorded")


def validate_artifacts(root: Path, run_id: str, manifest: dict[str, Any]) -> None:
    directory = run_directory(root, run_id)
    for artifact in manifest["artifacts"]:
        if artifact["type"] in {"reference", "repository"}:
            continue
        path = directory / artifact["path"]
        if not path.is_file():
            raise EvidenceError(f"missing evidence: {artifact['path']}")
        if sha256_file(path) != artifact["sha256"]:
            raise EvidenceError(f"evidence hash mismatch: {artifact['path']}")


def validate_events(root: Path, run_id: str, manifest: dict[str, Any]) -> None:
    previous_hash: str | None = None
    for sequence in range(1, manifest["revision"] + 1):
        path = event_path(root, run_id, sequence)
        event = load_json(path)
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise EvidenceError(f"unsupported event schema: {path.name}")
        if event.get("run_id") != run_id or event.get("sequence") != sequence:
            raise EvidenceError(f"event identity mismatch: {path.name}")
        if event.get("previous_event_sha256") != previous_hash:
            raise EvidenceError(f"event chain mismatch: {path.name}")
        require_list(event.get("changes"), f"{path.name}.changes")
        previous_hash = sha256_file(path)
    extras = list(
        (run_directory(root, run_id) / "events").glob(
            f"event-{manifest['revision'] + 1:04d}.json"
        )
    )
    if extras:
        raise EvidenceError("event journal extends beyond manifest revision")
    final_event = load_json(event_path(root, run_id, manifest["revision"]))
    if final_event.get("manifest_sha256") != json_sha256(manifest):
        raise EvidenceError("current manifest does not match the final event")


def validate_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    manifest = load_manifest(root, args.run_id)
    validate_artifacts(root, args.run_id, manifest)
    if has_lifecycle(manifest):
        validate_events(root, args.run_id, manifest)
    print(f"valid run: {args.run_id}")


def supporting_evidence(root: Path, references: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for reference in references:
        if ":" not in reference:
            raise EvidenceError("support must use RUN_ID:RELATIVE_PATH")
        run_id, relative = reference.split(":", 1)
        manifest = load_manifest(root, run_id)
        matching = [
            artifact
            for artifact in manifest["artifacts"]
            if artifact["type"] == "file" and artifact.get("path") == relative
        ]
        if not matching:
            raise EvidenceError(f"supporting evidence is not registered: {reference}")
        artifact = matching[0]
        results.append(
            {
                "run_id": run_id,
                "path": relative,
                "sha256": artifact["sha256"],
            }
        )
    return results


def lesson_create_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    validate_identifier(args.lesson_id, "lesson ID")
    reject_sensitive_text(args.observation, "lesson observation")
    reject_sensitive_text(args.why, "lesson rationale")
    for reference in args.support:
        run_id = reference.split(":", 1)[0]
        manifest = load_manifest(root, run_id)
        if manifest["ended_at"] is None:
            raise EvidenceError("candidate lessons require evidence from closed runs")
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "lesson_id": args.lesson_id,
        "created_at": utc_now(),
        "observation": args.observation,
        "supporting_evidence": supporting_evidence(root, args.support),
        "why_it_matters": args.why,
        "proposed_target": args.target,
        "status": "proposed",
        "automatically_applied": False,
    }
    destination = root / "evidence" / "lessons" / args.lesson_id / "candidate.json"
    write_json_new(destination, candidate, readonly=True)
    (destination.parent / "status").mkdir()
    print(args.lesson_id)


def lesson_status_command(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    validate_identifier(args.lesson_id, "lesson ID")
    reject_sensitive_text(args.reason, "lesson decision reason")
    candidate_path = root / "evidence" / "lessons" / args.lesson_id / "candidate.json"
    candidate = load_json(candidate_path)
    if candidate.get("status") != "proposed":
        raise EvidenceError("candidate lesson is malformed")
    support = supporting_evidence(root, [args.decision_evidence])[0]
    status_directory = candidate_path.parent / "status"
    sequence = len(list(status_directory.glob("*.json"))) + 1
    record = {
        "schema_version": SCHEMA_VERSION,
        "lesson_id": args.lesson_id,
        "status": args.status,
        "reason": args.reason,
        "human_decision_evidence": support,
        "recorded_at": utc_now(),
        "automatically_applied": False,
    }
    write_json_new(
        status_directory / f"{sequence:04d}-{args.status}.json",
        record,
        readonly=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".adlc", help="ADLC evidence root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create an evidence run")
    create.add_argument("--run-id")
    create.add_argument("--project")
    create.add_argument(
        "--adlc-version",
        help="compatibility assertion; the canonical version comes from .adlc/VERSION",
    )
    create.add_argument("--base-branch")
    create.add_argument("--harness")
    create.add_argument("--model")
    create.add_argument("--request-file")
    create.set_defaults(handler=create_run)

    attach = subparsers.add_parser("attach", help="attach immutable raw evidence")
    attach.add_argument("run_id")
    attach.add_argument("category", choices=RAW_CATEGORIES)
    attach.add_argument("file")
    attach.add_argument("--name")
    attach.set_defaults(handler=attach_command)

    identify = subparsers.add_parser(
        "identify", help="register a committed repository file without copying it"
    )
    identify.add_argument("run_id")
    identify.add_argument("category", choices=RAW_CATEGORIES)
    identify.add_argument("file")
    identify.set_defaults(handler=identify_command)

    derived = subparsers.add_parser(
        "derive", help="store derived evidence with raw support"
    )
    derived.add_argument("run_id")
    derived.add_argument("category", choices=DERIVED_CATEGORIES)
    derived.add_argument("file")
    derived.add_argument("--name")
    derived.add_argument("--support", action="append", required=True)
    derived.set_defaults(handler=derived_command)

    reference = subparsers.add_parser("reference", help="record external evidence")
    reference.add_argument("run_id")
    reference.add_argument("category", choices=RAW_CATEGORIES)
    reference.add_argument("uri")
    reference.add_argument("--description", required=True)
    reference.set_defaults(handler=reference_command)

    stage = subparsers.add_parser("stage", help="record a stage outcome")
    stage.add_argument("run_id")
    stage.add_argument("stage", choices=STAGES)
    stage.add_argument("status", choices=OUTCOMES)
    stage.add_argument("--evidence", action="append", default=[])
    stage.set_defaults(handler=stage_command)

    lifecycle = subparsers.add_parser(
        "lifecycle", help="show current and permitted lifecycle stages"
    )
    lifecycle.add_argument("run_id")
    lifecycle.set_defaults(handler=lifecycle_command)

    transition = subparsers.add_parser(
        "transition", help="record one stage outcome and advance atomically"
    )
    transition.add_argument("run_id")
    transition.add_argument("target", choices=LIFECYCLE_STAGES)
    transition.add_argument(
        "outcome", choices=("pass", "fail", "incomplete", "blocked")
    )
    transition.add_argument("--evidence", action="append", default=[])
    transition.set_defaults(handler=transition_command)

    publish = subparsers.add_parser(
        "publish", help="push a verified feature branch and create its pull request"
    )
    publish.add_argument("run_id")
    publish.set_defaults(handler=publish_command)

    specification = subparsers.add_parser(
        "specification", help="record the approved specification reference"
    )
    specification.add_argument("run_id")
    specification.add_argument("evidence")
    specification.set_defaults(handler=specification_command)

    gate = subparsers.add_parser("gate", help="record a quality-gate result")
    gate.add_argument("run_id")
    gate.add_argument("name")
    gate.add_argument("status", choices=("pass", "fail", "incomplete"))
    gate.add_argument("--evidence", action="append", default=[])
    gate.set_defaults(handler=gate_command)

    verification = subparsers.add_parser(
        "verification", help="record a test or runtime result"
    )
    verification.add_argument("run_id")
    verification.add_argument("kind", choices=("test", "runtime-e2e"))
    verification.add_argument("name")
    verification.add_argument("status", choices=("pass", "fail", "incomplete"))
    verification.add_argument("--evidence", action="append", default=[])
    verification.set_defaults(handler=verification_command)

    unverified = subparsers.add_parser(
        "unverified", help="record an unverified acceptance criterion"
    )
    unverified.add_argument("run_id")
    unverified.add_argument("criterion")
    unverified.add_argument("--reason", required=True)
    unverified.set_defaults(handler=unverified_command)

    ci_result = subparsers.add_parser("ci", help="record a CI result")
    ci_result.add_argument("run_id")
    ci_result.add_argument("status", choices=("pass", "fail", "incomplete"))
    ci_result.add_argument("--workflow-run")
    ci_result.add_argument("--evidence", action="append", default=[])
    ci_result.set_defaults(handler=ci_command)

    decision = subparsers.add_parser("decision", help="record a human decision")
    decision.add_argument("run_id")
    decision.add_argument(
        "gate",
        choices=(
            "specification",
            "implementation",
            "merge",
            "lesson",
            "quality-gate",
        ),
    )
    decision.add_argument("decision", choices=("approved", "rejected", "pending"))
    decision.add_argument("--evidence", action="append", default=[])
    decision.set_defaults(handler=decision_command)

    failure = subparsers.add_parser("failure", help="record a failure or intervention")
    failure.add_argument("run_id")
    failure.add_argument("--summary", required=True)
    failure.add_argument("--intervention")
    failure.add_argument("--retry", action="store_true")
    failure.add_argument("--deviation", action="store_true")
    failure.add_argument("--evidence", action="append", default=[])
    failure.set_defaults(handler=failure_command)

    close = subparsers.add_parser("close", help="close a run")
    close.add_argument("run_id")
    close.set_defaults(handler=close_command)

    merge_context = subparsers.add_parser(
        "merge-context", help="record pull request and merge context"
    )
    merge_context.add_argument("run_id")
    merge_context.add_argument("status", choices=("not_merged", "merged"))
    merge_context.add_argument("--pull-request-number", type=int)
    merge_context.add_argument("--pull-request-url")
    merge_context.add_argument("--final-commit")
    merge_context.set_defaults(handler=merge_context_command)

    validate = subparsers.add_parser("validate", help="validate a run and raw hashes")
    validate.add_argument("run_id")
    validate.set_defaults(handler=validate_command)

    lesson = subparsers.add_parser("lesson-create", help="propose a candidate lesson")
    lesson.add_argument("lesson_id")
    lesson.add_argument("--observation", required=True)
    lesson.add_argument("--support", action="append", required=True)
    lesson.add_argument("--why", required=True)
    lesson.add_argument("--target", choices=LESSON_TARGETS, required=True)
    lesson.set_defaults(handler=lesson_create_command)

    lesson_status = subparsers.add_parser(
        "lesson-status", help="append a human lesson decision"
    )
    lesson_status.add_argument("lesson_id")
    lesson_status.add_argument(
        "status",
        choices=tuple(status for status in LESSON_STATUSES if status != "proposed"),
    )
    lesson_status.add_argument("--decision-evidence", required=True)
    lesson_status.add_argument("--reason", required=True)
    lesson_status.set_defaults(handler=lesson_status_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except EvidenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
