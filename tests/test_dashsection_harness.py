#!/usr/bin/env python3
"""What the section harness guarantees, proven by breaking it.

The shell is a guard, so a green run on the tree it was written against
proves nothing. Each case drives the real `dashboard/api.js` or the shell
itself through a Node child and reads the report it printed.

None of this asserts how a section behaves. It asserts that a scenario
can see what it drove: a selector that answers the same element twice, a
sibling walk that ends, an insertion that agrees with it, a class set that
agrees with `className` in both directions, a timer that stays parked
until it is fired, a refusal recorded as well as thrown, an envelope the
fake cannot pass off as somebody else's, a poll loop that retries rather
than settles for the first answer, and a bus that dispatches the way the
shipped one does.

The scenario sources live in `tests/_dashsection_controls.py`. They grew
under the assertions while the assertions are what this file is for, and
a suite a reviewer mutates does not belong twelve lines under a ceiling.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashsection  # noqa: E402
import _dashsection_controls as scenarios  # noqa: E402
import _util  # noqa: E402
from _dashnode import run_dashboard_node  # noqa: E402
from _dashsection import build_harness, run_scenario, section_path  # noqa


def _legs(report):
    """The command, the poll and the consume legs a report recorded.

    Split by the target, not by position: the consume leg is the poll
    target plus two parameters, so `'consume' in target` separates it, and
    a `/command` row is neither. Counting the wrong set is how an
    assertion ends up proving nothing.
    """
    rows = report['requests']
    command = [r for r in rows if r['target'].endswith('/command')]
    polls = [r for r in rows
             if r['target'].startswith('/result')
             and 'consume' not in r['target']]
    consumed = [r for r in rows if 'consume' in r['target']]
    return command, polls, consumed


def _phases(scenario, sections=()):
    """The diagnostic checkpoint labels one scenario run recorded."""
    result = run_dashboard_node(build_harness(scenario, sections=sections))
    return re.findall(r'^\[phase\] (.+)$', result.stderr, re.MULTILINE)


def test_a_registered_selector_answers_the_same_element_twice(_tmp):
    """`block-rules`, `cookies`, `fetch-timings` and `net-capture` all
    write `document.querySelector('#sNN [data-sub]').textContent` with no
    null guard, and a double that mints a fresh element per call throws
    that write away. Returning a throwaway leaves `observed` empty."""
    report = run_scenario(scenarios.SELECTORS)
    assert report['same'] is True, report
    assert report['isSub'] is True, report
    assert report['observed'] == '1 active', report
    assert report['refusal'] is not None, report
    assert 'unmodeled selector' in report['refusal'], report
    assert '#s04 [data-sub]' in report['refusal'], report


def test_an_unregistered_selector_is_refused_by_name(_tmp):
    """The refusal names the selector, because a silent `null` turns every
    happy-path assertion in a section suite into an error-pane assertion
    that reads as the section's behaviour."""
    report = run_scenario(scenarios.SELECTORS)
    assert report['refusal'] == 'unmodeled selector #s04 [data-sub]', report


def test_next_sibling_walks_and_ends(_tmp):
    """`net-capture` toggles a detail row through `tr.nextSibling`, so a
    walk that answers the first child forever, or `undefined` past the
    end rather than `null`, is a row that never collapses."""
    report = run_scenario(scenarios.SIBLINGS)
    assert report['first'] is True, report
    assert report['second'] is True, report
    assert report['end'] is True, report
    assert report['endKind'] == 'null', report
    assert report['orphan'] is True, report
    assert report['orphanKind'] == 'null', report
    assert report['parentNode'] is True, report
    assert report['depth'] == 0, report


