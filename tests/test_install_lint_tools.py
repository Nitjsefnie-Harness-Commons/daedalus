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
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
# The name a real wheel has, so a refusal about the version can
# tell a wheel of the pinned version from one of another.
DEFAULT_WHEEL = 'shellcheck_py-0.11.0.1-py3-none-any.whl'
SCRIPTS = 'shellcheck_py-0.11.0.1.data/scripts'


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
    payload = _release_asset('actionlint', False)
    with _on('Linux', 'x86_64'), \
            mock.patch.dict(os.environ, {
                'GITHUB_PATH': str(later), 'GITHUB_ENV': str(recorded),
                'PATH': '/usr/bin:/bin'}), \
            mock.patch.dict(
                installer.ACTIONLINT_SHA256,
                {('Linux', 'x86_64'):
                 hashlib.sha256(payload).hexdigest()}), \
            mock.patch.object(installer, 'TOOL_DIR', tools), \
            mock.patch.object(installer, '_fetch',
                              return_value=payload), \
            _installing(installer, [('shellcheck', EXECUTABLE)]):
        assert installer.main() == 0
        installed = tools / 'shellcheck'
        assert installer.shutil.which('shellcheck') == str(installed), (
            'shellcheck did not resolve into the directory this step '
            'installed it into')
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
