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
"""
import contextlib
import io
import time
import types

import _cli_parse

from daedalus_cli import commands_content, invoke
from daedalus_cli.cli import DISPATCH

# The transport-level names a CLI handler module resolves out of its own
# namespace. `invoke` carries the same set, so a handler that delegates to
# the real `send_and_wait` reaches the same double the handler's own `api`
# would have. The list is the whole of what `commands_media` resolves out of
# `transport` that opens a socket, so naming a module whose handlers use a
# sixth one is a gap in this tuple rather than a gap in the harness.
WIRE_NAMES = ('api', 'api_delete', 'api_raw', 'ext_cmd', 'tab', 'token',
              'wait_for_result')

_ABSENT = object()


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
        # Every timeout a CALLER chose: the two a handler picks a wait for.
        # `api` is absent because its timeout is the transport's own default,
        # which no handler in this tree sets.
        self.timeouts = []
        self.issued = []

    def __call__(self, cmd_id, cmd_type, timeout=10, **fields):
        self._record({'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
                      'fields': fields, 'timeout': timeout})
        self.calls.append((cmd_id, cmd_type, fields))
        self.timeouts.append(timeout)
        return self._answer()

    def api(self, method, path, body=None, timeout=30, headers=None):
        del timeout
        # `headers` is recorded only when a caller sent one, so a plan entry
        # that declares no headers means "none were sent" and nothing else
        # has to say so. `{}` and `None` are the same request on the wire —
        # `transport._request` merges either into its own header dict.
        entry = {'via': 'api', 'method': method, 'path': path, 'body': body}
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
    """Install the wire fakes `module` and `invoke` resolve, and restore.

    Restoration is unconditional: a handler that raises still puts every
    global back, or the next test in the same process would run against
    this one's doubles.
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


def run_cli(argv, answers, module=commands_content, plan=None, **options):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    args = _cli_parse.accepted(argv)
    recorded = RecordingExtCmd(answers, plan=plan, **options)
    out = io.StringIO()
    with wired(module, recorded):
        with contextlib.redirect_stdout(out):
            DISPATCH[args.cmd](args)
    recorded.assert_plan_consumed()
    return recorded, out.getvalue()
