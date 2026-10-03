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
from _wffixtures import _audit_step

# The keys Dependabot's group schema accepts. Anything else inside a group is
# a key this reader does not understand, and a reader that silently absorbed
# one would take the group it belongs to out of the compared set.
_GROUP_KEYS = ('applies-to', 'dependency-type', 'patterns')

# The `pip install` OPERATION, however the shell spells it: an interpreter of
# any version, under any path, and a `pip` that carries one. Named as one
# operation because a spelling this pattern does not know is a version
# nobody is told to move.
_PIP_INSTALL = re.compile(
    r'^(?:\S*python[\d.]* -m )?(?:\S*/)?pip[\d.]* install\b')


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

    The filter names the OPERATION rather than an enumerated list of
    spellings: `python`, `python3` and `python3.13` are the same interpreter
    with a version the reader must not have to know, and `pip`/`pip3` are the
    same tool for the same reason. Enumerating them is what made `pip3
    install` walk past a guard written for the operation.
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
    return [command for command in logical if _PIP_INSTALL.match(command)]


def assert_ci_tool_pins_live_in_a_watched_manifest():
    """These two CI-tool installs resolve their pin from a `-r` manifest.

    Dependabot reads manifest files and never a workflow, so a version
    written into a step is a version nobody is ever told to move. The
    positive half matters as much as the negative one: the manifest install
    is asserted to EXIST, so an empty scan or a renamed step fails here
    instead of passing vacuously.

    It covers the two installs this table names — zizmor in the actionlint
    job and pip-audit in the audit job — and nothing else in either workflow.
    An install this control does not reach is not held by it: the release
    job's own `python -m pip install --upgrade pip build twine==7.0.0` is an
    inline pin of the same kind, outside this table and out of scope here.
    The claim is about the two CI-tool gates, not about every install the
    repository runs.
    """
    for read, step, manifest in (
            (_job_step, 'Install zizmor', 'requirements-zizmor.txt'),
            (_audit_step, 'Install pip-audit', 'requirements-pip-audit.txt')):
        installs = _pip_installs(read(step))
        assert installs, f'{step} runs no pip install to carry a pin'
        for command in installs:
            assert '==' not in command, command
            assert f'-r {manifest}' in command, command


def assert_the_zizmor_manifest_is_hash_pinned():
    """zizmor gates the gates, so every artifact behind its pin is named.

    `--require-hashes` makes a hash a constraint rather than a note, and it
    is asserted through `_pip_installs` because inside a `run: |` block a
    `#` line is string content: reading the raw scalar lets a comment satisfy
    the pin while the install runs with no hash enforcement at all.

    Each requirement's hash count is checked against the count the manifest
    states for it in its own header, because one hash short of the stated
    count is a broken install on one platform's runner and nothing else: the
    control that only asks whether a hash is PRESENT passes a manifest with
    nine of zizmor's ten wheels named, and the job then fails on exactly one
    leg of the matrix with no local symptom at all.
    """
    installs = _pip_installs(_job_step('Install zizmor'))
    assert installs, 'the zizmor step runs no pip install to hash-check'
    for command in installs:
        assert '--require-hashes' in command, command
    manifest = (ROOT / 'requirements-zizmor.txt').read_text(encoding='utf-8')
    stated = {name: int(count) for name, count in re.findall(
        r'^#\s+(\S+) (\d+) hash(?:es)?\b', manifest, re.MULTILINE)}
    assert stated, 'the manifest states no artifact count to check against'
    pins = [line for line in re.sub(r'\\\n\s*', ' ', manifest).splitlines()
            if line.strip() and not line.startswith('#')]
    assert pins, 'the manifest pins no requirement at all'
    for pin in pins:
        name = pin.split('==', 1)[0]
        assert name in stated, f'{name} states no artifact count'
        assert pin.count('--hash=sha256:') == stated[name], (
            f'{name} carries {pin.count("--hash=sha256:")} hashes and its '
            f'header states {stated[name]}; the missing one is an artifact '
            f'some runner resolves and no other does')