def test_insert_before_agrees_with_the_sibling_walk(_tmp):
    """`net-capture` inserts with `tr.parentNode.insertBefore(detail,
    tr.nextSibling)`, so the insertion has to land at the reference's own
    index in the parent that owns the row, and the walk has to agree
    afterwards. Appending instead leaves `index` at 2."""
    report = run_scenario(scenarios.INSERT)
    assert report['returned'] is True, report
    assert report['index'] == 1, report
    assert report['afterFirst'] is True, report
    assert report['afterDetail'] is True, report
    assert report['afterLast'] is True, report
    assert report['identity'] is True, report
    assert report['size'] == 3, report


def test_the_class_set_and_class_name_agree_in_both_directions(_tmp):
    """`armedAction` adds and removes `.armed` while `_util.h()` writes
    `className` as a whole string, so a set that does not follow the
    string leaves `rewritten.has` true and a control that only ever adds
    passes against a set that never removes."""
    report = run_scenario(scenarios.CLASSES)
    assert report['added'] == {'value': 'meta-v dim armed',
                               'has': True}, report
    assert report['rewritten'] == {'value': 'meta-v', 'has': False}, report
    assert report['value'] == 'meta-v', report
    assert report['has'] is False, report
    assert report['length'] == 1, report


def test_a_parked_timer_runs_only_when_fired(_tmp):
    """The armed control in the shape the section uses. A clock that ran
    the callback as it was scheduled disarms the button inside the first
    click, so `confirmed.ran` would stay 0; a clock that never fires it
    leaves `reverted.text` at the confirm label."""
    report = run_scenario(scenarios.CLOCK, sections=('sections/_util.js',))
    assert report['armed']['ran'] == 0, report
    assert report['armed']['text'] == 'sure?', report
    assert report['armed']['has'] is True, report
    assert len(report['armed']['live']) == 1, report
    # clearTimeout cancelled the second timer, so the id it returned is
    # gone rather than waiting to be fired into the assertions below.
    assert report['afterClear']['id'] not in report['armed']['live'], report
    assert report['afterClear']['live'] == report['armed']['live'], report
    assert report['reverted'] == {'ran': 0, 'text': 'delete', 'has': False}, \
        report
    assert len(report['rearmed']) == 1, report
    assert report['confirmed']['ran'] == 1, report
    assert report['confirmed']['live'] == [], report


def test_inner_html_yields_a_child_and_refuses_what_it_cannot_parse(_tmp):
    """The three list hosts reset to one bare `<div class="...">` and read
    it back, so the parse has to produce an observable child. A shape it
    does not model is refused by name and leaves the element alone, rather
    than emptying a host the section is about to render into."""
    report = run_scenario(scenarios.INNER_HTML)
    assert report['parsed'] == {'tag': 'div', 'text': 'loading',
                                'value': 'dim italic small', 'size': 1}, report
    assert report['refusal'] is not None, report
    assert 'does not model an innerHTML assignment of' in report['refusal'], \
        report
    assert '<span>two</span>' in report['refusal'], report
    assert report['untouched'] == 1, report
    assert report['stillThere'] is True, report


def test_an_unplanned_request_is_refused_and_recorded(_tmp):
    """Both halves, per the #1083 wording: the target the scenario never
    declared is on the record AND the request is refused. A double that
    answers 200 for everything unrecognised leaves `unplanned` empty and
    `refusal` null while the section reads the refusal as a result.

    The recorded body is compared as an object, not searched as a string:
    a section suite asserts `'tabId' not in command`, and on a raw string
    that is a substring match a field named `xTabId` would satisfy. A
    double that records the string and parses it separately for the
    ledger leaves every other case in this suite green, so the comparison
    here is what holds the shape."""
    report = run_scenario(scenarios.UNPLANNED, sections=('api.js',))
    assert report['unplanned'] == [{'n': 1, 'target': '/command'}], report
    request = report['requests'][0]
    assert request['target'] == '/command', report
    assert request['method'] == 'PUT', report
    assert request['authorization'] == 'Bearer ' + scenarios.TOKEN, report
    body = request['body']
    assert isinstance(body, dict), (
        'the recorded body is not the parsed object', report)
    assert body['type'] == 'list-block-rules', report
    assert body['token'] == scenarios.TOKEN, report
    assert body['tab'] == 'extension', report
    # The command id is minted per run, so the key SET is what pins it:
    # a body with a key the section never sent, or missing one it did.
    assert sorted(body) == ['id', 'tab', 'token', 'type'], report
    assert report['refusal'] == 'unexpected request /command', report


