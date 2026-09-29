#!/usr/bin/env python3
"""Install the external tools the test suites shell out to, and record them.

A suite that drives a real binary skips when the binary is absent, so a job
that has not installed one reports green having verified nothing. Every job
that reaches the suites calls this script; the tool set it declares is what
`tests/test_ci_lint_tools.py` holds each of those jobs to.

`actionlint` is downloaded and checksum-verified rather than piped from an
install script, and the transfer is bounded in size. `shellcheck` is
installed from its pinned wheel, at the pin READ OUT OF
`requirements-test.txt` rather than written here: a version pin spelled in
two files is a pin that will drift, and the whole point of a pin is that
the thing running is the thing named. `requirements-test.txt` keeps the pin
it always had, and the three jobs that install that file into their own
environment still resolve a shellcheck from it — at the same version,
because it is the same line of the same file. This script's copy lands in
the tool directory, which is prepended to PATH, so on those three it is the
copy that wins; that is the uniformity the pin is for, not a shadow of a
different version.

A runner image shipping a shellcheck of its own is not a supply route this
relies on. It was, once: the `timed` job invokes its interpreters by
absolute path, so a venv's console scripts are invisible to the suites
running under it, and the job resolved shellcheck 0.9.0 from the hosted
image while the other three doors linted with the pinned 0.11.0.1. A pin
that is not the version running on one of the four doors is the class of
defect this script exists to remove, so every door now installs it.

On a CI runner the installed directories are prepended to PATH for the
steps that follow, and TOOLS is written to $GITHUB_ENV under
LINT_TOOLS_ENV. A control reads that variable back and requires each tool
to resolve; a broken install is a failure there, never a skip.
"""
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# What a suite may skip on, and therefore what a job reaching the suites
# installs. The next binary a suite skips on is added here and nowhere else.
TOOLS = ('actionlint', 'shellcheck')
# The name this script writes TOOLS under, and the name the control reads.
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
# The one place the shellcheck version is written down. This script reads
# it and never repeats it, so a bump in this file moves every door at once
# and there is no second copy to forget.
REQUIREMENTS = ROOT / 'requirements-test.txt'
SHELLCHECK_PACKAGE = 'shellcheck-py'
# The binary the wheel is expected to carry, and the directory inside
# the wheel that carries it. The member's FILE NAME is what lands on
# PATH — `shellcheck` on POSIX, `shellcheck.exe` on Windows — and it
# is read from the wheel rather than computed here.
SHELLCHECK_BINARY = 'shellcheck'
SCRIPTS_SCHEME = '.data/scripts/'
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
# A whole-process bound, because pip owns the wheel transfer below, so
# the per-socket one above does not apply to it.
WHEEL_TIMEOUT = 300
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


def shellcheck_pin():
    """The shellcheck version `requirements-test.txt` pins, read from it.

    A pin written here as well would be a second copy, and the two would
    drift the first time somebody bumped one. Zero pins and two pins are
    both refused: a requirement spelled without a version would install
    whatever is newest, which is the failure a pin exists to prevent.
    """
    wanted = f'{SHELLCHECK_PACKAGE}=='
    try:
        lines = REQUIREMENTS.read_text(encoding='utf-8').splitlines()
    except OSError as why:
        # The same register as every other refusal here. A traceback out of
        # a version read is a stack trace about a file the reader is
        # looking at, which is the one thing a message like this must not
        # be when the file is missing, renamed, or unreadable on a runner.
        raise SystemExit(
            f'{REQUIREMENTS} could not be read ({why}); this script takes '
            f'the {SHELLCHECK_PACKAGE} version from that file, so a file it '
            'cannot read is a version it cannot install') from why
    found = [line for line in lines
             if line.strip() and not line.lstrip().startswith('#')
             and line.split(';')[0].strip().startswith(wanted)]
    if not found:
        raise SystemExit(
            f'{REQUIREMENTS.name} pins no {SHELLCHECK_PACKAGE}==<version>; '
            'this script installs the version that file names, so a pin '
            'there is the whole supply route')
    if len(found) > 1:
        raise SystemExit(
            f'{REQUIREMENTS.name} pins {SHELLCHECK_PACKAGE} {len(found)} '
            f'times, at {sorted(found)}; one file must name one version')
    return found[0].split(';')[0].strip()[len(wanted):]


