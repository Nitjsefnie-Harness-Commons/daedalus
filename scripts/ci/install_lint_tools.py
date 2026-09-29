#!/usr/bin/env python3
"""Install the external tools the test suites shell out to, and record them.

A suite that drives a real binary skips when the binary is absent, so a job
that has not installed one reports green having verified nothing. Every job
that runs a suite runner calls this script; the tool set it declares is what
`tests/test_ci_lint_tools.py` holds each of those jobs to.

`actionlint` is downloaded and checksum-verified rather than piped from an
install script, and the transfer is bounded so a hung mirror fails the step
instead of holding the runner. `shellcheck` is not fetched here: the PyPI
package that ships the binary is pinned in requirements-test.txt, which every
one of these jobs already installs, and running pip again between the restore
and the save of the pip cache would change what that cache holds.

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

ROOT = Path(__file__).resolve().parents[2]
# What a suite may skip on, and therefore what a suite-running job installs.
# The next binary a suite skips on is added here and nowhere else.
TOOLS = ('actionlint', 'shellcheck')
# The name this script writes TOOLS under, and the name the control reads.
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
ACTIONLINT_VERSION = '1.7.12'
# sha256 of each release asset, taken from the release's own checksums.txt.
# Bump the version and this table together.
ACTIONLINT_SHA256 = {
    ('Linux', 'x86_64'):
        '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8',
    ('Darwin', 'x86_64'):
        '5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644',
    ('Darwin', 'arm64'):
        'aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f',
    ('Windows', 'x86_64'):
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
    """The release asset this host needs, and the machine it is for."""
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)
    if key not in ACTIONLINT_SHA256:
        raise SystemExit(
            f'no pinned actionlint {ACTIONLINT_VERSION} for {system} '
            f'{machine}; add its sha256 to ACTIONLINT_SHA256 rather than '
            'installing an unpinned one')
    suffix = 'zip' if system == 'Windows' else 'tar.gz'
    name = (f'actionlint_{ACTIONLINT_VERSION}_{system.lower()}_'
            f'{ARCHITECTURES[machine.lower()]}.{suffix}')
    return name, key


def _fetch(name):
    """The release asset's bytes, bounded in both time and size."""
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
    return digest


def _extract(payload, destination):
    """Unpack the one executable out of the verified archive."""
    windows = platform.system() == 'Windows'
    binary = 'actionlint.exe' if windows else 'actionlint'
    target = destination / binary
    if binary.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extract(binary, destination)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            member = archive.extractfile(binary)
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
    return target


def _publish(path):
    """Put the tool directory on PATH for this process and the steps after."""
    entry = f'{TOOL_DIR}{os.pathsep}{os.environ["PATH"]}'
    os.environ['PATH'] = entry
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
    _publish(TOOL_DIR)
    _record()
    return 0


if __name__ == '__main__':
    sys.exit(main())
