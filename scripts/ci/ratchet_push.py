#!/usr/bin/env python3
"""Commit a ratcheted file and push it to main, telling a rejected push apart
from a concurrent one.

ONE implementation, because two copies of a write credential is where they
drift: the second is a copy, so an edit to the discrimination in the first
does not follow it. Both jobs that hold the deploy key call this with the
file they changed and the message that says so.

  usage: ratchet_push.py <path> <commit message>

Python and not a shell script because `tests/_suite_jobs.py` resolves a
tracked file a workflow step runs, and it reads Python only: a step running a
`.sh` reaches nothing in that walk and leaves the job out of the suite door
set in silence, which `tests/test_ci_tool_declarations.py` refuses. This way
the walk reads this file, sees that it runs no suite, and says so.

The credential is a deploy key, not the built-in token, and the difference
is not a preference. A ruleset may list a job among its required contexts,
and a commit pushed from here has no green checks yet, so the built-in token
is refused; GitHub Actions cannot be added as a bypass actor either. A deploy
key can, it is scoped to this one repository rather than to an account, and
revoking it is deleting one key.

A rejected push is usually main moving while this ran, and failing the job
for that would turn a required context red for an ordinary concurrent push.
So the two causes are distinguished rather than swallowed: if the remote
really did move past the commit this run measured, the next push retries and
there is nothing wrong here; if it did not, the push failed for some other
reason — a revoked key, a ruleset refusal, a hook — and the job says so.
"""
import os
import subprocess
import sys
from pathlib import Path

# github.com's published ed25519 host key (GET /meta). Pinned, not scanned:
# ssh-keyscan would trust whatever answers, and this is the one step in this
# workflow holding a write credential.
HOST_KEY = ('github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6'
            'UOoqKLsabgH5C9okWi0dh2l9GKJl')
AUTHOR = 'github-actions[bot]'
EMAIL = '41898282+github-actions[bot]@users.noreply.github.com'
SUMMARY_NAME = 'GITHUB_STEP_SUMMARY'
MOVED = ('Main moved while this run measured; the next push retries instead.')


def _git(*argv):
    """Run git, and abort on its failure the way the shell's errexit did.

    Every git call below except the push is one whose failure means the run
    cannot say what it meant to say, so each aborts rather than continuing:
    a commit with nothing to commit would otherwise go on to push a no-op and
    report success having recorded nothing.
    """
    subprocess.run(('git', *argv), check=True)


def _out(*argv):
    return subprocess.run(('git', *argv), check=True, capture_output=True,
                          text=True).stdout.strip()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or not argv[0] or not argv[1]:
        print('usage: ratchet_push.py <path> <commit message>',
              file=sys.stderr)
        return 2
    path, message = argv
    key = os.environ.get('RATCHET_SSH_KEY')
    repository = os.environ.get('REPO')
    if not key:
        print('the deploy key is not set; nothing is pushed', file=sys.stderr)
        return 2
    if not repository:
        print('the repository is not set; there is nowhere to push',
              file=sys.stderr)
        return 2

    ssh = Path.home() / '.ssh'
    ssh.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh.chmod(0o700)
    secret = ssh / 'ratchet'
    secret.write_text(f'{key}\n', encoding='utf-8')
    secret.chmod(0o600)
    hosts = ssh / 'known_hosts'
    hosts.write_text(f'{HOST_KEY}\n', encoding='utf-8')
    hosts.chmod(0o600)

    _git('config', 'user.name', AUTHOR)
    _git('config', 'user.email', EMAIL)
    _git('add', '--', path)
    # No co-author trailer: no model authored this commit.
    _git('commit', '-m', message)
    # HEAD:main, never a force: overwriting someone's commit to record a
    # number is never the right trade.
    os.environ['GIT_SSH_COMMAND'] = (
        f'ssh -i {ssh / "ratchet"} -o IdentitiesOnly=yes'
        f' -o UserKnownHostsFile={hosts}')
    remote = f'git@github.com:{repository}.git'
    try:
        _git('push', remote, 'HEAD:main')
    except subprocess.CalledProcessError:
        pass
    else:
        return 0

    _git('fetch', '--quiet', remote, 'main')
    if _out('rev-parse', 'HEAD^') == _out('rev-parse', 'FETCH_HEAD'):
        print('the ratchet push was rejected while main stood still',
              file=sys.stderr)
        return 1
    summary = os.environ.get(SUMMARY_NAME, os.devnull)
    with open(summary, 'a', encoding='utf-8') as handle:
        handle.write(f'{MOVED}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
