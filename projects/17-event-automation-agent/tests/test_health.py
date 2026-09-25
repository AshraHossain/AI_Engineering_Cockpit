"""Behavioural tests for HealthChecker's aggregation logic and the real
HTTP server it can be exposed through."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from health import HealthChecker, serve_health


def test_ready_when_no_checks_registered():
    checker = HealthChecker()
    ok, statuses = checker.ready()
    assert ok is True
    assert statuses == {}


def test_not_ready_when_any_individually_required_check_fails():
    checker = HealthChecker()
    checker.register("a", lambda: True)
    checker.register("b", lambda: False)
    ok, statuses = checker.ready()
    assert ok is False
    assert statuses == {"a": True, "b": False}


def test_ready_when_all_individually_required_checks_pass():
    checker = HealthChecker()
    checker.register("a", lambda: True)
    checker.register("b", lambda: True)
    ok, _ = checker.ready()
    assert ok is True


def test_require_any_group_passes_if_one_member_is_healthy():
    checker = HealthChecker()
    checker.register("retriever", lambda: False)
    checker.register("search_fallback", lambda: True)
    checker.require_any("retriever", "search_fallback")
    ok, _ = checker.ready()
    assert ok is True


def test_require_any_group_fails_if_all_members_unhealthy():
    checker = HealthChecker()
    checker.register("retriever", lambda: False)
    checker.register("search_fallback", lambda: False)
    checker.require_any("retriever", "search_fallback")
    ok, _ = checker.ready()
    assert ok is False


def test_a_raising_check_counts_as_unhealthy_not_a_crash():
    checker = HealthChecker()

    def broken():
        raise RuntimeError("boom")

    checker.register("flaky", broken)
    ok, statuses = checker.ready()
    assert ok is False
    assert statuses["flaky"] is False


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_healthz_returns_200_regardless_of_readiness():
    checker = HealthChecker()
    checker.register("down", lambda: False)
    server = serve_health(checker, port=0)
    try:
        status, body = _get(f"http://127.0.0.1:{server.server_port}/healthz")
        assert status == 200
        assert body["status"] == "alive"
    finally:
        server.shutdown()
        server.server_close()


def test_readyz_returns_503_when_not_ready():
    checker = HealthChecker()
    checker.register("retriever", lambda: False)
    server = serve_health(checker, port=0)
    try:
        status, body = _get(f"http://127.0.0.1:{server.server_port}/readyz")
        assert status == 503
        assert body["components"]["retriever"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_readyz_returns_200_when_ready():
    checker = HealthChecker()
    checker.register("retriever", lambda: True)
    server = serve_health(checker, port=0)
    try:
        status, body = _get(f"http://127.0.0.1:{server.server_port}/readyz")
        assert status == 200
        assert body["status"] == "ready"
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_path_returns_404():
    checker = HealthChecker()
    server = serve_health(checker, port=0)
    try:
        status, _ = _get(f"http://127.0.0.1:{server.server_port}/nope")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
