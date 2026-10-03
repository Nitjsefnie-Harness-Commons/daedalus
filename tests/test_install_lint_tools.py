#!/usr/bin/env python3
"""The lint-tool installer has to work on hosts this one is not.

Every measurement taken while it was written was taken on Linux, and every
defect this suite exists to catch was Windows-only: a checksum key CPython
never returns there, an archive branch no host could take, a wheel member
placed where no PATH entry reached. So the platform functions are stubbed
and a real archive of each shape is built in memory — this suite fails
anywhere, which is the only way a Windows leg is covered before one runs.
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
# What CPython reports on a windows-latest x64 runner. POSIX spells the
# same machine `x86_64`, and the table carries no such key: the first defect.
WINDOWS_HOST = ('Windows', 'AMD64')
WINDOWS_X64 = WINDOWS_HOST
# (host pair, the asset the release names for it, whether it is a zip).
# The asset names are SPELLED on purpose: they are the values this suite
# asserts the installer produces, and an expectation derived from the code
# under test asserts only that the code agrees with itself. The KEYS are
# not spelled — `_key_for` asks, because a key is built and has moved.
# Spelled here for the reason the table is: see above.
_BUILD = 'actionlint_1.7.12-queue.1_'
PINNED = (
    (('Linux', 'x86_64'), _BUILD + 'linux_amd64.tar.gz', False),
    (('Darwin', 'x86_64'), _BUILD + 'darwin_amd64.tar.gz', False),
    (('Darwin', 'arm64'), _BUILD + 'darwin_arm64.tar.gz', False),
    (WINDOWS_HOST, _BUILD + 'windows_amd64.zip', True),
)
EXECUTABLE = b'#!/not/really/an/executable\n'
_ACTIONLINT = 'actionlint'
# The binary the release ships, and the one every fixture names. The
# EXTENSION is added by the platform, never written here.
ACTIONLINT_BINARY = 'actionlint'
# A real wheel's name, so a version refusal can tell it from another.
DEFAULT_WHEEL = 'shellcheck_py-0.11.0.1-py3-none-any.whl'
SCRIPTS = 'shellcheck_py-0.11.0.1.data/scripts'


def assert_same_file(resolved, written, what):
    """Not a string comparison. `which` on nt builds its answer from
        PATHEXT, whose extensions are upper case, so it returns
        `actionlint.EXE` for a file written `actionlint.exe`. Whether those
        are one entry is a property of the PARENT, so it is asked of the
        parent through `tests/_case_fold.py` rather than decided by string
        comparison or by branching on `os.name` — the guess that produced
        three of the last four Windows rounds."""
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

        Asked for, not spelled: `_asset_name` lowercases the machine, so a
        fixture spelling the raw pair patches an entry the installer never
        reads, and the pin check then refuses the synthetic payload
        against the REAL digest — the check right and the fixture wrong."""
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


def _host(installer):
    """The `(system, machine)` pair this host reports to the installer."""
    return (installer.platform.system(), installer.platform.machine())


def _shellcheck_tool(installer):
    """The other tool the installer declares, read out of its own set.

    Not spelled, so a change to `TOOLS` cannot leave a fixture calling
    something the installer no longer installs.
    """
    other = [tool for tool in installer.TOOLS if tool != ACTIONLINT_BINARY]
    assert len(other) == 1, installer.TOOLS
    return other[0]


def _key_for(installer, host):
    """The checksum key the installer builds for `host`, by asking it.

        Never the raw pair: `_asset_name` lowercases the machine."""
    with _on(*host):
        _asset, key = installer._asset_name()
    return key


def actionlint_binary(host):
    """The name the installer gives the actionlint executable on `host`.

        One place: this file used to spell it three times, each written
        when the installer named it differently."""
    return (f'{ACTIONLINT_BINARY}.exe' if _is_windows(host)
            else ACTIONLINT_BINARY)


def wheel_member(host, tool):
    """The member name the wheel for `host` carries `tool` under.

        A fact about the WHEEL, which the installer reads off it rather
        than computing; read from the platform once, here."""
    return f'{tool}.exe' if _is_windows(host) else tool


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
    assert key == _key_for(installer, WINDOWS_X64), key
    assert name == PINNED[3][1], name


