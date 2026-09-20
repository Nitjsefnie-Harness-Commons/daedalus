"""MCP test environment isolation."""
import contextlib
import os


@contextlib.contextmanager
def isolated(applied):
    """Keep shell settings out of test imports."""
    saved = {key: value for key, value in os.environ.items()
             if key.startswith('DAEDALUS_')}
    other = {key: os.environ.get(key) for key in applied
             if not key.startswith('DAEDALUS_')}
    for key in saved:
        del os.environ[key]
    try:
        os.environ.update(applied)
        yield
    finally:
        for key in applied:
            if key.startswith('DAEDALUS_') and key not in saved:
                os.environ.pop(key, None)
        for key, value in other.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.update(saved)
