"""Command-line entry point for offline evaluation."""

import argparse
import asyncio
import json

from travel_agent_orchestrator.compat import configure_event_loop_policy
from travel_agent_orchestrator.evaluation.runner import run_evaluation
from travel_agent_orchestrator.infrastructure.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run hotel Multi-Agent offline evaluation")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Use the configured LLM for optional answer-quality judging.",
    )
    args = parser.parse_args()
    configure_event_loop_policy()
    report = asyncio.run(run_evaluation(settings=get_settings(), judge=args.judge))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
