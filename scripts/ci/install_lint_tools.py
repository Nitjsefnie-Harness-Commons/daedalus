#!/usr/bin/env python3
"""Install the external tools the test suites shell out to, and record them.

A suite that drives a real binary skips when the binary is absent, so a job
that has not installed one reports green having verified nothing. Every job
that runs a suite runner calls this script; the tool set it declares is what
`tests/test_ci_lint_tools.py` holds each of those jobs to.

`actionlint` is downloaded and checksum-verified rather than piped from an
install script, and the transfer is bounded in size. `shellcheck` is not
fetched here: the PyPI package that ships the binary is pinned in
requirements-test.txt, which every one of these jobs already installs, and
running pip again between the restore and the save of the pip cache would
change what that cache holds.

On a CI runner the installed directory is prepended to PATH for the steps
that follow, and TOOLS is written to $GITHUB_ENV under LINT_TOOLS_ENV. A
control reads that variable back and requires each tool to resolve; a broken
install is a failure there, never a skip.
"""
import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# What a suite may skip on, and therefore what a suite-running job installs.
# The next binary a suite skips on is added here and nowhere else.
TOOLS = ('actionlint', 'shellcheck')
# The name this script writes TOOLS under, and the name the control reads.
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
ACTIONLINT_VERSION = '1.7.12'
# sha256 of each release asset, taken from the release's own checksums.txt.
# Bump the version and this table together. The key is (platform.system(),
# platform.machine().lower()), which is why the Windows row is amd64 and not
# the x86_64 a POSIX host reports: the table is spelled in the same
# normalised values the lookup builds, or the lookup misses a key the table
# plainly has.
ACTIONLINT_SHA256 = {
    ('Linux', 'x86_64'):
        '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8',
    ('Darwin', 'x86_64'):
        '5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644',
    ('Darwin', 'arm64'):
        'aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f',
    ('Windows', 'amd64'):
        '6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9',
}
RELEASE = 'https://github.com/rhysd/actionlint/releases/download'
# What the release calls each architecture, which is not what Python calls it.
ARCHITECTURES = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64',
                 'arm64': 'arm64'}
DOWNLOAD_TIMEOUT = 30
MAX_TRANSFER = 64 * 1024 * 1024
INSTALL_DIR = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir()))
TOOL_DIR = INSTALL_DIR / 'daedalus-lint-tools'


def _asset_name():
    """The release asset this host needs, and the key that pins it.

    One normalised machine string feeds both the table key and the asset
    name, so the two cannot disagree about which platform this is: a key
    the table does not carry fails the install rather than resolving an
    asset nothing was verified against.
    """
    system = platform.system()
    machine = platform.machine().lower()
    key = (system, machine)
    if key not in ACTIONLINT_SHA256:
        raise SystemExit(
            f'no pinned actionlint {ACTIONLINT_VERSION} for {system} '
            f'{machine}; add its sha256 to ACTIONLINT_SHA256 rather than '
            'installing an unpinned one')
    suffix = 'zip' if system == 'Windows' else 'tar.gz'
    name = (f'actionlint_{ACTIONLINT_VERSION}_{system.lower()}_'
            f'{ARCHITECTURES[machine]}.{suffix}')
    return name, key


def _fetch(name):
    """The release asset's bytes, bounded in size, on a per-read timeout.

    `timeout` bounds one socket operation, not the transfer, so a mirror
    dribbling a byte a minute can hold the step for the whole download;
    MAX_TRANSFER is the bound that is real.
    """
    url = f'{RELEASE}/v{ACTIONLINT_VERSION}/{name}'
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        payload = response.read(MAX_TRANSFER + 1)
    if len(payload) > MAX_TRANSFER:
        raise SystemExit(f'{url} served more than {MAX_TRANSFER} bytes')
    return payload


def _verify(payload, key):
    """Refuse a transfer whose digest is not the pinned one."""
    digest = hashlib.sha256(payload).hexdigest()
    if digest != ACTIONLINT_SHA256[key]:
        raise SystemExit(
            f'{digest} is not the pinned sha256 of the {key[0]} '
            f'{ACTIONLINT_VERSION} asset; refusing to install it')


def _extract(payload, destination):
    """Unpack the one executable out of the verified archive.

    The archive kind follows the same `windows` that chose the binary name,
    read once: the two shapes of release asset are a property of the
    platform, and re-deriving it from the host a line later is how a zip
    came to be handed to tarfile.
    """
    windows = platform.system() == 'Windows'
    binary = 'actionlint.exe' if windows else 'actionlint'
    target = destination / binary
    if windows:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if binary not in archive.namelist():
                raise SystemExit(f'the archive carried no {binary}')
            target.write_bytes(archive.read(binary))
    else:
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            member = (archive.extractfile(binary)
                      if binary in archive.getnames() else None)
            if member is None:
                raise SystemExit(f'the archive carried no {binary}')
            target.write_bytes(member.read())
    target.chmod(0o755)
    return target


def install_actionlint():
    """Download, verify and unpack actionlint into the tool directory."""
    name, key = _asset_name()
    payload = _fetch(name)
    _verify(payload, key)
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    target = _extract(payload, TOOL_DIR)
    print(f'actionlint {ACTIONLINT_VERSION} installed at {target}')


def _publish():
    """Put the tool directory on PATH for this process and the steps after."""
    os.environ['PATH'] = f'{TOOL_DIR}{os.pathsep}{os.environ["PATH"]}'
    later = os.environ.get('GITHUB_PATH')
    if later:
        with open(later, 'a', encoding='utf-8') as handle:
            handle.write(f'{TOOL_DIR}\n')


def _record():
    """Write TOOLS where the control reads them, and verify each resolves."""
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        raise SystemExit(
            f'{", ".join(missing)} does not resolve on PATH after this '
            'install. actionlint is installed by this script; shellcheck '
            'comes from shellcheck-py in requirements-test.txt, so a job that '
            'has not installed that file cannot run the suites this script '
            'exists for. This is a failure and not a skip: a suite that skips '
            'here reports success having verified nothing.')
    later = os.environ.get('GITHUB_ENV')
    if later:
        with open(later, 'a', encoding='utf-8') as handle:
            handle.write(f'{LINT_TOOLS_ENV}={",".join(TOOLS)}\n')
    print(f'{LINT_TOOLS_ENV}={",".join(TOOLS)}')
    for tool in TOOLS:
        print(f'  {tool}: {shutil.which(tool)}')


def main():
    install_actionlint()
    _publish()
    _record()
    return 0


if __name__ == '__main__':
    sys.exit(main())
