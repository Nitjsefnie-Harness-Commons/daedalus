"""Stand-ins for the refusal arms of `scripts/ci/install_lint_tools.py`.

Eleven statements in the installer exist to REFUSE, and no healthy runner
takes any of them: the pinned requirements file is readable, one pip download
brings one wheel, the transfer is under the ceiling, the wheel is a zip, and
each tool resolves inside the directory this install published. A green CI
run therefore says nothing about whether those refusals still work, which is
the same defect the installer itself was written to close one level down — a
check that cannot fail is not a check.

Each control here drives the real function with the input it was written to
refuse, and stands in at the boundary rather than crossing it: the pinned
requirements file, the `pip` subprocess, `shutil.which`, the tool directory.
Nothing here downloads a wheel, runs pip, or invokes a linter, and nothing
waits on a clock — the transfer ceiling is compared against a real file's
real size, never against elapsed time.

Every refusal names the thing that was wrong and the reason, so each
assertion is on the tokens the message must carry: the requirement, the
wheel's name, the size and the ceiling, the tool that resolved elsewhere. An
assertion that only a SystemExit came out is satisfied by a refusal that
names nothing, and a refusal that names nothing is the shape this file exists
to rule out.
"""
import sys
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_mechanisms import INSTALLER_SOURCE  # noqa: E402


def _installer_module(name):
    """The installer as its own module, so a control can stand a boundary in.

    A fresh module per control is what makes patching a constant here safe:
    the module reads `MAX_TRANSFER` and `TOOL_DIR` as globals at call time, and
    a shared module would carry one control's stand-in into the next.
    """
    return _util.load(INSTALLER_SOURCE, name)


def _the_refusal(call, *args):
    """The SystemExit `call` refuses with, or a failure naming what it did."""
    try:
        call(*args)
    except SystemExit as why:
        return why
    raise AssertionError(
        f'{getattr(call, "__name__", call)} returned where the input it '
        'refuses was handed to it, so this control is not driving the arm it '
        'names')


def _must_name(raised, *tokens):
    """Every token the refusal must carry, checked in one place.

    `SystemExit.code` is the message: the installer raises with the string,
    so that is what a reader of a dead install step sees.
    """
    message = str(raised.code)
    absent = [token for token in tokens if token not in message]
    assert not absent, (
        f'the refusal does not name {absent}: {message!r}. A refusal that '
        'names neither the thing that was wrong nor the reason is the shape '
        'these controls exist to rule out')


def _wheel_name(installer, version):
    """The file name pip resolves this pin to, spelled the module's way.

    Built from the module's own constants rather than written here, so a
    control cannot pass against a wheel the module would have refused on its
    name before reaching the arm under test.
    """
    escaped = installer.SHELLCHECK_PACKAGE.replace('-', '_')
    return f'{escaped}-{version}-py3-none-any.whl'


class Pip:
    """`subprocess.run` as `_wheel_for` calls it: the argv recorded, no pip.

    `check=True` and the bound are the two properties this call is written
    around, so a stand-in that ignored either would let a control pass against
    a download whose failure no longer stops the install. What pip is said to
    have brought is what the caller wrote into `staging`, because the refusal
    under test is a verdict on what is there.
    """

    def __init__(self, installer, staging, requirement):
        self.requirement = requirement
        self.staging = staging
        self.timeout = installer.WHEEL_TIMEOUT
        self.calls = []

    def __call__(self, argv, check=False, timeout=None):
        words = [str(word) for word in argv]
        assert check, (
            'the download is not run with check=True, so a pip failure no '
            'longer stops the install and the refusal under test is reached '
            'with nothing downloaded')
        assert timeout == self.timeout, (
            f'the download was handed timeout={timeout!r} where '
            f'WHEEL_TIMEOUT is {self.timeout}')
        assert self.requirement in words, (
            f'the download asked for {words}, which does not name '
            f'{self.requirement!r}; the pin is what decides the wheel')
        assert '--dest' in words and str(self.staging) in words, (
            f'the download was handed --dest outside {self.staging}, so the '
            'files it brings are not the ones this control placed')
        self.calls.append(words)


class Which:
    """`shutil.which` with the answer this control needs, and the calls read.

    Recording the names it was asked for is what keeps a control honest about
    TOOLS: a lookup answered for a tool the installer does not declare would
    make a refusal name a binary nothing here installs.
    """

    def __init__(self, answers):
        self.answers = dict(answers)
        self.calls = []

    def __call__(self, tool):
        self.calls.append(tool)
        return self.answers.get(tool)


