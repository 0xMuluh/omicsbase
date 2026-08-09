"""Concurrency-safe project title ownership transitions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.project import Project


def claim_auto_title(
    db: Session,
    *,
    project_id: str,
    expected_name: str,
    proposed_name: str,
) -> str | None:
    """Atomically apply an LLM title only while the original name is default.

    The source and expected-name predicates form a compare-and-set. If a user
    renames the project while the LLM request is in flight, the update touches
    zero rows and the user's name wins.
    """
    clean = (proposed_name or "").strip()[:255]
    if not clean or clean == expected_name:
        return None

    updated = (
        db.query(Project)
        .filter(
            Project.id == str(project_id),
            Project.name_source == "default",
            Project.name == expected_name,
        )
        .update(
            {
                Project.name: clean,
                Project.name_source: "auto",
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        return None
    db.commit()
    return clean