def install_shellcheck():
    """Install the pinned shellcheck from its wheel, on every platform.

    Not `pip install --target`. The wheels carry the binary as package data
    under the scripts scheme and declare no console-script entry point at
    all, so where `--target` puts such a member is pip's scheme logic, and
    on windows-latest it put it somewhere no PATH entry reached. Reading
    the member out of the wheel ourselves makes this the same three lines
    on all three operating systems, and the one place that can be wrong is
    a place this file owns rather than one it infers.
    """
    version = shellcheck_pin()
    with tempfile.TemporaryDirectory() as staging:
        wheel = _wheel_for(staging, version)
        _place_scripts_member(wheel, version)
    print(f'shellcheck {version} installed from {wheel.name}')


def _wheel_for(staging, version):
    """The one wheel pip resolves the pinned requirement to, right here.

    pip chooses, and that is the whole point. The release publishes five
    artifacts for this one version — a macOS x86_64 wheel, a macOS arm64
    wheel, a manylinux wheel, a Windows wheel and an sdist — and
    macos-latest is arm64, so a wheel named here from a platform table
    would be another table to keep right by hand, in the one function
    whose job is to stop assuming what a platform calls something.
    `--only-binary=:all:` forbids the sdist fallback, which would
    otherwise try to build the tool from source on a runner.

    The transfer is pip's here, so this bounds what is ACCEPTED rather
    than what is fetched: an over-limit wheel is refused before a byte of
    it is opened. The actionlint path bounds the transfer directly because
    it owns the URL, and the difference is worth naming rather than
    glossing over.
    """
    requirement = f'{SHELLCHECK_PACKAGE}=={version}'
    subprocess.run(
        [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
         '--no-deps', '--dest', staging, requirement],
        check=True, timeout=WHEEL_TIMEOUT)
    brought = sorted(Path(staging).iterdir())
    wheels = [path for path in brought if path.suffix == '.whl']
    if not wheels:
        raise SystemExit(
            f'pip brought no wheel for {requirement}, only '
            f'{[path.name for path in brought]}; this script installs the '
            'binary out of a wheel and will not build one from source')
    if len(wheels) > 1:
        raise SystemExit(
            f'pip brought {len(wheels)} wheels for {requirement}, at '
            f'{[path.name for path in wheels]}; one pin must name one wheel')
    wheel = wheels[0]
    escaped = SHELLCHECK_PACKAGE.replace('-', '_')
    if not wheel.name.startswith(f'{escaped}-{version}-'):
        raise SystemExit(
            f'{requirement} resolved to {wheel.name}, which is not that '
            'version; installing it would put a binary in place of the one '
            'the pin names')
    return wheel


def _place_scripts_member(wheel, version):
    """Write the wheel's one scripts-scheme member into the tool directory.

    Four shapes are refused rather than guessed at, because each is a
    supply-chain question and not a formatting one: no scripts member, more
    than one, a name that is not the declared tool, and a wheel whose
    version is not the pin. A member is also directory-skipped, because
    the manylinux wheel carries a zero-byte `….data/scripts/` directory
    entry beside its binary and counting that would make every Linux leg
    refuse a wheel that is perfectly correct.
    """
    size = wheel.stat().st_size
    if size > MAX_TRANSFER:
        raise SystemExit(
            f'{wheel.name} is {size} bytes, over the {MAX_TRANSFER} ceiling '
            'this script accepts; refusing beats unpacking a transfer that '
            'should not have been trusted')
    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as why:
        raise SystemExit(
            f'{wheel.name} is not a readable wheel: {why}') from why
    with archive:
        members = [info for info in archive.infolist()
                   if not info.is_dir()
                   and SCRIPTS_SCHEME in info.filename
                   and '/' not in info.filename.split(SCRIPTS_SCHEME)[1]]
        names = [info.filename.split(SCRIPTS_SCHEME)[1] for info in members]
        if not members:
            raise SystemExit(
                f'{wheel.name} carries no {SCRIPTS_SCHEME} member, so it '
                'carries no binary; the tool this script declares would not '
                'resolve, and a wheel without the binary is not a wheel to '
                'guess at')
        if len(members) > 1:
            raise SystemExit(
                f'{wheel.name} carries {len(members)} {SCRIPTS_SCHEME} '
                f'members, at {names}; one wheel must carry one binary')
        member = members[0]
        name = names[0]
        if os.path.splitext(name)[0] != SHELLCHECK_BINARY:
            raise SystemExit(
                f'{wheel.name} carries {SCRIPTS_SCHEME}{name}, which is not '
                f'the {SHELLCHECK_BINARY} this script declares; a wheel that '
                'would put something else on PATH is not one to install')
        target = TOOL_DIR / name
        TOOL_DIR.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, open(target, 'wb') as out:
            out.write(source.read())
    target.chmod(0o755)
    print(f'  {target}: {target.stat().st_size} bytes, mode '
          f'{oct(target.stat().st_mode)[-3:]}, from {version}')