def test_a_duplicate_route_is_refused(_tmp):
    """A second plan for a target is a scenario bug, and answering it
    silently lets the second plan decide the answer to the first."""
    report = run_scenario(scenarios.DUPLICATE_ROUTE)
    assert report['refusal'] == 'route already planned: /tabs', report
    assert report['planned'] == ['/tabs'], report


def test_an_envelope_naming_another_command_is_not_a_match(_tmp):
    """The fake has to be able to tell a wrong result from a right one, and
    the shipped loop is what decides: it skips an envelope whose `id` is
    not the command's and keeps polling to its own budget. Repairing the
    envelope in the double hands the section a result it never sent.

    The discriminator is the consume leg, and it is exact: the consume leg
    was reached or it was not, and it is reached only for a result the
    loop accepted. The `> 1` on the poll leg is what keeps that exact
    enough to be worth anything -- a loop that polled once and gave up, or
    a recorder that logged nothing, both satisfy `consumed == []` for the
    wrong reason. THIS case still runs the loop to a 700 ms budget on
    purpose, because "gave up" is the behaviour under test; the retry case
    below is the one whose budget had to grow."""
    report = run_scenario(scenarios.ENVELOPE, sections=('api.js',))
    _command, polls, consumed = _legs(report)
    assert len(polls) > 1, (
        'no poll was recorded, so the consume assertion proves nothing',
        report)
    assert report['outcome'] is not None, (
        'the mismatched envelope was delivered as a match', report)
    assert report['outcome'].startswith('Timeout (700ms) waiting for '), report
    assert consumed == [], report
    assert report['unplanned'] == [], report


def test_the_poll_retries_until_the_result_is_the_commands_own(_tmp):
    """The loop retries rather than accepting the first envelope: two
    leading polls carry somebody else's envelope, so the third has to be
    reached. A `continue` turned into a `break` in the shipped loop would
    answer on the first poll and time out, which is the shape this pins.

    Three polls is the PLAN's number, not the host's, and the scenario
    runs a 5 s budget so the loop's own clock check -- host time plus the
    750 ms the pump spent -- has seconds of margin rather than 200 ms. The
    sleeps are virtual, so the margin costs no wall time.

    The wrong envelopes are read back off the responses and pinned
    member by member, because "the loop retried past somebody else's
    envelope" is a property of what the fake handed over. An envelope
    missing its `deliveryId`, or carrying its own `result`, would be
    rejected at a different check in `api.js` and leave the poll count
    identical, so the count alone cannot say the id mismatch was what
    the loop saw."""
    report = run_scenario(scenarios.LATE_ENVELOPE, sections=('api.js',))
    _command, polls, consumed = _legs(report)
    # A result that is `undefined` vanishes from the report rather than
    # failing on it, so the claim is asserted on its presence first.
    assert 'result' in report, ('the command returned no result', report)
    assert report['result'] == 'the right result', report
    assert len(polls) == 3, report
    assert len(consumed) == 1, report
    # Anchored on the command, wrong only in its id, and carrying no
    # result: an envelope the loop can reject on `id` and only on `id`.
    assert report['seen'][:2] == [
        {'id': 'a command this is not', 'deliveryId': 'd1',
         'resultGeneration': 1},
        {'id': 'a command this is not', 'deliveryId': 'd1',
         'resultGeneration': 1},
    ], report
    assert report['seen'][2] == {
        'id': report['seen'][2]['id'], 'deliveryId': 'd1',
        'resultGeneration': 1, 'result': 'the right result',
        'error': None,
    }, report
    assert report['unplanned'] == [], report