def unreadable_requirements():
    """The version read refuses a file it cannot read, by name.

    A missing or renamed requirements file on a runner is the case this arm
    is for, and what it owes the reader is a message naming the file and the
    package whose version that file carried — not a stack trace about a path
    the reader is already looking at. The cause travels with it, because the
    reason the read failed is half of what names the problem.

    MUTANT: drop the `except OSError` arm. The refusal then leaves as a
    FileNotFoundError, the control raises on the type, and the arm is gone.
    """
    installer = _installer_module('lint_installer_unreadable')
    absent = Path(installer.REQUIREMENTS).with_name('no-such-requirements.txt')
    assert not absent.exists(), (
        f'{absent} exists, so this control has no subject')
    with mock.patch.object(installer, 'REQUIREMENTS', absent):
        raised = _the_refusal(installer.shellcheck_pin)
    _must_name(raised, absent.name, installer.SHELLCHECK_PACKAGE,
           'could not be read')
    assert isinstance(raised.__cause__, OSError), (
        f'the refusal carries {raised.__cause__!r} as its cause rather than '
        'the read failure that produced it, so the reason is lost')


def no_wheel(staging):
    """An sdist where a wheel was pinned: refused, and both halves named.

    `--only-binary=:all:` forbids the fallback pip would otherwise take, so
    a download that brought an sdist means the pin did not resolve to a
    wheel at all. The refusal has to name the requirement AND the file that
    came back, because those are the two facts a reader needs to tell a moved
    pin from a broken one.

    MUTANT: drop the `if not wheels` arm. `_wheel_for` then indexes an empty
    list, so what comes out is an IndexError rather than the refusal, and
    neither token is present.
    """
    installer = _installer_module('lint_installer_no_wheel')
    version = installer.shellcheck_pin()
    brought = staging / f'{installer.SHELLCHECK_PACKAGE}-{version}.tar.gz'
    brought.write_bytes(b'an sdist, which this script will not build\n')
    requirement = f'{installer.SHELLCHECK_PACKAGE}=={version}'
    pip = Pip(installer, staging, requirement)
    with mock.patch.object(installer.subprocess, 'run', pip):
        raised = _the_refusal(installer._wheel_for, staging, version)
    _must_name(raised, requirement, brought.name, 'no wheel')
    assert len(pip.calls) == 1, (
        f'the download was run {len(pip.calls)} time(s) for one pin')


def too_many_wheels(staging):
    """Two wheels for one pin: refused, with the count and both names.

    Which of the two is installed is pip's choice and not this script's, so
    the refusal is the only place the ambiguity can be caught. A reader needs
    the count and the names to see it, so both are asserted.

    MUTANT: drop the `if len(wheels) > 1` arm. `_wheel_for` then takes
    whichever wheel sorted first, and the control raises because no refusal
    came out.
    """
    installer = _installer_module('lint_installer_too_many_wheels')
    version = installer.shellcheck_pin()
    names = [_wheel_name(installer, version)]
    other = _wheel_name(installer, version).replace('py3-none-any', 'py2-none')
    names.append(other)
    for name in names:
        (staging / name).write_bytes(b'not opened: this is refused first\n')
    requirement = f'{installer.SHELLCHECK_PACKAGE}=={version}'
    pip = Pip(installer, staging, requirement)
    with mock.patch.object(installer.subprocess, 'run', pip):
        raised = _the_refusal(installer._wheel_for, staging, version)
    _must_name(raised, requirement, str(len(names)), *names)
    assert pip.calls and len(pip.calls) == 1, (
        f'the download was run {len(pip.calls)} time(s) for one pin')


def oversize_wheel(staging):
    """A wheel over the ceiling is refused before a byte of it is opened.

    The file is real and its size is real; only the ceiling is lowered, and it
    is lowered to one byte under that size so the comparison is decided by
    exactly one. The refusal must carry the name, the size AND the ceiling —
    a reader who has only one of the three cannot tell a wheel at the limit
    from a wheel nobody bounded.

    MUTANT: drop the `if size > MAX_TRANSFER` arm. The wheel is then opened
    as a zip, so the refusal that comes out is the unreadable-wheel one and
    neither the ceiling nor the size is in it.
    """
    installer = _installer_module('lint_installer_oversize')
    version = installer.shellcheck_pin()
    wheel = staging / _wheel_name(installer, version)
    payload = b'not a wheel either, and over the ceiling as well'
    wheel.write_bytes(payload)
    with mock.patch.object(installer, 'MAX_TRANSFER', len(payload) - 1):
        raised = _the_refusal(installer._place_scripts_member, wheel, version)
    _must_name(raised, wheel.name, str(len(payload)), str(len(payload) - 1))


