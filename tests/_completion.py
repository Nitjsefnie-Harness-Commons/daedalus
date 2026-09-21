"""The one call the suite runner makes per test (issue 872).

Calling an `async def` test only creates a coroutine, so a runner that
looked no further printed PASS over a body that never executed. The
runner refuses rather than awaits: it has no event loop policy of its
own, and a test that wants one says so with `asyncio.run` in a plain def.
"""
import inspect


def run_to_completion(test, tmp):
    """Call one test, refusing a shape whose body the call did not run."""
    if inspect.iscoroutinefunction(test) or inspect.isasyncgenfunction(test):
        raise AssertionError(
            'an async def test is not awaited by the runner; drive the '
            'coroutine with asyncio.run inside a plain def test')
    result = test(tmp)
    if inspect.iscoroutine(result):
        result.close()
    if inspect.isawaitable(result) or inspect.isasyncgen(result):
        raise AssertionError(
            'returned an awaitable the runner does not await: '
            f'{type(result).__name__}')
