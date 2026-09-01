#!/usr/bin/env python3
"""The request transport mixin and the answer convention.

`daedalus_bridge/http_transport.py` carries the request parsing,
authentication, body limits and response writing that used to sit on
`server.py`'s Handler, plus the `answer` convention every route module
returns into. The mixin is exercised here through a stub subclass that
records what was sent instead of holding a socket, so the rules are pinned
in-process rather than only end to end through a bridge child.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _noglibc  # noqa: E402
import _util  # noqa: E402

# The module reads only the byte limits out of daedalus_bridge.config, which
# still refuses to import without these two. Nothing is written under the
# root, so the system temp directory serves and no fixture tree is left behind.
os.environ.setdefault('DAEDALUS_DIR', tempfile.gettempdir())
os.environ.setdefault('DAEDALUS_PORT', '0')

transport = _util.load(
    _util.ROOT / 'daedalus_bridge' / 'http_transport.py',
    'fixture_http_transport')


class _Headers:
    """The header view a BaseHTTPRequestHandler exposes, repeats included."""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def get(self, name, default=None):
        for key, value in self._pairs:
            if key.lower() == name.lower():
                return value
        return default

    def keys(self):
        return [key for key, _value in self._pairs]


class _Connection:
    def __init__(self):
        self.timeouts = []
        self._timeout = 60

    def gettimeout(self):
        return self._timeout

    def settimeout(self, value):
        self.timeouts.append(value)
        self._timeout = value


class _Stub(transport.RequestMixin):
    """A RequestMixin that records its response instead of writing a socket."""

    def __init__(self, headers=(), body=b'', path='/'):
        self.headers = _Headers(headers)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.connection = _Connection()
        self.path = path
        self.status = None
        self.headers_sent = {}
        self.ended = False

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.headers_sent[name] = value

    def end_headers(self):
        self.ended = True


def test_answer_writes_a_json_pair_as_a_json_response(_tmp):
    stub = _Stub()
    stub.answer((418, {'error': 'teapot'}))
    assert stub.status == 418, stub.status
    assert json.loads(stub.wfile.getvalue()) == {'error': 'teapot'}
    assert stub.headers_sent['Content-Type'] == 'application/json'


def test_answer_streams_a_file_answer_with_its_mime(tmp):
    path = Path(tmp) / 'shot.png'
    path.write_bytes(b'\x89PNG' * 1000)
    stub = _Stub()
    stub.answer(transport.FileAnswer(path, 'image/png'))
    assert stub.status == 200, stub.status
    assert stub.headers_sent['Content-Type'] == 'image/png'
    assert stub.headers_sent['Content-Length'] == str(path.stat().st_size)
    assert stub.wfile.getvalue() == path.read_bytes()


def test_answer_writes_a_bytes_answer_with_extra_headers(_tmp):
    stub = _Stub()
    stub.answer(transport.BytesAnswer(
        b'<html>', 'text/html; charset=utf-8',
        (('X-Frame-Options', 'DENY'),)))
    assert stub.status == 200, stub.status
    assert stub.headers_sent['Content-Type'] == 'text/html; charset=utf-8'
    assert stub.headers_sent['Content-Length'] == '6', stub.headers_sent
    assert stub.headers_sent['Cache-Control'] == 'no-cache'
    assert stub.headers_sent['X-Frame-Options'] == 'DENY'
    assert stub.wfile.getvalue() == b'<html>'


def test_answer_refuses_a_value_that_is_not_an_answer(_tmp):
    """A route that forgot to answer is a programming error, not a 200.

    A bare payload is the shape a route is likeliest to return by mistake,
    so it is refused for the same reason None is: neither says a status,
    and inventing 200 for one would serve an error body as a success.
    """
    for wrong in (None, {'error': 'teapot'}):
        stub = _Stub()
        try:
            stub.answer(wrong)
        except TypeError:
            assert stub.status is None, (wrong, stub.status)
            assert stub.wfile.getvalue() == b'', (wrong, stub.wfile.getvalue())
            continue
        raise AssertionError(f'answer({wrong!r}) was accepted')


def test_declared_body_length_rules_survive_the_move(_tmp):
    absent = _Stub()
    assert absent._declared_body_length() is None
    assert absent.status == 411, absent.status

    unparsable = _Stub(headers=[('Content-Length', 'nine')])
    assert unparsable._declared_body_length() is None
    assert unparsable.status == 400, unparsable.status

    negative = _Stub(headers=[('Content-Length', '-1')])
    assert negative._declared_body_length() is None
    assert negative.status == 400, negative.status

    oversized = _Stub(
        headers=[('Content-Length', str(transport.MAX_BODY_SIZE + 1))])
    assert oversized._declared_body_length() is None
    assert oversized.status == 413, oversized.status

    declared = _Stub(headers=[('Content-Length', '4')], body=b'abcd')
    assert declared._declared_body_length() == 4


def test_bridge_token_refuses_a_header_and_query_that_disagree(_tmp):
    stub = _Stub(headers=[('Authorization', 'Bearer headertoken')])
    assert stub._bridge_token({'token': ['querytoken']}) is None
    assert stub.status == 400, stub.status
    assert json.loads(stub.wfile.getvalue()) == {'error': 'conflicting token'}


def test_json_nests_deeper_than_counts_structure_outside_strings(_tmp):
    assert transport.json_nests_deeper_than(b'[[[1]]]', 2)
    assert not transport.json_nests_deeper_than(b'[[1]]', 2)
    # A brace inside a string literal opens nothing, and an escaped quote
    # does not end the literal it sits in.
    assert not transport.json_nests_deeper_than(b'{"a": "[[[[["}', 2)
    assert not transport.json_nests_deeper_than(b'{"a": "\\"[[[["}', 2)


def test_json_object_remembers_a_repeated_authority_carrier(_tmp):
    once = transport.JSONObject([('token', 'a'), ('id', 'x')])
    twice = transport.JSONObject([('token', 'a'), ('token', 'a')])
    assert once.duplicate_carrier is None, once.duplicate_carrier
    assert twice.duplicate_carrier == 'token', twice.duplicate_carrier
    assert once != twice


def test_the_answer_types_import_without_daedalus_configuration(_tmp):
    """The answer types are stdlib-only so a route module stays config-free.

    `http_transport` re-exports them and does read config; a route module
    imports them from `route_answer` and must not inherit that requirement.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    done = subprocess.run(
        [sys.executable, '-c',
         'from daedalus_bridge.route_answer import BytesAnswer, FileAnswer'],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_importing_the_transport_prints_nothing(tmp):
    """The tuning diagnostic is startup output, not import output.

    Importing a module is not a moment anything is entitled to write to
    stdout: a caller that imports the transport to read one constant gets
    the line too, and a tool parsing its own stdout gets it in the middle
    of the answer. The message survives as a value the entry point prints.
    """
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [_noglibc.no_glibc_pythonpath(tmp), str(_util.ROOT)])
    program = ('import daedalus_bridge.http_transport as t\n'
               'import sys\n'
               'sys.stderr.write(repr(t.malloc_tuning_note()))\n')
    done = subprocess.run(
        [sys.executable, '-c', program],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout == '', done.stdout
    assert 'malloc tuning unavailable' in done.stderr, done.stderr


def test_the_transport_re_exports_the_answer_types(_tmp):
    """`server.py` and Task 1's controls import them from here."""
    from daedalus_bridge import route_answer  # noqa: PLC0415
    assert transport.FileAnswer is route_answer.FileAnswer
    assert transport.BytesAnswer is route_answer.BytesAnswer


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='httptransport_')


if __name__ == '__main__':
    raise SystemExit(main())