def test_every_pinned_key_names_the_asset_and_the_archive_it_carries(tmp):
    """Each row of the table is a key a host reports and an asset it serves.

    Read as a table rather than as four spellings: a key nothing reports
    is a row nothing can fail over, and an asset name whose suffix and
    archive kind disagree is the shape the second defect took.
    """
    del tmp
    installer = _installer()
    assert set(installer.ACTIONLINT_SHA256) == {
        _key_for(installer, host) for host, _asset, _zipped in PINNED}, (
        'the keys the installer builds and the hosts this suite unpacks for '
        f'have drifted apart: {sorted(installer.ACTIONLINT_SHA256)}')
    for host, asset, zipped in PINNED:
        key = _key_for(installer, host)
        with _on(*host):
            name, resolved = installer._asset_name()
        assert resolved == key, (host, resolved)
        assert name == asset, (host, name)
        assert name.endswith('.zip') is zipped, (host, name)
        assert len(installer.ACTIONLINT_SHA256[key]) == 64, (host, key)


def test_a_windows_release_asset_unpacks_from_its_zip(tmp):
    """The zip arm is the live arm on Windows, and tarfile is not in it."""
    tmp = Path(tmp)
    installer = _installer()
    destination = tmp / 'tools'
    destination.mkdir()
    with _on(*WINDOWS_X64):
        binary = actionlint_binary(WINDOWS_X64)
        target = installer._extract(
            _release_asset(binary, True), destination)
    assert target == destination / binary, target
    assert target.read_bytes() == EXECUTABLE
    assert os.access(target, os.X_OK), 'the binary is not executable'


def test_a_unix_release_asset_unpacks_from_its_tarball(tmp):
    """The tarball arm still works, on the shape the release really ships."""
    tmp = Path(tmp)
    installer = _installer()
    destination = tmp / 'tools'
    destination.mkdir()
    host = ('Linux', 'x86_64')
    with _on(*host):
        binary = actionlint_binary(host)
        target = installer._extract(
            _release_asset(binary, False), destination)
    assert target == destination / binary, target
    assert target.read_bytes() == EXECUTABLE
    assert os.access(target, os.X_OK), 'the binary is not executable'


def test_an_archive_without_the_binary_is_refused_not_silently_empty(tmp):
    """Both arms name the member they looked for when it is not there."""
    tmp = Path(tmp)
    installer = _installer()
    # A member that is NOT the binary on either platform, or the archive
    # carries what was asked for and there is nothing to refuse. Spelled
    # deliberately, because this case is about a name that is wrong.
    for host, zipped in ((WINDOWS_X64, True), (('Linux', 'x86_64'), False)):
        member = 'something-else'
        destination = tmp / f'tools-{_host_slug(host)}'
        destination.mkdir()
        with _on(*host):
            try:
                installer._extract(
                    _release_asset(member, zipped), destination)
            except SystemExit as refusal:
                assert ACTIONLINT_BINARY in str(refusal), refusal
            else:
                raise AssertionError(
                    f'the {host[0]} archive carried no '
                    f'{ACTIONLINT_BINARY} '
                    'and the installer unpacked it anyway')


def test_every_name_this_file_uses_is_the_one_the_installer_uses(tmp):
    """This file's names against the installer's, for every pinned host.

        A hand-spelled name outliving the code that stopped spelling it is
        the condition, and four of them did it. One helper each now; this
        pins the helpers against `_extract` and `_asset_name` themselves."""
    tmp = Path(tmp)
    installer = _installer()
    for host, asset, zipped in PINNED:
        key = _key_for(installer, host)
        with _on(*host):
            name, resolved = installer._asset_name()
            destination = tmp / f'tools-{_host_slug(host)}'
            destination.mkdir()
            written = installer._extract(
                _release_asset(actionlint_binary(host), zipped), destination)
            assert name == asset, (host, name)
            assert resolved == key, (host, resolved)
            assert written == destination / actionlint_binary(host), (
                f'{host}: this file calls the binary '
                f'{actionlint_binary(host)!r} and the installer wrote '
                f'{written.name!r}')
            assert [p.name for p in destination.iterdir()] == [
                actionlint_binary(host)], sorted(
                    p.name for p in destination.iterdir())


def _host_slug(host):
    """A directory name for one host, carrying BOTH halves of its pair."""
    return '-'.join(part.lower() for part in host)


