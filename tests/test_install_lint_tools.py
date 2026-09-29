#!/usr/bin/env python3
"""The lint-tool installer has to work on hosts this one is not.

Every measurement taken while the installer was written was taken on Linux,
and the two defects this suite exists to catch were both Windows-only: a
checksum table keyed on the machine string POSIX reports where CPython
reports `AMD64`, and an archive branch no host could take. Both reached
five legs of the matrix and neither was caught here, because every gate
that ran ran on the one platform. So the platform functions are stubbed
and a real archive of each shape is built here in memory: this suite
fails anywhere, which is the only way a Windows leg is covered before one
runs.

`tests/test_ci_lint_tools.py` is the other half: it holds the jobs to the
tool set this installer declares.
"""
import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _case_fold  # noqa: E402
import _util  # noqa: E402

ROOT = _util.ROOT
INSTALLER_SOURCE = ROOT / 'scripts' / 'ci' / 'install_lint_tools.py'
# What the four pinned keys say the release calls each architecture, and
# whether that release asset is a zip. The installer has to fetch the one
# its table pins and unpack it as the shape it is.
PINNED = {
    ('Linux', 'x86_64'): ('actionlint_1.7.12_linux_amd64.tar.gz', False),
    ('Darwin', 'x86_64'): ('actionlint_1.7.12_darwin_amd64.tar.gz', False),
    ('Darwin', 'arm64'): ('actionlint_1.7.12_darwin_arm64.tar.gz', False),
    ('Windows', 'amd64'): ('actionlint_1.7.12_windows_amd64.zip', True),
}
# What CPython reports on a windows-latest x64 runner. POSIX spells the
# same machine `x86_64`, and a key spelled the POSIX way is a key the
# table does not carry, which is the whole of the first defect.
WINDOWS_X64 = ('Windows', 'AMD64')
EXECUTABLE = b'#!/not/really/an/executable\n'
# The name the control resolves, read through the same constant the
# installer's own refusal names.
_ACTIONLINT = 'actionlint'
# The binary the release ships, and the one every fixture here names: the
# archive member, the unpacked file, and the name a `which` looks for. The
# EXTENSION is added by the platform, never written here.
ACTIONLINT_BINARY = 'actionlint'
# The name a real wheel has, so a refusal about the version can
# tell a wheel of the pinned version from one of another.
DEFAULT_WHEEL = 'shellcheck_py-0.11.0.1-py3-none-any.whl'
SCRIPTS = 'shellcheck_py-0.11.0.1.data/scripts'
# The name the wheel for THIS host carries its member under, which is the
# one thing a fake wheel has to get right: the member name is what lands on
# PATH, and `shutil.which` will not see a bare name on Windows at all. It
# resolves a command plus a PATHEXT extension, and uses a direct match only
# when the command already ends in one — CPython says so in the branch's own
# comment, and the Windows job log is the proof: `_record` printed
# `actionlint.EXE`, an uppercase extension, never a bare name. The real
# wheels' names are the property `test_a_wheel_that_carries_the_wrong_binary
# _is_refused` pins, so this stands in for the wheel and must agree with it.
# The one place in this file that guesses a host's spelling, and the only
# one: the name is a fact about the WHEEL, not about this module, so it
# cannot be derived from the platform and is read from the wheel instead.
WHEEL_SCRIPTS_NAME = 'shellcheck.exe' if os.name == 'nt' else 'shellcheck'


def assert_same_file(resolved, written, what):
    """A resolved path IS the file that was written, on THIS parent.

    Not a string comparison. `shutil.which` on nt builds its answer from
    PATHEXT, whose extensions are upper case, so it returns
    `actionlint.EXE` for a file the installer wrote as `actionlint.exe`.
    Whether those are one entry or two is a property of the PARENT, so it
    is asked of the parent — through `tests/_case_fold.py`, the question
    this tree already asks before every fixture that depends on a name —
    rather than decided here by comparing strings or by branching on
    `os.name`. Neither a string comparison nor a platform branch would do:
    the first reports a working install as a failure, the second is the
    guess that produced every one of the last three Windows rounds.
    """
    assert resolved, f'{what} did not resolve at all'
    assert Path(resolved).parent == written.parent, (
        f'{what} resolved to {resolved!r}, which is not even in the tool '
        f'directory the install published ({written.parent})')
    folding = _case_fold.folds(written.parent)
    if folding:
        assert Path(resolved).name.lower() == written.name.lower(), (
            f'{what} resolved to {resolved!r} and the install wrote '
            f'{written.name!r}; this parent folds case, so the two are '
            'different names')
        return
    assert Path(resolved).name == written.name, (
        f'{what} resolved to {resolved!r} and the install wrote '
        f'{written.name!r}; this parent does not fold case, so the two are '
        'different files')


