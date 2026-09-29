"""What one MCP poll answered: the body, or the refusal that ended it.

Both poll suites assert on the answered value rather than on a raise, so
a rejection is a string naming the exception instead of a traceback that
stops the suite at the first axis it happens to reach.
"""
import asyncio


def _poll_outcome(coroutine):
    try:
        return asyncio.run(coroutine)
    except Exception as failure:  # noqa: BLE001
        return f'raised {type(failure).__name__}: {failure}'
