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
dot, and a run joined to a word by a hyphen in EITHER direction -- `SHA-256`
before the run, `N-line` after it. Only the first of those four never reaches
the scan as a run at all; the other three it reads and then lets past, which
is a weaker promise than invisibility and the honest one to make. So
`3.5 seconds` is as unchecked as `SHA-256`, and a maintainer must not read
the familiar-looking one as the boundary.

The hyphen rule is there for the `N-line` figure a future editor would add.
The cost is that a hyphen-joined figure is admitted whatever it is, and the
position rule cannot catch it, because the scan never gets that far.

The case docstring repeats this rather than claiming completeness the scan
does not have. This is deliberate: the alternative is a natural-language
parser, and a prose gate is worth more when its own limits are written down
than when it pretends to have none.

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
    """`0 of N code lines` — the N is that file's own physical code lines,
    and it moves the moment the file does. No figure is copied into this
    line: a hand-copied count in the guard's own documentation drifts
    exactly as the one in the prose did."""
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


def _claim_subject(text, said, count):
    """What the claim says about, between the claim phrase and the figure.

    The module is the subject the claim is made about, and the prose puts it
    between "no suite reaches" and the count. Bounding the sentence instead
    would red a rewrap that merely split the claim in two, which is the same
    false positive as a rewrap anywhere else.

    A wording that states the figure FIRST has no such span, and slicing
    backwards yields an empty string, which the assertion below then reports
    as a module that is not there. So that order falls back to the whole
    paragraph, and the binding is positional only when the figure follows the
    claim phrase.
    """
    if said.end() < count.start():
        return text[said.end():count.start()]
    return text


def test_the_paragraph_still_claims_the_unreached_module(tmp):
    """The qualitative claim is what the paragraph is for. The figure beside
    it is decoration on it, so a gate that only checked the figure would pass
    on a paragraph that had quietly stopped claiming anything.

    The three things the claim needs are checked together rather than
    separately: the phrase, the module, and the module AS THE SUBJECT
    BETWEEN THE PHRASE AND THE FIGURE. Held apart they are three presences
    a rewrap can separate, and a paragraph that passes all three while
    claiming a different module is false rather than vague.
    """
    del tmp
    text = _paragraph()
    said = re.search(r'no\s+suite\s+reaches', text)
    assert said, 'the paragraph no longer claims the module is unreached'
    assert UNREACHED in text, 'the paragraph no longer names the module'
    count = re.search(COUNT_PHRASE, text)
    assert count, 'the paragraph no longer states the module count'
    subject = _claim_subject(text, said, count)
    assert UNREACHED in subject, (
        f'the claim names a different module as the one no suite reaches: '
        f'...{" ".join(subject.split())}... , so {UNREACHED} is named '
        f'somewhere in the paragraph but is not the module the claim is about')


def _digit_runs(text):
    """Every maximal digit run, with where it sits, for the figure scan."""
    return [(m.group(0), m.start(), m.end())
            for m in re.finditer(r'\d+(?:\.\d+)?', text)]


def _part_of_a_token(text, start, end, run):
    """True when a digit run belongs to a name or a version, not a figure.

    A run is a token's own when it touches a letter, a digit or an
    underscore (`V8`, `base64`, `NODE_V8_COVERAGE`), when it carries its own
    dot (`Python 3.13`), or when a hyphen runs into it (`SHA-256`). A hyphen
    running the other way is the same token only when a word follows it --
    `51-line` -- because `40-11` and `10-20` put an operator between two
    figures and the scan has to keep reading those. Those are spellings of
    something else, and the paragraph is allowed to name things.

    This is an EXEMPTION list, not a description of what the scan can read.
    The scan reads digit form: a figure spelled in words is invisible to it,
    and so is one written with a thousands separator in a way that splits it
    into runs it will judge separately. The case docstring says so, because a
    gate that reads as complete and is not will be trusted past its reach.
    """
    before = text[start - 1] if start else ' '
    after = text[end] if end < len(text) else ''
    if before.isalnum() or before == '_':
        return True
    if after.isalnum() or after == '_':
        return True
    if '.' in run or before in '.-':
        return True
    # A hyphen running OUT of the run, but only into a word. `51-line` is one
    # token the figure belongs to, and it is the likeliest figure this
    # paragraph could gain; `40-11` and `10-20` put an operator between two
    # figures, which the scan has to keep reading. The character after the
    # hyphen is the whole of the difference.
    return after == '-' and text[end + 1:end + 2].isalpha()


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


def test_no_claim_the_pattern_can_spell_carries_a_figure_beyond_the_two(tmp):
    """The pattern's own capacity, which no other control measures.

    The rule and the refusal message read one match of one constant, so a
    single widening re-admits a tree-wide figure in both at once -- and with
    the prose restated to match, the suite is green with the very figure
    this branch exists to refuse. Nothing else here would notice: every
    other control reads the paragraph as shipped, and the shipped paragraph
    states no denominator.

    So this drives the PATTERN and asks what a claim carrying extra figures
    would let it admit, and the answer has to stay the two figures the
    claim makes -- the 0 and the count -- because every other digit in
    those sentences is one the tree proves about a different subject. A
    pattern that simply refuses them is passing: refusing is the point. The
    fixture is a family of phrases, not a copy of the paragraph, because a
    fixture that restated the pattern could not catch a change to the
    pattern, which is the whole of what is being pinned.
    """
    del tmp
    said = re.search(COUNT_PHRASE, _paragraph())
    assert said, 'the paragraph no longer states the module count'
    count = said.group(1)
    total = sum(len(code_lines(text, rel))
                for rel, text in tracked_sources(ROOT).items())
    admits = {'0', count}
    greedy = [
        f'at 0 of {count} of {total} code lines',
        f'at 0 of {count} and {total} code lines',
        f'at 0 of {count} of {total} of {total} code lines',
        f'at 0 of {count} code lines of {total}',
    ]
    for phrase in greedy:
        matched = re.search(COUNT_PHRASE, phrase)
        if matched is None:
            continue
        figures = {run for _, _, run in _claim_figures(matched.group(0),
                                                       matched.start())}
        assert figures <= admits, (
            f'the count phrase admits {sorted(figures)} from a claim '
            f'that also carries the tree-wide total {total}, where it may '
            f'state only {sorted(admits)}. The paragraph can then restate '
            f'the denominator and this suite calls it true')


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
    claim = ' '.join(said_count.group(0).split())
    claims = len(re.findall(COUNT_PHRASE, text))

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
            f'by the claim it sits in; the rule admits the figures of one '
            f'claim -- `{claim}` -- of the {claims} the paragraph states, '
            f'and within it the count is the tree\'s while the 0 is the '
            f'claim itself, permitted rather than derived. A figure anywhere '
            f'else is refused at every value, including one that happens to '
            f'equal the {count} code lines of {UNREACHED}. A count measured '
            f'by a coverage run, and a denominator over every tracked '
            f'shipped JavaScript file, both belong in the coverage step '
            f'summary that run prints')

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
