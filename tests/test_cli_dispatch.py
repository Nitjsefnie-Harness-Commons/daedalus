#!/usr/bin/env python3
"""What the shared CLI harness promises, one control per strictness limb.

These are tests OF tests/_cli_dispatch.py, so most drive a small local
callable rather than a handler: a control parked in a consumer's suite
welds that consumer to every future change here, and the red then arrives
for a reason that has nothing to do with what the consumer tests. The
controls whose NAMES mention a handler are the exceptions, and each says
in its docstring why it needs a real one. That is the boundary a reader
can act on: a new control here that grows a second consumer announces
itself by its name, because which module a traceback names tells you
nothing — a failure inside one of those names `daedalus_cli/commands_media.py`
for the most ordinary reason there is.

The three media controls below share one stand-in: a real HTTP listener on
the loopback address, pointed at by the handler under test and consulted
afterwards. That is the only shape of evidence that separates "the harness
recorded this" from "nothing left the process", and loopback is the whole
of its reach — no control in this file names a service.
"""
import contextlib
import http.server
import json
import socket
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import (SEGMENT_SIG_HEADER, commands_eval,  # noqa: E402
                          commands_media)

drive = _cli_dispatch.drive
run_cli = _cli_dispatch.run_cli

TABS = {'via': 'api', 'method': 'GET', 'path': '/tabs', 'body': None}
COMMAND = {'via': 'api', 'method': 'PUT', 'path': '/command',
           'body': {'token': 'clitok', 'id': 'job0', 'code': '1+1'}}
STORE = {'via': 'ext_cmd', 'id': '_store_hf', 'type': 'store-hotfix',
         'fields': {'fixId': 'fx'}, 'timeout': 30}
SEGMENT_STATUS_PATH = '/segment-status?job=j0'
SEGMENT_STATUS = {'via': 'api', 'method': 'GET', 'path': SEGMENT_STATUS_PATH,
                  'body': None, 'headers': {SEGMENT_SIG_HEADER: 'sigvalue'}}
SCREENSHOT_PATH = '/screenshot?path=job0/shot.png'
DOWNLOAD = {'via': 'api_raw', 'method': 'GET', 'path': SCREENSHOT_PATH}
REMOVAL = {'via': 'api_delete', 'path': '/upload',
           'body': {'token': 'clitok', 'id': 'job0'}}


def _refused(fragment, call, answers, plan, **options):
    """The harness's own verdict on a call it must refuse."""
    try:
        drive(call, answers, plan, **options)
    except AssertionError as error:
        assert fragment in str(error), str(error)
        return str(error)
    raise AssertionError(
        f'the harness accepted a call it must refuse; wanted {fragment!r}')


def _ask(space):
    space.api('GET', '/tabs')


def _store(space):
    # 30, not the 10 the signature defaults to: a recorder reporting a
    # hardcoded 10 would agree with a control that never chooses a deadline.
    space.ext_cmd('_store_hf', 'store-hotfix', 30, fixId='fx')


@contextlib.contextmanager
def _stand_in(payload=None):
    """A real listener on 127.0.0.1: its URL, and what reached it.

    The answer is a well-formed one, so a control can only pass by never
    arriving: a refusal that came from a malformed response would prove
    nothing about the request. It is bound before any handler runs, which
    is also why the harness's own seal never stops it — a listener dials
    nothing, it accepts.
    """
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            received.append((self.command, self.path))
            body = json.dumps(payload if payload is not None else {}) \
                .encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer
        do_POST = _answer
        do_PUT = _answer
        do_DELETE = _answer

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def _aimed_at(module, url):
    """Point a handler module's own `URL` at a stand-in, and put it back.

    The unmodelled call reads the module global rather than the
    environment, so this is the one fake a test has to bind by hand, and
    it is undone whatever the handler does with it.
    """
    original = module.URL
    module.URL = url
    try:
        yield
    finally:
        module.URL = original


def test_a_planned_request_that_arrives_is_accepted(tmp):
    """The whole point of a plan: matching requests pass and are recorded."""
    del tmp
    recorded = drive(_ask, [{}], [TABS])

    assert recorded.api_calls == [('GET', '/tabs', None)], recorded.api_calls
    assert recorded.issued == [TABS], recorded.issued


def test_a_planned_ext_cmd_that_arrives_is_accepted(tmp):
    """The plan speaks the ext_cmd shape too, fields and timeout included."""
    del tmp
    recorded = drive(_store, [{}], [STORE])

    assert recorded.calls == [
        ('_store_hf', 'store-hotfix', {'fixId': 'fx'})], recorded.calls
    assert recorded.timeouts == [30], recorded.timeouts


