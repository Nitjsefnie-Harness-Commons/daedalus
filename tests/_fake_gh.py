"""An executable double for `gh`, and the environment that puts it on PATH.

Its consumers shell out to `gh`, so a `gh` earlier on PATH is a complete
seam: they drive real `gh` processes with no network in the loop. Each
call is answered from a JSON fixture file and appended to a call log,
which is what makes the request budget measurable - the log, not a
claim, is the number.

The launcher is written per platform rather than assumed: a POSIX script
named `gh` is executable on Linux and macOS and nothing on Windows,
where the launcher is a `.bat` the client is pointed at by absolute
path (`DAEDALUS_GH`). The launcher finds this file relative to itself,
and an install proves it executes before a suite trusts it. The fake
mirrors real `gh api -i`: status line, header block and body on stdout,
`gh: ... (HTTP NNN)` on stderr, exit 1 for any error status, rate-limit
refusals included.

That is the SHAPE of an answer, and the part it left out is the part a
throttled query really arrives in: a 200 carrying the spent rate-limit
headers, `gh` exiting 1 over it, and `gh: API rate limit already
exceeded ...` on stderr. So an answer may also state the exit code, the
stderr and the stdout `gh` leaves behind, and a shape the fake cannot
render is REFUSED BY NAME rather than answered plausibly: a double that
fills in what it was not told is a control with no opinion on the case
it is standing in for.
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

# What a fixture answer may name. Naming any one of them makes the object
# a spec; an object naming none is a 200 whose body is that object.
RESPONSE = frozenset({'status', 'headers', 'body'})
OUTCOME = frozenset({'exit', 'stderr', 'stdout'})

REASONS = {200: 'OK', 400: 'Bad Request', 403: 'Forbidden',
           404: 'Not Found', 429: 'Too Many Requests',
           500: 'Internal Server Error'}

# A REST path arrives as the final argument and a GraphQL request on stdin;
# fragments are matched against whichever, so one answers file serves this
# tree and a base commit's scripts.
GRAPHQL_MARK = 'graphql'

# The name a watcher publishes its poll index under, recorded beside every
# request so a poll is a group in the log. Kept for the untracked watcher
# that still sets it.
POLL_MARK = 'DAEDALUS_WATCHER_POLL'

# A path whose existence releases the calls this fake is holding; every
# other answer is written at once.
GATE = 'DAEDALUS_FAKE_GH_GATE'
# Where the hold's entry and its release are recorded, beside the call log -
# a line there would be counted as a call. A release is the only terminal
# fact about a wait; a case that DEPENDS on the hold needs both.
RELEASES = 'DAEDALUS_FAKE_GH_RELEASES'


def _hold():
    """Withhold this answer until the gate the caller named exists.

    The call is logged before this runs, so a reader counting entries
    can see a call entered and still open. It cannot RETURN from the
    call, and that is all: a signal removes it, so a case reading
    liveness here must name the parent it is reading about.

    No bound in here, and that is the point: a bound would turn the hold
    into a guess that expires silently. Two sit outside it: the client's
    GH_TIMEOUT bounds the call the caller is blocked in, and the gate is
    opened by the caller's `finally` - so a caller SIGKILLed outright
    leaves the fake here for the life of the box, and its tmp tree is
    never cleaned."""
    path = os.environ.get(GATE)
    if path is None:
        return
    # Inside the wait, once: a record written above the loop says the call
    # arrived, not that it is held, so a hold skipped for exactly the
    # watcher's own calls would satisfy a reader counting entries. Written
    # unconditionally in the loop it would record once per poll, and the
    # count would stop being a count.
    entered = False
    while not os.path.exists(path):
        if not entered:
            _recorded('entered', path)
            entered = True
        time.sleep(0.02)
    _recorded('release', path)


def _recorded(stage, path):
    """Leave one line saying a hold began or ended, for a reader to have.

    Absent when no record was named, so a fake that is not holding writes
    nothing anywhere and the call log stays the whole record.
    """
    log = os.environ.get(RELEASES)
    if log is None:
        return
    _logged(log, {'t': time.time(), 'gate': path, 'stage': stage})


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


def _load_answers(path):
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

    Successive pages of one connection are a list, consumed in order and
    the last repeated, so a two-page answer needs no counter that could
    race between two watcher processes; the count is taken before this
    call is logged, so the first request gets the first page.
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


class Unmodelled(Exception):
    """A fixture asked for a shape this fake will not invent."""


def _response(answer):
    """One fixture answer, as the fields of a `gh` run.

    `status`, `headers` and `body` are the response; `exit` and `stderr`
    are what `gh` leaves behind it, and `stdout` replaces the rendered
    response outright. A bare string is a 200 whose body is that value.
    Each field is checked for the type its renderer needs and a value of
    the wrong type is refused by name rather than defaulted."""
    spec = (answer if isinstance(answer, dict)
            and set(answer) & (RESPONSE | OUTCOME)
            else {'body': answer})
    out = {'status': 200, 'headers': {}, 'body': '',
           'exit': None, 'stderr': None, 'stdout': None}
    for name, kind in (('status', int), ('headers', dict),
                       ('exit', int), ('stderr', str), ('stdout', str)):
        if name in spec and not isinstance(spec[name], kind):
            raise Unmodelled(f'the {name} field is {spec[name]!r}, '
                             f'which is not {kind.__name__}')
        if name in spec:
            out[name] = spec[name]
    out['body'] = spec.get('body', '')
    return out


def _render(status, headers, text):
    """The `-i` shape: status line, headers, blank line, body."""
    if status not in REASONS:
        raise Unmodelled(f'there is no reason phrase for HTTP {status}, so a '
                         'status line written for one would be a guess')
    lines = [f'HTTP/2.0 {status} {REASONS[status]}']
    for name, value in headers.items():
        lines.append(f'{name}: {value}')
    return '\r\n'.join(lines) + '\r\n\r\n' + text


def main(argv):
    """Answer one call from the fixtures, or refuse it loudly."""
    if argv[:1] == ['--self-test']:
        print('fake gh ready')
        return 0
    request = (sys.stdin.read() if GRAPHQL_MARK in argv
               else (argv[-1] if argv else ''))
    answers = _load_answers(os.environ['DAEDALUS_FAKE_GH_ANSWERS'])
    fragment, response = _fixture(answers, request)
    _logged(os.environ['DAEDALUS_FAKE_GH_LOG'],
            {'t': time.time(), 'argv': list(argv), 'request': request,
             'fragment': fragment, 'poll': os.environ.get(POLL_MARK),
             'pid': os.getpid()})
    if response is None:
        # Refused before the hold, so a call this fake cannot answer fails
        # by name rather than waiting for a gate that has nothing to open.
        sys.stderr.write(f'fake gh: no fixture carries {request[:200]!r}\n')
        return 1
    _hold()
    try:
        return _respond(response, argv)
    except Unmodelled as exc:
        sys.stderr.write(f'fake gh: this fake does not model {exc}\n')
        return 2


def _respond(response, argv):
    """Write one answered run: its stdout, its stderr and the code it
    exits. `gh` exits 1 on any error status and writes
    `gh: ... (HTTP NNN)` to stderr; a suite overrides that pair with
    `exit` and `stderr` because a throttled GraphQL query is a 200 that
    exits 1 over a limit (issue 1338) - a shape the status alone cannot
    produce."""
    spec = _response(response)
    status = spec['status']
    text = (spec['body'] if isinstance(spec['body'], str)
            else json.dumps(spec['body']))
    # Headers only when asked; the base scripts never ask, and a header
    # block on their stdout is the unparseable answer a base watcher would
    # have met in the wild.
    if '-i' in argv or '--include' in argv:
        text = (spec['stdout'] if spec['stdout'] is not None
                else _render(status, spec['headers'], text))
    elif spec['stdout'] is not None:
        raise Unmodelled('a stdout it was handed, on a call that asked for no '
                         'header block: answering the rendered response '
                         'instead would ignore it')
    if os.environ.get('DAEDALUS_FAKE_GH_CRLF'):
        # What a Windows text stream does to a response that already spells
        # its line endings: a Linux run can hand the client those bytes.
        text = text.replace('\n', '\r\n')
    if text:
        sys.stdout.write(text + '\n')
    if spec['stderr'] is not None:
        sys.stderr.write(spec['stderr'])
    elif status >= 400:
        sys.stderr.write(f'gh: {text.strip()[:200]} (HTTP {status})\n')
    return spec['exit'] if spec['exit'] is not None else int(status >= 400)


class FakeGh:
    """One installed fake: a launcher, an answers file and a call log.

    The launcher is proved executable here, so a suite never learns about a
    platform difference from a watcher that silently could not start.
    """

    def __init__(self, directory, answers=None, gate=False):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.answers_path = self.dir / 'answers.json'
        self.log = self.dir / 'calls.jsonl'
        self.gate_path = self.dir / 'gate'
        self.releases_path = self.dir / 'releases.jsonl'
        self.holding = bool(gate)
        if self.holding:
            # Only where a hold is asked for: truncating a record a live
            # held process appends to is a reset, and the two hundred
            # instances that never hold must not pay for either file.
            self.gate_path.unlink(missing_ok=True)
            self.releases_path.write_text('', encoding='utf-8')
        self.launcher = self.dir / ('gh.bat' if WINDOWS else 'gh')
        # Copied beside the launcher, not referenced from where this module
        # lives: an install that moves keeps working and the launcher holds
        # no absolute path into this tree.
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
        if self.holding:
            env[GATE] = str(self.gate_path)
            env[RELEASES] = str(self.releases_path)
        return env

    def releases(self):
        """Every release this fake recorded, or none if it held nothing:
        which calls came back, and which never did."""

        return [entry for entry in self.stages()
                if entry['stage'] == 'release']

    def entered(self):
        """Every hold this fake began: a case that only counts releases
        reads the same whether the hold ran for it or not."""

        return [entry for entry in self.stages()
                if entry['stage'] == 'entered']

    def stages(self):
        """Both recorded stages, in the order the fake wrote them."""
        return _entries(self.releases_path)

    def open_gate(self):
        """Release every call this fake is holding: the release is a path
        appearing, so the calls already waiting and every one after are
        released, with nothing to pair up."""
        self.gate_path.write_text('', encoding='utf-8')

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
