#!/usr/bin/env python3
"""Verify that every actions/cache pin is the release its comment names.

The offline suites compare each pin with REVIEWED_CACHE_RELEASES, which is
one more repository literal: a SHA and release that agree with each other
pass whether or not the release exists. This resolves each pinned release
through the GitHub API and fails closed on anything it cannot read.

A line scanner recognises the pins and is the only source of the
`# vX.Y.Z` comment: an inline `uses:` scalar with a trailing comment, or
a `>-` / `|-` block with the comment on its header and one content line.
It refuses a `uses:` value it cannot classify when that value or the line
after it spells `actions/cache`, and any other non-comment line spelling
an `actions/cache…@` reference. A file the scanner finds clean is then
read by the repository's workflow decoder: every step whose decoded
`uses` names a cache action must hold exactly one recognised pin with
that value, and a file the decoder cannot read is refused by path.
"""
import importlib
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

if __package__:
    # pylint: disable-next=relative-beyond-top-level,no-name-in-module
    from . import workflow_yaml
else:
    workflow_yaml = importlib.import_module('workflow_yaml')

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
_BLOCK_HEADER = re.compile(r'([>|]-)' + _COMMENT + '$')
_REFERENCE = re.compile(
    r'(?<![A-Za-z0-9_./-])actions/cache(?:/restore|/save)?@', re.IGNORECASE)
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
    relative = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return [], [f'{relative}: not UTF-8']
    lines = text.splitlines()
    pins, refusals, accounted = [], [], set()
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
            token = _block_content(lines, index, len(match.group(1)))
            comment = header.group(2)
        else:
            token, comment = None, None
        if token is None:
            # The next line is where a continued value would hide a pin.
            text = ' then '.join(
                repr(part.strip()) for part in
                [value, *lines[index + 1:index + 2]])
            if 'actions/cache' in text.casefold():
                refusals.append(f'{relative}:{index + 1}: uses value '
                                f'cannot be classified: {text}')
                accounted.update((index + 1, index + 2))
            continue
        pin = _pin(relative, index + 1, token, comment)
        if pin is not None:
            pins.append(pin)
            accounted.update((index + 1, index + 2 if header else index + 1))
    # Every line carrying a reference is a pin, a refusal or a comment;
    # a spelling the grammar above did not classify is refused here.
    for number, line in enumerate(lines, 1):
        if (number not in accounted and not line.lstrip().startswith('#')
                and _REFERENCE.search(line)):
            refusals.append(
                f'{relative}:{number}: unclassified actions/cache reference')
    if refusals:
        return pins, refusals
    return pins, decoder_refusals(relative, text, pins)


def _cache_reference(uses):
    action, at, _ref = uses.partition('@')
    return bool(at) and action.casefold() in CACHE_ACTIONS


def decoder_refusals(relative, text, pins):
    """Every cache reference the workflow decoder finds that the line
    scanner did not recognise at that step with the same value."""
    try:
        jobs = workflow_yaml.job_names(text) or []
        steps = [(job, item) for job in jobs
                 for item in workflow_yaml.workflow_step_items(text, job)
                 or []]
    except workflow_yaml.YAMLReadError as error:
        return [f'{relative}: {error}']
    refusals = []
    for _job, item in steps:
        if item.uses is None or not _cache_reference(item.uses):
            continue
        seen = [pin for pin in pins
                if item.index < pin.line <= item.end_index]
        if [f'{pin.action}@{pin.ref}' for pin in seen] != [item.uses]:
            refusals.append(f'{relative}:{item.index + 1}: decoded uses '
                            f'{item.uses!r} matches no recognised pin')
    return refusals


def scan(root):
    root = Path(root)
    pins, refusals = [], []
    for path in workflow_files(root):
        found, refused = pins_in(root, path)
        pins.extend(found)
        refusals.extend(refused)
    return pins, refusals


def shape_refusal(pin):
    """The regex is the only thing that admits a comment into a request
    path."""
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
        where = ', '.join(f'{pin.path}:{pin.line}' for pin in members)
        try:
            commit = upstream_commit(tag, run)
        except Refused as refusal:
            refusals.append(f'{refusal} at {where}')
            continue
        if commit != ref:
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
