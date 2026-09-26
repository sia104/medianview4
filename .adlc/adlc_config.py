"""Minimal reusable ADLC configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

BRANCH_NAME = re.compile(r"^(?![./])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]+(?<![./])$")


class ConfigError(ValueError):
    """Raised when reusable ADLC configuration is invalid."""


@dataclass(frozen=True)
class AdlcConfig:
    base_branch: str
    repository_visibility: str


def load_config(adlc_root: Path) -> AdlcConfig:
    path = adlc_root / "config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"missing ADLC configuration: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"invalid ADLC configuration: {error.msg}") from error
    if not isinstance(value, dict) or set(value) != {
        "base_branch",
        "repository_visibility",
    }:
        raise ConfigError(
            "ADLC configuration must contain only base_branch and "
            "repository_visibility"
        )
    base_branch = value["base_branch"]
    visibility = value["repository_visibility"]
    if not isinstance(base_branch, str) or not BRANCH_NAME.fullmatch(base_branch):
        raise ConfigError("base_branch is not a valid branch name")
    if visibility not in {"public", "private"}:
        raise ConfigError("repository_visibility must be public or private")
    return AdlcConfig(base_branch, visibility)
