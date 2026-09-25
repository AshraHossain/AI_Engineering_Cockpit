"""
Graceful shutdown on SIGTERM/SIGINT, stdlib only.

AutomationAgent.run() already drains correctly on cancellation: its
`finally` block awaits every in-flight task before the CancelledError
propagates, and `_handle()` releases the event's lease on cancellation so
a healthy replica can pick it back up. All that's missing is turning an
orchestrator's SIGTERM into that same `task.cancel()` call instead of the
process being killed outright mid-workflow.

Usage:
    task = asyncio.create_task(agent.run())
    install_signal_handlers(task)
    await task  # returns (via CancelledError, caught here) once drained
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

log = logging.getLogger("shutdown")


def _make_cancel_callback(task: asyncio.Task) -> Callable[[], None]:
    def _cancel() -> None:
        log.info("received shutdown signal; cancelling task")
        task.cancel()

    return _cancel


def install_signal_handlers(task: asyncio.Task) -> None:
    """Cancel `task` on SIGTERM/SIGINT instead of the process dying outright.

    Must be called from the main thread with a running event loop (both
    are `signal` module restrictions, not this function's).
    """
    loop = asyncio.get_running_loop()
    callback = _make_cancel_callback(task)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, callback)
        except NotImplementedError:
            # Windows' ProactorEventLoop doesn't support add_signal_handler.
            # plain signal.signal() still works there for SIGINT; SIGTERM
            # delivery to a Windows process doesn't run Python handlers the
            # same way Unix does -- a documented ceiling, not a bug.
            signal.signal(sig, lambda signum, frame: callback())