def _pinned_to(installer, payload):
    """Pin the table entry for THIS host's key to this payload's digest.

    The key is ASKED FOR rather than spelled, for the reason the key exists
    at all: `_asset_name` lowercases `platform.machine()` before building
    it, so a fixture that spells the raw pair patches an entry the
    installer never reads. The pin check then compares the synthetic
    payload against the REAL pinned digest and refuses — which is the check
    working correctly and the fixture being wrong, and the one way to tell
    them apart is to have the fixture ask instead of guess.
    """
    _asset, key = installer._asset_name()
    return mock.patch.dict(
        installer.ACTIONLINT_SHA256,
        {key: hashlib.sha256(payload).hexdigest()})


def _is_windows(host):
    """Whether a `(system, machine)` pair names a Windows host.

    The same predicate the installer branches on, read the same way, so a
    fixture and the code it drives cannot disagree about which platform
    this is.
    """
    return host[0] == 'Windows'


def _installer():
    """The installer module, loaded by path so its `__main__` never runs."""
    return _util.load(INSTALLER_SOURCE, 'lint_installer_platform')


@contextlib.contextmanager
def _on(system, machine):
    """The platform pair the installer reads, as that host reports it."""
    with mock.patch('platform.system', return_value=system), \
            mock.patch('platform.machine', return_value=machine):
        yield


def _zip(member, payload):
    """A real zip archive carrying one member, as the release builds one."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def _tarball(member, payload):
    """A real gzipped tarball carrying one member."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        entry = tarfile.TarInfo(member)
        entry.size = len(payload)
        entry.mode = 0o755
        archive.addfile(entry, io.BytesIO(payload))
    return buffer.getvalue()


def _release_asset(binary, zipped):
    """The bytes the release serves for a platform's `binary`."""
    if zipped:
        return _zip(binary, EXECUTABLE)
    return _tarball(binary, EXECUTABLE)


def test_the_checksum_key_and_the_asset_name_read_one_normalised_machine(tmp):
    """A Windows runner's key resolves, and resolves to what it pins."""
    del tmp
    installer = _installer()
    with _on(*WINDOWS_X64):
        assert installer.platform.machine() == 'AMD64'
        name, key = installer._asset_name()
    assert key == ('Windows', 'amd64'), key
    assert name == PINNED[('Windows', 'amd64')][0], name


def test_every_pinned_key_names_the_asset_and_the_archive_it_carries(tmp):
    """Each row of the table is a key a host reports and an asset it serves.

    Read as a table rather than as four spellings: a key nothing reports
    is a row nothing can fail over, and an asset name whose suffix and
    archive kind disagree is the shape the second defect took.
    """
    del tmp
    installer = _installer()
    assert set(installer.ACTIONLINT_SHA256) == set(PINNED), (
        'the pinned keys and the assets this suite unpacks have drifted '
        f'apart: {sorted(installer.ACTIONLINT_SHA256)}')
    for key, (asset, zipped) in PINNED.items():
        system, machine = key
        with _on(system, machine.upper() if system == 'Windows'
                 else machine):
            name, resolved = installer._asset_name()
        assert resolved == key, (key, resolved)
        assert name == asset, (key, name)
        assert name.endswith('.zip') is zipped, (key, name)
        digest = installer.ACTIONLINT_SHA256[key]
        assert len(digest) == 64, (key, digest)


def test_a_windows_release_asset_unpacks_from_its_zip(tmp):
    """The zip arm is the live arm on Windows, and tarfile is not in it."""
    tmp = Path(tmp)
    installer = _installer()
    destination = tmp / 'tools'
    destination.mkdir()
    with _on(*WINDOWS_X64):
        target = installer._extract(
            _release_asset('actionlint.exe', True), destination)
    assert target == destination / 'actionlint.exe', target
    assert target.read_bytes() == EXECUTABLE
    assert os.access(target, os.X_OK), 'the binary is not executable'


