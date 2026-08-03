"""Smoke coverage for the repeatable orchestration benchmark."""

from scripts import benchmark_orchestrator


def test_benchmark_reports_required_dimensions():
    result = benchmark_orchestrator.run_benchmark(iterations=5)

    assert result["benchmark"] == "omicsbase-orchestrator-v1"
    names = {item["name"] for item in result["results"]}
    assert names == {"idempotency_lookup", "replay_100_events", "append_event_commit"}
    assert all(item["p95_ms"] >= item["min_ms"] for item in result["results"])
    assert result["interpretation"]["external_comparison"] == "not_measured"

