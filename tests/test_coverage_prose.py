#!/usr/bin/env python3
"""The JavaScript-coverage figures CONTRIBUTING.md states in prose.

The paragraph in `CONTRIBUTING.md` that names the one shipped module the
coverage run does not reach carries one figure: that module's own code-line
count, beside the `0` the run reports for it. The count is a function of
that one file and is derived here, in one command, with no suite run and no
coverage data. The `0` is not, and cannot be: whether a suite executes the
module is decidable only from V8 output. The phrase's shape is what holds
the two apart, so each is admitted for being part of one claim and not for
being a number in a set.

So there is no reach CHECK here, and the reason is structural rather than a
budget. `NODE_V8_COVERAGE` reaches every suite that launches Node, and they
launch through primitives spread across the shared harness helpers, so no
single chokepoint carries the flag for them. A static scan cannot stand in
either, because the reach set is not decidable from the tests tree's
source: `tests/_worker_sources.py` parses the shipped
`extension/background.js` for its own `importScripts(...)` call and loads
every worker module that call names, and `tests/_dashshell.py` imports a
dashboard module through an ES `import()` of a path that arrives only as an
argv string. Both execute shipped JavaScript whose path is in no suite, so
a scan asking which modules the suites name reports those as unreached and
reds against correct code. `tests/test_vm_file_load_guard.py` does not
close that gap: it fixes the SPELLING of each `vm.runInContext` site, so a
`readdirSync` loop that builds its path and then loads it canonically
passes it, and a load never spelled as a call site is outside its discovery
entirely. `scripts/ci/js_coverage.py` resolves the reach set properly, from
the V8 records, in the coverage job -- so the paragraph attributes the
figure to the coverage step summary that run prints, and the control below
holds that attribution rather than the truth of a claim no gate in this
repository can check.

The figures the paragraph used to carry and no longer does are refused
rather than quietly forgotten, because a refused figure is what stops the
next one being written. A covered count is a measurement of one run, so no
gate here can hold it. A denominator over every tracked shipped JavaScript
file IS a function of the tree, and that is exactly why it is refused here:
it moves whenever any other shipped file grows, for reasons that have
nothing to do with the module this paragraph is about, so two pull requests
that each ship JavaScript leave it behind between them. The percentage
derived from such a denominator is refused for the same reason.
Admissibility is therefore a POSITION and not a value: the figures the
paragraph may state are the ones the count phrase itself carries, so an
unrelated figure is refused wherever it appears, at every value, including
one that happens to equal the count.

The module the paragraph names is also checked against the population.

The scan reads DIGIT FORM, and its exemptions are by SHAPE rather than by
example, so the shapes are what is written down here: a figure written in
words, a run that touches a letter or an underscore, a run carrying its own
dot, and a run joined to a word by a hyphen in EITHER direction -- `SHA-256`
before the run, `N-line` after it. Only the first of those four never reaches
the scan as a run at all; the other three it reads and then lets past, which
is a weaker promise than invisibility and the honest one to make. So
`3.5 seconds` is as unchecked as `SHA-256`, and a maintainer must not read
the familiar-looking one as the boundary. The cost of the `N-line` exemption
is that a hyphen-joined figure is admitted whatever it is, and the position
check never sees it, because the exemption is applied first.

The population is `js_coverage.tracked_sources` rather than a second copy of
its rule, so this suite cannot drift from the definition it checks against:
if the report's meaning of "shipped JavaScript" moves, both move together.

Where a claim is judged is a WINDOW, and it is stated here with its
direction because a boundary stated without one reads wider than it is. A
reach claim is looked for in the count phrase's sentence and in the
sentence immediately on EITHER side of it, so a rendering that writes the
figure before the claim is judged exactly as one that writes it after. A
one-sided window was the defect this replaced, and it made the control
refuse the paragraph's own claim the moment an editor split its sentence
into a first half ending `...is the extension options page.` and a second
beginning `It stands at 0 of 51 code lines`, with the claim stranded one
sentence ahead of its own figure. Further than a single neighbour is NOT
judged, whatever it names.

A window is not a predicate, though, and position was the wrong axis for
one: a general coverage sentence and a claim about another module are the
same thing to a position-based rule, no width of window separates them,
and that is why three successive widenings of this one each pulled a new
false alarm in from the other side. Within the window a sentence is
judged on TWO conditions, and failing either leaves it ADMITTED: it
carries a reach PREDICATE, and it NAMES A SHIPPED MODULE. At least one
judged sentence must then name the named module. A claim names a shipped
module in any of the forms this tree uses -- the full repo-relative path,
the bare filename, or the shipped root the path sits in -- and all three
are read off the same population the coverage report means by "shipped
JavaScript", so a root added later is judged without a change here. Both
conditions carry weight in the paragraph as shipped: the long sentence
above names a dozen shipped modules and is admitted because it carries no
predicate, and `no suite reaches the tree on its own` carries one and is
admitted because it names no module. What makes skipping such a sentence
safe is the ORACLE: a window holding no sentence that names the named
module is a refusal, not a pass, and a window holding only a predicate
about a different module is that same refusal, so nothing can be dropped
from judgement and leave the paragraph unchecked.

The price of that rule falls on BOTH sides, and the far side has paid it
since before the window was symmetric: on the wave-2 base every reach
predicate in the paragraph was judged, so a general coverage sentence one
sentence after the count phrase was refused. What changed is that it is
no longer refused for naming no module -- which the prose does not assert
-- and no longer refused at all.

What is left, after this rule: a reach-predicate sentence naming a
shipped module OTHER than the named module, sitting more than one
sentence from the count phrase, is admitted. It was measured to be
admitted, not merely suspected. Closing it is not a matter of reading
the paragraph harder, because whether that other module is in fact
unreached is not decidable from this tree either, for the reason at the
top of this docstring. The honest name for the residual is a claim this
control does not govern. The shapes to recognise it in are the shortest
spelling -- `No suite runs `sse.js`.` -- a shipped root -- `No suite
reaches `extension/`.` -- and a full path -- `No suite runs
`extension/worker/tabs.js`.`

No natural-language understanding is owed here, and the earlier claim
that it was was measured false. `No suite runs `dashboard/app.js`.` and
`No suite runs `dashboard/app.js` either.` were driven across the offsets
-2, -1, +1, +2 and +3 from the count phrase, on both spellings: each pair
takes the same verdict at each one -- red inside the window, admitted
outside it -- and the anaphor changes no cell. An earlier version of
this docstring reported the opposite and told a maintainer the innocent
and defective sentences were in irreducible tension. They were not; the
discriminator is distance and whether the sentence names a shipped path,
and both are read here.

The attribution is read off the claim sentence with the count phrase cut out
of it, and naming the report is not enough: a sentence may name it while
attributing nothing, and a sentence that credits the count attributes the
one figure here the tree proves.

The cut is by SPAN and not at clause boundaries, because no clause boundary
sits between the count and the credit: `at 0 of 51 code lines -- the
coverage step summary, which the run prints, records the leading figure` is
ONE attribution whose subject is an appositive spanning three
comma-delimited clauses. Reading clause by clause refused that rewording
for a figure stated one clause away -- the shape the shipped paragraph
itself uses.

The count is still looked for in the clauses that NAME the report, and that
asymmetry is the point. A misattribution is stated in those words; a credit
need not be. So `at 0 of 51 code lines, and the count is the tree's, which
the coverage step summary does not report; that summary reports the leading
figure` is admitted, because the count it names is the sentence's own
subject matter and not something handed to the run. Drop the trailing
credit and that same sentence is refused -- by the figure assertion and not
by this one, since it disclaims the count and then credits the report with
nothing, which leaves the `0` standing on no report at all.

That is a referent test with a stated vocabulary, so it is written down here: a
figure is named by a digit run or by the word `figure`, and the count is named
by `count` / `code line`. A digit run equal to the count is NOT part of that
vocabulary: the credit check reads the noun, so a rewording that credits the
run with the count by its VALUE is caught by the figure scan instead, which is
the control that judges figures by their position. A correct rewording that
reaches the run-only figure by some other noun is outside the vocabulary and
reds.
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
# hand-wrapped, and a rewrap of a correct figure is not a change to it. Case is
# not part of the phrase either, for the reason the reach family below states:
# an editor who moves the phrase to the front of a sentence capitalises it.
# Compiled rather than spelled twice over, so every reader of the paragraph
# takes that case rule with it.
COUNT_PHRASE = re.compile(r'at\s+0\s+of\s+(\d+)\s+code\s+lines', re.IGNORECASE)


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
    said = COUNT_PHRASE.search(_paragraph())
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
    not a population.
    """
    del tmp
    sources = tracked_sources(ROOT)
    assert UNREACHED in sources, (
        f'{UNREACHED} is not in the population of tracked shipped JavaScript, '
        f'so CONTRIBUTING.md cannot make it the module its unreached-module '
        f'claim is about')


