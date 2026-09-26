#!/usr/bin/env python3
"""Every branch of do_inject_css and do_remove_css in
daedalus_cli/commands_content.py.

Both handlers read their CSS from one of two sources and add each of the
two options only when it was asked for, so this file asserts all three
choices per handler: the inline string, the contents of a named file, and
the `tabId` / `allFrames` fields the operator did and did not name.

`--chrome-tab 0` is the value that separates `is not None` from a
truthiness test, so the tab is pinned with the zero Chrome really can
number a tab rather than with a large number. A handler that had written
`if args.chrome_tab:` would drop that field and leave every other test in
this file green.

No marker glyphs are folded here: both handlers print plain ASCII, so the
expected lines are the literals below with nothing between them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli

TOK = 'clitok'


def _ext(cmd_id, cmd_type, fields, timeout=10):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}


def _css_file(directory, name, text):
    path = Path(directory) / name
    path.write_text(text, encoding='utf-8')
    return str(path)


# ── do_inject_css ────────────────────────────────────────────────────

def test_do_inject_css_sends_the_inline_css_and_neither_option(tmp):
    """The bare arm: `css` alone, and the count the extension echoed.

    The recorded fields carry no `tabId` and no `allFrames`, so this is
    the half of the flag control that shows both absent; the two tests
    below are the half that shows them present.
    """
    del tmp
    recorded, out = run_cli(
        ['inject-css', '--css', 'a{color:red}'],
        [{'injected': 10, 'tabId': 7}],
        plan=[_ext('_inject_css', 'inject-css', {'css': 'a{color:red}'})],
        token=TOK)

    assert recorded.calls == [('_inject_css', 'inject-css',
                               {'css': 'a{color:red}'})], recorded.calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == 'Injected 10 chars CSS into tab 7\n', repr(out)


def test_do_inject_css_reads_its_css_out_of_the_file_it_was_given(tmp):
    """`--file` sends the file's contents, newlines and all.

    The source travels as the field's value rather than as a path, so a
    file the CLI could read and the extension cannot would otherwise be
    stored as a path the extension must resolve on its own.
    """
    text = 'body {\n  color: red;\n}\n'
    path = _css_file(tmp, 'style.css', text)
    recorded, out = run_cli(
        ['inject-css', '--file', path],
        [{'injected': 21, 'tabId': 3}],
        plan=[_ext('_inject_css', 'inject-css', {'css': text})],
        token=TOK)

    assert recorded.calls == [('_inject_css', 'inject-css',
                               {'css': text})], recorded.calls
    assert out == 'Injected 21 chars CSS into tab 3\n', repr(out)


def test_do_inject_css_names_the_tab_chrome_numbered_zero(tmp):
    """`--chrome-tab 0` sends `tabId: 0`; the guard is a presence test.

    Zero is the only value that tells `is not None` apart from truthiness
    here, and it is a value Chrome can really assign. A handler that
    dropped a falsy tab would still satisfy every other tab in this file.
    """
    del tmp
    recorded, _out = run_cli(
        ['inject-css', '--css', 'a{}', '--chrome-tab', '0'],
        [{'injected': 3, 'tabId': 0}],
        plan=[_ext('_inject_css', 'inject-css', {'css': 'a{}', 'tabId': 0})],
        token=TOK)

    assert recorded.calls == [('_inject_css', 'inject-css',
                               {'css': 'a{}', 'tabId': 0})], recorded.calls


def test_do_inject_css_carries_all_frames_only_when_it_was_asked_for(tmp):
    """`--all-frames` adds `allFrames` and nothing else."""
    del tmp
    recorded, _out = run_cli(
        ['inject-css', '--css', 'a{}', '--all-frames'],
        [{'injected': 3, 'tabId': 2}],
        plan=[_ext('_inject_css', 'inject-css',
                   {'css': 'a{}', 'allFrames': True})],
        token=TOK)

    assert recorded.calls == [('_inject_css', 'inject-css',
                               {'css': 'a{}', 'allFrames': True})], \
        recorded.calls


def test_do_inject_css_prints_placeholders_for_a_result_counting_nothing(tmp):
    """A result carrying neither key reads `?` twice, never `None`."""
    del tmp
    _recorded, out = run_cli(
        ['inject-css', '--css', 'a{}'], [{}],
        plan=[_ext('_inject_css', 'inject-css', {'css': 'a{}'})],
        token=TOK)

    assert out == 'Injected ? chars CSS into tab ?\n', repr(out)


# ── do_remove_css ────────────────────────────────────────────────────

def test_do_remove_css_sends_the_inline_css_and_neither_option(tmp):
    """The bare arm: `css` alone, with no `tabId` and no `allFrames`."""
    del tmp
    recorded, out = run_cli(
        ['remove-css', '--css', 'a{color:red}'],
        [{'removed': 10, 'tabId': 7}],
        plan=[_ext('_remove_css', 'remove-css', {'css': 'a{color:red}'})],
        token=TOK)

    assert recorded.calls == [('_remove_css', 'remove-css',
                               {'css': 'a{color:red}'})], recorded.calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == 'Removed 10 chars CSS from tab 7\n', repr(out)


def test_do_remove_css_reads_its_css_out_of_the_file_it_was_given(tmp):
    """`--file` sends the file's contents, not the path it was read from.

    The removal has to match the injection byte for byte, so the value
    that travels is the one the injection would have sent for the same
    file rather than anything derived from the path.
    """
    text = 'body {\n  color: red;\n}\n'
    path = _css_file(tmp, 'style.css', text)
    recorded, out = run_cli(
        ['remove-css', '--file', path],
        [{'removed': 21, 'tabId': 3}],
        plan=[_ext('_remove_css', 'remove-css', {'css': text})],
        token=TOK)

    assert recorded.calls == [('_remove_css', 'remove-css',
                               {'css': text})], recorded.calls
    assert out == 'Removed 21 chars CSS from tab 3\n', repr(out)


def test_do_remove_css_names_the_tab_chrome_numbered_zero(tmp):
    """`--chrome-tab 0` sends `tabId: 0`, the value a truthiness test drops."""
    del tmp
    recorded, _out = run_cli(
        ['remove-css', '--css', 'a{}', '--chrome-tab', '0'],
        [{'removed': 3, 'tabId': 0}],
        plan=[_ext('_remove_css', 'remove-css', {'css': 'a{}', 'tabId': 0})],
        token=TOK)

    assert recorded.calls == [('_remove_css', 'remove-css',
                               {'css': 'a{}', 'tabId': 0})], recorded.calls


def test_do_remove_css_carries_all_frames_only_when_it_was_asked_for(tmp):
    """`--all-frames` adds `allFrames` and nothing else."""
    del tmp
    recorded, _out = run_cli(
        ['remove-css', '--css', 'a{}', '--all-frames'],
        [{'removed': 3, 'tabId': 2}],
        plan=[_ext('_remove_css', 'remove-css',
                   {'css': 'a{}', 'allFrames': True})],
        token=TOK)

    assert recorded.calls == [('_remove_css', 'remove-css',
                               {'css': 'a{}', 'allFrames': True})], \
        recorded.calls


def test_do_remove_css_prints_placeholders_for_a_result_counting_nothing(tmp):
    """A result carrying neither key reads `?` twice, never `None`."""
    del tmp
    _recorded, out = run_cli(
        ['remove-css', '--css', 'a{}'], [{}],
        plan=[_ext('_remove_css', 'remove-css', {'css': 'a{}'})],
        token=TOK)

    assert out == 'Removed ? chars CSS from tab ?\n', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clicontentcss_')


if __name__ == '__main__':
    raise SystemExit(main())