def test_an_unplanned_request_is_refused(tmp):
    """One request the test never declared fails here, not at the handler."""
    del tmp
    _refused('unplanned request 1', _ask, [{}], [])


def test_a_second_unplanned_request_is_refused(tmp):
    """The count is read off the recorder, so an issue beyond the plan is
    named by its own number rather than as the first one."""
    del tmp

    def twice(space):
        space.api('GET', '/tabs')
        space.api('GET', '/tabs')

    _refused('unplanned request 2', twice, [{}, {}], [TABS])


def test_a_planned_request_whose_fields_differ_is_refused(tmp):
    """A renamed body field is a refusal, not a near miss.

    A field NAME is what the wire contract turns on, so this pins the
    equality the plan is read through rather than a key-set approximation:
    a recorder that compared the keys alone would accept the request below.
    """
    del tmp

    def renamed(space):
        space.api('PUT', '/command', {'token': 'clitok', 'id': 'job0',
                                      'source': '1+1'})

    message = _refused("'source': '1+1'", renamed, [{}], [COMMAND])
    # Both halves have to be in the refusal: a message naming only what
    # arrived leaves the reader to guess what the test had expected.
    assert 'arrived' in message, message
    assert 'planned' in message, message


def test_a_planned_request_whose_path_differs_is_refused(tmp):
    """A path built elsewhere is a different endpoint, not the same one."""
    del tmp
    _refused('request 1 is not the one planned', _ask, [{}],
             [dict(TABS, path='/result')])


def test_a_planned_request_carrying_headers_is_accepted(tmp):
    """A header is part of the request, so a plan may name one.

    `commands_media.do_segment_status` sends a job capability as a header
    and this repository keeps that capability out of the request target on
    purpose, so the header is the whole difference between a segment route
    call and any other `/segment-status` call.
    """
    del tmp

    def asks_status(space):
        space.api('GET', SEGMENT_STATUS_PATH, None, 30,
                  headers={SEGMENT_SIG_HEADER: 'sigvalue'})

    recorded = drive(asks_status, [{}], [SEGMENT_STATUS])

    assert recorded.issued == [SEGMENT_STATUS], recorded.issued


def test_a_planned_request_carrying_another_header_value_is_refused(tmp):
    """A header's VALUE is part of the request, not only its presence.

    The sibling control proves a plan may name a header and a request
    carrying it passes. This is the half that one cannot see: a recorder
    comparing the header's presence but never its value accepts the
    request below, and a plan's capability then stops being pinned at
    all. The control this replaces asked for a request carrying NO header
    against a plan naming one, which every recorder refuses identically —
    one that dropped `headers` and one that kept it — so it discriminated
    nothing this suite did not already pin.
    """
    del tmp

    def asks_status(space):
        space.api('GET', SEGMENT_STATUS_PATH, None, 30,
                  headers={SEGMENT_SIG_HEADER: 'another-sig'})

    _refused("'another-sig'", asks_status, [{}], [SEGMENT_STATUS])


def test_a_planned_api_raw_request_that_arrives_is_accepted(tmp):
    """The bytes-returning sibling is recorded, not passed through.

    `do_screenshot`'s download is the only `api_raw` call in the CLI, and a
    harness that did not fake it would write a real file from a real
    socket. What this control pins is the recorder's handling of the name
    on a namespace this file built; the handler that reaches it is
    `test_do_screenshot_downloads_through_the_recorder_and_no_socket`.
    """
    del tmp

    def downloads(space):
        space.api_raw('GET', SCREENSHOT_PATH)

    recorded = drive(downloads, [b'png-bytes'], [DOWNLOAD])

    assert recorded.issued == [DOWNLOAD], recorded.issued


def test_a_planned_api_raw_request_that_differs_is_refused(tmp):
    """Same limb, refused: one component of the selector is the request."""
    del tmp

    def downloads(space):
        space.api_raw('GET', SCREENSHOT_PATH)

    _refused('request 1 is not the one planned', downloads, [b''],
             [dict(DOWNLOAD, path='/screenshot?path=job0/other.png')])


def test_a_planned_api_delete_request_that_arrives_is_accepted(tmp):
    """The body-carrying DELETE is a third shape, and it is planned too.

    As with `api_raw` above, this is the recorder's limb on a namespace
    this file built; `test_do_uploads_delete_reaches_no_socket` is the
    same name arriving from a real handler.
    """
    del tmp

    def removes(space):
        space.api_delete('/upload', {'token': 'clitok', 'id': 'job0'})

    recorded = drive(removes, [{}], [REMOVAL])

    assert recorded.issued == [REMOVAL], recorded.issued


