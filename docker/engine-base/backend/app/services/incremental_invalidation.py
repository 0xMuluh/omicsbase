"""Dependency-aware invalidation for adaptive ReportPack reruns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.services.report_pack import ReportPack


@dataclass(frozen=True)
class InvalidationPlan:
    changed_paths: tuple[str, ...]
    impacted_capabilities: tuple[str, ...]
    invalidated_steps: tuple[str, ...]
    earliest_step_index: int | None
    resume_from_step: str | None
    targeted_pages: tuple[str, ...]

    @property
    def full_workflow_invalidated(self) -> bool:
        return self.earliest_step_index == 0 and bool(self.invalidated_steps)

    def as_dict(self) -> dict[str, object]:
        return {
            "changed_paths": list(self.changed_paths),
            "impacted_capabilities": list(self.impacted_capabilities),
            "invalidated_steps": list(self.invalidated_steps),
            "earliest_step_index": self.earliest_step_index,
            "resume_from_step": self.resume_from_step,
            "targeted_pages": list(self.targeted_pages),
            "full_workflow_invalidated": self.full_workflow_invalidated,
        }


def plan_invalidation(pack: ReportPack, changed_paths: Iterable[str]) -> InvalidationPlan:
    """Return the smallest safe rerun scope for changed source paths.

    An executable source invalidates its own step and all downstream steps;
    a helper/data-loader change invalidates from the first consuming step. QMD
    pages remain targeted render work when no executable source is affected.
    """
    paths = tuple(sorted({str(path).replace("\\", "/").lstrip("./") for path in changed_paths if str(path).strip()}))
    steps = tuple(pack.execution.steps) if pack.execution else ()
    step_indexes = {step.path: index for index, step in enumerate(steps)}
    impacted_capabilities: set[str] = set()
    impacted_indexes: set[int] = set()
    targeted_pages: set[str] = set()
    for relative in paths:
        classification = pack.classify(relative)
        if classification.role == "page" or Path(relative).suffix.lower() in {".qmd", ".rmd"}:
            # The runner discovers Quarto pages relative to the execution
            # working directory, so persist the same coordinate system. Keep
            # project-relative changed_paths for provenance and auditability.
            page = relative
            working_directory = (
                pack.execution.working_directory if pack.execution is not None else ""
            ).strip("/")
            if working_directory and page.startswith(working_directory + "/"):
                page = page[len(working_directory) + 1:]
            elif not working_directory and page.startswith("code/"):
                page = page[5:]
            targeted_pages.add(page)
        if relative in step_indexes:
            impacted_indexes.add(step_indexes[relative])
        elif classification.role in {"data_loader", "helper", "analysis", "validator", "orchestrator"}:
            # A declared source helper may be consumed by any subsequent step;
            # use the earliest executable step as the conservative boundary.
            if steps:
                impacted_indexes.add(0)
        for capability in pack.capabilities:
            if relative in set(capability.sources) | set(capability.validators):
                impacted_capabilities.add(capability.capability_id)
                for step_id in capability.execution_steps:
                    index = next((i for i, step in enumerate(steps) if step.step_id == step_id), None)
                    if index is not None:
                        impacted_indexes.add(index)
    if impacted_indexes:
        earliest = min(impacted_indexes)
        invalidated = tuple(step.step_id for step in steps[earliest:])
        resume = steps[earliest].step_id
    else:
        earliest = None
        invalidated = ()
        resume = None
    return InvalidationPlan(
        changed_paths=paths,
        impacted_capabilities=tuple(sorted(impacted_capabilities)),
        invalidated_steps=invalidated,
        earliest_step_index=earliest,
        resume_from_step=resume,
        targeted_pages=tuple(sorted(targeted_pages)),
    )


__all__ = ["InvalidationPlan", "plan_invalidation"]
