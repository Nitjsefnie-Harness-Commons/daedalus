#!/usr/bin/env python3
"""The one eval handler that READS a result instead of producing one.

`do_result` is here rather than in tests/test_cli_eval_handlers.py for the
file-size gate, and for no other reason: it shares that file's contract
exactly — the exact request the handler put on the wire, and the exact
output an operator reads, each as one whole string — and nothing else.

The marker glyphs are spelled out rather than read from
`daedalus_cli.output`, for the reason given in the sibling file: an
expected value taken from the module under test pins nothing about it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_eval  # noqa: E402

run_cli = _cli_dispatch.run_cli

IN = '←'

TOK = 'clitok'


def _get(path):
    return {'via': 'api', 'method': 'GET', 'path': path, 'body': None}


def _rendered(out):
    """`out` with the fallback marker spelling folded onto the pinned glyph.

    `output._output_markers` picks one of two spellings per glyph, and only
    those two are folded, so a third one reaches the assertions untouched
    and every whole-string comparison here fails.
    """
    return out.replace('<-', IN)


def _result(**over):
    base = {'id': 'job1', 'result': 'ok', 'error': None, 'ts': 1}
    return dict(base, **over)


# ── do_result ────────────────────────────────────────────────────────

def test_do_result_asks_for_the_tab_it_was_given(tmp):
    """The tab is a query parameter, percent-encoded by the shared builder."""
    del tmp
    recorded, out = run_cli(
        ['result'], [_result(tabId='tab0')],
        module=commands_eval, plan=[_get('/result?tab=tab0')],
        target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('GET', '/result?tab=tab0', None)], \
        recorded.api_calls
    assert _rendered(out) == f'{IN} job1  tab=tab0\nok\n', repr(out)


def test_do_result_asks_for_the_broadcast_result_when_no_tab_is_set(tmp):
    """No tab means the path carries no query at all, not an empty one."""
    del tmp
    _recorded, out = run_cli(
        ['result'], [_result()], module=commands_eval, plan=[_get('/result')],
        target_tab='', token=TOK)

    assert _rendered(out) == f'{IN} job1\nok\n', repr(out)


def test_do_result_adds_the_consume_flag_only_when_it_was_asked(tmp):
    """`consume=1` travels after `tab`, and only with the flag.

    The rendered line is byte-identical to the unconsumed arm's, and that
    is the point: `-c` changes the QUERY and nothing an operator reads
    back. Pinned here as a whole string, because this arm's rendering was
    otherwise pinned nowhere for this handler.
    """
    del tmp
    _recorded, out = run_cli(
        ['result', '-c'], [_result(tabId='tab0')], module=commands_eval,
        plan=[_get('/result?tab=tab0&consume=1')], target_tab='tab0',
        token=TOK)

    assert _rendered(out) == f'{IN} job1  tab=tab0\nok\n', repr(out)


def test_do_result_says_so_when_nothing_is_pending(tmp):
    """The empty arm is a sentence and an early return, not a print of the
    pending envelope the bridge sent."""
    del tmp
    _recorded, out = run_cli(
        ['result'], [{'pending': True}], module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert out == 'No result pending\n', repr(out)


def test_do_result_prints_raw_json_when_asked(tmp):
    """`--raw` hands the machine the envelope, unindented by no printer."""
    del tmp
    _recorded, out = run_cli(
        ['result', '--raw'], [_result()], module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert out == (
        '{\n  "id": "job1",\n  "result": "ok",\n  "error": null,\n'
        '  "ts": 1\n}\n'), repr(out)


def test_do_result_renders_an_undefined_result_as_a_word(tmp):
    """A command that produced nothing says so, rather than printing None.
    """
    del tmp
    _recorded, out = run_cli(
        ['result'], [_result(result=None, tabId='tab0')],
        module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert _rendered(out) == f'{IN} job1  tab=tab0\n(undefined)\n', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cliresult_')


if __name__ == '__main__':
    raise SystemExit(main())
