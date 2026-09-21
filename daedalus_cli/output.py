"""Keep result printing with the encoding policy that makes it safe."""
import json
import os
import sys

from .result_view import public_result


def _output_markers():
    """Decorative markers must not abort output on legacy console encodings."""
    fancy = {'in': '\u2190', 'out': '\u2192', 'warn': '\u26a0'}
    encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
    try:
        for glyph in fancy.values():
            glyph.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return {'in': '<-', 'out': '->', 'warn': '!'}
    return fancy


def configure_stdio():
    """Preserve terminal encodings; give pipe/file consumers predictable UTF-8.

    PYTHONIOENCODING is an explicit operator choice and takes precedence.
    Replacement errors prevent unencodable caller data from aborting output.
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


# Choose markers after configuration to avoid ASCII fallbacks on UTF-8 pipes.
configure_stdio()

MARK = _output_markers()


def validate_result(res):
    expected = {'id', 'result', 'error', 'ts'}
    missing = expected - set(res.keys())
    if missing:
        print(f'{MARK["warn"]} Missing fields: {", ".join(sorted(missing))}',
              file=sys.stderr)
    if 'id' in res and not isinstance(res['id'], str):
        print(f'{MARK["warn"]} id is {type(res["id"]).__name__}, expected str',
              file=sys.stderr)


def _format_eval_world(world):
    """Present the wire `world` value only as an execution channel."""
    return f'channel={world}'


def print_result(res, raw=False):
    res = public_result(res)
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
        # stdout carries only result data; the diagnostic is stderr's.
        print(f'{hdr}  ERROR: {err}', file=sys.stderr)
        sys.exit(1)

    print(hdr)
    r = res.get('result')
    if isinstance(r, (dict, list)):
        print(json.dumps(r, indent=2, ensure_ascii=False))
    elif r is not None:
        print(r)
    else:
        print('(undefined)')
