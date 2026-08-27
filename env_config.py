"""Environment-free HTTP constants and parsers shared by both front ends.

The parsers live in this environment-free module so the MCP front end can
share them without importing server.py. Importing server.py requires its
environment, runs module-level configuration, and imports mcp_server at
startup; this module reads no environment until a parser is called.
"""
import math
import os


# Refused request bodies are discarded only far enough to keep an ordinary
# refusal from becoming a connection reset. Both HTTP front ends use this
# environment-free leaf module, so one bound governs both without either
# importing the other's startup path.
REFUSED_BODY_DRAIN = 65536


def env_int(name, default, minimum, maximum=None):
    """Read one integer setting and stop startup with a specific error.

    Bare int() used to let a malformed value reach the caller as an import-time
    ValueError traceback, and let a negative body size be accepted, which made
    every non-negative Content-Length exceed the configured maximum and
    refused every request the front end received.
    """
    raw = os.environ.get(name, str(default))
    requirement = (
        f'an integer from {minimum} to {maximum}' if maximum is not None
        else 'a non-negative integer')
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f'{name} must be {requirement}; got {raw!r}') from None
    if value < minimum or (maximum is not None and value > maximum):
        raise SystemExit(f'{name} must be {requirement}; got {raw!r}')
    return value


def env_positive_float(name, default):
    """Read one finite positive floating-point setting or stop startup."""
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(
            f'{name} must be a finite positive number; got {raw!r}') from None
    if not math.isfinite(value) or value <= 0:
        raise SystemExit(
            f'{name} must be a finite positive number; got {raw!r}')
    return value
