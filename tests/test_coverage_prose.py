#!/usr/bin/env python3
"""The JavaScript coverage figures CONTRIBUTING.md states in prose.

The paragraph in `CONTRIBUTING.md` that names the one shipped module no
suite reaches carries two numbers: the unreached module's own code-line
count, and the total the measurement is taken over. Both are functions of
the tree — the first of one file, the second of every tracked shipped
JavaScript file — so both are derived here, in one command, with no suite
run and no coverage data.

The covered count and the percentage are NOT functions of the tree; they are
measurements of one run, and this suite therefore asserts that the prose does
not restate them. That split is the point of the case. The figures that can
be checked are checked here and go red the moment a shipped file grows; the
ones that cannot are pointed at the run's own summary rather than copied into
a file where nothing would notice them going stale.

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


def test_every_figure_in_the_paragraph_is_one_the_tree_proves(tmp):
    """The property, not a token: a figure may appear only if the tree
    proves it, so the test is the VALUE and never the wording around it.

    Two figures are provable here and are pinned by the cases above: the
    unreached module's own code-line count, and the denominator. The
    paragraph's one percentage must be computed FROM those two, which is why
    it is recomputed below and compared rather than matched against a
    phrasing -- `about 1%` is a rounding of 51/4943, and a rotated `about
    99%` is the same sentence with a different number in it.

    Every other figure in the paragraph is a measurement of a coverage run,
    which nothing in this repository can check without making that run. The
    covered count and the total are therefore banned as VALUES, so rewording
    around them does not slip past, and inserting one is caught however it is
    phrased. The bare `0` is permitted because it is not a measurement: it is
    the claim that the module is unreached, which is what the paragraph is
    for. The scan is bounded by identifier characters, so the `8` in `V8` and
    the one in `NODE_V8_COVERAGE` are read as part of a name rather than as
    figures the paragraph states.
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

    for said in re.findall(r'(\d+(?:\.\d+)?)\s*%', text):
        assert abs(float(said) - share) < 1, (
            f'the paragraph states {said}%, which is not the '
            f'{share:.2f}% the two figures above give')
    prose = re.sub(r'\d+(?:\.\d+)?\s*%', '', text)
    for said in re.findall(r'(?<![A-Za-z0-9_])(\d+)(?![A-Za-z0-9_])', prose):
        assert int(said) in (0, count, total), (
            f'the paragraph states {said}, which is a measurement of a '
            'coverage run rather than a figure the tree proves; it belongs '
            'in the coverage step summary')
    assert 'coverage step summary' in text, (
        'the run-only figures must point at the coverage step summary')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='covprose_')


if __name__ == '__main__':
    raise SystemExit(main())
