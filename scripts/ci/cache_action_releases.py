#!/usr/bin/env python3
"""Verify that every actions/cache pin is the release its comment names.

The offline suites compare each pin with REVIEWED_CACHE_RELEASES, which is
one more repository literal: a SHA and release that agree with each other
pass whether or not the release exists. This resolves each pinned release
through the GitHub API and fails closed on anything it cannot read.

The scanner is line-based and admits the two `uses:` shapes the workflows
use: an inline scalar with an optional trailing comment, and a `>-`
folded scalar whose comment sits on the header line and whose one content
line is the reference. Any other `uses:` value naming a cache action is
refused, because inside a block scalar a `#` is content and a value this
reader cannot classify is one it cannot verify.
"""
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = Path('.github') / 'workflows'
CACHE_ACTIONS = frozenset((
    'actions/cache', 'actions/cache/restore', 'actions/cache/save'))
GH_API = ('gh', 'api', '-H', 'Cache-Control: no-cache')
UPSTREAM = 'repos/actions/cache/git'

Pin = namedtuple('Pin', 'path line action ref comment')

_USES = re.compile(r'^(\s*(?:-\s+)?)uses:(?:\s+(.*?))?\s*$')
_COMMENT = r'(?:\s+#\s*(.*))?'
_INLINE = re.compile(
    r'("[^"]*"|\'[^\']*\'|[^\s"\'#>|]\S*)' + _COMMENT + '$')
_BLOCK_HEADER = re.compile(r'([>|][-+]?)' + _COMMENT + '$')
_COMMIT = re.compile(r'[0-9a-f]{40}')
_RELEASE = re.compile(r'v[0-9]+\.[0-9]+\.[0-9]+')


def workflow_files(root):
    directory = Path(root) / WORKFLOWS
    return sorted(path for path in directory.glob('*')
                  if path.suffix in ('.yml', '.yaml') and path.is_file())


def _pin(path, number, token, comment):
    action, at, ref = token.strip('"\'').partition('@')
    if not at or action.casefold() not in CACHE_ACTIONS:
        return None
    return Pin(path, number, action, ref, comment)


def _block_content(lines, index, key_indent):
    """The one content line of a block scalar opened at `index`, or None
    when the block is empty or runs past one line."""
    if index + 1 >= len(lines):
        return None
    content = lines[index + 1]
    deeper = len(content) - len(content.lstrip()) > key_indent
    if not content.strip() or not deeper or len(content.split()) != 1:
        return None
    rest = [line for line in lines[index + 2:] if line.strip()]
    if rest and len(rest[0]) - len(rest[0].lstrip()) > key_indent:
        return None
    return content.strip()


def pins_in(root, path):
    """The (pins, refusals) one workflow file yields."""
    lines = path.read_text(encoding='utf-8').splitlines()
    relative = path.relative_to(root).as_posix()
    pins, refusals = [], []
    for index, line in enumerate(lines):
        match = _USES.match(line)
        if not match:
            continue
        value = match.group(2) or ''
        inline = _INLINE.fullmatch(value)
        header = _BLOCK_HEADER.fullmatch(value)
        if inline:
            token, comment = inline.groups()
        elif header:
            # The key's column is the block's indentation reference.
            token = _block_content(lines, index, len(match.group(1)))
            comment = header.group(2)
        else:
            token, comment = None, None
        if token is None:
            # An empty or multi-line value may continue on the next line,
            # which is where a hidden pin would sit.
            text = ' then '.join(
                repr(part.strip()) for part in
                [value, *lines[index + 1:index + 2]])
            if 'actions/cache' in text.casefold():
                refusals.append(f'{relative}:{index + 1}: uses value '
                                f'cannot be classified: {text}')
            continue
        pin = _pin(relative, index + 1, token, comment)
        if pin is not None:
            pins.append(pin)
    return pins, refusals


def scan(root):
    """Every actions/cache-family pin under `root`, and every `uses:` line
    naming one that could not be classified."""
    root = Path(root)
    pins, refusals = [], []
    for path in workflow_files(root):
        found, refused = pins_in(root, path)
        pins.extend(found)
        refusals.extend(refused)
    return pins, refusals


def shape_refusal(pin):
    """Why `pin` may not be resolved upstream, or None when it may be.

    The regex is the only thing that admits a comment into a request path.
    """
    where = f'{pin.path}:{pin.line}'
    if not _COMMIT.fullmatch(pin.ref):
        return (f'{where}: {pin.action}@{pin.ref} is not pinned to a '
                '40-hex commit')
    if pin.comment is None:
        return f'{where}: {pin.action}@{pin.ref} carries no release comment'
    if not _RELEASE.fullmatch(pin.comment):
        return f'{where}: release comment {pin.comment!r} is not vX.Y.Z'
    return None


class Refused(Exception):
    pass


def _git_object(tag, path, run):
    try:
        stdout = run([*GH_API, f'{UPSTREAM}/{path}'])
    except subprocess.CalledProcessError as error:
        observed = ((error.stderr or '').strip()
                    or (error.stdout or '').strip() or 'no output')
        raise Refused(
            f'{tag}: gh api exited {error.returncode}: {observed}') from None
    try:
        body = json.loads(stdout)
    except ValueError:
        raise Refused(f'{tag}: response is not JSON: {stdout!r}') from None
    found = body.get('object') if isinstance(body, dict) else None
    sha = found.get('sha') if isinstance(found, dict) else None
    kind = found.get('type') if isinstance(found, dict) else None
    if (not isinstance(sha, str) or not _COMMIT.fullmatch(sha)
            or kind not in ('commit', 'tag')):
        raise Refused(
            f'{tag}: response names no commit or tag object: {body!r}')
    return sha, kind


def upstream_commit(tag, run):
    sha, kind = _git_object(tag, f'ref/tags/{tag}', run)
    if kind == 'commit':
        return sha
    commit, kind = _git_object(tag, f'tags/{sha}', run)
    if kind != 'commit':
        raise Refused(f'{tag}: tag object {sha} names a {kind!r}, not a '
                      'commit')
    return commit


def verify(root, run):
    root = Path(root)
    if not workflow_files(root):
        return [], [f'no workflow files under {root / WORKFLOWS}']
    pins, refusals = scan(root)
    refusals += [refusal for refusal in map(shape_refusal, pins) if refusal]
    if refusals:
        return [], refusals
    groups = {}
    for pin in pins:
        groups.setdefault((pin.ref, pin.comment), []).append(pin)
    verified = []
    for (ref, tag), members in sorted(groups.items()):
        try:
            commit = upstream_commit(tag, run)
        except Refused as refusal:
            refusals.append(str(refusal))
            continue
        if commit != ref:
            where = ', '.join(f'{pin.path}:{pin.line}' for pin in members)
            refusals.append(
                f'{tag}: upstream is {commit}, pinned {ref} at {where}')
            continue
        verified.append(f'{ref} {tag} {len(members)} pin(s)')
    return verified, refusals


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else DEFAULT_ROOT

    def run(command):
        return subprocess.run(
            command, capture_output=True, text=True, check=True,
            timeout=60).stdout

    verified, refusals = verify(root, run)
    for line in verified:
        print(line)
    for refusal in refusals:
        print(refusal, file=sys.stderr)
    return 1 if refusals else 0


if __name__ == '__main__':
    raise SystemExit(main())