def test_the_envelope_the_transport_anchors_is_the_commands_own(_tmp):
    """The other half of the same claim: a default envelope takes its `id`
    and `deliveryId` from the command the transport received, so a
    matching command completes. An envelope anchored on a constant matches
    nothing, and the pair of cases is what stops either one passing alone."""
    report = run_scenario(scenarios.OWN_ENVELOPE, sections=('api.js',))
    assert report['result'] == 'the right result', report
    assert report['unplanned'] == [], report
    consumed = [r['target'] for r in _legs(report)[2]]
    assert consumed == [
        '/result?tab=extension&consume=1&expected=1'], report


def test_the_session_store_round_trips(_tmp):
    """`css-injector` keeps its session list in `localStorage` as JSON. A
    `setItem` that does nothing leaves the store permanently empty, which
    reads as a module that never records a session."""
    report = run_scenario(scenarios.STORAGE)
    assert report['back'] == [{'css': 'a{b:c}', 'tabId': '17',
                               'allFrames': True, 'ts': 1750000000000}], report
    assert report['emptied'] == '', report
    assert report['absent'] is None, report
    assert report['storage']['daedalus-dash-css-sessions'] == '', report


def test_loading_an_undeclared_module_is_refused(_tmp):
    """`build_harness` passes one path per declared section, so a name it
    was not given has no `process.argv` slot to read. Importing something
    else instead would load a module the scenario never declared."""
    report = run_scenario(scenarios.UNDECLARED_MODULE)
    assert report['refusal'] == 'no module argument for api.js', report
    assert report['planned'] == [], report


def test_the_bus_reaches_every_listener_and_only_those_still_registered(_tmp):
    """A fan-out double's contract is its breadth: one listener is never
    enough, and here each one does something different.

    The dispatch is LIVE, as `app.js`'s is over its `Set`, so `late` — the
    listener a dispatch registers — is reached by the dispatch that
    registered it. That is a property of JavaScript iteration rather than
    a choice this harness makes, and a double iterating a copy would be
    modelling a collection the shipped bus does not have.

    A listener that throws is reported through `console.error` and the
    rest of the dispatch still runs, and the unsubscribe removes its own
    listener and nobody else's."""
    report = run_scenario(scenarios.BUS)
    assert report['escaped'] is None, report
    assert report['first'] == {
        'seen': ['second:tabs-synced', 'third', 'fourth:tabs-synced',
                 'late:tabs-synced'],
        'errors': 1,
    }, report
    assert report['second']['seen'] == [
        'second:tabs-synced', 'third', 'fourth:tabs-synced',
        'late:tabs-synced',
        'second:tab-updated', 'third', 'fourth:tab-updated',
        'late:tab-updated',
    ], report
    # The unsubscribed listener is gone; the other three are not.
    assert report['third']['seen'] == [
        'second:tabs-synced', 'third', 'fourth:tabs-synced',
        'late:tabs-synced',
        'second:tab-updated', 'third', 'fourth:tab-updated',
        'late:tab-updated',
        'third', 'fourth:tab-unregistered', 'late:tab-unregistered',
    ], report
    assert report['errors'] == ['listener failed'] * 3, report


def test_the_pump_spends_the_poll_sleep_and_not_a_timer_beside_it(_tmp):
    """A scenario that parks its own long timer inside a command window
    must still see it parked: a pump that spends by index fires a callback
    the scenario never fired, skips the sleep the command was waiting for,
    and blows the virtual budget on a delay the window did not open for."""
    report = run_scenario(scenarios.PUMP_SELECTIVITY, sections=('api.js',))
    assert report['result'] == 'the right result', report
    assert report['planted'] == 0, report
    assert len(report['live']) == 1, report
    assert report['unplanned'] == [], report


