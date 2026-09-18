"""CLI: put the order-support agent through a canary rollout and report.

Examples:
    # No API key, no network: a scripted scenario on a fake clock.
    uv run python src/main.py --dry-run --scenario bad-canary

    # Same, with every trace sent to a local Phoenix (see README).
    uv run python src/main.py --dry-run --scenario late-regression --phoenix

    # Real Claude calls. Spends money: see README before raising --requests.
    uv run python src/main.py --requests 60 --canary-percent 50 --phoenix
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse
import logging
import time
from collections.abc import Callable, Sequence

import anthropic
from cockpit.governance.approval_workflow import (
    ApprovalError,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalWorkflow,
)
from cockpit.governance.audit_trail import verify_trail_integrity
from cockpit.governance.model_versioning import ModelRegistry
from cockpit.monitoring.cost_tracking import CostTracker
from cockpit.monitoring.dashboard import build_dashboard_snapshot, render_dashboard_text
from cockpit.monitoring.performance_metrics import PerformanceTracker
from cockpit.security.secrets_manager import get_secret
from dotenv import load_dotenv

from agent import CANARY, STABLE, AgentVersion, RunRecord, run_agent
from alerts import AlertMonitor, jsonl_sink, log_sink
from canary import (
    DEFAULT_CANARY_PERCENT,
    MODEL_NAME,
    RolloutController,
    register_versions,
)
from fake_model import FakeClock, scripted_client
from scenarios import SCENARIOS
from tools import SAMPLE_QUESTIONS
from tracing import build_tracer_provider, phoenix_exporter

PROJECT_DIR = Path(__file__).resolve().parents[1]
ALERTS_PATH = PROJECT_DIR / "outputs" / "alerts.jsonl"
DEFAULT_REQUESTS = 20


def prompt_approvals(workflow: ApprovalWorkflow, request: ApprovalRequest) -> None:
    """Ask at the terminal for decisions until the request is decided or no one is left.

    Args:
        workflow: Workflow holding the request.
        request: The open promotion request.
    """
    print(f"\nApproval needed ({request.required_approvals} approvers):\n{request.description}")
    while workflow.get(request.request_id).status is ApprovalStatus.PENDING:
        name = input("Approver name (blank to stop): ").strip()
        if not name:
            return
        approved = input("Approve or reject? [a/r]: ").strip().lower() == "a"
        comment = input("Comment (optional): ").strip() or None
        try:
            workflow.decide(request.request_id, approved, name, comment=comment)
        except ApprovalError as exc:
            print(f"  not recorded: {exc}")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse and cross-check the command line.

    Args:
        argv: Arguments, or None for ``sys.argv[1:]``.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="scripted models, fake clock, no API key"
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="dry-run scenario to play")
    parser.add_argument(
        "--requests", type=int, default=DEFAULT_REQUESTS, help="live mode: requests to send"
    )
    parser.add_argument(
        "--canary-percent",
        type=int,
        default=DEFAULT_CANARY_PERCENT,
        help="live mode: share routed to the canary",
    )
    parser.add_argument("--phoenix", action="store_true", help="export traces to a local Phoenix")
    args = parser.parse_args(argv)
    if args.dry_run != (args.scenario is not None):
        parser.error("--dry-run and --scenario go together")
    if not 0 <= args.canary_percent <= 100:
        parser.error("--canary-percent must be between 0 and 100")
    if args.requests < 1:
        parser.error("--requests must be at least 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run the rollout and print the dashboard, timeline and trail check.

    Args:
        argv: Arguments, or None for ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 2 for a missing or rejected API key.
    """
    args = parse_args(argv)
    load_dotenv(PROJECT_DIR / ".env")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    clock: Callable[[], float]
    if args.dry_run:
        scenario = SCENARIOS[args.scenario]
        clock = FakeClock()
        client = scripted_client(scenario.profiles, clock)
        requests = scenario.requests
        approve = scenario.approve
        percent = DEFAULT_CANARY_PERCENT
    else:
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "ANTHROPIC_API_KEY is not set. Use --dry-run, or add the key to .env.",
                file=sys.stderr,
            )
            return 2
        clock = time.time
        client = anthropic.Anthropic(api_key=api_key)
        requests = [
            (f"req-{n:04d}", SAMPLE_QUESTIONS[(n - 1) % len(SAMPLE_QUESTIONS)])
            for n in range(1, args.requests + 1)
        ]
        approve = prompt_approvals
        percent = args.canary_percent

    provider = build_tracer_provider(phoenix_exporter() if args.phoenix else None)
    tracer = provider.get_tracer("production-agent")
    perf = PerformanceTracker(clock=clock)
    costs = CostTracker()
    registry = ModelRegistry()
    register_versions(registry, STABLE, CANARY)

    def run(version: AgentVersion, question: str, request_id: str, group: str) -> RunRecord:
        return run_agent(
            client,
            version,
            question,
            request_id=request_id,
            group=group,
            tracer=tracer,
            perf=perf,
            costs=costs,
            clock=clock,
        )

    controller = RolloutController(
        registry=registry,
        workflow=ApprovalWorkflow(),
        stable=STABLE,
        canary=CANARY,
        run=run,
        approve=approve,
        tracer=tracer,
        monitor=AlertMonitor([log_sink, jsonl_sink(ALERTS_PATH)]),
        perf=perf,
        clock=clock,
        canary_percent=percent,
    )
    try:
        for request_id, question in requests:
            controller.handle(request_id, question)
    except anthropic.AuthenticationError:
        print(
            "Authentication failed: the API key was rejected. Check ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2
    finally:
        provider.shutdown()

    print(render_dashboard_text(build_dashboard_snapshot(costs, perf)))
    print("\nRollout timeline")
    for request_id, event in controller.timeline:
        print(f"  {request_id}  {event}")
    print(
        f"\nPhase: {controller.phase}. In production: {registry.get_production(MODEL_NAME).version}."
    )
    print(f"Governance trail intact: {verify_trail_integrity()}")
    print(f"Alert log: {ALERTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