def test_a_unix_release_asset_unpacks_from_its_tarball(tmp):
    """The tarball arm still works, on the shape the release really ships."""
    tmp = Path(tmp)
    installer = _installer()
    destination = tmp / 'tools'
    destination.mkdir()
    with _on('Linux', 'x86_64'):
        target = installer._extract(
            _release_asset('actionlint', False), destination)
    assert target == destination / 'actionlint', target
    assert target.read_bytes() == EXECUTABLE
    assert os.access(target, os.X_OK), 'the binary is not executable'


def test_an_archive_without_the_binary_is_refused_not_silently_empty(tmp):
    """Both arms name the member they looked for when it is not there."""
    tmp = Path(tmp)
    installer = _installer()
    for system, machine, member, zipped in (
            ('Windows', 'AMD64', 'actionlint', True),
            ('Linux', 'x86_64', 'something-else', False)):
        destination = tmp / f'tools-{system}'
        destination.mkdir()
        with _on(system, machine):
            try:
                installer._extract(
                    _release_asset(member, zipped), destination)
            except SystemExit as refusal:
                assert 'actionlint' in str(refusal), refusal
            else:
                raise AssertionError(
                    f'the {system} archive carried no actionlint and the '
                    'installer unpacked it anyway')


def test_a_platform_the_table_does_not_pin_refuses_rather_than_installing(
        tmp):
    """An unpinned platform is a refusal naming the remedy, not a download."""
    del tmp
    installer = _installer()
    with _on('Linux', 'aarch64'):
        try:
            installer._asset_name()
        except SystemExit as refusal:
            assert 'no pinned actionlint' in str(refusal), refusal
            assert 'ACTIONLINT_SHA256' in str(refusal), refusal
        else:
            raise AssertionError(
                'Linux aarch64 is not in ACTIONLINT_SHA256 and the installer '
                'named an asset for it anyway')


def test_the_whole_step_runs_on_a_host_this_machine_is_not(tmp):
    """The end to end step, on Windows, with both defects live at once.

    The first defect masked the second: a key that misses refuses before
    the archive is ever opened, so fixing only the key turns a clean
    refusal into a traceback. One test drives the whole `main` on the
    synthesised host so neither order leaves this green.
    """
    tmp = Path(tmp)
    installer = _installer()
    tools = tmp / 'tools'
    later = tmp / 'path.txt'
    recorded = tmp / 'env.txt'
    payload = _release_asset('actionlint.exe', True)
    with _on(*WINDOWS_X64), \
            mock.patch.dict(os.environ, {
                'GITHUB_PATH': str(later), 'GITHUB_ENV': str(recorded)}), \
            mock.patch.dict(
                installer.ACTIONLINT_SHA256,
                {('Windows', 'amd64'):
                 hashlib.sha256(payload).hexdigest()}), \
            mock.patch.object(installer, 'TOOL_DIR', tools), \
            mock.patch.object(installer, '_fetch',
                              return_value=payload), \
            _installing(installer, [('shellcheck.exe', EXECUTABLE)]), \
            mock.patch.object(installer, 'shutil', mock.Mock(
                which=lambda tool: str(tools / f'{tool}.exe'))):
        # The stub answers from inside the tool directory, because that is
        # the only place `_record` will accept an answer from. A stub that
        # answered from anywhere else used to pass and does not now — which
        # is the check doing its job, not the test being wrong.
        assert installer.main() == 0
    installed = tools / 'actionlint.exe'
    assert installed.read_bytes() == EXECUTABLE
    assert str(tools) in later.read_text(encoding='utf-8').split('\n')
    assert (f'{installer.LINT_TOOLS_ENV}='
            f'{",".join(installer.TOOLS)}') in recorded.read_text(
                encoding='utf-8')


def _wheel(directory, members, name=None, scheme=None):
    """A real wheel carrying `members`, the way the release's wheels do.

    Built rather than stubbed, because the code under test OPENS the
    wheel and reads the member out of it: a fake that only satisfied the
    call would leave the whole extraction path untested, which is the part
    the Windows failure was in. Each member is `(name-in-the-scripts-
    scheme, bytes)`; a directory entry can be asked for with a trailing
    slash, which the manylinux wheel really does carry.
    """
    wheel = Path(directory) / (name or DEFAULT_WHEEL)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for member, payload in members:
            archive.writestr(f'{scheme or SCRIPTS}/{member}', payload)
    return wheel