def test_a_headers_bag_a_poll_with_no_command_and_a_twice_registered_selector(
        _tmp):
    """Three refusals that were reachable and unexercised. A `Headers`
    instance carries the credential just as truly as the plain object
    `api.js` builds, so reading it as absent would assert the opposite of
    the truth. A poll with no command behind it is a scenario that never
    sent one, not a bridge with a stale slot. A selector registered twice
    is a scenario that meant to register two documents."""
    report = run_scenario(scenarios.REFUSALS)
    assert report['bag'] is not None, report
    assert 'Headers' in report['bag'], report
    assert 'plain header object' in report['bag'], report
    assert report['polled'] is not None, report
    assert 'a result poll before any command for' in report['polled'], report
    assert '/result?tab=extension' in report['polled'], report
    assert report['twice'] == 'selector already registered: #s08 [data-sub]', \
        report
    # The bag is refused before the request is recorded, so the record
    # carries the poll and nothing before it.
    assert report['requests'] == 1, report
    assert report['errors'] == [], report


def test_console_error_is_recorded_and_an_unprintable_one_does_not_throw(_tmp):
    """A failed mount and a failed bus listener reach a scenario only
    through this recorder, so a value whose own `toString` throws must not
    take the call site with it — `app.js`'s bus catches and logs, and a
    throw here would replace a recorded line with a crash."""
    report = run_scenario(scenarios.CONSOLE_ERROR)
    assert report['escaped'] is None, report
    assert report['errors'] == [
        '[mount] net-capture failed boom',
        '[bus] listener failed [unprintable value]',
    ], report


def test_a_scenario_records_the_six_phase_checkpoints(_tmp):
    """`test_dashboard_harness.py` pins this trace for every shipped
    harness. `load` emits the two import checkpoints and `report` the two
    that close the run, so a scenario cannot emit them out of order or
    leave one out."""
    assert _phases(scenarios.PHASE_TRACE, ('api.js',)) == [
        'dashboard harness started',
        'dashboard module import started',
        'dashboard module imported',
        'dashboard call started',
        'dashboard call settled',
        'dashboard harness finished',
    ]


def test_a_scenario_cannot_disagree_with_its_declared_bound(_tmp):
    """`build_harness` counts the bounds in the assembled source, so the
    count a scenario's own `await bounded(` earns is the count the child
    is timed against."""
    harness = build_harness(
        "await bounded(work, 'a step', _dashnodeStepTimeoutMs);\n"
        "await bounded(more, 'another step', _dashnodeStepTimeoutMs);\n",
        sections=())
    assert harness.bounded_steps == 2, harness.bounded_steps
    assert harness.module is True, harness
    assert harness.arguments == (), harness


def test_a_module_name_that_escapes_the_dashboard_is_refused(_tmp):
    """`section_path` mirrors `_dashshell.dashboard_module`: the name is
    resolved under `dashboard/`, and a name that climbs out of it is
    refused rather than resolved."""
    assert section_path('sections/net-capture.js').name == 'net-capture.js'
    assert section_path('api.js').name == 'api.js'
    for name in ('../server.py', '/etc/passwd', ''):
        try:
            section_path(name)
        except ValueError:
            continue
        raise AssertionError(f'escaping module name accepted: {name!r}')


def test_the_public_surface_is_the_set_the_suite_imports(_tmp):
    """`__all__` is a promise, and a name in it that does not exist is a
    section suite's `ImportError` rather than a test failure here."""
    assert _dashsection.__all__ == ['SHELL', 'build_harness', 'run_scenario',
                                    'section_path'], _dashsection.__all__
    for name in _dashsection.__all__[1:]:
        assert callable(getattr(_dashsection, name)), name
    assert isinstance(_dashsection.SHELL, str), type(_dashsection.SHELL)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashsect_')


if __name__ == '__main__':
    raise SystemExit(main())
