#!/usr/bin/env python3
"""The JavaScript-coverage figures CONTRIBUTING.md states in prose.

The paragraph in `CONTRIBUTING.md` that names the one shipped module no
suite reaches carries one figure: that module's own code-line count, beside
the `0` that is the claim of being unreached. The count is a function of
that one file and is derived here, in one command, with no suite run and no
coverage data. The `0` is PERMITTED, not derived: whether a suite executes
the module is the claim itself, so the count phrase admits it on the
prose's word and no assertion here would fail if it were untrue. The
phrase's shape is what holds the two apart, naming the `0` and the code-line
count as separate parts of one claim, so each is admitted for being part
of that claim and not for being a number in a set. The coverage report is
what settles the claim, and #1245 tracks the gap between the two.

The figures the paragraph used to carry and no longer does are refused
rather than quietly forgotten, because a refused figure is what stops the
next one being written. A covered count is a measurement of one run, so no
gate here can hold it. A denominator over every tracked shipped JavaScript
file IS a function of the tree, and that is exactly why it is refused here:
it moves whenever any other shipped file grows, for reasons that have
nothing to do with the module this paragraph is about, so two pull requests
that each ship JavaScript leave it behind between them. The percentage
derived from such a denominator is refused for the same reason -- a share
whose base the paragraph does not state has nothing to prove it against.
Admissibility is therefore a POSITION and not a value: the figures the
paragraph may state are the ones the count phrase itself carries, so an
unrelated figure is refused wherever it appears, at every value, including
one that happens to equal the count.

What the paragraph calls a shipped module is also checked against the
population, so a file that is renamed, moved out of `extension/`, or stops
shipping as JavaScript reds here rather than leaving the prose describing a
shipped module the coverage report never sees.

The scan reads DIGIT FORM, and its exemptions are by SHAPE rather than by
example, so the shapes are what is written down here: a figure written in
words, a run that touches a letter or an underscore, a run carrying its own
dot, and a run a hyphen runs into. All four are outside what it sees -- so
`3.5 seconds` is as unchecked as `SHA-256`, and a maintainer must not read
the familiar-looking second one as the boundary. The case docstring repeats
this rather than claiming completeness the scan does not have. This is
deliberate: the alternative is a natural-language parser, and a prose gate is
worth more when its own limits are written down than when it pretends to have
none.

The population is `js_coverage.tracked_sources` rather than a second copy of
its rule, so this suite cannot drift from the definition it checks against:
if the report's meaning of "shipped JavaScript" moves, both move together.
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
# Every token boundary takes whitespace, not one space: the paragraph is
# hand-wrapped, and a rewrap of a correct figure is not a change to it.
COUNT_PHRASE = r'at\s+0\s+of\s+(\d+)\s+code\s+lines'


def _prose():
    return CONTRIBUTING.read_text(encoding='utf-8')


def _paragraph():
    """The coverage paragraph alone, bounded at BOTH ends.

    Without the upper bound the slice ran to end of file, so a correct
    unrelated sentence further down -- a percentage in House style, say --
    was read as a restatement and reded this suite with a message blaming
    the paragraph, and the next editor's move would be to delete correct
    prose. Both markers are sentences, so a reword of either is a loud
    failure rather than a silent change of scope.
    """
    text = _prose()
    start = text.index('Coverage is two numbers')
    return text[start:text.index('The matrix is not ceremony')]


def test_the_unreached_module_line_count_is_the_trees(tmp):
    """`0 of 51 code lines` — the 51 is that file's own physical code lines,
    and it moves the moment the file does."""
    del tmp
    said = re.search(COUNT_PHRASE, _paragraph())
    assert said, 'the paragraph no longer states the unreached module count'
    source = (ROOT / UNREACHED).read_text(encoding='utf-8')
    real = len(code_lines(source, UNREACHED))
    assert int(said.group(1)) == real, (
        f'CONTRIBUTING.md says {said.group(1)} of {UNREACHED} code lines; '
        f'the file has {real}')


def test_the_named_module_is_still_shipped_javascript(tmp):
    """`the one shipped module` — the population claim the denominator test
    also carried, and the one claim this paragraph has besides the count.

    Dropping the denominator must not drop this with it. A file that stops
    being tracked shipped JavaScript has not changed a line, so the count
    control still measures it and passes; a file that is gone as well makes
    that control raise rather than judge, which reports a missing file and
    not a population. This is the only control here that speaks to whether
    the coverage run still sees the module the prose is about.
    """
    del tmp
    sources = tracked_sources(ROOT)
    assert UNREACHED in sources, (
        f'{UNREACHED} is not in the population of tracked shipped JavaScript, '
        f'so CONTRIBUTING.md cannot call it the one shipped module no suite '
        f'reaches')


def test_the_paragraph_still_claims_the_unreached_module(tmp):
    """The qualitative claim is what the paragraph is for. The figure beside
    it is decoration on it, so a gate that only checked the figure would pass
    on a paragraph that had quietly stopped claiming anything."""
    del tmp
    text = _paragraph()
    assert UNREACHED in text, 'the paragraph no longer names the module'
    assert re.search(r'no\s+suite\s+reaches', text), (
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


def _claim_figures(phrase, offset):
    """The digit runs one claim asserts, as absolute spans plus their text.

    Admissibility is POSITIONAL, so the rule and the refusal message both
    read this one list: the rule tests a scanned run's span against the
    spans returned here, and the message prints the runs returned here. A
    message that spelled the admissible figures out itself would be a second
    copy of the rule, and the copy is what drifts -- a message naming "0 and
    the count" once outlived the set it described.
    """
    return [(offset + start, offset + end, run)
            for run, start, end in _digit_runs(phrase)]


def test_every_figure_in_the_paragraph_is_one_the_tree_proves(tmp):
    """The property, not a token: a figure may appear only if the claim it
    sits in is one the tree proves, so admissibility is a POSITION.

    The claim is the count phrase, and the figures it may state are the ones
    that phrase itself carries, each admitted for being part of that claim:
    the bare `0`, PERMITTED rather than derived because it is the claim that
    the module is unreached, and the code-line count, which the case above
    derives. That is what refuses an unrelated figure colliding with the
    count, wherever it appears and at whatever value.

    A percentage is refused outright and the paragraph is required to state
    none, because a share is provable only against a base it states and the
    only base that would make one checkable is the tree-wide count.

    What this does NOT read is in the module docstring: the scan is digit
    form, so a figure spelled in words passes, and so does a percentage
    spelled in words.
    """
    del tmp
    text = _paragraph()
    said_count = re.search(COUNT_PHRASE, text)
    assert said_count, 'the paragraph no longer states the module count'
    count = int(said_count.group(1))
    figures = _claim_figures(said_count.group(0), said_count.start())
    spans = {(start, end) for start, end, _ in figures}
    admits = ' and '.join(run for _, _, run in figures)
    claim = ' '.join(said_count.group(0).split())

    # Percentages are blanked first, so their digits are not also judged as
    # bare figures; they keep their offset either way.
    blanked = re.sub(r'\d+(?:\.\d+)?\s*%',
                     lambda m: ' ' * len(m.group(0)), text)
    for run, start, end in _digit_runs(blanked):
        if _part_of_a_token(blanked, start, end, run):
            continue
        assert (start, end) in spans, (
            f'the paragraph states {run!r} in '
            f'...{blanked[max(0, start - 14):end + 14].strip()}... , which is '
            f'not a figure this paragraph can prove. A figure is admissible '
            f'by the claim it sits in, and the only claim here the tree '
            f'checks is `{claim}`, whose figures are {admits}; an unrelated '
            f'figure is refused at every value, including one that happens '
            f'to equal the {count} code lines of {UNREACHED}. A count '
            f'measured by a coverage run, and a denominator over every '
            f'tracked shipped JavaScript file, both belong in the coverage '
            f'step summary that run prints')

    percents = re.findall(r'(\d+(?:\.\d+)?)\s*%', text)
    assert not percents, (
        f'the paragraph states {percents[0]}%, a share of a denominator it '
        f'does not state, so there is nothing here to prove it against. The '
        f'only denominator that would make it checkable is a count of every '
        f'shipped JavaScript file in the tree, which moves for reasons that '
        f'have nothing to do with {UNREACHED}; the run prints the share in '
        f'the coverage step summary')
    assert re.search(r'coverage\s+step\s+summary', text), (
        'the run-only figures must point at the coverage step summary')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='covprose_')


if __name__ == '__main__':
    raise SystemExit(main())