def _downloaded(members, name=None, scheme=None):
    """Stub `pip download`, handing back a real wheel in its staging dir.

    pip owns the network and the platform choice, and neither is what
    this file is testing; what it tests is what the installer does with
    the wheel pip brought. The command is asserted so a change in the
    resolution — losing `--only-binary`, or `--no-deps` — is visible.
    """
    def run(command, **kwargs):
        assert kwargs.get('check') is True, 'a pip failure must fail the step'
        assert '--only-binary=:all:' in command, (
            'the sdist fallback would try to build the tool from source on '
            'a runner')
        assert '--no-deps' in command, 'nothing else is wanted from the index'
        return subprocess.CompletedProcess(
            command, 0, '', _wheel(command[command.index('--dest') + 1],
                                   members, name, scheme))
    return run


def _installing(installer, members, name=None, scheme=None):
    """The wheel pip brings, and nothing else stubbed."""
    return mock.patch.object(
        installer.subprocess, 'run',
        side_effect=_downloaded(members, name, scheme))


def test_the_shellcheck_version_is_written_down_exactly_once(tmp):
    """The pin is read out of the requirements file, not repeated here.

    Two copies of a pin is a pin that will drift: the first person to bump
    one of them gets a job that installs a version no requirements file
    names, which is the defect this change exists to close, wearing the
    costume of the fix. So the installer's own source must not spell a
    REQUIREMENT for shellcheck at the version the file names.

    The requirement form and not the bare version, because a docstring
    saying which version a job used to resolve from the runner image is
    evidence a reader wants, and forbidding prose about a version would buy
    nothing.
    """
    del tmp
    installer = _installer()
    version = installer.shellcheck_pin()
    assert re.fullmatch(r'\d+(\.\d+)*', version), version
    source = INSTALLER_SOURCE.read_text(encoding='utf-8')
    requirement = f'{installer.SHELLCHECK_PACKAGE}=={version}'
    assert requirement not in source, (
        f'the installer spells {requirement} in its own source as well as '
        'reading it from requirements-test.txt; one of the two will be the '
        'one that is running')


def test_a_requirements_file_that_names_no_single_version_is_refused(tmp):
    """No pin, or two pins, is a refusal naming the file to fix."""
    tmp = Path(tmp)
    installer = _installer()
    for label, body in {
            'no pin at all': 'coverage==7.16.1\nPyYAML==6.0.3\n',
            'an unpinned requirement': 'shellcheck-py\n',
            'the same version twice': 'shellcheck-py==0.11.0.1\n'
                                      'shellcheck-py==0.11.0.1\n',
            'two different versions': 'shellcheck-py==0.11.0.1\n'
                                      'shellcheck-py==0.10.0.1\n',
    }.items():
        requirements = tmp / f'requirements-{len(label)}-{body[7]}.txt'
        requirements.write_text(body, encoding='utf-8')
        with mock.patch.object(installer, 'REQUIREMENTS', requirements):
            try:
                installer.shellcheck_pin()
            except SystemExit as refusal:
                assert 'shellcheck' in str(refusal), refusal
            else:
                raise AssertionError(
                    f'a requirements file with {label} produced a version, '
                    'and the step would install something no pin names')


def test_the_member_name_comes_from_the_wheel_not_from_a_platform_branch(tmp):
    """`shellcheck` here, `shellcheck.exe` there, and the wheel decides.

    The branch this replaces is what broke: pip's `--target` put a
    scripts-scheme member somewhere no PATH entry reached on
    windows-latest. Nothing here computes the name from the platform, so
    there is no second answer to keep correct.
    """
    tmp = Path(tmp)
    installer = _installer()
    for name in ('shellcheck', 'shellcheck.exe'):
        tools = tmp / name
        with _installing(installer, [(name, b'#!/not/really\n')]), \
                mock.patch.object(installer, 'TOOL_DIR', tools):
            installer.install_shellcheck()
        assert (tools / name).read_bytes() == b'#!/not/really\n', name
        assert [p.name for p in tools.iterdir()] == [name], sorted(
            p.name for p in tools.iterdir())


