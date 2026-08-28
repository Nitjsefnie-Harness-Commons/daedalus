#!/usr/bin/env python3
"""Verify every version string in the repo agrees, or rewrite them all at once.

The version lives in several places across the extension, the dashboard and the CLI package; SITES below is the list, and the check reports how many it found rather than restating a number that goes stale. Bumping some and not others
leaves no way to tell which code is actually running — the failure the project
rule exists to prevent, and the one that let the dashboard footer sit 13
releases stale.

  python3 scripts/check_versions.py              # check the working tree
  python3 scripts/check_versions.py --staged     # check what a commit would record
  python3 scripts/check_versions.py --rev HEAD   # check a revision
  python3 scripts/check_versions.py --set 0.18.0 # rewrite every site
  python3 scripts/check_versions.py --print      # the canonical version alone

Every site carries the identical string, the manifest included. Chrome accepts
only dot-separated integers there, so the version the tree carries is spelled
that way everywhere rather than translated at one site — a checker that
translates cannot report the disagreement it is translating away.
"""
import argparse
import contextlib
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Chrome accepts a manifest version of one to four dot-separated integers,
# each below 65536. Every site carries the same string, so that is the shape
# the whole tree is limited to: a version outside it cannot be spelled
# identically everywhere, which is the one thing this script exists to keep
# true. Refusing it here is what stops a tree passing every gate while the
# extension it describes is one Chrome will not load.
#
# The digit class is spelled out rather than written `\d`, which matches every
# Unicode decimal digit — `\u0661.\u0662.\u0663` names the same value as
# `1.2.3` without being a string Chrome reads as an integer. A leading zero is
# refused for the neighbouring reason: it is a second spelling of a value that
# already has one, and a version with two spellings is what this whole file
# exists to prevent.
_SPELLING = re.compile(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){0,3}')


def spelling_error(version):
    """Why `version` cannot be carried by every site, or None if it can."""
    if not _SPELLING.fullmatch(version):
        return (f'{version!r} is not one to four dot-separated integers in '
                f'ASCII digits without a leading zero, so no tree can spell '
                f'it the same way at every site — Chrome refuses it as a '
                f'manifest version')
    if any(int(part) > 65535 for part in version.split('.')):
        return (f'{version!r} has a segment above 65535, which Chrome '
                f'refuses as a manifest version')
    return None


# (path, description, regex). Each regex needs a 'v' group holding the version;
# --set rewrites exactly that group and leaves the rest of the line untouched.
SITES = [
    ('extension/manifest.json', 'manifest version',
     r'"version"\s*:\s*"(?P<v>[^"]+)"'),
    ('extension/background.js', '@version header',
     r'@version\s+(?P<v>\S+)'),
    ('extension/background.js', 'VERSION constant',
     r"const VERSION\s*=\s*'(?P<v>[^']+)'"),
    # Same reasoning as the __version__ site below: a second assignment
    # written with a different quote character is still valid JavaScript
    # and still wins, so the pattern has to see any of the three ways a
    # string can be spelled here to catch a duplicate written another way
    # (#247, then a template literal reported in review). The two keys are
    # assumed bare, matching this file's own style; a duplicate that also
    # quotes them would need its own pattern, and none of page.js's own
    # sites do that today.
    ('extension/page.js', 'GM bridge info.script.version',
     r'''script:\s*\{\s*version:\s*(?P<q>['"`])(?P<v>[^'"`]+)(?P=q)'''),
    ('dashboard/index.html', 'dashboard rail footer',
     r'class="rail-foot">v(?P<v>[^ <]+)'),
    ('dashboard/index.html', 'dashboard status line',
     r'<span class="sl-v">(?P<v>[^<]+)</span>'),
    # The published wheel is a version claim about the wire format, so it is
    # checked like any other site. pyproject reads this same attribute, so
    # there is nothing separate to keep in step.
    #
    # Either quote character, not just the one the file happens to use: a
    # second `__version__ = '...'` assignment is exactly as valid Python as
    # the first and wins the binding just the same, so a pattern that only
    # recognizes the convention already in use cannot see a duplicate spelled
    # the other way (#228) -- it has to see every assignment to be able to
    # refuse more than one.
    ('daedalus_cli/__init__.py', 'package __version__',
     r'''__version__\s*=\s*(?P<q>['"])(?P<v>[^'"]+)(?P=q)'''),
]

# The site every other site is compared against.
CANONICAL = ('extension/background.js', 'VERSION constant')
MANIFEST = ('extension/manifest.json', 'manifest version')


