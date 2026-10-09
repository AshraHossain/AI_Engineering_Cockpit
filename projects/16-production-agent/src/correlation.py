"""Tag every log record with the request it belongs to.

Tools log from deep inside the Tool Runner loop (a service outage, an
off-contract answer, a breaker opening) with no idea which request caused it.
``bound_request`` sets a context variable for the duration of a run, and
``RequestIdFilter`` copies it onto each record so a format string can print
``%(request_id)s``. Records logged outside a run show ``-``.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Iterator

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


@contextlib.contextmanager
def bound_request(request_id: str) -> Iterator[None]:
    """Mark everything logged inside the block as belonging to ``request_id``."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Adds ``record.request_id``. Attach to a handler, not a logger, so it sees every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True