def test_the_installer_on_a_forced_windows_host_produces_windows_names(tmp):
    """The Windows arm, run HERE, with the platform seams forced at once.

        Five red Windows runs found five real defects while 101 local
        suites read green, and every one was knowable by forcing the host."""
    tmp = Path(tmp)
    installer = _installer()
    with _forced_windows():
        host = _host(installer)
        assert host == WINDOWS_HOST, host
        key = _key_for(installer, host)
        assert key in installer.ACTIONLINT_SHA256, (
            f'the installer built a key {key} that its own table does not '
            'carry, so a Windows host would be refused before it installed '
            'anything')
        name, _resolved = installer._asset_name()
        assert name.endswith('.zip'), name
        destination = tmp / 'tools'
        destination.mkdir()
        written = installer._extract(
            _release_asset(actionlint_binary(host), True), destination)
        assert written.name == actionlint_binary(host) == 'actionlint.exe', (
            written.name)
        # The RESOLVER is deliberately absent: it is unreachable here
        # (see `_forced_windows`), so every property that depends on it is
        # a property of the RUNNER rather than of the installer.


@contextlib.contextmanager
def _forced_windows():
    """Force the platform seams the INSTALLER reads, to a Windows one.

        `os.name` is NOT patched: `shutil` binds its `nt` module at IMPORT
        time from it, so no later patch supplies one, and patching it makes
        `pathlib` refuse to build a `WindowsPath` here. The win32 branch of
        `shutil.which` is therefore unreachable without a Windows
        interpreter, so the resolver is left to the runner. Nothing in
        production was changed to make this easier."""
    with mock.patch.object(sys, 'platform', 'win32'), \
            mock.patch('platform.system', return_value='Windows'), \
            mock.patch('platform.machine', return_value='AMD64'):
        yield


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
    """The end to end step, on a Windows host, with both archive defects
        live at once."""
    tmp = Path(tmp)
    installer = _installer()
    tools = tmp / 'tools'
    later = tmp / 'path.txt'
    recorded = tmp / 'env.txt'
    tool = _shellcheck_tool(installer)
    payload = _release_asset(actionlint_binary(WINDOWS_X64), True)
    with _on(*WINDOWS_X64), \
            mock.patch.dict(os.environ, {
                'GITHUB_PATH': str(later), 'GITHUB_ENV': str(recorded)}), \
            mock.patch.dict(
                installer.ACTIONLINT_SHA256,
                {_key_for(installer, WINDOWS_X64):
                 hashlib.sha256(payload).hexdigest()}), \
            mock.patch.object(installer, 'TOOL_DIR', tools), \
            mock.patch.object(installer.actionlint_asset, 'fetch',
                              return_value=payload), \
            _installing(installer, [(wheel_member(WINDOWS_X64, tool),
                                     EXECUTABLE)]), \
            mock.patch.object(installer, 'shutil', mock.Mock(
                which=lambda tool: str(tools / f'{tool}.exe'))):
        # The stub answers from inside the tool directory, the only place
        # `_record` accepts. A stub from anywhere else used to pass and
        # does not now: the check working, not the test being wrong.
        assert installer.main() == 0
    installed = tools / actionlint_binary(WINDOWS_X64)
    assert installed.read_bytes() == EXECUTABLE
    assert str(tools) in later.read_text(encoding='utf-8').split('\n')
    assert (f'{installer.LINT_TOOLS_ENV}='
            f'{",".join(installer.TOOLS)}') in recorded.read_text(
                encoding='utf-8')


def _wheel(directory, members, name=None, scheme=None):
    """A real wheel carrying `members`, the way the release's wheels do.

        Built rather than stubbed, because the code under test OPENS the
        wheel: a fake that only satisfied the call would leave the whole
        extraction path untested, which is where the Windows failure was.
        A member is `(name-in-the-scripts-scheme, bytes)`; a trailing
        slash asks for the directory entry the manylinux wheel carries."""
    wheel = Path(directory) / (name or DEFAULT_WHEEL)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for member, payload in members:
            archive.writestr(f'{scheme or SCRIPTS}/{member}', payload)
    return wheel