def read_source(path, staged, rev):
    """File content from the worktree, the index, or a revision."""
    if not staged and rev is None:
        return (REPO / path).read_text(encoding='utf-8')
    spec = f':{path}' if staged else f'{rev}:{path}'
    out = subprocess.run(['git', 'show', spec], cwd=REPO,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f'cannot read {spec}: {out.stderr.strip()}')
    return out.stdout


def _find_site(path, desc, pattern, text):
    """The site's one match in `text`, or a FAIL naming why there wasn't one.

    A second assignment further down the file is invisible to `re.search`,
    which returns only the first: the checker would report that value as
    canonical while the runtime binds whichever assignment executes last,
    and `--set` would rewrite only the first occurrence, leaving the second
    stale. Neither can happen once a site is required to match exactly once.
    """
    matches = list(re.finditer(pattern, text))
    if not matches:
        raise SystemExit(
            f'FAIL: no version found for {desc} in {path} — the file changed '
            f'shape and SITES in {__file__} needs updating')
    if len(matches) > 1:
        values = ', '.join(sorted({m.group('v') for m in matches}))
        raise SystemExit(
            f'FAIL: {desc} in {path} matches {len(matches)} times ({values}) '
            f'— a checker that reports the first match cannot report the '
            f'value the file actually binds')
    return matches[0]


def collect(staged=False, rev=None):
    """[(path, description, version)] for every site, in SITES order."""
    cache = {}
    found = []
    for path, desc, pattern in SITES:
        if path not in cache:
            cache[path] = read_source(path, staged, rev)
        m = _find_site(path, desc, pattern, cache[path])
        found.append((path, desc, m.group('v')))
    return found


def check(staged=False, rev=None):
    found = collect(staged, rev)
    canonical = next(v for p, d, v in found if (p, d) == CANONICAL)
    problem = spelling_error(canonical)
    if problem:
        print(f'FAIL: {problem}', file=sys.stderr)
        return 1

    bad = [(p, d, v) for p, d, v in found if v != canonical]
    if bad:
        where = 'the index' if staged else (f'rev {rev}' if rev else 'the working tree')
        print(f'FAIL: version strings disagree in {where}. '
              f'{CANONICAL[1]} in {CANONICAL[0]} says {canonical!r}:', file=sys.stderr)
        for p, d, v in bad:
            print(f'  {p}: {d} is {v!r}, expected {canonical!r}',
                  file=sys.stderr)
        print('\nBump every site in one pass: '
              'python3 scripts/check_versions.py --set <version>', file=sys.stderr)
        return 1

    print(f'ok: version {canonical} consistent across '
          f'{len(found)} sites in {len({p for p, _, _ in found})} files')
    return 0


def apply(version):
    """Rewrite every site in the working tree to `version`."""
    problem = spelling_error(version)
    if problem:
        raise SystemExit(f'FAIL: {problem}')

    by_file = {}
    for path, desc, pattern in SITES:
        by_file.setdefault(path, []).append((desc, pattern))

    for path, entries in by_file.items():
        target = REPO / path
        text = target.read_text(encoding='utf-8')
        for desc, pattern in entries:
            m = _find_site(path, desc, pattern, text)
            # Replace only the 'v' group so surrounding markup stays byte-identical.
            start, end = m.span('v')
            text = text[:start] + version + text[end:]
        target.write_text(text, encoding='utf-8')
        print(f'set {path} -> {version}')
    return check()


def print_canonical(staged=False, rev=None):
    """Print the canonical version alone; still refuse a tree that disagrees.

    Whatever reads this must not be handed a version from a tree whose sites
    disagree, so the ordinary check still runs — its report goes to stderr so
    stdout carries the version and nothing else.
    """
    with contextlib.redirect_stdout(sys.stderr):
        status = check(staged, rev)
    if status:
        return status
    found = collect(staged, rev)
    print(next(v for p, d, v in found if (p, d) == CANONICAL))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--staged', action='store_true',
                     help='read from the index (what a commit would record)')
    src.add_argument('--rev', help='read from a revision, e.g. HEAD')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--set', dest='set_to', metavar='VERSION',
                      help='rewrite every site in the working tree to VERSION')
    mode.add_argument('--print', dest='print_only', action='store_true',
                      help='print the canonical version alone, for a script to read')
    args = ap.parse_args()

    if args.set_to:
        if args.staged or args.rev:
            raise SystemExit('--set rewrites the working tree; drop --staged/--rev')
        return apply(args.set_to)
    if args.print_only:
        return print_canonical(staged=args.staged, rev=args.rev)
    return check(staged=args.staged, rev=args.rev)


if __name__ == '__main__':
    raise SystemExit(main())
