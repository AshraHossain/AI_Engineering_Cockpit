"""Behavioural test for the cancel-on-signal callback.

Doesn't fire real OS signals: signal delivery semantics differ enough
between platforms (the CI matrix includes Windows, where SIGTERM doesn't
reach a Python handler at all) that testing the two-line `signal.signal` /
`loop.add_signal_handler` wiring via actual `os.kill` would be flaky by
platform, not by bug. What's worth pinning is the one piece of actual
logic: that the callback cancels the right task.
"""

from __future__ import annotations

import asyncio

from shutdown import _make_cancel_callback


def test_callback_cancels_the_task():
    async def forever() -> None:
        while True:
            await asyncio.sleep(1)

    async def run_test() -> None:
        task = asyncio.create_task(forever())
        await asyncio.sleep(0)  # let it start
        callback = _make_cancel_callback(task)
        callback()
        try:
            await task
            raised = False
        except asyncio.CancelledError:
            raised = True
        assert raised is True
        assert task.cancelled()

    asyncio.run(run_test())