def test_the_real_pin_table_refuses_the_fixture_payload(tmp):
    """The seam that lets a test pin a synthetic payload is a SEAM.

    `_pinned_to` puts a digest into `ACTIONLINT_SHA256` so a fixture can
    install a payload it built, and that is the only way any test here can
    reach the unpack path at all. It is also, read the wrong way, a way to
    make the installer accept an asset no pin names — which is the defect
    this whole change exists to close, and the one that left a job green
    having linted nothing before it.

    So the refusal is pinned from the OTHER side, against the table as it
    ships: the real pinned digest, this payload, and a refusal. If a
    change ever made the production path read anything other than its own
    table, or made `_verify` lenient, this goes red — and the seam stops
    being a way to accept anything the moment it could be.
    """
    del tmp
    installer = _installer()
    payload = _release_asset('actionlint', _is_windows((
        installer.platform.system(), installer.platform.machine())))
    digest = hashlib.sha256(payload).hexdigest()
    for key, pinned in sorted(installer.ACTIONLINT_SHA256.items()):
        assert digest != pinned, (
            f'this fixture payload now IS the pinned {key[0]} asset, so the '
            'seam it uses would be indistinguishable from a real download '
            'and the refusal below would prove nothing')
        try:
            installer._verify(payload, key)
        except SystemExit as refusal:
            assert 'not the pinned sha256' in str(refusal), refusal
        else:
            raise AssertionError(
                f'the shipped table accepted a synthetic payload for {key}, '
                'so anything that reaches _verify with the wrong bytes is '
                'installed, and that is the defect this branch closes')


def test_the_binary_the_installer_names_on_this_host_is_one_which_can_find(
        tmp):
    """The name and the resolver must come from the same platform.

    A run that got past `shellcheck` and then failed on `actionlint` was
    this, and nothing to do with the install: the fixture stubbed
    `platform.system()` to say Linux while the process was Windows, so the
    installer named the binary the POSIX way and the `shutil.which` that
    then went looking for it followed the Windows rule — a command plus a
    PATHEXT extension — and found nothing. Stated as a property rather
    than a platform, so it holds wherever it is run: the binary the
    installer writes under the HOST's own pair is one this process's
    `which` can resolve.
    """
    tmp = Path(tmp)
    installer = _installer()
    host = (installer.platform.system(), installer.platform.machine())
    destination = tmp / 'tools'
    destination.mkdir()
    binary = (f'{ACTIONLINT_BINARY}.exe' if _is_windows(host)
              else ACTIONLINT_BINARY)
    with _on(*host), mock.patch.dict(
            os.environ, {'PATH': str(destination) + os.pathsep
                         + os.environ['PATH']}):
        written = installer._extract(
            _release_asset(binary, _is_windows(host)), destination)
        resolved = shutil.which(_ACTIONLINT, path=os.environ['PATH'])
    assert_same_file(
        resolved, written,
        f'the binary the installer names on {host[0]}')


def test_the_tool_directory_reaches_this_process_and_not_only_github_path(
        tmp):
    """Why an in-process `shutil.which` can mean anything at all.

    `$GITHUB_PATH` reaches SUBSEQUENT steps, never the one that writes it,
    so a check reading PATH inside this process would be reading a PATH
    this step had not yet changed — and every assertion about residency
    would be about whatever the runner image happened to carry. The
    installer's answer is that it does both: it prepends the tool
    directory to THIS process's own PATH and writes the same line to
    `$GITHUB_PATH` for the steps after. Both halves are pinned here, so
    neither can be dropped without this going red.
    """
    tmp = Path(tmp)
    installer = _installer()
    later = tmp / 'path.txt'
    with mock.patch.dict(os.environ, {'GITHUB_PATH': str(later),
                                      'PATH': '/usr/bin:/bin'}), \
            mock.patch.object(installer, 'TOOL_DIR', tmp / 'tools'):
        installer._publish()
        # Read inside the block: `patch.dict` restores PATH on the way out,
        # and the claim is about the moment the step published.
        on_path = os.environ['PATH'].split(os.pathsep)
    assert str(tmp / 'tools') in on_path, (
        'the tool directory is not on this process PATH, so an in-process '
        'resolution of either tool is really a resolution of the runner '
        "image's copy, and the residency guard proves nothing")
    assert str(tmp / 'tools') in later.read_text(encoding='utf-8').split(
        '\n'), (
        'the tool directory is not on GITHUB_PATH, so the steps that run '
        'the suites would not see it at all')


