#!/usr/bin/env python3
"""The JavaScript coverage figures CONTRIBUTING.md states in prose.

The paragraph in `CONTRIBUTING.md` that names the one shipped module no
suite reaches carries two numbers: the unreached module's own code-line
count, and the total the measurement is taken over. Both are functions of
the tree — the first of one file, the second of every tracked shipped
JavaScript file — so both are derived here, in one command, with no suite
run and no coverage data.

The covered count is NOT a function of the tree; it is a measurement of one
run, so no gate here can hold it and the prose is not permitted to state it.
The percentage IS a function of the tree — it is the module's share of the
denominator — so it is recomputed from the two figures above and compared by
value, to the precision the prose states. Every remaining figure in the
paragraph must be one the tree proves, and anything else is refused as a
value rather than as a phrase, so rewording around it does not slip past.

That split is the point. The figures that can be checked are checked here and
go red the moment a shipped file grows; the ones that cannot are pointed at
the run's own summary rather than copied into a file where nothing would
notice them going stale.

The scan reads DIGIT FORM. A figure written in words, or one attached to a
name or a version — `V8`, `base64`, `Python 3.13`, `SHA-256` — is outside
what it sees, and the case docstring says so rather than claiming
completeness it does not have. This is deliberate: the alternative is a
natural-language parser, and a prose gate is worth more when its own limits
are written down than when it pretends to have none.

The population is `js_coverage.tracked_sources` rather than a second copy of
its rule, so this suite cannot drift from the number it is checking: if the
report's definition of "shipped JavaScript" moves, both move together.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from js_coverage import tracked_sources  # noqa: E402
from js_lines import code_lines  # noqa: E402

CONTRIBUTING = ROOT / 'CONTRIBUTING.md'
UNREACHED = 'extension/options.js'


def _prose():
    return CONTRIBUTING.read_text(encoding='utf-8')


def _paragraph():
    """The coverage paragraph alone, bounded at BOTH ends.

    The upper bound is what the docstring used to omit and what the code did
    not have: the slice ran from the opening sentence to end of file, so a
    correct unrelated sentence further down -- a percentage in House style,
    say -- was read as a restatement and reded this suite with a message
    blaming the paragraph. The next editor's move would be to delete correct
    prose. Both markers are sentences, so a reword of either is a loud failure
    here rather than a silent change of scope.
    """
    text = _prose()
    start = text.index('Coverage is two numbers')
    return text[start:text.index('The matrix is not ceremony')]


def test_the_unreached_module_line_count_is_the_trees(tmp):
    """`0 of 51 code lines` — the 51 is that file's own physical code lines,
    and it moves the moment the file does."""
    del tmp
    said = re.search(r'at 0 of (\d+) code\s+lines', _paragraph())
    assert said, 'the paragraph no longer states the unreached module count'
    source = (ROOT / UNREACHED).read_text(encoding='utf-8')
    real = len(code_lines(source, UNREACHED))
    assert int(said.group(1)) == real, (
        f'CONTRIBUTING.md says {said.group(1)} of {UNREACHED} code lines; '
        f'the file has {real}')


def test_the_denominator_is_every_tracked_shipped_javascript_file(tmp):
    """`the 4943 the number is measured over` — the same sum the coverage
    report prints, over the same population, so any shipped file that grows
    or shrinks turns this red instead of quietly making the prose false."""
    del tmp
    said = re.search(r'the (\d+) the number is measured over', _paragraph())
    assert said, 'the paragraph no longer states the denominator'
    sources = tracked_sources(ROOT)
    total = sum(len(code_lines(text, rel)) for rel, text in sources.items())
    assert int(said.group(1)) == total, (
        f'CONTRIBUTING.md says the number is measured over {said.group(1)}; '
        f'the tree has {total}')
    assert UNREACHED in sources, f'{UNREACHED} is not in the population'


def test_the_paragraph_still_claims_the_unreached_module(tmp):
    """The qualitative claim is what the paragraph is for. The two numbers
    are decoration on it, so a gate that only checked the numbers would pass
    on a paragraph that had quietly stopped claiming anything."""
    del tmp
    text = _paragraph()
    assert UNREACHED in text, 'the paragraph no longer names the module'
    assert 'no suite reaches' in text, (
        'the paragraph no longer claims the module is unreached')


def _digit_runs(text):
    """Every maximal digit run, with where it sits, for the figure scan."""
    return [(m.group(0), m.start(), m.end())
            for m in re.finditer(r'\d+(?:\.\d+)?', text)]


def _part_of_a_token(text, start, end, run):
    """True when a digit run belongs to a name or a version, not a figure.

    A run is a token's own when it touches a letter, a digit or an
    underscore (`V8`, `base64`, `NODE_V8_COVERAGE`), when it carries its own
    dot (`Python 3.13`), or when a hyphen runs into it (`SHA-256`). Those are
    spellings of something else, and the paragraph is allowed to name things.

    This is an EXEMPTION list, not a description of what the scan can read.
    The scan reads digit form: a figure spelled in words is invisible to it,
    and so is one written with a thousands separator in a way that splits it
    into runs it will judge separately. The case docstring says so, because a
    gate that reads as complete and is not will be trusted past its reach.
    """
    before = text[start - 1] if start else ''
    after = text[end] if end < len(text) else ''
    if before.isalnum() or before == '_':
        return True
    if after.isalnum() or after == '_':
        return True
    return '.' in run or before in '.-'


def test_every_figure_in_the_paragraph_is_one_the_tree_proves(tmp):
    """The property, not a token: a figure may appear only if the tree
    proves it, so the test is the VALUE and never the wording around it.

    Two figures are provable here and are pinned by the cases above: the
    unreached module's own code-line count, and the denominator. The
    paragraph's percentage is the third, because it is computed FROM those
    two -- it is the module's share of the denominator -- so it is
    recomputed here and compared at the precision the prose states. That
    admits `about 1%` and refuses `about 2%`, which a flat one-point
    tolerance would have let through.

    Every other figure is a measurement of a coverage run, which nothing here
    can check, so it is refused as a VALUE: rewording around one does not
    slip past, and neither does inserting one into the sentence that
    disclaims it. The bare `0` is permitted because it is not a measurement
    but the claim that the module is unreached.

    What this does NOT read is in the module docstring and repeated here: the
    scan is digit form, so a figure spelled in words -- "four thousand three
    hundred and forty" -- passes. That is a known limit rather than a bug to
    be fixed by writing a prose parser, and the honest remedy is this
    sentence, not a bigger regular expression.
    """
    del tmp
    text = _paragraph()
    said_count = re.search(r'at 0 of (\d+) code\s+lines', text)
    said_total = re.search(r'the (\d+) the number is measured over', text)
    assert said_count, 'the paragraph no longer states the module count'
    assert said_total, 'the paragraph no longer states the denominator'
    count = int(said_count.group(1))
    total = int(said_total.group(1))
    share = 100.0 * count / total

    percents = re.findall(r'(\d+(?:\.\d+)?)\s*%', text)
    assert percents, (
        'the paragraph states no percentage in digits, so the share it '
        'expresses is not checked against anything -- and a percentage '
        'written in words is exactly as unchecked as none at all, so both '
        'are refused rather than passed')
    for said in percents:
        decimals = len(said.split('.')[1]) if '.' in said else 0
        half = 0.5 * 10 ** -decimals
        assert abs(float(said) - round(share, decimals)) < half, (
            f'the paragraph states {said}%, which is not the '
            f'{share:.2f}% the two figures above give')

    # Percentages are judged above; blank them so their digits are not also
    # scanned as bare figures, keeping every other offset where it was.
    blanked = re.sub(r'\d+(?:\.\d+)?\s*%',
                     lambda m: ' ' * len(m.group(0)), text)
    for run, start, end in _digit_runs(blanked):
        if _part_of_a_token(blanked, start, end, run):
            continue
        assert float(run) in (0, count, total), (
            f'the paragraph states {run!r} in '
            f'...{blanked[max(0, start - 14):end + 14].strip()}... , which is '
            'a measurement of a coverage run rather than a figure the tree '
            'proves; it belongs in the coverage step summary')
    assert 'coverage step summary' in text, (
        'the run-only figures must point at the coverage step summary')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='covprose_')


if __name__ == '__main__':
    raise SystemExit(main())
