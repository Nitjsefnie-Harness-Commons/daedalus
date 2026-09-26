#!/usr/bin/env python3
"""Every branch of do_store_hotfix, do_clear_hotfix, do_clear_hotfixes
and do_set_permanent in daedalus_cli/commands_content.py.

`tests/test_cli_hotfixes.py` already pins the scope forwarding for
`store-hotfix` and the scope column of `list-hotfixes`, and
`tests/test_cli_segment_origins.py` owns the three segment-origin
handlers; neither is extended here. What this file adds is the branches
those suites do not reach, and the one of them that is a defect.

`--match ''` is the centre of the file. The handler guarded its scope
field with a truthiness test, so an empty pattern was dropped from the
request instead of being sent for the extension to refuse -- the CLI,
the MCP tool and the dashboard form all reported a fix stored unscoped
for a pattern the operator had typed. The control below fails against
that guard and passes against a presence test, and `''` is the only
value that tells the two apart.

`do_list_hotfixes` is here for one arm only. Its rows are rendered
through `time.strftime` on a local timestamp, so a whole-string
comparison of a row would bake this machine's timezone into the suite;
`tests/test_cli_hotfixes.py` owns the two columns an operator can act
on, and this file pins the empty listing, whose `No hotfixes stored`
line no suite in the tree asserted before -- the branch was driven
through `tests/test_cli.py` with its output unchecked.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli
run_cli_exit = _cli_dispatch.run_cli_exit

TOK = 'clitok'
SCOPE = '*://*.example.com/*'
CODE = 'console.log(1)'


def _ext(cmd_id, cmd_type, fields, timeout=10):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}


def _stored(**over):
    base = {'stored': 'fix', 'total': 1, 'permanent': False, 'match': None}
    return dict(base, **over)


# ── do_store_hotfix ──────────────────────────────────────────────────

def test_store_hotfix_sends_the_code_and_neither_flag_when_none_was_given(tmp):
    """The bare arm: `fixId` and `code`, and nothing else on the wire.

    `permanent` is absent here rather than sent as `False`, which is the
    half of the flag control that shows it absent; the `--permanent` test
    below is the half that shows it present. A handler that sent
    `permanent: False` on every store would demote every permanent fix an
    operator updated without restating the flag.
    """
    del tmp
    recorded, out = run_cli(
        ['store-hotfix', 'fix', '--code', CODE], [_stored()],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': CODE})],
        token=TOK)

    assert recorded.calls == [('_store_hf', 'store-hotfix',
                               {'fixId': 'fix', 'code': CODE})], \
        recorded.calls
    assert out == 'Stored hotfix "fix" [unscoped] (1 total)\n', repr(out)


def test_store_hotfix_sends_an_empty_pattern_rather_than_dropping_it(tmp):
    """`--match ''` puts `'match': ''` on the wire, and no other field.

    The empty string is what separates this guard from a truthiness
    test: a handler reading `if args.match:` drops the field entirely,
    the extension is never asked, and the fix is stored unscoped while
    the operator believes it was scoped. The field set is the whole
    assertion -- `''` present and nothing beside it -- so a handler that
    sent the empty pattern but also a flag it was not given fails too.

    The answer is the default record, because that is what the worker
    sends for any fix it holds without a scope: `hotfixes.js` answers
    `match: match || null`, and `''` is falsy, so an empty pattern can
    never come back as an empty pattern. Against the real extension
    this command does not render at all -- it exits on the worker's
    refusal -- and that surfacing is `ext_cmd`'s, not this handler's:
    `invoke.py` raises it, and the harness here rebinds `ext_cmd` itself,
    so this suite cannot reach it. `tests/test_cli_error_reporting.py`
    is where a refused command's exit is pinned, and driving the real
    `ext_cmd` against a live worker is the end-to-end check.
    """
    del tmp
    recorded, out = run_cli(
        ['store-hotfix', 'fix', '--code', CODE, '--match', ''],
        [_stored()],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': CODE, 'match': ''})],
        token=TOK)

    assert recorded.calls == [('_store_hf', 'store-hotfix',
                               {'fixId': 'fix', 'code': CODE,
                                'match': ''})], recorded.calls
    # `[unscoped]` and not `[SCOPE]`: the record that comes back carries
    # no scope, which is the state a refused store leaves nothing of.
    # The arm that renders a scope is the one above, and the arm that
    # renders `[PERM]` beside one is `..._renders_both_markers_...`.
    assert out == 'Stored hotfix "fix" [unscoped] (1 total)\n', repr(out)


def test_store_hotfix_reads_its_source_out_of_the_file_it_was_given(tmp):
    """`--file` sends the file's contents, newlines and all.

    The source travels as the field's value, and the whole of it: a
    handler that stripped or truncated the file would store a fix that
    no longer parses.
    """
    source = '(() => {\n  const x = 1;\n})();\n'
    path = Path(tmp) / 'fix.js'
    path.write_text(source, encoding='utf-8')
    recorded, out = run_cli(
        ['store-hotfix', 'fix', '--file', str(path)],
        [_stored(stored='fix', total=2)],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': source})],
        token=TOK)

    assert recorded.calls == [('_store_hf', 'store-hotfix',
                               {'fixId': 'fix', 'code': source})], \
        recorded.calls
    assert out == 'Stored hotfix "fix" [unscoped] (2 total)\n', repr(out)


def test_store_hotfix_carries_the_permanent_flag_only_when_it_was_given(tmp):
    """`--permanent` adds `permanent`, and the record echoes it back.

    Both halves in one line: the flag on the wire and the `[PERM]`
    marker the operator reads, which is the only place the CLI reports
    that a stored fix will survive a version bump.
    """
    del tmp
    recorded, out = run_cli(
        ['store-hotfix', 'fix', '--code', CODE, '--permanent'],
        [_stored(permanent=True)],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': CODE, 'permanent': True})],
        token=TOK)

    assert recorded.calls == [('_store_hf', 'store-hotfix',
                               {'fixId': 'fix', 'code': CODE,
                                'permanent': True})], recorded.calls
    assert out == 'Stored hotfix "fix" [PERM] [unscoped] (1 total)\n', \
        repr(out)


def test_store_hotfix_renders_both_markers_for_a_permanent_scoped_fix(tmp):
    """A permanent fix with a scope prints `[PERM]` then the scope.

    The order is the permanent marker first, which is the arm the bare
    and `--permanent` tests above do not reach: each of those has an
    absent scope, so neither pins the two markers together.
    """
    del tmp
    recorded, out = run_cli(
        ['store-hotfix', 'fix', '--code', CODE, '--permanent',
         '--match', SCOPE],
        [_stored(permanent=True, match=SCOPE)],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': CODE, 'permanent': True,
                    'match': SCOPE})],
        token=TOK)

    assert recorded.calls == [('_store_hf', 'store-hotfix',
                               {'fixId': 'fix', 'code': CODE,
                                'permanent': True, 'match': SCOPE})], \
        recorded.calls
    assert out == f'Stored hotfix "fix" [PERM] [{SCOPE}] (1 total)\n', \
        repr(out)


def test_store_hotfix_prints_placeholders_for_a_result_naming_nothing(tmp):
    """A result with no `stored` and no `total` reads `?` for both.

    The scope cell is not a placeholder: `match` is read with `get`, so
    an absent key and an explicit null both render `[unscoped]`, and
    only a record that really carries a pattern renders one.
    """
    del tmp
    _recorded, out = run_cli(
        ['store-hotfix', 'fix', '--code', CODE], [{}],
        plan=[_ext('_store_hf', 'store-hotfix',
                   {'fixId': 'fix', 'code': CODE})],
        token=TOK)

    assert out == 'Stored hotfix "?" [unscoped] (? total)\n', repr(out)


# ── do_clear_hotfix ──────────────────────────────────────────────────

def test_do_clear_hotfix_reports_the_fix_it_cleared_and_what_remains(tmp):
    """The one call, `fixId` alone, and the line with both counts.

    `found` and `remaining` come from the extension, so the line is the
    only way an operator learns that the id they typed was not there.
    """
    del tmp
    recorded, out = run_cli(
        ['clear-hotfix', 'fix'], [{'cleared': 'fix', 'found': True,
                                   'remaining': 2}],
        plan=[_ext('_clear_hf', 'clear-hotfix', {'fixId': 'fix'})],
        token=TOK)

    assert recorded.calls == [('_clear_hf', 'clear-hotfix',
                               {'fixId': 'fix'})], recorded.calls
    assert out == 'Cleared hotfix "fix" (found=True, remaining=2)\n', \
        repr(out)


def test_do_clear_hotfix_renders_a_result_carrying_neither_count(tmp):
    """An absent `found` reads `False` and an absent `remaining` reads 0.

    Two defaults on one line, and the `found` one is the one that
    matters: an absent key must not read as a cleared fix.
    """
    del tmp
    _recorded, out = run_cli(
        ['clear-hotfix', 'ghost'], [{}],
        plan=[_ext('_clear_hf', 'clear-hotfix', {'fixId': 'ghost'})],
        token=TOK)

    assert out == 'Cleared hotfix "?" (found=False, remaining=0)\n', \
        repr(out)


# ── do_clear_hotfixes ────────────────────────────────────────────────

def test_do_clear_hotfixes_sends_include_permanent_false_when_not_asked(tmp):
    """The flag travels on every clear, `False` included.

    The mirror of `permanent` on `store-hotfix`, and pinned in the
    opposite direction: this handler states the decision explicitly
    rather than leaving it to an absent field, so the counts the
    extension returns describe a clear it was actually told to perform.
    """
    del tmp
    recorded, out = run_cli(
        ['clear-hotfixes'], [{'removed': 3, 'kept': 1}],
        plan=[_ext('_clear_all_hf', 'clear-all-hotfixes',
                   {'includePermanent': False})],
        token=TOK)

    assert recorded.calls == [('_clear_all_hf', 'clear-all-hotfixes',
                               {'includePermanent': False})], \
        recorded.calls
    assert out == 'Cleared 3 hotfix(es); 1 permanent kept\n', repr(out)


def test_do_clear_hotfixes_renders_zero_counts_when_the_extension_has_nothing(
        tmp):
    """A result naming neither count reads a zero and a zero."""
    del tmp
    _recorded, out = run_cli(
        ['clear-hotfixes'], [{}],
        plan=[_ext('_clear_all_hf', 'clear-all-hotfixes',
                   {'includePermanent': False})],
        token=TOK)

    assert out == 'Cleared 0 hotfix(es); 0 permanent kept\n', repr(out)


def test_do_clear_hotfixes_reports_the_wider_clear_without_the_counts(tmp):
    """`--include-permanent` prints one sentence and ignores the counts.

    The result deliberately carries a kept count of nine, so the line
    being the wide sentence and not the counted one is pinned rather
    than merely assumed from the flag.
    """
    del tmp
    recorded, out = run_cli(
        ['clear-hotfixes', '--include-permanent'],
        [{'removed': 0, 'kept': 9}],
        plan=[_ext('_clear_all_hf', 'clear-all-hotfixes',
                   {'includePermanent': True})],
        token=TOK)

    assert recorded.calls == [('_clear_all_hf', 'clear-all-hotfixes',
                               {'includePermanent': True})], \
        recorded.calls
    assert out == 'All hotfixes cleared (incl. permanent)\n', repr(out)


# ── do_list_hotfixes ─────────────────────────────────────────────────

def test_do_list_hotfixes_says_so_when_none_is_stored(tmp):
    """The empty listing is a sentence and an early return.

    One line, no timestamp: the twelve-wide cell and the strftime below
    it are what make a row unpinnable here, and neither of them runs
    on this arm. Nothing asserted this string before, so a handler that
    printed nothing at all for an empty buffer passed every suite.
    """
    del tmp
    recorded, out = run_cli(
        ['list-hotfixes'], [{'version': '1.0', 'fixes': []}],
        plan=[_ext('_list_hf', 'list-hotfixes', {})], token=TOK)

    assert recorded.calls == [('_list_hf', 'list-hotfixes', {})], \
        recorded.calls
    assert out == 'No hotfixes stored\n', repr(out)


# ── do_set_permanent ─────────────────────────────────────────────────

def test_do_set_permanent_sends_the_flag_it_decoded_from_either_spelling(
        tmp):
    """`true` travels as `True` and `false` as `False`, and both print.

    The two spellings are the two arms of the same field, so they are
    pinned together: a handler that sent the string it was given, or
    that inverted the false spelling, would fail one of the two.
    """
    del tmp
    recorded, on = run_cli(
        ['set-permanent', 'fix1', 'yes'],
        [{'found': True}],
        plan=[_ext('_set_perm', 'set-permanent',
                   {'fixId': 'fix1', 'permanent': True})],
        token=TOK)

    assert recorded.calls == [('_set_perm', 'set-permanent',
                               {'fixId': 'fix1', 'permanent': True})], \
        recorded.calls
    assert on == 'Set permanent=True on "fix1"\n', repr(on)

    recorded, off = run_cli(
        ['set-permanent', 'fix1', 'off'],
        [{'found': True}],
        plan=[_ext('_set_perm', 'set-permanent',
                   {'fixId': 'fix1', 'permanent': False})],
        token=TOK)

    assert recorded.calls == [('_set_perm', 'set-permanent',
                               {'fixId': 'fix1', 'permanent': False})], \
        recorded.calls
    assert off == 'Set permanent=False on "fix1"\n', repr(off)


def test_do_set_permanent_exits_when_the_extension_found_no_such_fix(tmp):
    """A refusal is an exit naming the id, with nothing rendered first.

    The command still reached the extension -- the plan is checked
    request by request as they arrive -- and the failure is the
    extension's answer, not the CLI's.
    """
    del tmp
    code, out = run_cli_exit(
        ['set-permanent', 'ghost', 'true'], [{'found': False}],
        plan=[_ext('_set_perm', 'set-permanent',
                   {'fixId': 'ghost', 'permanent': True})],
        token=TOK)

    assert code == 'No hotfix with id "ghost"', code
    assert out == '', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clicontfix_')


if __name__ == '__main__':
    raise SystemExit(main())
