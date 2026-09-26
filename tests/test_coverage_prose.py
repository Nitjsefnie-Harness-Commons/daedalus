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
    """The coverage paragraph alone, so a number elsewhere cannot satisfy it."""
    text = _prose()
    start = text.index('Coverage is two numbers')
    return text[start:]


def test_the_unreached_module_line_count_is_the_trees(tmp):
    """`0 of 51 code lines` — the 51 is that file's own physical code lines,
    and it moves the moment the file does."""
    del tmp
    said = re.search(r'at 0 of (\d+) code\s+lines', _paragraph())
    assert said, 'the paragraph no longer states the unreached module count'
    source = (ROOT / UNREACHED).read_text(encoding='utf-8')
    assert int(said.group(1)) == len(code_lines(source, UNREACHED)), (
        'CONTRIBUTING.md says %s of %s code lines; the file has %d'
        % (said.group(1), UNREACHED, len(code_lines(source, UNREACHED))))


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
        'CONTRIBUTING.md says the number is measured over %s; the tree has %d'
        % (said.group(1), total))
    assert UNREACHED in sources, (
        '%s is not in the measured population' % UNREACHED)


def test_the_paragraph_still_claims_the_unreached_module(tmp):
    """The qualitative claim is what the paragraph is for. The two numbers
    are decoration on it, so a gate that only checked the numbers would pass
    on a paragraph that had quietly stopped claiming anything."""
    del tmp
    text = _paragraph()
    assert UNREACHED in text, 'the paragraph no longer names the module'
    assert 'no suite reaches' in text, (
        'the paragraph no longer claims the module is unreached')


def test_no_run_only_figure_is_restated_in_prose(tmp):
    """The covered count and the total need a full coverage run, so they
    belong to that run's step summary rather than to this file.

    The one percentage the paragraph may carry is the derivable one: `about
    1%` is the unreached module's share of the denominator, and both of those
    are checked above, so it cannot rot on its own. Any other percentage is a
    measurement of a run, and this suite would not be able to check it — which
    is how the total sat wrong here twice without a gate noticing.
    """
    del tmp
    text = _paragraph()
    for said in re.finditer(r'\d+(?:\.\d+)?%', text):
        assert text[max(0, said.start() - 6):said.start()].endswith('about '), (
            'the paragraph restates %r, which only a coverage run can measure '
            'and nothing here can check' % said.group(0))
    assert 'the total is' not in text, (
        'the coverage total is a measurement of a run, not a fact about the '
        'tree, and it must not be restated here')
    assert 'coverage step summary' in text, (
        'the run-only figures must point at the coverage step summary')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='covprose_')


if __name__ == '__main__':
    raise SystemExit(main())
