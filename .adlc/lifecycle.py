"""Deterministic ADLC V0.5 lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LIFECYCLE_STAGES = (
    "specification",
    "human-specification-approval",
    "feature-branch",
    "implementation",
    "independent-testing",
    "remediation",
    "pull-request",
    "ci",
    "human-review",
    "merge",
    "run-closure",
    "retrospective",
)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "specification": ("human-specification-approval",),
    "human-specification-approval": ("feature-branch",),
    "feature-branch": ("implementation",),
    "implementation": ("independent-testing",),
    "independent-testing": ("remediation", "pull-request"),
    "remediation": ("implementation",),
    "pull-request": ("ci",),
    "ci": ("human-review",),
    "human-review": ("merge",),
    "merge": ("run-closure",),
    "run-closure": ("retrospective",),
    "retrospective": (),
}


class LifecycleError(ValueError):
    """Raised when a lifecycle state or transition is invalid."""


@dataclass(frozen=True)
class LifecycleState:
    active_run_id: str
    current_stage: str
    completed_stages: tuple[str, ...]
    open: bool

    @property
    def next_permitted_stages(self) -> tuple[str, ...]:
        return TRANSITIONS[self.current_stage]

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_run_id": self.active_run_id,
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "next_permitted_stages": list(self.next_permitted_stages),
            "open": self.open,
        }


def new_lifecycle(run_id: str) -> LifecycleState:
    return LifecycleState(run_id, "specification", (), True)


def lifecycle_from_dict(value: dict[str, Any]) -> LifecycleState:
    try:
        state = LifecycleState(
            active_run_id=value["active_run_id"],
            current_stage=value["current_stage"],
            completed_stages=tuple(value["completed_stages"]),
            open=value["open"],
        )
    except (KeyError, TypeError) as error:
        raise LifecycleError("malformed lifecycle state") from error
    validate_lifecycle(state, value.get("next_permitted_stages"))
    return state


def validate_lifecycle(
    state: LifecycleState, recorded_next: object | None = None
) -> None:
    if not isinstance(state.active_run_id, str) or not state.active_run_id:
        raise LifecycleError("active run ID is required")
    if state.current_stage not in LIFECYCLE_STAGES:
        raise LifecycleError(f"unknown lifecycle stage: {state.current_stage}")
    if any(stage not in LIFECYCLE_STAGES for stage in state.completed_stages):
        raise LifecycleError("completed stages contain an unknown stage")
    history = (*state.completed_stages, state.current_stage)
    if history[0] != "specification":
        raise LifecycleError("lifecycle history must begin with specification")
    for previous, following in zip(history, history[1:], strict=False):
        if following not in TRANSITIONS[previous]:
            raise LifecycleError(
                f"invalid lifecycle history transition: {previous} to {following}"
            )
    if state.open == (state.current_stage in {"run-closure", "retrospective"}):
        raise LifecycleError("lifecycle open state does not match its current stage")
    if recorded_next is not None:
        if not isinstance(recorded_next, list):
            raise LifecycleError("recorded next stages must be an array")
        if tuple(recorded_next) != state.next_permitted_stages:
            raise LifecycleError("recorded next stages do not match the state machine")


def advance_lifecycle(state: LifecycleState, target: str) -> LifecycleState:
    validate_lifecycle(state)
    if target not in state.next_permitted_stages:
        permitted = ", ".join(state.next_permitted_stages) or "none"
        raise LifecycleError(
            f"cannot transition from {state.current_stage} to {target}; "
            f"permitted: {permitted}"
        )
    if not state.open and target != "retrospective":
        raise LifecycleError("closed runs may only enter retrospective")

    next_state = LifecycleState(
        active_run_id=state.active_run_id,
        current_stage=target,
        completed_stages=(*state.completed_stages, state.current_stage),
        open=False if target == "run-closure" else state.open,
    )
    validate_lifecycle(next_state)
    return next_state
