"""What the CLI prints, and the console it has to survive printing to.

A result may carry anything a page returned, including text the console's
encoding cannot spell, so the stdio configuration and the printer belong
together: the printer is only correct in terms of what the encoding was set
to accept.
"""
import json
import os
import sys


def _output_markers():
    """Pick the decorative markers this console can actually represent.

    A Windows console defaults to a legacy code page — cp1252 on the hosted
    runners — and `print` raises UnicodeEncodeError there instead of
    degrading, so one arrow in a header aborted the command with a traceback
    and printed nothing at all. The markers carry no information the
    surrounding words do not, so where the stream cannot encode them they
    become ASCII rather than the command becoming a crash.
    """
    fancy = {'in': '\u2190', 'out': '\u2192', 'warn': '\u26a0'}
    encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
    try:
        for glyph in fancy.values():
            glyph.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return {'in': '<-', 'out': '->', 'warn': '!'}
    return fancy


def configure_stdio():
    """Give the output an encoding both a person and a program can rely on.

    Two unrelated consumers read this. A terminal has a code page its user
    chose, and overriding it produces mojibake on the very console it was
    meant to help — so a tty keeps its encoding and the markers above fall
    back to ASCII instead of the command dying. A pipe or a file has no such
    opinion, and leaving it on the locale makes the bytes a caller receives
    depend on the machine: a program reading this output on Windows decoded
    UTF-8 bytes as cp1252 and got mojibake for every non-ASCII id. A non-tty
    stream is therefore pinned to UTF-8, which is what a consumer can
    actually depend on.

    An explicit PYTHONIOENCODING is an operator decision and is left alone
    in both cases. `errors='replace'` applies throughout, because caller
    data — a tab title, an upload name, a job id — is not ours to choose and
    must never be able to abort the command.
    """
    pinned = bool(os.environ.get('PYTHONIOENCODING'))
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            if pinned or stream.isatty():
                reconfigure(errors='replace')
            else:
                reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            continue


# Before MARK: _output_markers reads the encoding stdout ends up with,
# so choosing markers first pins ASCII fallbacks on a stream that is
# about to become UTF-8. The original module called it in this order.
configure_stdio()

MARK = _output_markers()


def validate_result(res):
    """Check result JSON has expected fields, warn on missing."""
    expected = {'id', 'result', 'error', 'ts', 'token'}
    missing = expected - set(res.keys())
    if missing:
        print(f'{MARK["warn"]} Missing fields: {", ".join(sorted(missing))}', file=sys.stderr)
    if 'id' in res and not isinstance(res['id'], str):
        print(f'{MARK["warn"]} id is {type(res["id"]).__name__}, expected str', file=sys.stderr)


def _format_eval_world(world):
    """Present the wire `world` value only as an execution channel."""
    return f'channel={world}'


def print_result(res, raw=False):
    if raw:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    validate_result(res)

    hdr = f'{MARK["in"]} {res.get("id", "?")}'
    t = res.get('tabId', '')
    world = res.get('world', '')
    ms = res.get('exec_ms')
    if t:
        hdr += f'  tab={t[:12]}'
    if world:
        hdr += f'  @{_format_eval_world(world)}'
    if isinstance(ms, (int, float)):
        hdr += f'  {ms}ms'

    err = res.get('error')
    if err:
        print(f'{hdr}  ERROR: {err}')
        sys.exit(1)

    print(hdr)
    r = res.get('result')
    if isinstance(r, (dict, list)):
        print(json.dumps(r, indent=2, ensure_ascii=False))
    elif r is not None:
        print(r)
    else:
        print('(undefined)')