# A family of wordings rather than the one this branch replaced, so the check
# judges a restatement however it is spelled. Case is not part of a wording:
# restating the claim as its own sentence capitalises its first word, which is
# the most ordinary way to write the claim at all. What it does not read is in
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
# A dot inside a filename is followed by a letter, so `.py` and `.js` stay
# whole. A capital after the dot was once required, which made the splitter
# fail permissively — a sentence beginning with anything else merged into its
# predecessor, and a merged claim swallowed the attribution that followed it.
# Failing permissively is the wrong direction for a check whose absence half
# must not go vacuous.
SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
# Newline is NOT a boundary: the text is hand-wrapped, and a clause split
# across a line break is one clause.
CLAUSE_END = re.compile(r'[;:,—–]')
# The vocabulary both tests read is in the module docstring.
FIGURE = re.compile(r'\bfigure\b|\d+')
COUNT_NAME = re.compile(r'\bcode[ -]lines?\b|\bcount\b', re.IGNORECASE)


def _sentences(text):
    return SENTENCE_END.split(text)


def _credit(sentence):
    """What a claim says about the report, with the count taken out of it.

    A SPAN and not a clause, for the reason in the module docstring: the
    count and the credit are two things one sentence says, and no clause
    boundary sits between them. The phrase is cut out where it stands and the
    rest is read whole, which keeps the count out of the credit without
    cutting the credit short.
    """
    said = COUNT_PHRASE.search(sentence)
    if said is None:
        return sentence
    return sentence[:said.start()] + sentence[said.end():]


