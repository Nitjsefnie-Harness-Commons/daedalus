#!/usr/bin/env python3
"""The workflow checkout pin."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
import _wfcheckout  # noqa: E402


_FORBIDDEN_CHECKOUT_NAMES = ('head', 'branch', 'ref', 'sha', 'commit')


def _assert_checkout_refs_safe(workflow, workflow_name='fixture.yml'):
    """Apply the pin's conservative expression contract to one workflow."""
    offenders = []
    expressions = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
    identifier = r'[A-Za-z_][A-Za-z0-9_-]*'
    access = rf'{identifier}(?:\.{identifier})+'
    github_access = rf'github(?:\.{identifier})+'
    access_pattern = re.compile(
        rf'(?<![A-Za-z0-9_-]){access}(?![A-Za-z0-9_-])')
    github_only = re.compile(
        rf'{github_access}(?:\s*\|\|\s*{github_access})*')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(f'{workflow_name}: {error}') from error
    for job, ref in refs:
        matches = list(expressions.finditer(ref))
        if ref.count('${{') != len(matches):
            raise AssertionError(
                f'{workflow_name}, job {job}: unterminated expression in '
                f'{ref!r}')
        for match in matches:
            expression = match.group(1).strip()
            if github_only.fullmatch(expression):
                continue
            expression_offenders = []
            for dotted in access_pattern.findall(expression):
                segments = dotted.split('.')
                if segments[0] == 'github':
                    continue
                for segment in segments[1:]:
                    if segment == 'outputs':
                        continue
                    if any(word in spelling
                           for spelling in (segment, segment.lower())
                           for word in _FORBIDDEN_CHECKOUT_NAMES):
                        expression_offenders.append(
                            f'{workflow_name}, job {job}: {segment}')
            offenders.extend(expression_offenders)
            if expression_offenders:
                continue
            if not re.fullmatch(access, expression):
                raise AssertionError(
                    f'{workflow_name}, job {job}: cannot decompose '
                    f'expression {expression!r}')
    assert not offenders, (
        'a checkout takes its ref from a non-github expression named '
        f'{offenders}, which an analyser reads as an untrusted head')