def test_a_wheel_that_carries_the_wrong_binary_is_refused(tmp):
    """Each of the four supply-chain shapes, refused in the file's register.

    No member, more than one, a name that is not the declared tool, and a
    version that is not the pin. A wheel that would put something else on
    PATH is not one to install, and the directory entry the manylinux
    wheel really carries must not be counted as a second member — that
    error would make every Linux leg refuse a correct wheel.
    """
    tmp = Path(tmp)
    installer = _installer()
    cases = {
        'no scripts member': ([('mod.py', b'x')], 'carries no'),
        'two scripts members': ([('shellcheck', b'x'), ('other', b'y')],
                                'carries 2'),
        'a member that is not the tool': ([('shellcheck-wrapper', b'x')],
                                          'is not the shellcheck'),
        'a directory entry beside one binary': (
            [('scripts/', b''), ('shellcheck', b'x')], None),
    }
    for label, (members, expected) in cases.items():
        tools = tmp / label.replace(' ', '-')
        # A member that is not under the scripts scheme at all is the
        # first shape; one that is under it but is not the tool is the
        # last, and the two are different refusals.
        scheme = (SCRIPTS.replace('scripts', 'purelib')
                  if label == 'no scripts member' else None)
        with _installing(installer, members, scheme=scheme), \
                mock.patch.object(installer, 'TOOL_DIR', tools):
            if expected is None:
                # The one case that must SUCCEED: the directory entry is
                # not a second binary.
                installer.install_shellcheck()
                assert (tools / 'shellcheck').is_file()
                continue
            try:
                installer.install_shellcheck()
            except SystemExit as refusal:
                assert expected in str(refusal), f'{label}: {refusal}'
            else:
                raise AssertionError(
                    f'a wheel with {label} was installed rather than '
                    'refused')
    # A wheel whose version is not the pin is refused before it is opened,
    # and this one goes through `install_shellcheck` because that is where
    # the check lives: the placer is handed a wheel it has already agreed
    # about and would be testing nothing if asked to re-check.
    with _installing(installer, [('shellcheck', b'x')],
                     name='shellcheck_py-0.9.0-py3-none-any.whl'), \
            mock.patch.object(installer, 'TOOL_DIR', tmp / 'wrong-version'):
        try:
            installer.install_shellcheck()
        except SystemExit as refusal:
            assert 'not that version' in str(refusal), refusal
        else:
            raise AssertionError('a wheel of another version was installed')


def test_shellcheck_resolves_from_the_installer_not_from_the_image(tmp):
    """The whole point, on a PATH that has neither binary on it.

    `timed` used to pass this step because the ubuntu-latest image carries
    a shellcheck of its own, at a version no pin names, and a check that
    only asks "does it resolve" cannot tell that from an install. This
    runs the real entry point with a PATH of nothing, and then looks at
    WHERE the binary it found lives.
    """
    tmp = Path(tmp)
    installer = _installer()
    tools = tmp / 'tools'
    later = tmp / 'path.txt'
    recorded = tmp / 'env.txt'
    # The host's OWN platform pair, not a claim about a different one. A
    # fixture that stubs `platform.system()` to say Linux while running on a
    # Windows process makes the installer name the binary the POSIX way, and
    # then asserts about it with a `which` that follows Windows' rule — a
    # fixture that does not know the rules of the platform it is on, which
    # is what the other two failures in this round were too. Stabbing the
    # real values back keeps the checksum map keyed the way `_asset_name`
    # builds it, without pretending this is somewhere it is not.
    host = (installer.platform.system(), installer.platform.machine())
    payload = _release_asset('actionlint', _is_windows(host))
    with _on(*host), \
            mock.patch.dict(os.environ, {
                'GITHUB_PATH': str(later), 'GITHUB_ENV': str(recorded),
                'PATH': '/usr/bin:/bin'}), \
            _pinned_to(installer, payload), \
            mock.patch.object(installer, 'TOOL_DIR', tools), \
            mock.patch.object(installer, '_fetch', return_value=payload), \
            _installing(installer, [(WHEEL_SCRIPTS_NAME, EXECUTABLE)]):
        assert installer.main() == 0
        landed = tools / WHEEL_SCRIPTS_NAME
        assert landed.is_file(), sorted(p.name for p in tools.iterdir())
        resolved = installer.shutil.which('shellcheck')
        assert_same_file(resolved, landed, 'shellcheck')
        # And the OTHER tool, whose name the installer derives from the
        # platform rather than from anything in a fixture. It is here for
        # the same reason the failure was: a run that got this far and
        # failed on actionlint means the fixture, not the install, was
        # wrong.
        actionlint = installer.shutil.which('actionlint')
        assert_same_file(actionlint, tools / ACTIONLINT_BINARY,
                         'actionlint')
        assert str(tools) in later.read_text(
            encoding='utf-8').split('\n'), (
            'the tool directory is not on the PATH the later steps '
            'inherit, so a suite would find whatever the runner image '
            'happens to carry instead')
    assert recorded.read_text(encoding='utf-8')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='installtools_')


if __name__ == '__main__':
    raise SystemExit(main())