def unreadable_wheel(staging):
    """A download that is not a zip: refused with the reason attached.

    The file is a real file at a real wheel's name and is not an archive, so
    the arm is reached with nothing stubbed but the ceiling above. The
    refusal names the file and the failure, and the failure is chained as the
    cause so a reader gets zipfile's own words rather than a restatement.

    MUTANT: drop the `except (OSError, BadZipFile)` arm. The BadZipFile then
    propagates as itself, and the control raises on the type.
    """
    installer = _installer_module('lint_installer_not_a_zip')
    version = installer.shellcheck_pin()
    wheel = staging / _wheel_name(installer, version)
    wheel.write_bytes(b'this is a file on disk, not a zip archive\n')
    raised = _the_refusal(installer._place_scripts_member, wheel, version)
    _must_name(raised, wheel.name, 'not a readable wheel')
    assert isinstance(raised.__cause__, zipfile.BadZipFile), (
        f'the refusal carries {raised.__cause__!r} as its cause rather than '
        'the archive failure that produced it')


def resolved_outside_the_tool_dir(tmp):
    """A tool that resolved somewhere else, and the comparison behind it.

    `_record` refuses on this, so the predicate is the whole basis of that
    refusal and both of its answers are asserted: a predicate that always
    said yes would let a binary the image supplied answer in place of the
    pinned one, which is the defect the check's second revision exists to
    close, and a predicate that always said no would refuse a correct
    install.

    MUTANT: `return False` in the `except ValueError` arm becomes `return
    True`. The path outside the directory is then reported as installed, and
    the second assertion fails.
    """
    installer = _installer_module('lint_installer_installed_here')
    root = Path(tmp)
    tool_dir = root / 'daedalus-lint-tools'
    tool_dir.mkdir()
    inside = tool_dir / 'shellcheck'
    inside.write_bytes(b'')
    outside = root / 'elsewhere' / 'shellcheck'
    outside.parent.mkdir()
    outside.write_bytes(b'')
    with mock.patch.object(installer, 'TOOL_DIR', tool_dir):
        assert installer._installed_here(str(inside)) is True, (
            f'{inside} is under {tool_dir} and was not reported as the tool '
            'this install put on PATH, so every correct install is refused')
        assert installer._installed_here(str(outside)) is False, (
            f'{outside} is not under {tool_dir} and was reported as the tool '
            'this install put on PATH, so a binary the image supplied answers '
            'in place of the pinned one and the check cannot see it')


def recorded_outside_the_tool_dir(tmp):
    """A tool the machine answered with, somewhere this install never wrote.

    The refusal must name the tool, the directory the install published AND
    the path that was found — the docstring calls the path the part a reader
    needs, and a refusal carrying only the tool name is the one this control
    rules out. It must also NOT name the tool that did resolve inside, so a
    control cannot pass against a refusal that lists everything.

    MUTANT: drop the `if elsewhere` arm. `_record` then writes the record and
    prints it, and the control raises because no refusal came out.
    """
    installer = _installer_module('lint_installer_recorded_outside')
    root = Path(tmp)
    tool_dir = root / 'daedalus-lint-tools'
    tool_dir.mkdir()
    inside = tool_dir / 'actionlint'
    inside.write_bytes(b'')
    outside = root / 'elsewhere' / 'shellcheck'
    outside.parent.mkdir()
    outside.write_bytes(b'')
    finder = Which({'actionlint': str(inside), 'shellcheck': str(outside)})
    with mock.patch.object(installer, 'TOOL_DIR', tool_dir), \
            mock.patch.object(installer.shutil, 'which', finder):
        raised = _the_refusal(installer._record)
    _must_name(raised, 'shellcheck', str(tool_dir), str(outside))
    assert 'actionlint' not in str(raised.code), (
        f'the refusal names the tool that DID resolve inside {tool_dir}: '
        f'{raised.code!r}')
    assert finder.calls == sorted(finder.calls) and set(finder.calls) == (
        set(installer.TOOLS)), (
        f'shutil.which was asked for {finder.calls} where the installer '
        f'declares {sorted(installer.TOOLS)}')


def recorded_missing():
    """A tool that does not resolve at all, named one by one.

    The two arms are separate refusals and are separate controls: the one
    above catches a tool answered from elsewhere, this one catches a tool
    answered by nothing. Asserting the phrase the missing arm prints is what
    keeps a control from passing against the other refusal.

    MUTANT: drop the `if missing` arm. `_record` then writes the record naming
    both tools as installed, and the control raises because no refusal came
    out.
    """
    installer = _installer_module('lint_installer_recorded_missing')
    finder = Which({tool: None for tool in installer.TOOLS})
    with mock.patch.object(installer.shutil, 'which', finder):
        raised = _the_refusal(installer._record)
    _must_name(raised, *sorted(installer.TOOLS), 'does not resolve on PATH')
