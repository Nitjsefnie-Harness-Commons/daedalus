"""The contract both log-safe implementations in this tree must satisfy.

A test-case table, not a runner helper: it exercises the `log_safe` module
the bridge and MCP entry points share, against the behavior-identical copy
`scripts/gen_gitignore.py` keeps because the repository root is not on its
import path. Their suites run this one table against both implementations,
and an MCP-suite meta-test proves a deliberately divergent standalone copy
fails it: values that must pass through in full, values that must be
backslash-escaped, and values whose rendering must hit the fixed fallback
rather than raise or escape to the caller as a non-string.
"""


def log_safe_cases():
    class BrokenStr(Exception):
        def __str__(self):
            raise RuntimeError('broken __str__')

    class EvilStr(str):
        """str() returns this subclass unchanged, so .encode() dispatches to
        it."""
        # The invalid shape is the point: handing self back so the caller's
        # .encode() runs this subclass's code outside any guard.
        def __str__(self):  # pylint: disable=invalid-str-returned
            return self

        def encode(self, *args, **kwargs):
            raise RuntimeError('evil encode')

    class BadFormat:
        """What a hostile decode() hands back: interpolating it raises."""
        def __format__(self, _spec):
            raise RuntimeError('evil format')

    class HostileChain(str):
        """str() returns this unchanged; decode() returns a non-string."""
        def __str__(self):  # pylint: disable=invalid-str-returned
            return self

        def encode(self, *args, **kwargs):
            return self

        def decode(self, *args, **kwargs):
            return BadFormat()

    large = 'x' * 200000
    return (
        (b'\xff', repr(b'\xff')),
        (None, 'None'),
        (large, large),
        (10 ** 5000, '<unprintable value>'),  # past the 4300-digit str() limit
        ('\ud800', '\\ud800'),
        ('\udc80', '\\udc80'),
        ('\udcff', '\\udcff'),
        (BrokenStr('x'), '<unprintable value>'),
        (EvilStr('x'), '<unprintable value>'),
        (HostileChain('x'), '<unprintable value>'),
    )