def _reported(sentence):
    """The clauses of a sentence that name the run's report.

    Where a MISATTRIBUTION is looked for, and deliberately narrower than
    `_credit`, which is why this reads on clause boundaries and that one does
    not. A sentence may say the count is the tree's and credit the run with a
    different figure, and that is correct prose: a misattribution is STATED in
    the words that name the report, while a credit may sit in a clause that
    does not.
    """
    return ' '.join(clause for clause in CLAUSE_END.split(sentence)
                    if RUN_ATTRIBUTION.search(clause))


def _module_names(sources):
    """Every spelling of a shipped module the tree itself writes.

    Three forms, all read off the one population: the full repo-relative
    path, the bare filename, and the shipped root the path sits in. A
    predicate naming a module in one of the three and not the others says
    the same thing, so a rule reading only the full path judged fewer
    claims than the position rule it replaced.
    """
    names = set(sources)
    for rel in sources:
        root, _, leaf = rel.rpartition('/')
        if root:
            names.add(f'{root}/')
        names.add(leaf)
    return names


def _reach_claims(sentences):
    """Every reach claim this paragraph makes about its own module, paired
    with where it sits so the attribution can look at the sentence after it.

    A window sentence is judged on TWO conditions, and both are load-bearing.
    It carries a reach predicate, and it NAMES A SHIPPED MODULE.
    Position alone was the wrong axis: a general coverage sentence and a
    claim about a different module were the same thing to it, and no width
    of window separated them, which is why each widening of the window
    pulled a new false alarm in from the other side. The population is
    `tracked_sources`, the same one the report means by "shipped
    JavaScript", so this file cannot drift from the definition.

    Skipping a predicate that names no shipped module is safe only because
    of the ORACLE in the test: a window left with no sentence naming this
    paragraph's module is a refusal, not a pass. That assertion is the
    property to test deliberately, and the module docstring says what it
    leaves open.
    """
    sources = tracked_sources(ROOT)
    names = _module_names(sources)
    pairs = []
    for index, sentence in enumerate(sentences):
        if not COUNT_PHRASE.search(sentence):
            continue
        window = range(max(0, index - 1), min(index + 2, len(sentences)))
        for position in window:
            claim = sentences[position]
            if not REACH_CLAIM.search(claim):
                continue
            if not any(name in claim for name in names):
                continue
            pairs.append((position, claim))
    return pairs


