#!/usr/bin/env python3
"""Classify a CI run: documentation-only change or full change.

tests.yml cannot filter its triggers by path (the contribution gates must
report on every commit, and push and pull_request must filter alike), so the
cheap run happens inside the workflow instead: this script reads the paths a
run changed and emits the matrix the suites job should execute and whether
the run was documentation-only.

Documentation is the same set speed.yml and version.yml ignore. Every
fallback runs the FULL matrix: an event this script cannot identify, a file
list it cannot read, or a list that may be truncated all cost a full run,
because an API failure that under-runs would merge untested code while one
that over-runs only wastes minutes.
"""
import fnmatch
import json
import os
import subprocess

DOCUMENTATION_PATTERNS = ('**/*.md', 'LICENSE', '.gitignore')

# Every stable minor requires-python admits. 3.14 was accepted by the
# metadata and executed nowhere, so the oldest and the newest supported
# interpreter were both claims rather than results.
FULL_MATRIX = {
    'os': ['ubuntu-latest', 'windows-latest', 'macos-latest'],
    'python': ['3.11', '3.12', '3.13', '3.14'],
}

DOCUMENTATION_MATRIX = {'os': ['ubuntu-latest'], 'python': ['3.13']}

_HEX40 = frozenset('0123456789abcdef')

# The compare endpoint caps `files` at 300 entries; a list that long may
# be truncated, and a truncated list can read as documentation-only.
COMPARE_FILES_CAP = 300

# The pulls files endpoint paginates, but the API hard-caps the collection
# at 3000 files; past that a code file can sort beyond the cutoff unseen.
PULL_REQUEST_FILES_CAP = 3000


def matches(pattern, path):
    """Whether a GitHub filter `pattern` selects `path`."""
    if pattern.startswith('**/'):
        # GitHub's `*` does not cross a `/`, so only the final segment of
        # the path is compared against the rest of the pattern.
        return fnmatch.fnmatchcase(path.rsplit('/', 1)[-1], pattern[3:])
    if '*' not in pattern:
        # Filter patterns are rooted: LICENSE selects LICENSE, never
        # sub/LICENSE or LICENSE.txt.
        return pattern == path
    raise ValueError(f'unsupported pattern shape: {pattern!r}')


def is_documentation(path):
    """Whether `path` is selected by any pattern in DOCUMENTATION_PATTERNS."""
    return any(matches(pattern, path) for pattern in DOCUMENTATION_PATTERNS)


def documentation_only(paths):
    """Whether `paths` is nonempty and every entry is documentation."""
    return bool(paths) and all(is_documentation(path) for path in paths)


def _hex40(value):
    return (isinstance(value, str) and len(value) == 40
            and all(char in _HEX40 for char in value))


def _read(run, argv, cap=None):
    try:
        stdout = run(argv)
    except Exception:  # any read failure means over-run, never under-run
        return None
    paths = [line for line in stdout.splitlines() if line]
    if cap is not None and len(paths) >= cap:
        return None
    return paths or None


def changed_paths(event, run):
    """The paths this run changed, or None when they cannot be read."""
    repository = event.get('repository')
    if not repository:
        return None
    name = event.get('name')
    if name == 'pull_request':
        number = event.get('pull_request')
        if not (isinstance(number, str) and number.isascii()
                and number.isdigit()):
            return None
        return _read(run, [
            'gh', 'api', '--paginate', '-H', 'Cache-Control: no-cache',
            f'repos/{repository}/pulls/{number}/files', '--jq',
            '.[].filename'], cap=PULL_REQUEST_FILES_CAP)
    if name == 'push':
        before, sha = event.get('before'), event.get('sha')
        if not (_hex40(before) and _hex40(sha)) or before == '0' * 40:
            return None
        return _read(run, [
            'gh', 'api', '-H', 'Cache-Control: no-cache',
            f'repos/{repository}/compare/{before}...{sha}', '--jq',
            '.files[].filename'], cap=COMPARE_FILES_CAP)
    return None


def classify(event, run):
    """Return (documentation_only, matrix, reason)."""
    paths = changed_paths(event, run)
    if paths is None:
        return (False, FULL_MATRIX,
                'could not read the changed paths; running the full matrix')
    if documentation_only(paths):
        return (True, DOCUMENTATION_MATRIX,
                f'documentation-only change: {len(paths)} paths')
    outside = sum(1 for path in paths if not is_documentation(path))
    return (False, FULL_MATRIX,
            f'{len(paths)} paths changed, {outside} outside documentation')


def event_from_environment(environ):
    """Build the `event` mapping from a process environment."""
    return {
        'name': environ.get('GITHUB_EVENT_NAME', ''),
        'repository': environ.get('GITHUB_REPOSITORY'),
        'sha': environ.get('GITHUB_SHA'),
        'pull_request': environ.get('PR_NUMBER'),
        'before': environ.get('BEFORE_SHA'),
    }


def write_outputs(path, documentation, matrix):
    """Append the step outputs to the file `path` names."""
    if not path:
        return
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(f"docs_only={'true' if documentation else 'false'}\n")
        handle.write(f'matrix={json.dumps(matrix)}\n')


def main(argv=None):
    """Classify this run and record the outputs. Returns an exit status."""
    event = event_from_environment(os.environ)

    def run(command):
        return subprocess.run(
            command, capture_output=True, text=True, check=True,
            timeout=60).stdout

    documentation, matrix, reason = classify(event, run)
    write_outputs(os.environ.get('GITHUB_OUTPUT'), documentation, matrix)
    print(reason)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