def test_checkout_pin_checks_all_contexts_and_identifier_segments(tmp):
    """The pin checks step/job ids and every non-github context uniformly."""
    del tmp
    expressions = (
        '${{ steps.find_base_ref.outputs.point }}',
        '${{ needs.release_commit.outputs.point }}',
        '${{ jobs.build_branch.outputs.point }}',
        '${{ env.base_ref }}',
        '${{ secrets.base_ref }}',
        '${{ inputs.base_ref }}',
        '${{ matrix.base_ref }}',
        '${{ vars.base_ref }}',
        ('${{ steps.baseline.outputs.point }}\u00a0'
         '# ${{ steps.baseline.outputs.ref }}'),
    )
    for expression in expressions:
        workflow = (
            'jobs:\n'
            '  release_commit:\n'
            '    steps:\n'
            '      - run: echo release\n'
            '  build:\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            f'          ref: {expression}\n')
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            assert any(word in str(error)
                       for word in _FORBIDDEN_CHECKOUT_NAMES), str(error)
            assert 'job build' in str(error), str(error)
        else:
            raise AssertionError(f'expected refusal for {expression}')


def test_checkout_pin_checks_mixed_case_checkout_actions(tmp):
    """Action-name casing cannot hide an unsafe checkout ref from the pin."""
    del tmp
    failures = []
    for action in (
            'actions/checkout@v4',
            'Actions/Checkout@v4',
            'ACTIONS/CHECKOUT@v4'):
        workflow = (
            'name: checkout identity oracle\n'
            'on: push\n'
            'jobs:\n'
            '  build:\n'
            '    runs-on: ubuntu-latest\n'
            '    steps:\n'
            '      - id: baseline\n'
            '        run: echo "ref=main" >> "$GITHUB_OUTPUT"\n'
            f'      - uses: {action}\n'
            '        with:\n'
            '          ref: ${{ steps.baseline.outputs.ref }}\n')
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            if 'ref' not in str(error):
                failures.append((action, str(error)))
        else:
            failures.append((action, 'pin accepted the checkout ref'))
    assert failures == [], failures


def test_checkout_pin_checks_contexts_after_a_github_access(tmp):
    """A leading GitHub access cannot exempt later dotted accesses."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    speed = path.read_text(encoding='utf-8')
    original = '${{ steps.baseline.outputs.point }}'
    assert original in speed
    needs = (
        'name: mixed contexts\n'
        'on: push\n'
        'jobs:\n'
        '  release_commit:\n'
        '    runs-on: ubuntu-latest\n'
        '    outputs:\n'
        '      point: ${{ steps.value.outputs.point }}\n'
        '    steps:\n'
        '      - id: value\n'
        '        run: echo point=main >> "$GITHUB_OUTPUT"\n'
        '  build:\n'
        '    needs: release_commit\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ github.sha || '
        'needs.release_commit.outputs.point }}\n')
    cases = (
        (
            '${{ github.sha || steps.baseline.outputs.ref }}',
            speed.replace(
                original,
                '${{ github.sha || steps.baseline.outputs.ref }}', 1),
            'ref',
        ),
        (
            '${{ github.sha || env.base_ref }}',
            speed.replace(
                original, '${{ github.sha || env.base_ref }}', 1),
            'base_ref',
        ),
        (
            '${{ github.sha || needs.release_commit.outputs.point }}',
            needs,
            'release_commit',
        ),
    )
    failures = []
    for expression, workflow, offender in cases:
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            message = str(error)
            if offender not in message or 'job ' not in message:
                failures.append((expression, message))
        else:
            failures.append((expression, 'pin accepted the checkout ref'))
    assert failures == [], failures


def test_checkout_pin_skips_github_and_rejects_non_reference_expressions(tmp):
    """GitHub refs are excluded; expressions the pin cannot decompose fail."""
    del tmp
    github = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ github.event.pull_request.base.sha }}\n')
    _assert_checkout_refs_safe(github)

    for expression in (
            "${{ format('{0}', steps.baseline.outputs.point) }}",
            '${{ steps.baseline.outputs.point || github.sha }}',
            '${{ steps.baseline.outputs.point[0] }}',
            '${{ true }}'):
        workflow = github.replace(
            '${{ github.event.pull_request.base.sha }}', expression)
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            assert expression[3:-3].strip() in str(error), str(error)
            assert 'job build' in str(error), str(error)
        else:
            raise AssertionError(f'expected refusal for {expression}')


def test_checkout_refs_from_step_outputs_avoid_the_analyser_heuristic(tmp):
    """No checkout's `ref:` may come from a name that reads like a head.

    CodeQL's untrusted-checkout query (code-scanning alert #82) reads an
    actions/checkout step as pulling a pull request's untrusted head when
    its `ref:` comes from a `steps.*` output whose name contains `head`,
    `branch`, `ref`, `sha` or `commit`. The baseline checkout in speed.yml
    pulls the pull request's base SHA or a release tag — trusted code — so
    the output carrying it is named `point`; naming it `ref` was what drew
    the alert. This pins the name class, so a rename back goes red here
    rather than in the next default-branch analysis.

    The pin is a conservative superset of the analyser heuristic's checked
    identifier-name class for non-`github` dotted expressions: every segment
    is checked, including step and needed-job ids and `secrets`, `inputs`,
    `matrix` and `vars` contexts.
    `vars` is intentionally over-approximated because CodeQL does not model
    it. The query's regexpMatch is case-sensitive, so `BASE_REF` would evade
    the analyser itself; the check tests each name as written and lowercased
    because a human-readable spelling stays one rename away from the alert.
    """
    workflows = sorted((ROOT / '.github' / 'workflows').glob('*.yml'))
    workflows += sorted((ROOT / '.github' / 'workflows').glob('*.yaml'))
    for path in workflows:
        text = path.read_text(encoding='utf-8')
        _assert_checkout_refs_safe(text, path.name)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