def test_a_planned_api_delete_request_whose_body_differs_is_refused(tmp):
    """A DELETE that names another id is another namespace."""
    del tmp

    def removes(space):
        space.api_delete('/upload', {'token': 'clitok', 'id': 'job0'})

    _refused("'id': 'job0'", removes, [{}],
             [dict(REMOVAL, body={'token': 'clitok', 'id': 'job9'})])


def test_run_cli_refuses_a_plan_the_handler_never_fully_issued(tmp):
    """The same check `drive` carries, on the path every consumer takes.

    The only control for `assert_plan_consumed` drove `drive`, and no
    consumer goes through `drive`. A `--no-result` navigation that issued
    its command and then stopped is the shape this catches, so the plan
    declares the wait it never made.
    """
    del tmp
    body = {'token': 'clitok', 'id': '_nav',
            'code': 'location.href = "https://example.com/"', 'tab': 'tab0'}
    plan = [{'via': 'api', 'method': 'PUT', 'path': '/command',
             'body': body},
            {'via': 'wait_for_result', 'id': '_nav', 'tab': 'tab0',
             'delivery': 'd0', 'timeout': 15, 'interval': 0.5}]
    try:
        run_cli(['navigate', 'https://example.com/'], [{'target': 'tab0'}],
                module=commands_eval, plan=plan, target_tab='tab0',
                token='clitok')
    except AssertionError as error:
        assert 'planned requests were never issued' in str(error), str(error)
    else:
        raise AssertionError('run_cli must refuse a plan it never consumed')


def test_a_plan_entry_the_caller_never_issues_is_refused(tmp):
    """A plan that over-declares fails: the silent short count is the defect.

    Nothing about a handler that quietly stops after one request would
    otherwise reach a test — its own output looks right and its assertions
    on the first request pass.
    """
    del tmp
    _refused('planned requests were never issued',
             _ask, [{}], [TABS, dict(TABS, path='/result')])


def test_the_fakes_are_put_back_when_the_callable_raises(tmp):
    """Restoration is unconditional, or the next test runs against these."""
    del tmp
    invoke = _cli_dispatch.invoke
    snapshot = {name: getattr(invoke, name, None)
                for name in _cli_dispatch.WIRE_NAMES}

    def raises(space):
        space.api('GET', '/tabs')
        raise ValueError('the handler blew up mid-command')

    try:
        drive(raises, [{}])
    except ValueError:
        pass
    else:
        raise AssertionError('the callable must be free to raise')

    assert {name: getattr(invoke, name, None)
            for name in _cli_dispatch.WIRE_NAMES} == snapshot


def test_wiring_restores_a_name_the_namespace_never_had(tmp):
    """A name the module lacked is REMOVED again, not left behind as a value.

    The snapshot control above can only see a name that was there before:
    `invoke` resolves all of them, so restoring it by writing the old
    value back and restoring it by deleting it look identical. Every
    handler module does not — `commands_eval` has no `ext_cmd` global of
    its own — and a restore that wrote a sentinel there instead of
    deleting it left `hasattr(module, 'ext_cmd')` true for every test
    after it, pointing at an object nothing can call. A namespace that
    starts out missing the names is what makes that branch reachable.
    """
    del tmp
    space = types.SimpleNamespace()
    recorded = _cli_dispatch.RecordingExtCmd([{}])

    with _cli_dispatch.wired(space, recorded):
        assert space.ext_cmd is recorded, 'the fakes must be installed'

    for name in _cli_dispatch.WIRE_NAMES:
        assert not hasattr(space, name), (
            f'{name} was left on a namespace that never had it')


def test_the_socket_seal_is_lifted_when_the_block_ends(tmp):
    """The seal must not outlive the block that put it up.

    Replacing the whole restore with `socket.socket.connect = refuse` — a
    harness that seals and never unseals — leaves every consumer suite
    green, because the damage is not a wrong value but a process-wide
    refusal: nothing here needs a real socket afterwards, and the suites
    that do are subprocess-driven and outside the harness entirely. The
    failure it hides is order-dependent and silent, so it is pinned here
    against the object that was there before, on the arm that returns and
    on the arm that raises.
    """
    del tmp
    original = socket.socket.connect

    with _cli_dispatch.wired(types.SimpleNamespace(),
                             _cli_dispatch.RecordingExtCmd([])):
        assert socket.socket.connect is not original, (
            'the seal must be up while a module is wired')

    assert socket.socket.connect is original, (
        'the seal outlived the block that put it up: '
        f'{socket.socket.connect}')

    def raises(space):
        del space
        raise ValueError('the handler blew up mid-command')

    try:
        drive(raises, [])
    except ValueError:
        pass
    else:
        raise AssertionError('the callable must be free to raise')

    assert socket.socket.connect is original, (
        'the seal outlived a handler that raised: '
        f'{socket.socket.connect}')


