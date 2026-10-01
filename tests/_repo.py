"""Shared fixtures for the repository-contract suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

These suites read the tree rather than run the bridge, so what they share is
where the tree is and how to walk the part of it that ships.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
EXTENSION_ROOT = ROOT / 'extension'


def iter_tree_files(root):
    """Yield every path Git tracks for the release rooted at `root`.

    The root is a parameter rather than this module's own, because the test
    that proves the scanners still catch a violation points them at a
    throwaway repository — and a helper that read one fixed global would
    quietly scan this one instead and pass.
    """
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z'], capture_output=True,
        check=True)
    paths = [path for path in listed.stdout.split(b'\0') if path]
    assert paths, 'Git returned no tracked release paths'
    for path in paths:
        yield root / os.fsdecode(path)


def git_index(root, *args):
    """Run a git command against `root`'s index, raising on failure.

    A fixture the CI planner reads has to be a git checkout, because the
    planner enumerates the TRACKED tree through `git ls-files`; `init`
    plus `add` populates the index, which is what `ls-files` reads, so
    no commit is made or needed.
    """
    subprocess.run(['git', '-C', str(root), *args], check=True,
                   capture_output=True, timeout=30)


def committable(root):
    """Make `root` a temp checkout that can actually COMMIT, anywhere.

    Three things have to be pinned, because the runners disagree and the
    workflow's own `git config` lines run only after the step's first
    command:

    - an IDENTITY in the repo's own config, so neither the seed commit
      here nor the step's commit can depend on a global config the
      harness has replaced;
    - `core.autocrlf=false`, because the system config on Windows sets
      it to true and a checkout that normalises differently from the
      fixture's text-mode writes is a diff that means something else
      than the test wrote;
    - nothing else. The child environment carries the rest
      (`commit_environment`), and any ambient hook or signing setting
      is a difference between this machine and the one that runs it.

    A control that drives a real commit needs a real commit to happen: it
    reads the subject back, and a checkout that cannot commit reads the
    PREVIOUS subject and reports it as if the step had produced the wrong
    one.
    """
    for setting, value in (('user.name', 'base'),
                           ('user.email', 'base@example.invalid'),
                           ('core.autocrlf', 'false'),
                           ('commit.gpgsign', 'false')):
        git_index(root, 'config', setting, value)


def commit_environment(home):
    """The environment a committing child needs, on every platform.

    `GIT_CONFIG_NOSYSTEM` and an empty `GIT_CONFIG_GLOBAL` keep the
    Windows system config's `core.autocrlf` and any inherited identity
    out of the child; the four identity variables make the commit
    possible even where the repository's own config is not read;
    `GIT_TERMINAL_PROMPT` keeps a credential prompt from hanging a
    scheduled suite, and `LC_ALL` keeps git's own messages parseable
    if they ever have to be.
    """
    return {
        'HOME': str(home),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': str(Path(home) / '.gitconfig'),
        'GIT_AUTHOR_NAME': 'github-actions[bot]',
        'GIT_AUTHOR_EMAIL': '41898282+github-actions[bot]@users.noreply.'
                            'github.com',
        'GIT_COMMITTER_NAME': 'github-actions[bot]',
        'GIT_COMMITTER_EMAIL': '41898282+github-actions[bot]@users.noreply.'
                               'github.com',
        'GIT_TERMINAL_PROMPT': '0',
        'LC_ALL': 'C',
    }


def git_output(root, *args):
    """Run a git command against `root` and return its stdout, stripped.

    The same launch as `git_index`, for a fixture that has to READ what
    it committed rather than only assert the command succeeded. No
    bound of its own: it reads the local repository and runs inside a
    suite, so a hang is the suite's to report and a margin here would
    only be one more number to be wrong on a loaded runner.
    """
    return subprocess.run(
        ['git', '-C', str(root), *args], check=True, capture_output=True,
        text=True).stdout.strip()
