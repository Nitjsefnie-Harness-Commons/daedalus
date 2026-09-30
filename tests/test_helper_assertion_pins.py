#!/usr/bin/env python3
"""Every assertion a shared test helper makes is observed by a case here.

The three shared fixture modules assert on their callers' behalf: the
composition-scan refusal names the site and the reason, the crediting
record reader wants exactly one record, the workflow rewriter wants a
block the workflow really carries and a rewrite that really changed
something, and the refusal reader wants a message carrying the text it
was asked for and wants `call` to have refused at all. Every consumer
passed each of those the values it wants, so deleting any of them left
every consumer green and the check was made by nothing (issue #1349).

One case per assertion, and each case states both directions of the one
helper: the call that must not raise, and the call that must. The
negative alone would be satisfied by a helper that raised on everything
and the positive alone by one that ignored the argument, so neither half
discriminates without the other.

The two helpers this suite declares are held to the same rule, which is
what issue #1349 is about one level down: `_assertion_raised`'s own two
statements are each observed by a case, and neither of its cases needs
a real fixture to do it.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import _assert_scan_refusal  # noqa: E402
from _node_harness_fixtures import (  # noqa: E402
    _bound_record, _node_harness)
from _wfgraph import _job_needs, _tests_yml  # noqa: E402
from _wffixtures import BLOCK_NEEDS, _refuses, _replaced  # noqa: E402

# The composition the closure suite plants, refusing at `composition:6`.
COMPUTED_IMPORT = (
    '\nimport importlib\n'
    '\n'
    '\n'
    'def load(name):\n'
    '    return importlib.import_module(name)\n')
SCAN_REASON = 'cannot read statically'
SCAN_SITE = 6

# A bound writes its crediting record on stderr as it settles, so a child
# running N of them writes N records and the newline ahead of the exit is
# what orders the flush after the last one.
_BOUND_DRIVER = """
(async () => {
  const work = Promise.resolve('settled');
%s
  process.stderr.write('\\n', () => process.exit(0));
})();
"""

# `suites` is absent from the first and present in the second, so the same
# reader refuses one and answers the other.
_NO_SUITES_JOB = (
    'jobs:\n  probe:\n    needs:\n    runs-on: ubuntu-latest\n')
_SUITES_JOB = (
    'jobs:\n  suites:\n    needs: [changes]\n    runs-on: ubuntu-latest\n')
_NEEDS_SPELLED = '    needs: [changes]\n'


def _assertion_raised(call, *args, mentions=None):
    """Require `call(*args)` to raise an AssertionError naming `mentions`.

    An IndexError or a TypeError is not a report: the message is what a
    reader of a failed run acts on, and each of these fixtures exists to
    produce one about the input it was handed.
    """
    try:
        call(*args)
    except AssertionError as error:
        if mentions is not None:
            assert mentions in str(error), error
        return
    raise AssertionError(f'{call.__name__} accepted what it must refuse')


def _refuses_asking_for(text):
    """`_refuses` asked for `text`, as a call taking one argument.

    The `contains` the case needs is a keyword, and a `**`-unpacked call
    is the shape the bounded-launch control reads as a launch, so the
    keyword is bound here instead of forwarded through the case.
    """
    return _refuses(_job_needs, _NO_SUITES_JOB, 'suites', contains=text)


def _raises_only(text):
    """A throwaway that raises an AssertionError carrying `text`.

    The two cases below drive `_assertion_raised` against its own two
    statements rather than against a real fixture, so each reads a
    message this run chose and neither can pass because a scan or a
    workflow happened to say something else.
    """
    def raising():
        raise AssertionError(text)
    return raising


def _never_raises():
    """A throwaway that answers normally, so nothing must come back."""
    return 'answered'


def _asks_for_a_phrase_no_message_carries():
    """`_assertion_raised` handed a phrase the raised message lacks."""
    _assertion_raised(
        _raises_only('a message naming nothing'), mentions='never printed')


def _bound_child(count, token):
    """Run a real dashboard child settling `count` bounds.

    Each bound is labelled with `token`, a value this run chose, so a
    reader that answered a constant instead of the record the child
    wrote cannot name it: the label the case asserts is not in any
    earlier run's source.
    """
    calls = ''.join(
        f"  await bounded(work, 'bound {token} {index}', 300);\n"
        for index in range(1, count + 1))
    return _dashnode.run_dashboard_node(
        _node_harness(_BOUND_DRIVER % calls, bounded_steps=count))


def _bound_label(token, index):
    """The label the `index`th bound of a `token` run settles under."""
    return f'bound {token} {index}'


def test_the_assertion_check_refuses_a_message_naming_nothing(_tmp):
    """`mentions` is this helper's own check, and this is its case.

    The naive implementation is `_assertion_raised` with the `mentions`
    branch dropped, which answers a message naming nothing exactly as it
    answers the one beside it; only this call separates them.
    """
    del _tmp
    _assertion_raised(
        _raises_only('a message naming nothing'), mentions='a message')
    _assertion_raised(
        _asks_for_a_phrase_no_message_carries,
        mentions='a message naming nothing')


def test_the_assertion_check_reports_a_call_that_raised_nothing(_tmp):
    """A call that answered is reported by name, not returned past.

    The naive implementation is `_assertion_raised` whose trailing raise
    was dropped, so it returns `None` for a call that raised nothing.
    The refusal is read here rather than through a second
    `_assertion_raised`, because that helper raises on the same input it
    is being asked about: a wrapper cannot tell the two failures apart.
    """
    del _tmp
    _assertion_raised(
        _raises_only('answered by a refusal'), mentions='answered')
    try:
        _assertion_raised(_never_raises)
    except AssertionError as refused:
        assert '_never_raises' in str(refused), refused
    else:
        raise AssertionError(
            'the assertion check returned for a call that raised nothing')


def test_the_scan_refusal_helper_names_the_site_it_was_given(_tmp):
    """A site the scan does not name is refused; the one it does is not.

    The naive implementation is a helper that kept the reason check and
    dropped the site one: it passes the control on the same line above,
    so only the wrong-site call tells the two apart.
    """
    _assert_scan_refusal(_tmp, COMPUTED_IMPORT, SCAN_SITE, SCAN_REASON)
    _assertion_raised(
        _assert_scan_refusal, _tmp, COMPUTED_IMPORT, SCAN_SITE + 1,
        SCAN_REASON, mentions=f'composition:{SCAN_SITE}')


def test_the_scan_refusal_helper_names_the_reason_it_was_given(_tmp):
    """A reason the refusal does not carry is refused; the real one is not.

    The naive implementation is a helper that kept the site check and
    dropped the reason one, rejected only by the wrong-reason call.
    """
    _assert_scan_refusal(_tmp, COMPUTED_IMPORT, SCAN_SITE, SCAN_REASON)
    _assertion_raised(
        _assert_scan_refusal, _tmp, COMPUTED_IMPORT, SCAN_SITE,
        'a reason the refusal never prints', mentions=SCAN_REASON)


def test_a_child_that_wrote_one_crediting_record_is_read(_tmp):
    """The record carries the label this run chose for the bound.

    The control the two refusals below are measured against, and the one
    that cannot be a constant: the label is a token minted per run, so
    `_bound_record` answering `{'label': 'bound 1'}` would not name it.
    """
    del _tmp
    token = uuid.uuid4().hex
    result = _bound_child(1, token)
    assert _bound_record(result)['label'] == _bound_label(token, 1), (
        result.stderr)


def test_the_bound_record_reader_refuses_a_child_that_wrote_none(_tmp):
    """A child that settled no bound has no crediting record to read.

    The naive implementation is `json.loads(records[0])` with no check,
    which answers an IndexError naming neither the child nor the stderr
    it was handed; this case refuses only the reported shape.
    """
    del _tmp
    result = _bound_child(0, uuid.uuid4().hex)
    _assertion_raised(_bound_record, result, mentions='[]')


def test_the_bound_record_reader_refuses_a_child_that_wrote_two(_tmp):
    """Two crediting records is the shape the first of them hides.

    The naive implementation is a check for at least one record: it
    refuses none of the two, and `records[0]` then reads the first
    silently, so a second settlement is spent and never reported. The
    one-record control above is what tells `== 1` from `>= 1`, and the
    token in the phrase is what tells two records from one.
    """
    del _tmp
    token = uuid.uuid4().hex
    result = _bound_child(2, token)
    _assertion_raised(
        _bound_record, result, mentions=_bound_label(token, 2))


def test_the_replaced_helper_refuses_a_block_the_workflow_lacks(tmp):
    """A block the shipped workflow does not carry cannot be rewritten.

    The naive implementation is a bare `workflow.replace(old, new, 1)`,
    which answers the workflow itself when `old` is absent, so the case
    would plant nothing and read as the unplanted control. The control is
    what refuses a `_replaced` that refused everything: without it the
    case would be satisfied by a helper that never rewrote anything.
    """
    del tmp
    assert _replaced(BLOCK_NEEDS, _NEEDS_SPELLED) != _tests_yml(), (
        'the control rewrote nothing')
    absent = '    needs:\n      - a_dependency_no_workflow_carries\n'
    _assertion_raised(
        _replaced, absent, _NEEDS_SPELLED,
        mentions='a_dependency_no_workflow_carries')


def test_the_replaced_helper_refuses_a_rewrite_that_changed_nothing(tmp):
    """`old` and `new` alike is a rewrite that rewrote nothing.

    The naive implementation is the first helper's without the second
    assertion: `old` is really present, so that check passes, and the
    case's own expectation is the only thing that can tell a planted
    defect from an unplanted workflow.
    """
    del tmp
    assert _replaced(BLOCK_NEEDS, _NEEDS_SPELLED) != _tests_yml(), (
        'the control rewrote nothing')
    _assertion_raised(_replaced, BLOCK_NEEDS, BLOCK_NEEDS)


def test_the_refuses_helper_names_the_text_it_was_asked_for(tmp):
    """A `contains` the refusal does not carry is itself a refusal.

    The naive implementation is a helper that returned the message and
    left the reader to check it, which passes the control on the first
    lines and passes every case that only reads the return value.
    """
    del tmp
    message = _refuses(_job_needs, _NO_SUITES_JOB, 'suites')
    assert message.startswith('AssertionError: '), message
    assert "'suites'" in message, message
    _assertion_raised(
        _refuses_asking_for, 'no text this refusal carries',
        mentions="'suites'")


def test_the_refuses_helper_reports_a_call_that_accepted_the_defect(tmp):
    """A reader the defect passed has no refusal message to return.

    The naive implementation is one whose `try` had no trailing raise,
    which returns `None` for a call that raised nothing; the case reads
    the returned message instead, so it sees the `None` and no failure.
    """
    del tmp
    assert "'suites'" in _refuses(
        _job_needs, _NO_SUITES_JOB, 'suites'), 'the control refused nothing'
    _assertion_raised(
        _refuses, _job_needs, _SUITES_JOB, 'suites',
        mentions='_job_needs accepted the planted defect')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