def test_asking_for_more_answers_than_were_supplied_fails_cleanly(tmp):
    """An exhausted answer queue is an assertion, not an IndexError.

    The permissive recorder raised IndexError, which a runner reports as an
    ERROR in the test that happened to ask — a fixture's own arithmetic
    error dressed as a handler failure.
    """
    del tmp
    _refused('the test supplied 0', _ask, [], None)


def test_a_passed_timeout_is_recorded(tmp):
    """The timeout is the only thing a caller can change about a send, and
    the recorder used to throw it away, so nothing could assert on it."""
    del tmp

    def waited(space):
        space.wait_for_result('_ping', 'tab0', 'd1', 10, interval=0.3)

    recorded = drive(
        waited, [{}],
        [{'via': 'wait_for_result', 'id': '_ping', 'tab': 'tab0',
          'delivery': 'd1', 'timeout': 10, 'interval': 0.3}])

    assert recorded.timeouts == [10], recorded.timeouts
    assert recorded.waits == [('_ping', 'tab0', 'd1', 10, 0.3)], \
        recorded.waits


def test_the_clock_hands_out_its_readings_in_order(tmp):
    """The clock is part of this harness, so its own contract is pinned here.

    `do_ping`'s rendered `(250ms)` is a difference between two of these
    readings, so the order matters as much as the values: a clock that
    handed them out backwards would leave that suite green. The last
    reading repeats rather than falling off the end, and the empty default
    starts at zero — the `(0ms)` arm.
    """
    del tmp
    seen = []

    def reads(space):
        seen.append(space.time.time())
        seen.append(space.time.time())
        seen.append(space.time.time())

    drive(reads, [], clock=[1000.0, 1000.25])
    assert seen == [1000.0, 1000.25, 1000.25], seen

    seen.clear()
    drive(reads, [], clock=())
    assert seen == [0.0, 0.0, 0.0], seen


def test_the_clock_delegates_an_undefined_name_to_the_real_time(tmp):
    """`__getattr__` is what the two hotfix listing callers rest on.

    `commands_content.do_list_hotfixes` and `commands_media.do_uploads`
    format a timestamp with `time.strftime` and `time.localtime` through
    this object, and neither call is faked. A clock without the delegation
    raises AttributeError on both.
    """
    del tmp
    seen = []

    def formats(space):
        seen.append(space.time.localtime is time.localtime)
        seen.append(space.time.strftime is time.strftime)

    drive(formats, [])

    assert seen == [True, True], seen


def test_the_target_tab_and_token_are_the_tests_own(tmp):
    """A handler reads both from its module globals, so both must be the
    test's to set — a wire body carrying a real credential is a leak."""
    del tmp

    def reads_both(space):
        body = {'tab': space.tab(), 'token': space.token()}
        space.api('PUT', '/command', body)

    recorded = drive(reads_both, [{}], [{'via': 'api', 'method': 'PUT',
                                         'path': '/command',
                                         'body': {'tab': 'tab7',
                                                  'token': 'clitok'}}],
                     target_tab='tab7', token='clitok')

    assert recorded.api_calls == [('PUT', '/command',
                                   {'tab': 'tab7', 'token': 'clitok'})], \
        recorded.api_calls


def test_run_cli_rebinds_the_module_it_is_given(tmp):
    """`module=` is what makes the harness general rather than eval's own.

    Without it the fakes land on commands_content and every other handler
    runs against a live socket, which is the hardwiring this parameter
    exists to remove. A real handler of that module is driven here, so the
    assertion is on the wire request it actually built — deliberately not
    on what it printed. This control asks `run_cli` a question about the
    harness; an assertion on `invoke.send_and_wait`'s own `(3 bytes)`
    rendering would turn a change to that sentence into a red here, which
    is the coupling this suite exists to prevent.
    """
    del tmp
    plan = [dict(COMMAND, body={'token': 'clitok', 'id': 'job0',
                                'code': '1+1', 'tab': 'tab7'})]
    recorded, _out = run_cli(
        ['exec', 'job0', '1+1', '--no-result'], [{'target': 'tab7'}],
        module=commands_eval, plan=plan, target_tab='tab7', token='clitok')

    assert recorded.api_calls == [('PUT', '/command', plan[0]['body'])], \
        recorded.api_calls


