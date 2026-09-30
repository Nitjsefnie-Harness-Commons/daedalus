#!/usr/bin/env python3
"""The tokens the launch analyser bounds on, pinned where a file can hide.

`tests/_launch_keep.py::in_launch_population` reads the whole tracked
Python tree and no source text, because nothing about a file's spelling
decides whether it carries a bounded launch. Two fixtures here are the
reasons, and each was a green control before it was a test: a MISSPELLED
bound the analyser still reports under a `keyword` head, and a fullwidth
`timeout` that PEP 3131 delivers to the analyser as ASCII from a file
that never spells it. A spelling filter reading `timeout` and `**` would
have put both out of the control's reach, silently.

The rest holds the limbs apart, so dropping one from the analyser is a
test that fails rather than a population that quietly shrinks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_audit import bound_sites  # noqa: E402
from _launch_keep import in_launch_population  # noqa: E402

# A bounded launch reached through the import machinery, which is the
# route `subprocess` never has to be spelled for. #1155 is the defect
# this shape reproduces: the old `'subprocess' in source` filter put
# this file out of the control's reach entirely.
REACHED_WITHOUT_SUBPROCESS = (
    'import importlib\n'
    '\n'
    '\n'
    'def probe():\n'
    "    proc = importlib.import_module('subprocess')\n"
    "    proc.run(['git', 'status'], timeout=30)\n")

# The unpacking limb on its own: `**` is spelled, `timeout` is not
# anywhere in the file, so a rule reading only `timeout` would drop it —
# a file whose call the analyser classifies as bounded all the same.
UNPACKED_ONLY = (
    'import importlib\n'
    '\n'
    '\n'
    'def probe():\n'
    "    proc = importlib.import_module('subprocess')\n"
    "    limits = {'deadline': 30}\n"
    "    proc.run(['git', 'status'], **limits)\n")

# A MISSPELLED bound, and the `keyword` kind the analyser's own comment
# calls "the interesting case": `timout=30` never runs, so the bound it
# was trying to place is invisible to every check that reads the word
# `timeout`. The analyser reports the site anyway. Nothing in this source
# spells `timeout` or `**`.
MISSPELLED_BOUND = (
    'import subprocess\n'
    '\n'
    '\n'
    'def probe():\n'
    '    subprocess.run(["git", "status"], timout=30)\n')

# The same hole with nothing misspelled, reached through the grammar
# rather than through a typo. CPython normalises identifiers per PEP
# 3131, so a fullwidth `timeout` arrives as ASCII `timeout` in
# `ast.keyword.arg` while the file's own text spells no ASCII `timeout`
# at all. Spelled as escapes so this file stays ASCII.
FULLWIDTH_TIMEOUT = '\uff54\uff49\uff4d\uff45\uff4f\uff55\uff54'
FULLWIDTH_BOUND = (
    'import subprocess\n'
    '\n'
    '\n'
    'def probe():\n'
    f'    subprocess.run(["git", "status"], {FULLWIDTH_TIMEOUT}=30)\n')


def test_a_bounded_launch_no_subprocess_spelling_reaches_is_still_found(tmp):
    """The #1155 shape: a bounded launch the old filter could not see.

    Every fixture both readings of the population agreed on proved
    nothing about it. This one separates them: the launch is bounded and
    the old filter's token is absent, so the file is in the population
    only under a rule that reads the tree rather than a spelling.
    """
    del tmp
    assert 'import subprocess' not in REACHED_WITHOUT_SUBPROCESS, (
        'the fixture spells the token the #1155 filter looked for, so it '
        'cannot tell that filter from this one')
    assert in_launch_population('probe.py', REACHED_WITHOUT_SUBPROCESS), (
        'a tracked file carrying a bounded launch that reaches its launcher '
        'without spelling "import subprocess" fell out of the launch '
        "control's population; that is issue #1155 reopened")
    assert bound_sites(REACHED_WITHOUT_SUBPROCESS, 'probe.py'), (
        'the fixture carries a bounded launch the analyser reports no '
        'site for, so it pins nothing about the population')


def test_the_unpacking_limb_bounds_a_call_that_spells_no_timeout(tmp):
    """The `**` half of the analyser's definition, read on its own.

    A keyword-argument name and an unpacking operator are spelled
    differently, so the two limbs can be reasoned about separately and
    one can be dropped from the analyser without the other noticing. This
    fixture spells `**` and never spells `timeout` anywhere.
    """
    del tmp
    assert 'timeout' not in UNPACKED_ONLY, (
        'the fixture spells "timeout", so it cannot tell the unpacking limb '
        'from the keyword-argument one')
    assert bound_sites(UNPACKED_ONLY, 'probe.py') == [(7, 'git', 'unpack')], (
        'the analyser no longer classifies a "**"-unpacked mapping as '
        'bounded, so this fixture pins nothing')


def test_a_misspelled_bound_the_analyser_refuses_is_still_read(tmp):
    """The `keyword` kind, which no spelling list can enumerate.

    The placed-launch path appends `keyword` for any launch carrying a
    keyword the stdlib does not take, so the set of names that produce a
    bounded site is "every name outside `_LAUNCH_KEYWORDS`" — open-ended,
    and a token tuple cannot cover it. This fixture spells neither
    `timeout` nor `**`, so a filter reading those two would put the file
    out of the control's reach while the analyser still finds a site.
    """
    del tmp
    assert not any(token in MISSPELLED_BOUND
                   for token in ('timeout', '**')), (
        'the fixture spells a bounding token, so it cannot tell a filter '
        'that reads only those two from one that reads what the analyser '
        'bounds on')
    assert bound_sites(MISSPELLED_BOUND, 'probe.py') == [
        (5, 'git', 'keyword')], (
        'the fixture no longer carries the misspelled bound it was written '
        'for, so it pins nothing about the `keyword` kind')
    assert in_launch_population('probe.py', MISSPELLED_BOUND), (
        'a file whose only bounded launch is a misspelled bound fell out '
        "of the launch control's population: the analyser's own comment "
        'calls this the interesting case, and no spelling of `timeout` '
        'appears anywhere in the file to find it by')


def test_a_normalised_identifier_bound_is_still_read(tmp):
    """The same hole reached through the grammar, with no typo at all.

    CPython normalises identifiers per PEP 3131, so a fullwidth `timeout`
    arrives as ASCII `timeout` in `ast.keyword.arg` while the file's text
    spells no ASCII `timeout`. A source-text spelling test is defeated by
    the identifier table alone, with no author doing anything.
    """
    del tmp
    assert 'timeout' not in FULLWIDTH_BOUND, (
        'the fixture spells ASCII "timeout", so it cannot tell a rule that '
        'reads the source text from one that reads the parse')
    assert bound_sites(FULLWIDTH_BOUND, 'probe.py') == [
        (5, 'git', 'timeout')], (
        'the analyser no longer normalises the fullwidth keyword, so the '
        'fixture pins nothing about the parse it was written for')
    assert in_launch_population('probe.py', FULLWIDTH_BOUND), (
        'a file whose only bounded launch is written in normalised '
        "identifiers fell out of the launch control's population: PEP 3131 "
        'delivers `timeout` to the analyser from a source that never '
        'spells it')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
