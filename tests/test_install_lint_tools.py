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
import hashlib
import io
import os
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


def _installer():
    """The installer module, loaded by path so its `__main__` never runs."""
    return _util.load(INSTALLER_SOURCE, 'lint_installer_platform')


def _on(system, machine):
    """Patch the platform pair the installer reads, as that host reports it."""
    return mock.patch.multiple(
        'platform', 'system', 'machine',
        system=lambda: system, machine=lambda: machine)


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
            mock.patch.object(installer, 'shutil', mock.Mock(
                which=lambda tool: f'C:\\hosted-tool\\{tool}.exe')):
        assert installer.main() == 0
    installed = tools / 'actionlint.exe'
    assert installed.read_bytes() == EXECUTABLE
    assert str(tools) in later.read_text(encoding='utf-8').split('\n')
    assert (f'{installer.LINT_TOOLS_ENV}='
            f'{",".join(installer.TOOLS)}') in recorded.read_text(
                encoding='utf-8')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='installtools_')


if __name__ == '__main__':
    raise SystemExit(main())