def _downloaded(members, name=None, scheme=None):
    """Stub `pip download`, handing back a real wheel in its staging dir.

        pip owns the network and the platform choice and neither is under
        test; what is under test is what the installer does with the wheel
        it was given. The command is asserted, so losing `--only-binary`
        or `--no-deps` is visible."""
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
    """The version in the requirements file is not written here too.

        Two copies of a pin will drift. The REQUIREMENT form is checked,
        not the bare version: prose about which version a job used to
        resolve is evidence a reader wants."""
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
        there is no second answer to keep correct."""
    tmp = Path(tmp)
    installer = _installer()
    tool = _shellcheck_tool(installer)
    for name in (wheel_member(('Linux', 'x86_64'), tool),
                 wheel_member(WINDOWS_X64, tool)):
        tools = tmp / name
        with _installing(installer, [(name, b'#!/not/really\n')]), \
                mock.patch.object(installer, 'TOOL_DIR', tools):
            installer.install_shellcheck()
        assert (tools / name).read_bytes() == b'#!/not/really\n', name
        assert [p.name for p in tools.iterdir()] == [name], sorted(
            p.name for p in tools.iterdir())


def test_the_real_pin_table_refuses_the_fixture_payload(tmp):
    """The shipped table refuses this fixture's own payload, every key.

        The seam is a SEAM: read the wrong way it would make the
        installer accept an asset no pin names, which is the defect this
        change exists to close. Pinned from the other side, so a lenient
        `_verify` goes red here."""
    del tmp
    installer = _installer()
    host = _host(installer)
    payload = _release_asset(actionlint_binary(host), _is_windows(host))
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
    """The binary the installer names on this host is one `which` finds.

        A run that passed `shellcheck` then failed on `actionlint` was a
        fixture stubbing the platform to say Linux on a Windows process.
        Stated as a property, not a platform, so it holds wherever the
        suite runs."""
    tmp = Path(tmp)
    installer = _installer()
    host = (installer.platform.system(), installer.platform.machine())
    destination = tmp / 'tools'
    destination.mkdir()
    binary = actionlint_binary(host)
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
    """The tool directory reaches this process AND the steps after.

        `$GITHUB_PATH` reaches only LATER steps, so an in-process check
        depends on the installer also prepending to this process's own
        PATH. Both halves are pinned, so neither drops silently."""
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

        No member, more than one, a name that is not the declared tool, a
        version that is not the pin. The directory entry the manylinux
        wheel really carries must NOT count as a second member: that error
        would make every Linux leg refuse a correct wheel."""
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
    # The version refusal goes through `install_shellcheck`, which is
    # where the check lives; the placer is handed a wheel already agreed.
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

        One job used to pass because the ubuntu-latest image carries a
        shellcheck at a version no pin names, and "does it resolve" cannot
        tell that from an install. This runs the real entry point and then
        looks at WHERE the binary it found lives."""
    tmp = Path(tmp)
    installer = _installer()
    tools = tmp / 'tools'
    later = tmp / 'path.txt'
    recorded = tmp / 'env.txt'
    # The host's OWN pair, not a claim about another one: stubbing the
    # platform to say Linux on a Windows process made the installer name
    # the binary the POSIX way and a `which` following Windows' rule miss
    # it. Restubbing the real values keeps the map keyed as `_asset_name`
    # builds it, without pretending this is somewhere it is not.
    host = _host(installer)
    tool = _shellcheck_tool(installer)
    member = wheel_member(host, tool)
    payload = _release_asset(actionlint_binary(host), _is_windows(host))
    with _on(*host), \
            mock.patch.dict(os.environ, {
                'GITHUB_PATH': str(later), 'GITHUB_ENV': str(recorded),
                'PATH': '/usr/bin:/bin'}), \
            _pinned_to(installer, payload), \
            mock.patch.object(installer, 'TOOL_DIR', tools), \
            mock.patch.object(installer.actionlint_asset, 'fetch',
                              return_value=payload), \
            _installing(installer, [(member, EXECUTABLE)]):
        assert installer.main() == 0
        landed = tools / member
        assert landed.is_file(), sorted(p.name for p in tools.iterdir())
        resolved = installer.shutil.which(tool)
        assert_same_file(resolved, landed, tool)
        # And the OTHER tool, whose name the installer derives from the
        # platform: a run that got this far and failed on actionlint means
        # the fixture was wrong, not the install.
        binary = installer.shutil.which(ACTIONLINT_BINARY)
        assert_same_file(binary, tools / actionlint_binary(host),
                         ACTIONLINT_BINARY)
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
