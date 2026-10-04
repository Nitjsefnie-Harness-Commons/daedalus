"""Read this repository's pins, and what Dependabot is told to do with them.

Not a suite itself — run_tests.py only loads `test_*.py`.

One question with three surfaces: where an action is pinned, where a CI tool
is pinned, and how dependabot.yml groups the first. Dependabot is the only
thing in this repository that ever moves any of them, and it reads manifest
and config files and never a workflow — so a pin in the wrong place is a pin
nobody is told to move, and a family Dependabot sees as several dependencies
arrives as several pull requests each of which can only be red.

Raw text rather than `_yamlread`, so the suite that tests that decoder does
not anchor its own fixture on the code under test.
"""
import fnmatch
import re

from _actionlint import _job_step
from _repo import ROOT
from _wfgraph import _job_section, _tests_yml
from _wffixtures import _audit_step
from _yamlread import job_mapping, step_scalar

# The keys Dependabot's group schema accepts. Anything else inside a group is
# a key this reader does not understand, and a reader that silently absorbed
# one would take the group it belongs to out of the compared set.
_GROUP_KEYS = ('applies-to', 'dependency-type', 'patterns')

# The `pip` TOOL, as one token: `pip`, a versioned one, one under a path,
# whatever stands in front of it. The boundaries keep a word that merely
# carries the letters out — `mypip` is another tool, and `pip-audit` is a
# different program sharing a prefix — and the tool is looked for wherever it
# stands rather than under a prefix spelled out ahead of it, so an assignment,
# a `sudo`, an interpreter or a path is not a shape this reader is taught
# one at a time. That prefix axis is where the pattern used to grow:
# `PIP_ROOT_USER_ACTION=ignore pip install` is the idiom GitHub's own pip
# setup documentation leads with, and anchoring on the line instead hid the
# install behind it.
_PIP_TOOL = re.compile(
    r'(?<![0-9A-Za-z._/-])(?:\S*/)?pip[\d.]*(?![0-9A-Za-z._/-])')

# pip's own commands that install nothing. `install` is the verb this reader
# polices, and `sync` is deliberately NOT here: it installs every requirement
# in a file, so a `pip sync` reaching this reader is refused rather than read
# as a `pip` that is not installing — the same miss, one command over. Naming
# the rest is what tells a `pip` that is not installing from a token standing
# in the verb's place that this reader cannot read, and the asymmetry is the
# point: a name left out costs a loud red on a step that uses it, and a name
# wrongly added can only make the reader pass over a command, which is the
# direction that stays silent.
_PIP_COMMANDS = frozenset({
    'bundle', 'cache', 'check', 'completion', 'config', 'debug', 'download',
    'freeze', 'hash', 'help', 'index', 'inspect', 'list', 'lock', 'search',
    'show', 'uninstall', 'wheel',
})


class WorkflowPinError(Exception):
    """A workflow does not pin an action exactly once."""


def pinned_action(workflow, action):
    """Return `action` with the 40-hex SHA `workflow` pins it to.

    Raises rather than guessing, because a plausible return is the silent
    no-op a derived mutation anchor exists to remove.
    """
    pattern = re.compile(
        r'(?<![0-9A-Za-z._/-])'
        + re.escape(action)
        + r'@[0-9a-f]{40}(?![0-9A-Za-z._/-])')
    pins = sorted(set(pattern.findall(workflow)))
    if not pins:
        raise WorkflowPinError(f'no pin for {action!r} in workflow text')
    if len(pins) > 1:
        raise WorkflowPinError(f'conflicting pins for {action!r}: {pins!r}')
    return pins[0]


