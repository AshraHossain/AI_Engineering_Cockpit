"""Log records carry the request ID of the run that produced them."""

from __future__ import annotations

import io
import logging

from correlation import RequestIdFilter, bound_request


def _logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter("[%(request_id)s] %(message)s"))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_records_inside_a_run_carry_its_request_id() -> None:
    logger, out = _logger("t.inside")
    with bound_request("req-7"):
        logger.info("lookup failed")
    assert out.getvalue() == "[req-7] lookup failed\n"


def test_records_outside_a_run_show_a_dash() -> None:
    logger, out = _logger("t.outside")
    logger.info("startup")
    assert out.getvalue() == "[-] startup\n"


def test_the_id_is_restored_after_the_run_even_on_error() -> None:
    logger, out = _logger("t.restore")
    try:
        with bound_request("req-1"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    logger.info("after")
    assert out.getvalue() == "[-] after\n"


def test_nested_runs_restore_the_outer_id() -> None:
    logger, out = _logger("t.nested")
    with bound_request("outer"):
        with bound_request("inner"):
            logger.info("a")
        logger.info("b")
    assert out.getvalue() == "[inner] a\n[outer] b\n"
