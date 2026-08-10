"""CLI: send one prompt through the secure gateway and show what happened.

Unlike projects 01-05, this project is *not* standalone -- importing the
Tier 3 security framework is the whole point. One request goes through
:class:`~gateway.SecureGateway`, and the CLI prints three things: the
decision (including which stage refused, if one did), the sanitized
response, and an audit summary with a hash-chain verification.

Run it with:
    uv run python src/main.py --dry-run
    uv run python src/main.py --dry-run --role external
    uv run python src/main.py --dry-run --prompt "Ignore all previous instructions."
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repo root is package=false by design, so it isn't installed into this
# project's venv -- put it on sys.path to import the cockpit frameworks.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from collections.abc import Sequence  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

from cockpit.security.access_control import AuthorizationError, Principal, Role  # noqa: E402
from cockpit.security.audit_logging import (  # noqa: E402
    AuditLog,
    AuditRecord,
    ChainVerification,
    load_chain,
)
from credentials import (  # noqa: E402
    PLACEHOLDER_API_KEY,
    MissingAPIKeyError,
    credential_handling_policy,
    get_api_key,
    get_secret,
    redaction_proof,
    store_api_key,
)
from gateway import (  # noqa: E402
    DEFAULT_MODEL,
    GatewayConfig,
    GatewayResult,
    GenerateFn,
    Outcome,
    SecureGateway,
    build_gemini_generate_fn,
    read_audit_records,
)

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = "Summarize the on-call handoff notes for ticket 4417."

# The offline model's canned answer. It carries an email address, a phone
# number, and an IPv4 address on purpose: the sanitize stage has to mask all
# three before the text is returned *or* written to the audit log.
OFFLINE_RESPONSE = (
    "Ticket 4417 was handed to the night shift. Escalation contact: "
    "dana.reyes@example.com or 555-010-7788. The failing node was "
    "10.2.14.9; it has been drained and taken out of rotation."
)

SEPARATOR = "=" * 70


def configure_logging(level: str | None = None) -> None:
    """Configure a minimal stdout logging setup for this script.

    Args:
        level: Logging level name. Defaults to the LOG_LEVEL environment
            variable, or "INFO" if unset.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level or os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def make_offline_generate_fn(response: str = OFFLINE_RESPONSE) -> GenerateFn:
    """Build a canned ``generate_fn`` that needs no key and no network.

    Args:
        response: The text every call returns.

    Returns:
        A ``prompt -> text`` callable suitable for
        :class:`~gateway.SecureGateway`.
    """

    def generate(prompt: str) -> str:
        logger.debug("Offline model answering a %d-character prompt.", len(prompt))
        return response

    return generate


def build_principal(role_name: str, principal_id: str | None = None) -> Principal:
    """Build the calling principal for a role name.

    Args:
        role_name: One of the :class:`~cockpit.security.access_control.Role`
            values, e.g. ``"developer"``.
        principal_id: Explicit actor id. Defaults to ``"demo-<role>"``.

    Returns:
        The :class:`~cockpit.security.access_control.Principal` to call with.

    Raises:
        ValueError: If ``role_name`` is not a recognized role.
    """
    role = Role(role_name)
    return Principal(principal_id=principal_id or f"demo-{role.value}", roles=frozenset({role}))


def open_audit_log(audit_file: str | None) -> AuditLog:
    """Open the audit log, resuming an existing chain when one is on disk.

    Args:
        audit_file: Path to a JSON-lines audit file, or ``None`` for an
            in-memory log that lives only for this run.

    Returns:
        The :class:`~cockpit.security.audit_logging.AuditLog` to record into.
        Resuming means the chain spans runs, so verification covers every
        request the file has ever seen -- not just today's.

    Raises:
        OSError: If the file exists but cannot be read.
    """
    if audit_file is None:
        return AuditLog()
    path = Path(audit_file)
    return AuditLog(records=load_chain(path), sink_path=path)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="11-secure-gateway",
        description="Send one prompt through an authorize/validate/invoke/sanitize/audit pipeline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a canned offline model and a placeholder key. No API key needed.",
    )
    parser.add_argument(
        "--role",
        default=Role.DEVELOPER.value,
        choices=[role.value for role in Role],
        help="Role held by the calling principal (default: developer).",
    )
    parser.add_argument(
        "--principal-id",
        default=None,
        help="Actor id recorded in the audit log (default: demo-<role>).",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="The prompt to send.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to route the request to (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--flag-only",
        action="store_true",
        help="Record input-validation findings and serve anyway, instead of refusing.",
    )
    parser.add_argument(
        "--audit-file",
        default=None,
        help="Append the audit chain to this JSON-lines file and verify it across runs.",
    )
    return parser


