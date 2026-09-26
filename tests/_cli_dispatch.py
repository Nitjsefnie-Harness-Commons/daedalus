"""One CLI subcommand, run through the real parser and the real DISPATCH.

`ext_cmd` is faked to record what the handler was asked to send, so the
command id, the wire type and the fields are pinned exactly as the
extension receives them, and the printed output is pinned exactly as an
operator reads it. Everything ahead of the socket stays real: the argv goes
through `build_parser` and the namespace goes through the dispatch table.

Handlers reach the wire in two shapes and this fakes both. Some call
`ext_cmd` and let it build the body. The rest build their own `/command`
body and pair `api` with `wait_for_result`; for those the fakes are the
module globals those names resolve to — in the module under test, and in
`invoke`, whose real `send_and_wait` is what puts that payload on the wire.
`module=` says whose globals are rebound, so one harness reaches every
`commands_*` module rather than only the one it was written for.

Faking a namespace is not the same as containing the process, and a handler
can open a socket by a path its own globals never name — `commands_media`
once did, while every faked name reported a run it had contained. So while a
module is wired the harness seals the layer below the namespace: no socket
connection can be opened at all, from any name. An unmodelled path therefore
raises a harness refusal naming the line it came from, rather than reaching a
service, and that refusal is the boundary. The name list below is a
convenience inside it, not the closure.
"""
import contextlib
import io
import os
import socket
import sys
import time
import types

import _cli_parse

from daedalus_cli import commands_content, invoke
from daedalus_cli.cli import DISPATCH

# The transport-level names a CLI handler module resolves out of its own
# namespace, faked so a call through one is recorded instead of sent.
# `invoke` carries the same set, so a handler that delegates to the real
# `send_and_wait` reaches the same double the handler's own `api` would have.
#
# This tuple is a census, not a boundary, and nothing here treats it as one:
# a module can open a socket without naming anything on it, which is what
# `commands_media`'s direct `urllib.request` call did while every name below
# reported containment. Adding a name here only makes a path convenient to
# test — the refusal that closes the rest is `_no_socket`, which fails a
# connection from ANY path while a module is wired. A name missing from this
# list is a gap in what the harness can record, never permission to dial.
WIRE_NAMES = ('api', 'api_delete', 'api_raw', 'ext_cmd', 'tab', 'token',
              'wait_for_result')

_ABSENT = object()

_HERE = os.path.abspath(__file__)
_STDLIB = os.path.dirname(os.path.abspath(os.__file__))


def _escapee():
    """Where a refused call was made: `file:line in function`.

    Everything between the refusal and the handler is the standard library —
    `http.client` to `socket`, several frames deep — so the first frame that
    is not the standard library is the line a reader needs. This file's own
    frames are skipped by path rather than by count, so the walk does not
    depend on how many helpers the refusal happens to be built from.
    """
    frame = sys._getframe(1)
    while frame is not None:
        path = os.path.abspath(frame.f_code.co_filename)
        if path != _HERE and not path.startswith(_STDLIB):
            return f'{path}:{frame.f_lineno} in {frame.f_code.co_name}'
        frame = frame.f_back
    return 'the standard library alone'


@contextlib.contextmanager
def _no_socket():
    """Refuse every outbound connection for as long as a module is wired.

    The boundary is `socket.socket.connect` rather than a socket object's
    construction, because a name bound before this ran — `from socket import
    socket` at some module's import — outlives a rebinding of `socket.socket`
    while every instance it produces still dials through the class. Connect
    is where the bytes would go, so that is where the refusal goes.

    The seal is process-wide and it is restored unconditionally, so a
    handler that raises leaves the next test in this process free to connect.
    `test_the_socket_seal_is_lifted_when_the_block_ends` is what holds that
    to the class rather than to this comment.

    The `del` below is the arm every CPython 3.13 run takes: `connect` is
    inherited from `_socket.socket` and is NOT in `socket.socket.__dict__`,
    so this module's own assignment is the only thing to take away. The
    `else` is reachable only where something patched the attribute before the
    seal — a nested `wired`, or another suite's monkeypatch — and it puts
    that value back.
    `test_the_seal_restores_a_connect_the_class_already_carried` arms that
    arm, and is what holds it to the class rather than to this comment.
    `del` is guarded because an `AttributeError` raised
    inside a `finally` replaces whatever was unwinding, and a teardown that
    hides the failure it was cleaning up after is the defect this harness
    exists to catch.
    """
    inherited = socket.socket.__dict__.get('connect', _ABSENT)

    def refuse(*args):
        del args
        covered = ', '.join(WIRE_NAMES)
        raise AssertionError(
            f'unmodelled socket call: this harness sealed '
            f'socket.socket.connect, and none of the faked transport names\n'
            f'  covered the path from {_escapee()}\n'
            f'  faked: {covered}\n'
            f'  the call is refused rather than sent, so route the handler '
            f'through a name on WIRE_NAMES or add one to it')

    socket.socket.connect = refuse
    try:
        yield
    finally:
        if inherited is _ABSENT:
            try:
                del socket.socket.connect
            except AttributeError:
                pass
        else:
            socket.socket.connect = inherited


