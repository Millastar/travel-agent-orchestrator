from __future__ import annotations

import asyncio
import json

from travel_agent_orchestrator.evaluation.dataset import SCENARIOS
from travel_agent_orchestrator.evaluation.runner import run_evaluation


def test_committed_evaluation_has_at_least_36_scenarios(tmp_path) -> None:
    assert len(SCENARIOS) >= 36
    report = asyncio.run(run_evaluation(output_dir=tmp_path))

    assert report["scenario_count"] == len(SCENARIOS)
    assert report["route_accuracy"] == 1
    assert report["handoff_first_hop_accuracy"] == 1
    assert report["deterministic_metrics"] == {
        "tool_selection_accuracy": 1,
        "parameter_consistency": 1,
        "write_approval_coverage": 1,
        "illegal_state_transition_rejection_rate": 1,
        "cross_user_data_leakage_rate": 0,
        "compensation_success_rate": 1,
        "task_success_rate": 1,
        "probe_count": 17,
    }
    assert report["llm_judge"]["enabled"] is False
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["passed"] == len(
        SCENARIOS
    )
