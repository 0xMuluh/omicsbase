"""Repeatable local benchmark for OmicsBase orchestration primitives.

This measures OmicsBase's own orchestration overhead. Comparisons with
external agents would require adapters that send the same scenario to each
system; this script deliberately does not fabricate those numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from typing import Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models.knowledge  # noqa: F401
import app.models.notes  # noqa: F401
import app.models.project  # noqa: F401
import app.models.runs  # noqa: F401
from app.services.agent_runs import (
    append_run_event,
    create_or_get_agent_run,
    list_run_events,
)
from scripts.agent_harness import run_harness


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
    return ordered[index]


def _measure(name: str, fn: Callable[[], None], iterations: int) -> dict[str, float | int | str]:
    for _ in range(min(3, iterations)):
        fn()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "name": name,
        "iterations": iterations,
        "p50_ms": round(statistics.median(samples), 4),
        "p95_ms": round(_percentile(samples, 95), 4),
        "mean_ms": round(statistics.mean(samples), 4),
        "min_ms": round(min(samples), 4),
        "max_ms": round(max(samples), 4),
    }


def _build_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def run_benchmark(iterations: int = 25) -> dict:
    engine, factory = _build_db()
    db = factory()
    try:
        run, _ = create_or_get_agent_run(
            db,
            tenant_id="benchmark-tenant",
            owner_id="benchmark-user",
            surface="workspace",
            idempotency_scope="benchmark:turn",
            idempotency_key="seed",
            request_payload={"message": "benchmark"},
        )
        db.commit()
        for sequence in range(100):
            append_run_event(
                db,
                run,
                "benchmark_event",
                {"sequence": sequence, "payload": "x" * 40},
                idempotency_key=f"seed-event-{sequence}",
            )
        db.commit()

        def idempotency_lookup() -> None:
            create_or_get_agent_run(
                db,
                tenant_id="benchmark-tenant",
                owner_id="benchmark-user",
                surface="workspace",
                idempotency_scope="benchmark:turn",
                idempotency_key="seed",
                request_payload={"message": "benchmark"},
            )

        def replay_events() -> None:
            assert len(list_run_events(db, str(run.id), after_sequence=0, limit=200)) == 101

        def append_and_commit() -> None:
            key = f"measurement-{time.time_ns()}"
            append_run_event(db, run, "benchmark_measurement", {"key": key}, idempotency_key=key)
            db.commit()

        results = [
            _measure("idempotency_lookup", idempotency_lookup, iterations),
            _measure("replay_100_events", replay_events, iterations),
            _measure("append_event_commit", append_and_commit, iterations),
        ]
        return {
            "benchmark": "omicsbase-orchestrator-v1",
            "python": os.sys.version.split()[0],
            "database": "sqlite",
            "iterations": iterations,
            "results": results,
            "agent_harness": run_harness(),
            "interpretation": {
                "external_comparison": "not_measured",
                "required_for_competitor_claim": "Run the same scenario through provider/tool adapters with identical model, prompt, tool, and network conditions.",
            },
        }
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=25)
    args = parser.parse_args()
    if args.iterations < 5 or args.iterations > 1000:
        parser.error("--iterations must be between 5 and 1000")
    print(json.dumps(run_benchmark(args.iterations), indent=2, default=str))


if __name__ == "__main__":
    main()