def _dependabot_config():
    return (ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')


def dependabot_groups():
    """Every `groups:` entry as (its patterns, the axis it applies to).

    Read structurally: the indent of a block's own shallowest line is its
    group level, so a group declared at any indent is read rather than
    dropped. The alternative is a reader that recognises exactly one indent,
    where a group re-indented one step stops being a group — the entry
    disappears from the compared set, and every assertion below it goes green
    on a config carrying the exact defect it exists to refuse.

    A key inside a group body outside `_GROUP_KEYS` is refused for the same
    reason: it is a group that has been absorbed into its neighbour, which is
    what re-indenting one produces, and a reader that dropped it silently
    would report the neighbour's shape as the whole file's. So is a line no
    group owns at all. Comment lines carry no structure and are not lines of
    the mapping.
    """
    groups = {}
    blocks = re.findall(r'^ {4}groups:\n((?:^ {6,}\S.*\n|\n)*)',
                        _dependabot_config(), re.MULTILINE)
    assert blocks, 'dependabot.yml declares no groups: block at all'
    for block in blocks:
        lines = [(len(m.group(1)), m.group(2))
                 for m in re.finditer(r'^( *)(\S.*)$', block, re.MULTILINE)
                 if not m.group(2).startswith('#')]
        level = min(indent for indent, _ in lines)
        starts = [index for index, (indent, _)
                  in enumerate(lines) if indent == level]
        ends = starts[1:] + [len(lines)]
        covered = {index
                   for start, end in zip(starts, ends)
                   for index in range(start, end)}
        orphans = [lines[index][1] for index in range(len(lines))
                   if index not in covered]
        assert not orphans, (
            f'lines under the groups: block that no group owns: {orphans}. A '
            f'group this reader cannot place is a group it drops, and a '
            f'dropped group empties the compared set every assertion below '
            f'reads — here, one re-indented step is enough')
        for start, end in zip(starts, ends):
            name = lines[start][1].rstrip(':')
            body = '\n'.join(line for _, line in lines[start + 1:end])
            keys = set(re.findall(r'^\s*([A-Za-z][\w-]*):', body,
                                  re.MULTILINE))
            unknown = sorted(keys - set(_GROUP_KEYS))
            assert not unknown, (
                f'the {name!r} group carries {unknown}, which no '
                f'Dependabot group key names; this reader understands '
                f'{list(_GROUP_KEYS)} and refuses the rest rather than '
                f'dropping the group')
            applies_to = re.search(r'applies-to: (\S+)', body)
            groups[name] = (tuple(re.findall(r'^[ \t]*- "([^"]+)"', body,
                                             re.MULTILINE)),
                            applies_to.group(1) if applies_to
                            else 'version-updates')
    assert any(patterns for patterns, _ in groups.values()), (
        'no group in dependabot.yml names a pattern, so the reader stopped '
        'reading the grouping this repository relies on rather than '
        'reporting that no group names one')
    assert groups, 'dependabot.yml declares no group at all'
    return groups


def assert_every_dependabot_group_has_a_security_mirror():
    """Security updates need a group of their own, or they arrive unpaired.

    A group with no `applies-to` covers version updates alone, and
    `github/codeql-action` is one component to CodeQL — which refuses to
    process SARIF from a tree whose halves disagree — and two dependencies
    to Dependabot. Read off the `applies-to` blocks themselves: elsewhere
    every group's patterns are one flat list, where a sibling occurrence of
    the same string satisfies a test meant to check for the mirror.

    A group with no patterns is NOT skipped. `patterns` is where this
    repository says what a group covers, and a group that declines to say —
    because it selects on `dependency-type`, or because the reader could not
    find its list — is a group with nothing to mirror, so it is held to the
    same rule as one that does.
    """
    groups = dependabot_groups()
    missing = [name for name, (patterns, applies_to) in groups.items()
               if applies_to != 'security-updates'
               and groups.get(f'{name}-security')
               != (patterns, 'security-updates')]
    assert not missing, (
        f'groups covering version updates alone: {missing}. Security updates '
        f'are enabled here, so a bump to one of those arrives as one pull '
        f'request per dependency.')


def assert_every_dependabot_group_family_covers_version_updates():
    """Each family a group names is grouped on the VERSION axis too.

    The mirror rule alone does not reach the axis: a family whose only group
    carries `applies-to: security-updates` satisfies "every group has a
    security mirror" trivially, because security-scoped groups are excluded
    from that check — and its version bumps then arrive one pull request per
    `uses:` line, which is the split the grouping exists to prevent, on the
    other axis than the one the mirror control reads.

    The families are derived from the patterns the file itself declares, so a
    family added without a version-updates group of its own fails here rather
    than escaping a remembered list.
    """
    groups = dependabot_groups()
    families = {pattern.rstrip('*')
                for patterns, _ in groups.values() for pattern in patterns}
    versioned = [pattern for patterns, applies_to in groups.values()
                 if applies_to == 'version-updates' for pattern in patterns]
    uncovered = sorted(
        family for family in families
        if not any(fnmatch.fnmatch(f'{family}/x', pattern)
                   or fnmatch.fnmatch(family, pattern)
                   for pattern in versioned))
    assert not uncovered, (
        f'groups declared for {uncovered} that cover security updates alone; '
        f'a version bump to one of those arrives one pull request per '
        f'dependency, which is what the grouping is for')


def _pip_installs(body):
    """Every `pip install` invocation in a decoded `run:` block.

    Continuations are joined first and comment lines are dropped first, so a
    pin cannot hide on a continuation and a `#` line cannot satisfy an
    assertion about the command beside it — inside a `run: |` block a `#` is
    string content, not a shell comment the reader may skip.

    What is modelled, and only this: the tool wherever it stands — `pip`,
    `pip3.13`, `/usr/bin/pip3`, the `pip` of a `python3.13 -m`, and any of
    them behind whatever a command line puts in front, whether that is an
    assignment, a `sudo`, a path or a prefix this reader was never taught;
    and, between the tool and the verb, options whose value is attached
    (`--opt=v`) or absent (`-q`). The verb is one of pip's own commands, and
    only `install` is returned.

    Anything else is REFUSED rather than skipped. A token standing in the
    verb's place that is neither one of those commands nor an option — the
    value of a detached option, `pip --proxy
    http://proxy.example.com:8080 install` — is a shape this reader does
    not model, and a reader that returned nothing for it could not tell
    that shape from there being no install here to police. So it fails by
    name instead. The direction is deliberate: a command refused wrongly
    is loud and says which form was not recognised, while one passed
    wrongly says nothing at all.
    """
    logical, current = [], ''
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        current = f'{current} {stripped}'.rstrip('\\').strip()
        if not stripped.endswith('\\'):
            logical.append(current)
            current = ''
    installs = []
    for command in logical:
        for tool in _PIP_TOOL.finditer(command):
            tokens = command[tool.end():].split()
            index = 0
            while index < len(tokens) and tokens[index].startswith('-'):
                index += 1
            verb = tokens[index] if index < len(tokens) else None
            if verb == 'install':
                installs.append(command)
                continue
            assert verb is None or verb in _PIP_COMMANDS, (
                f'{command!r}: {verb!r} stands where this reader expects a '
                f'pip command or one of its options, so this is a shape it '
                f'does not model — and it refuses the shape rather than '
                f'passing over it, because a form it cannot read is an '
                f'install it cannot police and an empty result cannot tell '
                f'the two apart')
    return installs


def assert_the_pip_install_reader_refuses_what_it_cannot_model():
    """The reader fails closed on a `pip` shape it cannot read.

    The span that walks the options between the tool and the verb covers an
    option with its value attached and an option that takes none. An option
    whose value is a SEPARATE token — `pip --proxy
    http://proxy.example.com:8080 install`, the same `pip` with one literal
    word standing where the verb belongs — is outside every form this reader
    models, and a reader that simply did not match it reported nothing while
    the install ran: a missed control and a control that found nothing to
    check are one silence. So the unmodelled form is refused by name, and a
    refusal on a legitimate command is the loud of the two directions,
    which is the one worth it.

    Both directions are checked here, because the refusal is worth little
    while the shapes around it stop reading: a verb that is not an install
    stays unread rather than refused, the tool under any prefix stays the
    same tool, and the two steps this reader reads as they stand.
    """
    for command in (
            'pip --proxy http://proxy.example.com:8080 install zizmor==9.9.9',
            'pip --timeout 30 install zizmor==9.9.9',
            'pip --retries 5 install zizmor==9.9.9',
            'pip --cache-dir /tmp/pc install zizmor==9.9.9',
            'pip --log /tmp/p.log install zizmor==9.9.9',
            'pip --cert /tmp/c.pem install zizmor==9.9.9',
            'pip --exists-action i install zizmor==9.9.9'):
        try:
            _pip_installs(command)
        except AssertionError:
            continue
        raise AssertionError(
            f'{command!r} installs, and this reader passed over it: an option '
            f'whose value is a token of its own is not a form the span '
            f'modelling the options carries')
    for command in ('pip uninstall zizmor',
                    'pip download --no-deps zizmor',
                    'pip-audit --progress-spinner off zizmor',
                    'pip list --outdated --format=json > install.json',
                    'pip wheel --no-deps install',
                    'mypip install zizmor'):
        assert not _pip_installs(command), command
    for command in ('uv pip install zizmor==9.9.9',
                    'pip3.13 install zizmor==9.9.9',
                    'pip --disable-pip-version-check install zizmor==9.9.9',
                    'sudo -u runner pip install zizmor==9.9.9',
                    'env A=1 B=2 pip install zizmor==9.9.9',
                    'PIP_ROOT_USER_ACTION=ignore pip install zizmor==9.9.9',
                    'python3.13 -m pip install zizmor==9.9.9',
                    '/usr/bin/pip3 install zizmor==9.9.9'):
        assert _pip_installs(command) == [command], command
    for step in (_job_step('Install zizmor'),
                 _audit_step('Install pip-audit')):
        assert len(_pip_installs(step)) == 1, step


def assert_ci_tool_pins_live_in_a_watched_manifest():
    """These CI-tool installs resolve their pins from `-r` manifests: the
    install is asserted to EXIST, so an empty scan or a renamed step fails
    instead of passing vacuously, and every install in a row's step
    resolves from one of that row's watched manifests.
    """
    for file, job, step, manifests in (
            ('tests.yml', 'actionlint', 'Install zizmor',
             ('requirements-zizmor.txt',)),
            ('audit.yml', 'pip-audit', 'Install pip-audit',
             ('requirements-pip-audit.txt',)),
            ('tests.yml', 'wheel', 'Build the wheel and the sdist',
             ('requirements-release.txt',)),
            ('release.yml', 'publish',
             'Install the build backend and test dependencies',
             ('requirements-release.txt', 'requirements-test.txt',
              'requirements-dev.txt'))):
        workflow = (ROOT / '.github' / 'workflows' / file).read_text(
            encoding='utf-8')
        installs = _pip_installs(step_scalar(workflow, job, step, 'run'))
        assert installs, f'{step} runs no pip install to carry a pin'
        for command in installs:
            assert '==' not in command, command
            assert any(f'-r {m}' in command for m in manifests), command


# The eslint job pins three npm packages in its own env block, installs
# them with --no-save, and compares each against its registry's latest
# major. No other module in the tree names those variables, so this table
# and the control below are the only things that read them.
_ESLINT_INSTALL = 'Install eslint (pinned, no package.json, no build step)'
_ESLINT_GATE = "Check the pins against the registries' latest majors"
_ESLINT_PINS = (
    ('ESLINT_VERSION', 'eslint'),
    ('ESLINT_JS_VERSION', '@eslint/js'),
    ('GLOBALS_VERSION', 'globals'),
)
_EXACT_PIN = re.compile(r'\d+\.\d+\.\d+\Z')


def assert_the_eslint_job_pins_exact_versions_behind_a_failing_gate():
    """Three exact pins ride one install, and the gate between reds.

    The fail-closed half is the one whose absence turns a red into a
    green. A renamed or deleted env pin resolves empty, and an empty pin
    makes the integer comparison error inside its own `if`, which reads
    as "not stale" -- so without the `drift` branch the job passes on a
    pin it never saw. Setting the flag is not the claim: each flag's own
    `-ne 0` block has to reach `exit 1`, and each is pinned to that.
    """
    workflow = _tests_yml()
    env = job_mapping(workflow, 'eslint', 'env')
    install = step_scalar(workflow, 'eslint', _ESLINT_INSTALL, 'run')
    gate = step_scalar(workflow, 'eslint', _ESLINT_GATE, 'run')
    lint = step_scalar(workflow, 'eslint', 'eslint', 'run')
    assert env and install and gate and lint, 'an eslint step is gone'
    assert sorted(env) == sorted(var for var, _ in _ESLINT_PINS), sorted(env)
    for var, package in _ESLINT_PINS:
        assert _EXACT_PIN.fullmatch(env[var]), (var, package, env[var])
    assert '--no-save --no-package-lock' in install, install
    assert '--no-save' not in lint, 'the lint step installs'
    for var, package in _ESLINT_PINS:
        assert f'"{package}@${{{var}}}"' in install, (package, var, install)
        assert f'"{var}:{package}"' in gate, (package, var, gate)
    assert 'if [ -z "${pinned}" ]' in gate, gate
    assert '    drift=1\n' in gate, gate
    for flag in ('drift', 'stale'):
        block = re.search(rf'\$\{{{flag}\}}.*-ne 0.*\n(.*\n)*?fi\n', gate)
        assert block and '  exit 1\n' in block.group(0), (
            f'{flag} has no -ne 0 block of its own ending in exit 1')
    assert "git ls-files '*.js' ':!:examples/*'" in lint, lint
    at = {match.group(1): offset for offset, line in
          enumerate(_job_section(workflow, 'eslint'))
          if (match := re.match(r'      - name: (.+)$', line))}
    order = (at[_ESLINT_INSTALL], at[_ESLINT_GATE], at['eslint'])
    assert order == tuple(sorted(order)), order


# The requirement requirements-zizmor.txt is expected to carry, and how many
# artifacts each one's full release set holds. A LITERAL here, not a number
# read back out of the manifest's own prose: a control whose expectation is
# read out of the file it checks agrees with every edit made to both at once,
# which is exactly the edit a bump makes. The red this raises on a legitimate
# count change is the point — it makes whoever opens the bump read the hash
# set rather than have it rewrite itself.
_EXPECTED_ARTIFACT_COUNTS = {'zizmor': 11}


def _manifest_pins(manifest):
    """Every pin in a hash-pinned manifest, as (name, hashes, welded, line).

    A pin is the run of lines a trailing backslash joins, and its requirement
    is named by the FIRST token of that run's own first line. Every token of
    the run is scanned for the damage, not only those after the first: a
    dropped continuation welds a second requirement onto a later token, and a
    second `==` inside the first token welds one onto the name itself.
    Either way it is reported as the structural damage it is rather than
    folded into the first name: after the join the name no longer identifies
    what is pinned, and the joined line's hashes are then counted against a
    requirement that does not own them.
    """
    pins, tokens, first = [], [], 0
    for number, line in enumerate(manifest.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not tokens:
            first = number
        if stripped.endswith('\\'):
            tokens.append(stripped[:-1].strip())
            continue
        tokens.append(stripped)
        pins.append((tokens, first))
        tokens = []
    assert not tokens, (
        f'requirements-zizmor.txt:{first} ends in a continuation with no line '
        f'after it, so pip reads the last requirement as unterminated')
    for run, number in pins:
        assert '==' in run[0], (
            f'requirements-zizmor.txt:{number}: {run[0]!r} pins no version, '
            f'so the hashes after it belong to no requirement')
    return [(run[0].split('==', 1)[0],
             [token for token in run
              if token.startswith('--hash=sha256:')],
             [token for index, token in enumerate(run)
              if token.count('==') > (1 if index == 0 else 0)],
             number)
            for run, number in pins]


def _canonical_name(name):
    """The requirement name the way pip resolves it.

    `canonicalize_name` in `pip/_vendor/packaging/utils.py`, read in pip
    26.2.1: lowercase the name, fold `_` and `.` onto `-`, then condense a
    run of separators down to one. Spelled out here because this suite
    imports no third-party package, and because the rule is three lines.
    """
    value = name.lower().replace('_', '-').replace('.', '-')
    while '--' in value:
        value = value.replace('--', '-')
    return value


def assert_the_zizmor_manifest_is_hash_pinned():
    """zizmor gates the gates, so every artifact behind its pin is named.

    `--require-hashes` makes a hash a constraint rather than a note, and it
    is asserted through `_pip_installs` because inside a `run: |` block a
    `#` line is string content: reading the raw scalar lets a comment satisfy
    the pin while the install runs with no hash enforcement at all.

    The artifact count is checked against `_EXPECTED_ARTIFACT_COUNTS` in
    BOTH directions, and the values are checked to be DISTINCT, because a
    count is satisfied by a token count rather than by a set: eleven tokens
    naming ten artifacts leave one artifact unresolvable under
    `--require-hashes`, and the count is the same eleven either way. A
    digest is compared the way its consumer compares it, lowercased — pip
    lowercases every digest before `is_hash_allowed` sees it, so a value
    differing only in case names the artifact beside it. The NAME is compared
    the way pip compares it too, and for the same reason: `_canonical_name`
    folds the spellings pip resolves to one package, because a name read
    byte-exactly is how one package ends up pinned twice under two names with
    every count here still satisfied. One hash short of the expected count is
    a broken install on one platform's runner
    and nothing else, so a control that only asks what a pin carries passes a
    manifest naming nine of zizmor's ten wheels and the job then fails on
    exactly one leg of the matrix with no local symptom. The other direction
    is the vacuous half: a manifest that stopped pinning a requirement is
    checked by nothing at all, which is the shape a Dependabot group with no
    patterns had on the other side of this file.
    """
    installs = _pip_installs(_job_step('Install zizmor'))
    assert installs, 'the zizmor step runs no pip install to hash-check'
    for command in installs:
        assert '--require-hashes' in command, command
    pins = _manifest_pins((ROOT / 'requirements-zizmor.txt').read_text(
        encoding='utf-8'))
    assert pins, 'the manifest pins no requirement at all'
    # The reader's own rule, in both directions. The manifest surface cannot
    # express the separator half — no spelling carrying `-`, `_` or `.`
    # canonicalises to `zizmor`, so only the case half is reachable through a
    # pin here, and a reader that folded nothing would pass every pin below.
    for spellings, canonical in (
            (('Zizmor', 'zizmor', 'ZIZMOR'), 'zizmor'),
            (('ziz-mor', 'ziz_mor', 'ziz.mor', 'ziz..mor', 'ziz--mor'),
             'ziz-mor')):
        for spelling in spellings:
            assert _canonical_name(spelling) == canonical, (
                f'{spelling!r} canonicalises to '
                f'{_canonical_name(spelling)!r}, not {canonical!r}')
    for left, right in (('zizmor', 'ziz-mor'), ('zizmor', 'zizmor2')):
        assert _canonical_name(left) != _canonical_name(right), (
            f'{left!r} and {right!r} are two packages to pip, and folding '
            f'them together would refuse a pin pip resolves')
    seen = {}
    listed = []
    for name, hashes, welded, number in pins:
        assert not welded, (
            f'requirements-zizmor.txt:{number}: {name} carries {welded}, and '
            f'a token holding more `==` than its position in the run allows '
            f'is a second requirement welded onto this pin — so what the run '
            f'pins is ambiguous, and every hash behind it is counted against '
            f'{name}')
        # Compared the way pip compares it, and for the same reason the digest
        # below is: a name that differs only in spelling names the requirement
        # the manifest already pins. Read byte-exactly, `Zizmor` beside
        # `zizmor` is two requirements here and one to pip, and the assertion
        # that fires names the second one unmonitored — which is the remedy
        # that makes it green, one package pinned twice under two names and
        # every count still satisfied.
        canonical = _canonical_name(name)
        assert canonical in _EXPECTED_ARTIFACT_COUNTS, (
            f'requirements-zizmor.txt:{number}: {name} is pinned and this '
            f'manifest is expected to carry '
            f'{sorted(_EXPECTED_ARTIFACT_COUNTS)}, so nothing checks it')
        assert canonical not in seen, (
            f'requirements-zizmor.txt:{number}: {name} is pinned twice — pip '
            f'resolves it as {canonical} either way — and the first pin at '
            f'line {seen[canonical][0]} is never read')
        for token in hashes:
            # Compared the way pip compares it: `Hashes.__init__` lowercases
            # every digest it is given, and the digest of the bytes is
            # lowercase by construction, so a value differing only in case
            # names the artifact the manifest already names.
            digest = token.partition(':')[2].lower()
            assert digest not in listed, (
                f'requirements-zizmor.txt:{number}: {token} is listed a '
                f'second time, and repeating a value leaves the count this '
                f'control checks exactly where it was while one artifact '
                f'stakes its place — that artifact cannot resolve under '
                f'--require-hashes, on the one matrix leg that lifts it, '
                f'with nothing red here')
            listed.append(digest)
        seen[canonical] = (number, len(hashes))
    missing = sorted(set(_EXPECTED_ARTIFACT_COUNTS) - set(seen))
    assert not missing, (
        f'the manifest does not pin {missing}, which it is expected to '
        f'carry: a requirement nothing names is one nothing checks')
    wrong = []
    for name, want in _EXPECTED_ARTIFACT_COUNTS.items():
        got = seen[name][1]
        if got == want:
            continue
        wrong.append(
            f'requirements-zizmor.txt: {name} carries {got} and {want} are '
            f'expected. '
            + ('The missing one is an artifact some runner resolves and no '
               'other does.' if got < want else
               'The extra one names no artifact the release ships.'))
    assert not wrong, '\n'.join(wrong)
