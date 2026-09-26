#!/usr/bin/env python3
"""What the shared CLI harness promises, one control per strictness limb.

These are tests OF tests/_cli_dispatch.py, so each drives a small local
callable rather than a handler: a control parked in a consumer's suite
welds that consumer to every future change here, and the red then arrives
for a reason that has nothing to do with what the consumer tests. A failure
here that names a module other than this one is a control in the wrong
file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_eval  # noqa: E402
from daedalus_cli.output import MARK  # noqa: E402

drive = _cli_dispatch.drive
run_cli = _cli_dispatch.run_cli

TABS = {'via': 'api', 'method': 'GET', 'path': '/tabs', 'body': None}
COMMAND = {'via': 'api', 'method': 'PUT', 'path': '/command',
           'body': {'token': 'clitok', 'id': 'job0', 'code': '1+1'}}
STORE = {'via': 'ext_cmd', 'id': '_store_hf', 'type': 'store-hotfix',
         'fields': {'fixId': 'fx'}, 'timeout': 10}


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
    space.ext_cmd('_store_hf', 'store-hotfix', fixId='fx')


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
    assert recorded.timeouts == [10], recorded.timeouts


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
    assertion is on the wire request it actually built.
    """
    del tmp
    plan = [dict(COMMAND, body={'token': 'clitok', 'id': 'job0',
                                'code': '1+1', 'tab': 'tab7'})]
    recorded, out = run_cli(
        ['exec', 'job0', '1+1', '--no-result'], [{'target': 'tab7'}],
        module=commands_eval, plan=plan, target_tab='tab7', token='clitok')

    assert recorded.api_calls == [('PUT', '/command', plan[0]['body'])], \
        recorded.api_calls
    arrow = MARK['out']
    assert out == f'{arrow} job0 {arrow} tab7  (3 bytes)\n', repr(out)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='clidispatch_'))
