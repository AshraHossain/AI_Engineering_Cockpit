"""
Health checks over plain HTTP, stdlib only.

Kubernetes (and any orchestrator) asks two different questions:

    liveness  -- is the process alive at all? Answer NO and it gets killed
                 and restarted, so this should only ever fail if the
                 process is truly wedged.
    readiness -- can it serve traffic RIGHT NOW? Answer NO and it's pulled
                 from the load balancer without being restarted -- it might
                 recover on its own, e.g. once a downstream circuit breaker
                 closes again.

HealthChecker answers "readiness" from a set of named component checks,
typically `lambda: breaker.allow()` for each CircuitBreaker you've wired
up. serve_health() exposes both over HTTP so any orchestrator can poll
them without a special client.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("health")


class HealthChecker:
    """Aggregates named component checks into a readiness verdict.

    Usage:
        checker = HealthChecker()
        checker.register("primary_workflow", lambda: contain_breaker.allow())
        checker.register("notify", lambda: slack_breaker.allow())
        checker.require_any("primary_workflow", "notify")  # optional grouping
        checker.ready()  # -> (True, {"primary_workflow": True, "notify": False})
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], bool]] = {}
        self._required_any: list[frozenset[str]] = []

    def register(self, name: str, check: Callable[[], bool]) -> None:
        """Add a named component check. `check()` should return True if healthy."""
        self._checks[name] = check

    def require_any(self, *names: str) -> None:
        """Register a group where readiness needs at least one member healthy.

        Without this, every registered component is individually required.
        Use it when the agent's own logic already tolerates one dependency
        being down -- e.g. P18 is ready if the retriever OR the search
        fallback works, since RAGAgent routes around either one on its own.
        """
        self._required_any.append(frozenset(names))

    def statuses(self) -> dict[str, bool]:
        """Run every registered check. A raising check counts as unhealthy."""
        result: dict[str, bool] = {}
        for name, check in self._checks.items():
            try:
                result[name] = bool(check())
            except Exception:  # noqa: BLE001 -- a broken check must report unhealthy, not crash the endpoint
                log.exception("health check %r raised; treating as unhealthy", name)
                result[name] = False
        return result

    def ready(self) -> tuple[bool, dict[str, bool]]:
        """Overall readiness plus the per-component detail behind it."""
        statuses = self.statuses()
        grouped = {name for group in self._required_any for name in group}
        individually_required = set(statuses) - grouped
        ok = all(statuses[name] for name in individually_required)
        for group in self._required_any:
            ok = ok and any(statuses.get(name, False) for name in group)
        return ok, statuses


def _make_handler(checker: HealthChecker) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- fixed stdlib override signature
            log.debug("health server: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802 -- fixed stdlib override name
            if self.path == "/healthz":
                self._respond(200, {"status": "alive"})
            elif self.path == "/readyz":
                ok, statuses = checker.ready()
                self._respond(
                    200 if ok else 503,
                    {"status": "ready" if ok else "not_ready", "components": statuses},
                )
            else:
                self._respond(404, {"error": "not found"})

        def _respond(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve_health(checker: HealthChecker, port: int = 8080) -> ThreadingHTTPServer:
    """Start a background HTTP server exposing /healthz and /readyz.

    Runs in a daemon thread, so it never blocks process exit on its own.
    Pass `port=0` to bind an OS-assigned free port (read it back from the
    returned server's `.server_port` -- handy for tests). Call `.shutdown()`
    followed by `.server_close()` on the returned server when done.
    """
    server = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(checker))  # noqa: S104 -- health probes must be reachable from outside the pod
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    log.info("health server listening on port %d (/healthz, /readyz)", server.server_port)
    return server