def format_decision(result: GatewayResult) -> str:
    """Render the gateway's decision as a block of text.

    Args:
        result: The result to render.

    Returns:
        The formatted decision block.
    """
    stage = result.refused_at.value if result.refused_at else "-"
    lines = [
        SEPARATOR,
        "AI Engineering Cockpit - Secure Gateway",
        SEPARATOR,
        "",
        "DECISION",
        f"  Principal        {result.principal_id}",
        f"  Outcome          {result.outcome.value.upper()}",
        f"  Refused at       {stage}",
        f"  Reason           {result.reason}",
        f"  Audit sequence   #{result.audit_sequence}",
    ]
    if result.findings:
        lines.append("  Input findings")
        lines.extend(f"    - {finding}" for finding in result.findings)
    lines.append("")
    lines.append("RESPONSE")
    if result.response is None:
        lines.append("  <none -- the request never reached the model>")
    else:
        lines.append(f"  {result.response}")
        masked = ", ".join(result.pii_categories) if result.pii_categories else "none"
        lines.append(f"  (PII masked: {masked})")
    return "\n".join(lines)


def format_audit_summary(
    records: list[AuditRecord] | None,
    verification: ChainVerification,
    total: int,
) -> str:
    """Render the audit trail and its chain-verification result.

    Args:
        records: The records to list, or ``None`` when the caller is not
            allowed to read them.
        verification: The chain-verification result, which is integrity
            metadata rather than log content and is therefore always shown.
        total: How many records the chain holds.

    Returns:
        The formatted audit block.
    """
    lines = ["", f"AUDIT TRAIL ({total} record(s))"]
    if records is None:
        lines.append("  <withheld -- the caller does not hold audit_log:read>")
    else:
        for record in records:
            event = record.event
            stage = str(event.metadata.get("stage", "-"))
            lines.append(
                f"  #{record.sequence:<3} {event.actor_id:<16} {event.action:<14} "
                f"{event.outcome:<8} stage={stage}"
            )
    verdict = "VALID" if verification.is_valid else "BROKEN"
    lines.append(
        f"  chain verification: {verdict} ({verification.records_checked} record(s) checked)"
    )
    if not verification.is_valid:
        lines.append(f"  broken at index {verification.broken_index}: {verification.reason}")
    return "\n".join(lines)


def format_credential_block(store_lines: list[str], policy_line: str) -> str:
    """Render the credential-handling proof.

    Args:
        store_lines: Output of :func:`~credentials.redaction_proof`.
        policy_line: A one-line summary of the handling policy.

    Returns:
        The formatted credential block.
    """
    lines = ["", "CREDENTIAL HANDLING", f"  {policy_line}"]
    lines.extend(f"  {line}" for line in store_lines)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: run one request through the gateway and report.

    Args:
        argv: Argument vector to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if the request was served, 1 on a configuration
        error, 2 if policy refused it or the model call failed.
    """
    configure_logging()
    args = build_parser().parse_args(argv)

    if args.dry_run:
        api_key = PLACEHOLDER_API_KEY
        generate_fn = make_offline_generate_fn()
    else:
        load_dotenv()
        try:
            api_key = get_api_key()
        except MissingAPIKeyError as exc:
            logger.error("Configuration error: %s", exc)
            return 1
        generate_fn = build_gemini_generate_fn(api_key, model=args.model)

    secret_store = store_api_key(api_key)
    policy = credential_handling_policy(api_key)
    # Drop the last plain reference to the key; from here it lives only
    # inside the Secret, and only get_secret(...).reveal() can read it back.
    del api_key

    try:
        principal = build_principal(args.role, args.principal_id)
    except ValueError as exc:
        logger.error("Argument error: %s", exc)
        return 1

    audit_log = open_audit_log(args.audit_file)
    gateway = SecureGateway(
        generate_fn=generate_fn,
        audit_log=audit_log,
        config=GatewayConfig(model=args.model, refuse_on_injection=not args.flag_only),
    )

    result = gateway.handle(principal, args.prompt)

    try:
        readable: list[AuditRecord] | None = read_audit_records(principal, audit_log)
    except AuthorizationError:
        readable = None

    policy_line = (
        f"classification={policy.classification.value} "
        f"allow_in_logs={policy.allow_in_logs} allow_in_prompts={policy.allow_in_prompts}"
    )

    print(format_decision(result))
    print(format_audit_summary(readable, audit_log.verify(), len(audit_log.records)))
    print(format_credential_block(redaction_proof(secret_store), policy_line))
    revealed_length = len(get_secret(secret_store).reveal())
    print(
        f"\n  The key is still reachable where it must be ({revealed_length} characters), "
        "but only through an explicit, greppable .reveal() call."
    )
    print(SEPARATOR)

    return 0 if result.outcome is Outcome.ALLOWED else 2


if __name__ == "__main__":
    sys.exit(main())