class VirtualTime:
    """The clock a handler reads, on the test's terms rather than the wall's.

    `do_ping` renders its round trip in milliseconds, so pinning its output
    means pinning what the clock said. `readings` are taken in order and the
    last one repeats, so `clock=[1000.0, 1000.25]` reports 250ms and the
    empty default reports 0ms. Anything this class does not define is the
    real `time` module's, so a handler that formats a timestamp with
    `time.strftime` still works against it.
    """

    def __init__(self, readings=()):
        self.readings = list(readings)
        self.now = 0.0
        self.slept = []

    def time(self):
        if self.readings:
            self.now = self.readings.pop(0)
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def __getattr__(self, name):
        return getattr(time, name)


class RecordingExtCmd:
    """Records every wire call a handler makes and replays canned answers.

    The name is the original one and stays: this records `ext_cmd`, and the
    direct `api` / `wait_for_result` shape beside it. `api_raw` and
    `api_delete` are recorded too, for the media module's two call sites
    that no other handler uses. A `plan` makes the recorder strict — each
    request the handler issues must be the next one the test declared, down
    to the body field names and values, and every declared request must be
    issued. Without a plan the recorder is permissive, which is what the
    suites that predate it rely on.
    """

    def __init__(self, answers, plan=None, target_tab='', token='clitok',
                 clock=()):
        self.answers = list(answers)
        self.supplied = len(self.answers)
        self.plan = None if plan is None else [dict(e) for e in plan]
        self.target_tab = target_tab
        self.token_value = token
        self.clock = VirtualTime(clock)
        # `calls` keeps the (id, type, fields) shape the original recorder
        # published; the other three are per-shape and new.
        self.calls = []
        self.api_calls = []
        self.waits = []
        # Every timeout a CALLER chose for a WAIT: the two a handler picks
        # one for. An `api` deadline is not here — it rides in that
        # request's own plan entry, so the plan pins it in the same
        # comparison that pins the method, path and body.
        self.timeouts = []
        self.issued = []

    def __call__(self, cmd_id, cmd_type, timeout=10, **fields):
        self._record({'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
                      'fields': fields, 'timeout': timeout})
        self.calls.append((cmd_id, cmd_type, fields))
        self.timeouts.append(timeout)
        return self._answer()

    def api(self, method, path, body=None, timeout=30, headers=None):
        # `headers` is recorded only when a caller sent one, so a plan entry
        # that declares no headers means "none were sent" and nothing else
        # has to say so. `{}` and `None` are the same request on the wire —
        # `transport._request` merges either into its own header dict.
        # `timeout` is recorded unconditionally, so a plan states the
        # deadline it expects. A handler that started passing one here
        # would otherwise change how long the CLI blocks an operator and
        # not one plan in the tree would notice.
        entry = {'via': 'api', 'method': method, 'path': path, 'body': body,
                 'timeout': timeout}
        if headers:
            entry['headers'] = headers
        self._record(entry)
        self.api_calls.append((method, path, body))
        return self._answer()

    def api_raw(self, method, path):
        """`do_screenshot`'s download: the one call that wants bytes back."""
        self._record({'via': 'api_raw', 'method': method, 'path': path})
        return self._answer()

    def api_delete(self, path, body):
        """`do_uploads --delete`, the only body-carrying DELETE in the CLI."""
        self._record({'via': 'api_delete', 'path': path, 'body': body})
        return self._answer()

    def wait_for_result(self, cmd_id, target_tab, delivery_id, timeout,
                        interval=0.5):
        self._record({'via': 'wait_for_result', 'id': cmd_id,
                      'tab': target_tab, 'delivery': delivery_id,
                      'timeout': timeout, 'interval': interval})
        self.waits.append(
            (cmd_id, target_tab, delivery_id, timeout, interval))
        self.timeouts.append(timeout)
        return self._answer()

    def _record(self, entry):
        self.issued.append(entry)
        if self.plan is None:
            return
        index = len(self.issued) - 1
        if index >= len(self.plan):
            raise AssertionError(
                f'unplanned request {index + 1}: the test planned '
                f'{len(self.plan)}\n  arrived: {entry!r}\n'
                f'  planned: {self.plan!r}')
        expected = self.plan[index]
        if entry != expected:
            raise AssertionError(
                f'request {index + 1} is not the one planned\n'
                f'  arrived: {entry!r}\n  planned: {expected!r}')

    def _answer(self):
        if not self.answers:
            raise AssertionError(
                f'the handler asked for a {len(self.issued)}th answer and the '
                f'test supplied {self.supplied}')
        return self.answers.pop(0)

    def assert_plan_consumed(self):
        """Every planned request must have been issued.

        Only reachable when the handler returned: one that exits is
        reporting a refusal, and the test asserts that message instead.
        """
        if self.plan is None:
            return
        unconsumed = self.plan[len(self.issued):]
        if unconsumed:
            raise AssertionError(
                f'{len(unconsumed)} of {len(self.plan)} planned requests were '
                f'never issued; the handler made {len(self.issued)}\n'
                f'  unconsumed: {unconsumed!r}')


