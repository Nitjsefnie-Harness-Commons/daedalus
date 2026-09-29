#!/usr/bin/env python3
"""The JavaScript-coverage figures CONTRIBUTING.md states in prose.

The paragraph in `CONTRIBUTING.md` that names the one shipped module the
coverage run does not reach carries one figure: that module's own code-line
count, beside the `0` the run reports for it. The count is a function of
that one file and is derived here, in one command, with no suite run and no
coverage data. The `0` is not, and cannot be: whether a suite executes the
module is decidable only from V8 output. The phrase's shape is what holds
the two apart, naming the `0` and the code-line count as separate parts of
one claim, so each is admitted for being part of that claim and not for
being a number in a set.

So there is no reach CHECK here, and the reason is structural rather than a
budget. Setting `NODE_V8_COVERAGE` means every suite that launches Node, and
they reach the interpreter through launch primitives spread across the
shared harness helpers, so no single chokepoint can carry the flag for them.
A static scan cannot stand in either, because the reach set is not decidable
from the tests tree's source: `tests/_worker_sources.py` parses the shipped
`extension/background.js` for its own `importScripts(...)` call and loads
every worker module that call names, and `tests/_dashshell.py`
imports a dashboard module through an ES `import()` of a path that arrives
only as an argv string. Both execute shipped JavaScript whose path is in no
suite, so a scan asking which modules the suites name reports those as
unreached and reds against correct code. `tests/test_vm_file_load_guard.py`
does not close that gap: it fixes the SPELLING of each `vm.runInContext`
site, so a `readdirSync` loop that builds its path and then loads it
canonically passes it, and a load never spelled as a call site is outside
its discovery entirely. `scripts/ci/js_coverage.py` resolves the reach set
properly, from the V8 records, in the coverage job -- so the paragraph
attributes the figure to the coverage step summary that run prints, and the
control below holds that attribution rather than the truth of a claim no
gate in this repository can check.

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
position check never sees it, because the exemption is applied first.

The case docstring repeats this rather than claiming completeness the scan
does not have. This is deliberate: the alternative is a natural-language
parser, and a prose gate is worth more when its own limits are written down
than when it pretends to have none.

The population is `js_coverage.tracked_sources` rather than a second copy of
its rule, so this suite cannot drift from the definition it checks against:
if the report's meaning of "shipped JavaScript" moves, both move together.

What the attribution check reads is the CLAUSE, and what it demands of it
is a referent, not a phrase: the clause must name a figure, and the figure it
names must not be the code-line count. Naming the report is not enough, since
a clause may name it while attributing nothing, and a clause that credits the
count attributes the one figure here the tree proves. That is a referent
test with a stated vocabulary, so it is written down here: a figure is named
by a digit run or by the word `figure`, and the count is named by a digit run
equal to it or by `count` / `code line`. A correct rewording that reaches the
run-only figure by some other noun is outside that vocabulary and reds.
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
        f'so CONTRIBUTING.md cannot make it the module its unreached-module '
        f'claim is about')


# The reach predicates a claim can be spelled with, so the check below judges
# a family of wordings rather than the one this branch replaced. Case is not
# part of a wording: restating the claim as its own sentence capitalises its
# first word, which is the most ordinary way to write the claim at all, and a
# case-sensitive family would miss exactly that. What it does not read is in
# the module docstring, as always.
REACH_CLAIM = re.compile(
    r'\bno\s+(?:suite|suites|test|tests)\s+'
    r'(?:reach(?:es)?|execute[sd]?|runs?)\b'
    r'|\bdoes\s+not\s+(?:reach|execute|run)\b'
    r'|\bnot\s+(?:reached|executed|covered)\b'
    r'|\bunreached\b', re.IGNORECASE)
# The report a run-only figure belongs to, spelled as the figure-admission
# control below already spells it, so one phrase carries both.
RUN_ATTRIBUTION = re.compile(r'coverage\s+step\s+summary')
# A sentence ends at a full stop, a bang or a question mark followed by
# whitespace, with nothing required of what comes next. A dot inside a
# filename is followed by a letter, so `.py` and `.js` stay whole; a capital
# was once required, which made the splitter fail permissively — a sentence
# beginning with anything else merged into its predecessor, and a merged
# claim swallowed the attribution that followed it. Failing permissively is
# the wrong direction for a check whose absence half must not go vacuous.
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
# A clause ends where the paragraph already ends one. Newline is NOT a
# boundary: the text is hand-wrapped, and a clause split across a line break
# is one clause.
CLAUSE_END = re.compile(r'[;:,—–]')
# The attribution is a relation, so the clause is read for what it credits.
# Naming the report is not crediting anything to it, and crediting the count
# is misattributing the one figure here the tree proves. The vocabulary both
# tests use is in the module docstring.
FIGURE = re.compile(r'\bfigure\b|\d+')
COUNT_NAME = re.compile(r'\bcode[ -]lines?\b|\bcount\b', re.IGNORECASE)


def _sentences(text):
    return SENTENCE_END.split(text)


def _attribution_clause(sentence):
    """The one clause of a sentence that names the run's report, or None.

    A clause and not the sentence, because a sentence may state the count and
    then credit a figure, and reading the sentence would put the count inside
    the credit.
    """
    for clause in CLAUSE_END.split(sentence):
        if RUN_ATTRIBUTION.search(clause):
            return clause
    return None


def test_no_reach_claim_stands_without_the_run_that_measures_it(tmp):
    """The claim is the coverage run's, and the paragraph must say so.

    The wording this replaced asserted on the paragraph's own word that no
    suite reaches the module, and the only guard beside it read the words as
    present. So a suite that began executing the module, or a second shipped
    module that stopped being reached, made the paragraph false and left
    this suite green: a presence check over a reach claim, which is the one
    claim in the tree nothing here can settle.

    Every assertion is over a SENTENCE, which is where a claim lives, so the
    oracle half comes first: with no claim to judge, the checks below pass
    over an empty paragraph, and that is the pass that reads as a green.

    Each claim then owes three things. It names the module, because a
    paragraph can name it somewhere else and claim a different one here. It
    carries the attribution, in its own sentence or the one immediately after
    it, so an ordinary two-sentence rendering is not punished for splitting
    a claim. And the clause carrying it credits a figure rather than the
    report, and that figure is not the count.

    What this does NOT read: a claim spelled as none of the predicates above,
    a run named as anything other than the coverage step summary, and a
    referent outside the vocabulary the module docstring states.
    """
    del tmp
    text = _paragraph()
    assert UNREACHED in text, 'the paragraph no longer names the module'
    assert re.search(COUNT_PHRASE, text), (
        'the paragraph no longer states the module count, so the run has no '
        'figure to report and the attributions checked below have none to '
        'carry')
    sentences = _sentences(text)
    claims = [(i, s) for i, s in enumerate(sentences)
              if REACH_CLAIM.search(s)]
    assert claims, (
        'the paragraph no longer claims the module is unreached, so every '
        'assertion below is judging nothing and the count phrase beside it '
        'has nothing to count')
    assert RUN_ATTRIBUTION.search(text), (
        'the paragraph no longer names the coverage step summary, so the '
        'figure it attributes there is attributed nowhere')
    for index, claim in claims:
        where = f'...{" ".join(claim.split())}...'
        assert UNREACHED in claim, (
            f'the sentence claiming a reach is about another module: {where}. '
            f'{UNREACHED} is named elsewhere in the paragraph, so the claim '
            f'and the module it is about are not the same sentence')
        clause = _attribution_clause(claim)
        if clause is None and index + 1 < len(sentences):
            clause = _attribution_clause(sentences[index + 1])
        assert clause, (
            f'a reach claim carries no attribution in its own sentence or the '
            f'one after it: {where}. Whether a suite executes {UNREACHED} is '
            f'decidable only from the coverage run, so the claim must name '
            f'the report that measures it')
        assert not COUNT_NAME.search(clause), (
            f'the attribution credits the code-line count to the coverage '
            f'run: ...{" ".join(clause.split())}... . That count is the '
            f'tree\'s, read off the file by this suite, and it is the one '
            f'figure here no run reports')
        assert FIGURE.search(clause), (
            f'the attribution names the report but credits it with no figure: '
            f'...{" ".join(clause.split())}... . A report named beside the '
            f'claim is not an attribution of the figure the claim rests on')


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
        f'at 0 of {count} code lines, of {total} in the tree',
        f'at 0 of {count} of the {total} code lines',
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
    assert RUN_ATTRIBUTION.search(text), (
        'the run-only figures must point at the coverage step summary')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='covprose_')


if __name__ == '__main__':
    raise SystemExit(main())