def test_a_socket_the_harness_does_not_model_is_refused(tmp):
    """The boundary is the process, not the list of names it fakes.

    This callable holds no faked name and reaches the network by a path
    `WIRE_NAMES` never mentions, so the refusal below is the seal's alone
    and not an artifact of the fixture. A harness that only faked names
    would have connected: that is exactly what a real handler's own
    `urllib.request` call did while every faked name reported a contained
    run. The stand-in is on the loopback address and answers whatever
    arrives, so the assertion below is about bytes that never came.
    """
    del tmp
    with _stand_in({'sig': 'sigvalue'}) as (url, received):
        address = url.partition('://')[2]
        try:
            drive(lambda space: socket.create_connection(
                (address.partition(':')[0], int(address.partition(':')[2]))),
                [])
        except AssertionError as error:
            assert 'unmodelled socket call' in str(error), str(error)
        else:
            raise AssertionError('the harness let a connection through')

    assert received == [], received


def test_do_uploads_delete_reaches_no_socket(tmp):
    """A real media handler's own request, and a stand-in that heard none.

    The `api_delete` control above drives a namespace this file built, so
    it proves the recorder records the name and nothing about whether a
    handler's call arrives here. This drives `commands_media.do_uploads`
    through the real parser and the real dispatch, and the plan is the
    request that handler builds — same path, same body — so dropping
    either `api_delete` or `api` from `WIRE_NAMES` now dies against a
    handler rather than against a fixture.
    """
    del tmp
    with _stand_in() as (url, received):
        with _aimed_at(commands_media, url):
            recorded, out = run_cli(
                ['uploads', '--delete', '--id', 'job0'], [{}],
                module=commands_media, plan=[REMOVAL], token='clitok')

    assert recorded.issued == [REMOVAL], recorded.issued
    assert out == 'Deleted\n', repr(out)
    assert received == [], received


def test_do_screenshot_downloads_through_the_recorder_and_no_socket(tmp):
    """The same for `api_raw`, on the handler whose download writes a file.

    Three requests in the plan and the bytes come back through the
    recorder, so the file on disk is the recorder's answer and not a
    socket's — a harness that let this one through would write whatever
    the stand-in returned, and `stand-in received []` is what says it did
    not.
    """
    path = Path(tmp) / 'shot.png'
    # The selector is percent-encoded on its way into the request target,
    # which the synthetic `DOWNLOAD` entry above does not model.
    download = dict(DOWNLOAD, path='/screenshot?path=job0%2Fshot.png')
    plan = [
        {'via': 'api', 'method': 'PUT', 'path': '/command',
         'body': {'id': '_ss', 'type': 'screenshot', 'token': 'clitok',
                  'tab': 'extension'}},
        {'via': 'wait_for_result', 'id': '_ss', 'tab': 'extension',
         'delivery': 'd1', 'timeout': 15, 'interval': 0.5},
        download,
    ]
    with _stand_in() as (url, received):
        with _aimed_at(commands_media, url):
            recorded, _out = run_cli(
                ['screenshot', '-o', str(path)],
                [{'target': 'tab7', 'did': 'd1'},
                 {'result': {'path': 'job0/shot.png', 'size': 9}},
                 b'png-bytes'],
                module=commands_media, plan=plan, token='clitok')

    assert recorded.issued == plan, recorded.issued
    assert path.read_bytes() == b'png-bytes'
    assert received == [], received


def test_do_segment_status_is_refused_before_it_dials(tmp):
    """The unmodelled path, driven by the handler that owns it.

    `commands_media.do_segment_status` mints its capability with a direct
    `urllib.request.urlopen` and then reads the status back through the
    faked `api`. Before the seal, the first request went to a listener
    while the recorder reported one request and a fully consumed plan, so
    the harness claimed a containment it had not achieved. Here the
    stand-in answers a well-formed capability at the URL the handler is
    aimed at, so the control can only pass by the call never arriving —
    and the refusal names the handler line that made it.
    """
    del tmp
    with _stand_in({'sig': 'sigvalue'}) as (url, received):
        with _aimed_at(commands_media, url):
            try:
                # The one answer is the status the faked `api` would have
                # returned; the refused path never reaches it, so a harness
                # that DID reach the stand-in runs to the end of the
                # handler and the refusal below is the only failure.
                run_cli(['segment-status', 'j0'],
                        [{'count': 2, 'done': [0, 1]}],
                        module=commands_media)
            except AssertionError as error:
                assert 'unmodelled socket call' in str(error), str(error)
                assert 'commands_media.py' in str(error), str(error)
            else:
                raise AssertionError(
                    f'the handler reached the network: {received}')

    assert received == [], received


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='clidispatch_'))