def test_no_reach_claim_stands_without_the_run_that_measures_it(tmp):
    """The claim is the coverage run's, and the paragraph must say so.

    The wording this replaced asserted on the paragraph's own word that no
    suite reaches the module, and the only guard beside it read the words as
    present. So a suite that began executing the module, or a second shipped
    module that stopped being reached, made the paragraph false and left
    this suite green — a presence check over the one claim in the tree that
    nothing here can settle.

    Every assertion is over a SENTENCE, which is where a claim lives, so the
    oracle half comes first: with no claim to judge, the checks below pass
    over an empty paragraph, and that is the pass that reads as a green. The
    sentences that carry one are the count phrase's and its immediate
    neighbour on either side, so a rendering that writes the figure before
    the claim is judged as one that writes it after, and one that NAMES A
    SHIPPED MODULE. A window whose only predicate is a general
    coverage sentence, and one whose only predicate names a different
    shipped module, are both refusals: neither leaves this paragraph's own
    claim unchecked, which is the property that makes it safe to read a
    predicate naming no shipped module as prose. A predicate match further
    than a single neighbour is outside the window and is admitted whether
    or not it names a shipped module; the residual that leaves is named in
    the module docstring, with what it would take to close.

    Each claim then owes three things. It names the module, because a
    paragraph can name it somewhere else and claim a different one here. It
    carries the attribution, in its own sentence or the one immediately after
    it, so an ordinary two-sentence rendering is not punished for splitting
    a claim. And what it says with the count phrase cut out names the report
    and names a figure, while the clauses naming the report do not name the
    count.

    What this does NOT read: a claim spelled as none of the predicates above,
    a reach claim more than one sentence from the count phrase's on either
    side, a run named as anything other than the coverage step summary, and a
    referent outside the vocabulary the module docstring states.
    """
    del tmp
    text = _paragraph()
    assert UNREACHED in text, 'the paragraph no longer names the module'
    assert COUNT_PHRASE.search(text), (
        'the paragraph no longer states the module count, so the run has no '
        'figure to report and the attributions checked below have none to '
        'carry')
    sentences = _sentences(text)
    claims = _reach_claims(sentences)
    assert any(UNREACHED in claim for _, claim in claims), (
        f'the window around the count phrase holds no reach claim naming '
        f'{UNREACHED}, so every assertion below is judging a claim that is '
        f'not this paragraph\'s, or is judging nothing at all, and the count '
        f'phrase has no claim resting on it. A window whose only predicate '
        f'is a general coverage sentence, and one whose only predicate '
        f'names a different shipped module, are both this refusal: neither '
        f'is a paragraph whose own claim went unchecked')
    assert RUN_ATTRIBUTION.search(text), (
        'the paragraph no longer names the coverage step summary, so the '
        'figure it attributes there is attributed nowhere')
    for index, claim in claims:
        where = f'...{" ".join(claim.split())}...'
        assert UNREACHED in claim, (
            f'the sentence claiming a reach is about another module: {where}. '
            f'{UNREACHED} is named elsewhere in the paragraph, so the claim '
            f'and the module it is about are not the same sentence')
        credit = _credit(claim)
        reported = _reported(claim)
        if not RUN_ATTRIBUTION.search(credit) and index + 1 < len(sentences):
            credit = _credit(sentences[index + 1])
            reported = _reported(sentences[index + 1])
        assert RUN_ATTRIBUTION.search(credit), (
            f'a reach claim carries no attribution in its own sentence or the '
            f'one after it: {where}. Whether a suite executes {UNREACHED} is '
            f'decidable only from the coverage run, so the claim must name '
            f'the report that measures it')
        assert not COUNT_NAME.search(reported), (
            f'the attribution credits the code-line count to the coverage '
            f'run: ...{" ".join(reported.split())}... . That count is the '
            f'tree\'s, read off the file by this suite, and it is the one '
            f'figure here no run reports')
        assert FIGURE.search(credit), (
            f'the attribution names the report but credits it with no figure: '
            f'...{" ".join(credit.split())}... . A report named beside the '
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
    # The character after the hyphen is the whole of the difference: `51-line`
    # is one token the figure belongs to, `40-11` is an operator between two
    # figures the scan has to keep reading.
    return after == '-' and text[end + 1:end + 2].isalpha()


def _claim_figures(phrase, offset):
    """The digit runs one claim asserts, as absolute spans plus their text.

    Admissibility is POSITIONAL, so the rule and the refusal message both
    read this one list: the rule tests a scanned run's span against the spans
    returned here, and the message prints the runs returned here. A message
    that spelled the admissible figures out itself would be a second copy of
    the rule, and the copy is what drifts -- a message naming "0 and the
    count" once outlived the set it described.
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
    said = COUNT_PHRASE.search(_paragraph())
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
        matched = COUNT_PHRASE.search(phrase)
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
    that phrase itself carries: the bare `0`, PERMITTED rather than derived
    because it is the claim that the module is unreached, and the code-line
    count, which the case above derives. That is what refuses an unrelated
    figure colliding with the count, wherever it appears and at whatever
    value.

    A percentage is refused outright and the paragraph is required to state
    none, because a share is provable only against a base it states and the
    only base that would make one checkable is the tree-wide count.

    What this does NOT read is in the module docstring: the scan is digit
    form, so a figure spelled in words passes, and so does a percentage
    spelled in words.
    """
    del tmp
    text = _paragraph()
    said_count = COUNT_PHRASE.search(text)
    assert said_count, 'the paragraph no longer states the module count'
    count = int(said_count.group(1))
    figures = _claim_figures(said_count.group(0), said_count.start())
    spans = {(start, end) for start, end, _ in figures}
    claim = ' '.join(said_count.group(0).split())
    claims = len(COUNT_PHRASE.findall(text))

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
