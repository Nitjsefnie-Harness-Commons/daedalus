"""The answer types a route function returns.

A route never touches the socket: it returns a `(status, payload)` pair, a
`FileAnswer` or a `BytesAnswer`, and the handler writes it. These live apart
from the transport that writes them because the transport reads process
configuration and a route module must not: importing an answer type is how a
route would otherwise inherit that requirement.
"""
import pathlib
import typing


class FileAnswer(typing.NamedTuple):
    """A stored file to stream back, and the type to serve it as."""
    path: pathlib.Path
    mime: str


class BytesAnswer(typing.NamedTuple):
    """A body already in memory, its type, and extra response headers."""
    data: bytes
    mime: str
    headers: tuple
