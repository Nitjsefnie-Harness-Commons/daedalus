"""Command-line client for a Daedalus browser bridge.

The wheel ships the client only. The bridge itself (`server.py`) and the Chrome
extension it drives live in the repository beside this package — they are
deployed, not pip-installed, because each one needs a host and a browser rather
than an interpreter.

The version tracks the EXTENSION's version, not this package's own history, and
`scripts/check_versions.py` enforces that they agree. A CLI whose version does
not name the extension it speaks to is worse than no version at all: the wire
format is the thing being versioned, and both ends have to claim the same one.
"""

from collections.abc import Iterable

__version__ = "0.18.2"

_REQUEST_FIELD_CARRIERS = {
    'token': 'token',
    'sig': 'sig',
    'job': 'job',
}
_REQUEST_HEADER_CARRIERS = {
    b'authorization': 'token',
    b'mcp-session-id': 'mcp-session-id',
    b'host': 'host',
    b'origin': 'origin',
}


def ambiguous_request_carrier(names: Iterable[str | bytes]) -> str | None:
    """Return the first security carrier presented more than once."""
    seen = set()
    for name in names:
        if isinstance(name, bytes):
            carrier = _REQUEST_HEADER_CARRIERS.get(name.lower())
        else:
            carrier = _REQUEST_FIELD_CARRIERS.get(name)
        if carrier is None:
            continue
        if carrier in seen:
            return carrier
        seen.add(carrier)
    return None