def _fake(name, recorded):
    if name == 'ext_cmd':
        return recorded
    if name == 'tab':
        return lambda: recorded.target_tab
    if name == 'token':
        return lambda: recorded.token_value
    return getattr(recorded, name)


@contextlib.contextmanager
def wired(module, recorded):
    """Install the wire fakes `module` and `invoke` resolve, and seal the rest.

    Restoration is unconditional: a handler that raises still puts every
    global back, or the next test in the same process would run against
    this one's doubles. The socket seal is undone with them, and it is what
    makes the fakes sufficient rather than hopeful.
    """
    targets = [module] if module is invoke else [module, invoke]
    saved = [(module, 'time', getattr(module, 'time', _ABSENT))]
    for target in targets:
        for name in WIRE_NAMES:
            saved.append((target, name, getattr(target, name, _ABSENT)))
    try:
        setattr(module, 'time', recorded.clock)
        for target in targets:
            for name in WIRE_NAMES:
                setattr(target, name, _fake(name, recorded))
        with _no_socket():
            yield module
    finally:
        for target, name, original in reversed(saved):
            if original is _ABSENT:
                delattr(target, name)
            else:
                setattr(target, name, original)


def drive(call, answers, plan=None, **options):
    """Run `call` against the fakes, the way `run_cli` dispatches a handler.

    The harness's own controls use this with a small local callable instead
    of a handler: the callable reads `api` and the rest off the namespace it
    is handed, which is how a handler reads them off its own module.
    """
    recorded = RecordingExtCmd(answers, plan=plan, **options)
    space = types.SimpleNamespace()
    with wired(space, recorded):
        call(space)
    recorded.assert_plan_consumed()
    return recorded


def _dispatch(argv, answers, module, plan, **options):
    """(recorder, stdout, exit code) for one dispatch, however it ended.

    The exit code is `None` for a handler that returned, and the plan is
    deliberately left unchecked on the arm that did not: a handler that
    exits is reporting a refusal, and the test asserts that message.
    """
    args = _cli_parse.accepted(argv)
    recorded = RecordingExtCmd(answers, plan=plan, **options)
    out = io.StringIO()
    try:
        with wired(module, recorded):
            with contextlib.redirect_stdout(out):
                DISPATCH[args.cmd](args)
    except SystemExit as exit_request:
        return recorded, out.getvalue(), exit_request.code
    return recorded, out.getvalue(), None


def run_cli(argv, answers, module=commands_content, plan=None, **options):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    recorded, out, code = _dispatch(argv, answers, module, plan, **options)
    if code is not None:
        raise SystemExit(code)
    recorded.assert_plan_consumed()
    return recorded, out


def run_cli_exit(argv, answers, module=commands_content, plan=None,
                 **options):
    """`run_cli` for an arm that ends in `sys.exit`: returns (code, stdout).

    An exit message reaches an operator on stderr, so a handler that
    printed a row and THEN exited would look exactly like one that printed
    nothing — the exit code alone cannot tell the two apart. Handing back
    the rendered half as well is what lets a test pin an exit arm's whole
    output, which is what such a test's docstring claims. A plan is still
    checked request by request as they arrive; only "every planned request
    was issued" belongs to the arm that returned.
    """
    _recorded, out, code = _dispatch(argv, answers, module, plan, **options)
    if code is None:
        raise AssertionError(
            f'{argv} returned instead of exiting; it printed {out!r}')
    return code, out
