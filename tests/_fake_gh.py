"""An executable double for `gh`, and the environment that puts it on PATH.

The pull-request watchers shell out to `gh`, so a `gh` earlier on PATH is a
complete seam: every watcher suite can then drive real watcher processes with
no network anywhere. Each call is answered from a JSON fixture file and
appended to a call log, which is what makes the request budget measurable:
the log, not a claim, is the number.

The launcher is written per platform rather than assumed. A POSIX shell script
named `gh` is executable on Linux and macOS and is nothing at all on Windows,
where a bare program name resolves only to `gh.exe`; the Windows launcher is
therefore a `.bat` and the client is pointed at it by an absolute path
(`DAEDALUS_GH`). Either way the launcher finds this file relative to itself,
so a tree that moves still runs, and `install` proves the launcher executes
before a suite trusts it.

The fake mirrors how real `gh api -i` behaves: the status line, the header
block and the body on stdout, `gh: ... (HTTP NNN)` on stderr, and exit 1
whenever the status is an error -- including the rate-limit refusals the
watchers must recognise, which real `gh` reports the same way.
"""
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = sys.platform.startswith('win')

REASONS = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden',
           404: 'Not Found', 429: 'Too Many Requests',
           500: 'Internal Server Error'}

# `gh` answers a REST path as its final argument and a GraphQL request on
# stdin; the fixture fragments are matched against whichever one this call
# carries, so the same answers file serves the tree and a base commit.
GRAPHQL_MARK = 'graphql'


def _self_test(launcher):
    """Prove the launcher this platform writes actually executes."""
    done = subprocess.run([str(launcher), '--self-test'], capture_output=True,
                          text=True, timeout=60)
    if done.returncode != 0 or 'fake gh ready' not in done.stdout:
        raise AssertionError(
            f'the fake gh launcher does not run on this platform: '
            f'rc={done.returncode} out={done.stdout!r} err={done.stderr!r}')


def _write_atomic(path, text):
    """Replace a file in one step, so a poll never reads half an answer."""
    tmp = path.with_name(path.name + '.new')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def _load(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _logged(log, entry):
    with open(log, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry) + '\n')


def _entries(log):
    """Every call the fake received; a torn last line is ignored."""
    if not os.path.exists(log):
        return []
    out = []
    with open(log, encoding='utf-8') as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _fixture(answers, request):
    """The first fragment the request carries, its key, and its response.

    Successive pages of one connection are a list, consumed in order and the
    last repeated, so a two-page answer needs no counter that could race
    between two watcher processes. The count is taken before this call is
    logged, so the first request gets the first page.
    """
    for fragment, answer in answers.items():
        if fragment in request:
            if isinstance(answer, list):
                prior = sum(1 for entry in _entries(os.environ[
                    'DAEDALUS_FAKE_GH_LOG'])
                    if entry.get('fragment') == fragment)
                return fragment, answer[min(prior, len(answer) - 1)]
            return fragment, answer
    return None, None


def _render(response):
    """One `-i` style response: status line, headers, blank line, body.

    A bare string or a JSON object that names none of the response fields is
    a 200 whose body is that value, which is the shape most fixtures use.
    """
    if isinstance(response, str):
        response = {'body': response}
    elif not set(response) & {'status', 'headers', 'body'}:
        response = {'body': response}
    status = int(response.get('status', 200))
    reason = REASONS.get(status, 'Status')
    lines = [f'HTTP/2.0 {status} {reason}']
    for name, value in (response.get('headers') or {}).items():
        lines.append(f'{name}: {value}')
    body = response.get('body', {})
    text = body if isinstance(body, str) else json.dumps(body)
    return '\r\n'.join(lines) + '\r\n\r\n' + text + '\n', status, text


def main(argv):
    """Answer one call from the fixtures, or refuse it loudly."""
    if argv[:1] == ['--self-test']:
        print('fake gh ready')
        return 0
    request = (sys.stdin.read() if GRAPHQL_MARK in argv
               else (argv[-1] if argv else ''))
    answers = _load(os.environ['DAEDALUS_FAKE_GH_ANSWERS'])
    fragment, response = _fixture(answers, request)
    _logged(os.environ['DAEDALUS_FAKE_GH_LOG'],
            {'t': time.time(), 'argv': list(argv), 'request': request,
             'fragment': fragment})
    if response is None:
        sys.stderr.write(f'fake gh: no fixture carries {request[:200]!r}\n')
        return 1
    text, status, body = _render(response)
    # `gh api` prints headers only when asked; the base scripts never ask,
    # and a header block on their stdout is exactly the unparseable answer a
    # base watcher would have met in the wild.
    if '-i' not in argv and '--include' not in argv:
        text = body + '\n'
    sys.stdout.write(text)
    if status >= 400:
        sys.stderr.write(f'gh: {body.strip()[:200]} (HTTP {status})\n')
        return 1
    return 0


class FakeGh:
    """One installed fake: a launcher, an answers file and a call log.

    The launcher is proved executable by `install`, so a suite never learns
    about a platform difference from a watcher that silently could not start.
    """

    def __init__(self, directory, answers=None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.answers_path = self.dir / 'answers.json'
        self.log = self.dir / 'calls.jsonl'
        self.launcher = self.dir / ('gh.bat' if WINDOWS else 'gh')
        # The stub is copied beside the launcher rather than referenced from
        # where this module happens to live, so an install that moves keeps
        # working and the launcher holds no absolute path into this tree.
        shutil.copy(HERE / '_fake_gh.py', self.dir / '_fake_gh.py')
        if WINDOWS:
            self.launcher.write_text(
                '@echo off\r\n'
                f'"{sys.executable}" "%~dp0_fake_gh.py" %*\r\n',
                encoding='utf-8')
        else:
            self.launcher.write_text(
                '#!/bin/sh\n'
                'here=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                f'exec "{sys.executable}" "$here/_fake_gh.py" "$@"\n',
                encoding='utf-8')
            self.launcher.chmod(self.launcher.stat().st_mode
                                | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.log.write_text('', encoding='utf-8')
        self.write_answers(answers or {})
        _self_test(self.launcher)

    def write_answers(self, answers):
        _write_atomic(self.answers_path, json.dumps(answers, indent=1))

    def env(self, base=None):
        """A child environment in which `gh` is this fake."""
        env = dict(os.environ if base is None else base)
        env['PATH'] = str(self.dir) + os.pathsep + env.get('PATH', '')
        env['DAEDALUS_GH'] = str(self.launcher)
        env['DAEDALUS_FAKE_GH_LOG'] = str(self.log)
        env['DAEDALUS_FAKE_GH_ANSWERS'] = str(self.answers_path)
        return env

    @contextlib.contextmanager
    def activate(self):
        """The same environment, for code this process runs in-line."""
        mine = self.env()
        saved = {name: os.environ.get(name) for name in mine}
        os.environ.update(mine)
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def calls(self, fragment=None):
        """Every call the fake received, optionally of one fixture."""
        found = _entries(self.log)
        if fragment is None:
            return found
        return [entry for entry in found if entry.get('fragment') == fragment]

    def clear(self):
        """Forget the calls so far, for a measurement that starts at zero."""
        self.log.write_text('', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