def _publish():
    """Put the tool directory on PATH here and for the steps after."""
    os.environ['PATH'] = f'{TOOL_DIR}{os.pathsep}{os.environ["PATH"]}'
    later = os.environ.get('GITHUB_PATH')
    if later:
        with open(later, 'a', encoding='utf-8') as handle:
            handle.write(f'{TOOL_DIR}\n')


def _installed_here(path):
    """Whether a resolved tool is the one this install put on PATH.

    The comparison is on the resolved path's own directory rather than on
    the PATH's order, because order is what the defect exploited: the
    published directories are prepended, so anything that is NOT under one
    of them answered the lookup from somewhere this script never wrote.
    """
    resolved = Path(path)
    root = TOOL_DIR.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def _record():
    """Write TOOLS where the control reads them, and verify each resolves.

    "Resolves" is not enough, and this is the check's second revision. A
    plain `shutil.which` scans the whole PATH, so it is satisfied by a
    binary this install did nothing about: pip leaves an empty console
    script directory, the hosted image's shellcheck answers instead, and
    the check passes on ubuntu-latest — the image this whole change is
    about. That is a pin that is not the version in use, passing because
    something else on the machine supplied it, which is the defect class
    this script was written to close, one function down.

    So each tool must resolve INSIDE the directory this install published.
    A tool found elsewhere is reported with the path that was found,
    because "it resolved somewhere else" is exactly the part a reader
    needs to see and the bare word `missing` would have hidden.
    """
    resolved = {tool: shutil.which(tool) for tool in TOOLS}
    elsewhere = {tool: path for tool, path in resolved.items()
                 if path is not None and not _installed_here(path)}
    if elsewhere:
        raise SystemExit(
            f'{", ".join(sorted(elsewhere))} resolved outside {TOOL_DIR} — '
            f'{sorted(elsewhere.values())} — so this install did not put it '
            'on PATH and whatever answered was something else on the '
            'machine. That is the failure this step exists to stop: a tool '
            'no pin names, running in place of the one that does. This is '
            'a failure and not a skip: a suite that skips here reports '
            'success having verified nothing.')
    missing = [tool for tool, path in resolved.items() if path is None]
    if missing:
        raise SystemExit(
            f'{", ".join(missing)} does not resolve on PATH after this '
            'install, though this script installed both: actionlint from '
            'its release and shellcheck from the wheel '
            'requirements-test.txt pins. This is a failure and not a '
            'skip: a suite that skips here reports success having '
            'verified nothing.')
    later = os.environ.get('GITHUB_ENV')
    if later:
        with open(later, 'a', encoding='utf-8') as handle:
            handle.write(f'{LINT_TOOLS_ENV}={",".join(TOOLS)}\n')
    print(f'{LINT_TOOLS_ENV}={",".join(TOOLS)}')
    for tool in TOOLS:
        print(f'  {tool}: {resolved[tool]}')


def main():
    install_actionlint()
    install_shellcheck()
    _publish()
    _record()
    return 0


if __name__ == '__main__':
    sys.exit(main())
