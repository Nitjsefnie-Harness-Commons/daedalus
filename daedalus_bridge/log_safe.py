"""Render untrusted values without turning diagnostics into failures.

This module is deliberately independent of Daedalus configuration and other
repository modules, so every entry point can import the renderer safely.
"""


def log_safe(value):
    """Render a caller-supplied value safe for a diagnostic stream.

    json.loads accepts lone surrogates (U+D800..U+DFFF), and a filesystem
    name containing an undecodable byte arrives as U+DC80..U+DCFF through
    surrogateescape. Interpolation passes either through unchanged, so a
    strict stdout or stderr encode can raise UnicodeEncodeError, kill a
    request thread, or tear down a live stream. Encoding through
    backslashreplace escapes those values for safe output.

    Every step is guarded because str() can raise on a conversion-limited
    huge int or an object whose __str__ fails, while a str subclass can reach
    encode() carrying an implementation that raises or a decode() that
    returns a non-string. The result leaves only when its type is exactly
    str, never a subclass, so caller interpolation cannot invoke a
    caller-controlled __format__. The fallback is fixed ASCII and never
    interpolates the object that just failed. except Exception is deliberate:
    KeyboardInterrupt and SystemExit still propagate.
    """
    try:
        rendered = (str(value).encode('utf-8', 'backslashreplace')
                    .decode('utf-8'))
    except Exception:
        return '<unprintable value>'
    # Exact type, not isinstance: a str subclass is itself the hostile shape.
    if type(rendered) is not str:  # pylint: disable=unidiomatic-typecheck
        return '<unprintable value>'
    return rendered
