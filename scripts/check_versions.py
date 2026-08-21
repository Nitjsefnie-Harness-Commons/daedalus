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

Chrome rejects a letter-suffixed version, so the manifest carries the suffix as
a fourth numeric segment (0.16.0a -> 0.16.0.1). That translation is expected,
not drift.
"""
import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# (path, description, regex). Each regex needs a 'v' group holding the version;
# --set rewrites exactly that group and leaves the rest of the line untouched.
SITES = [
    ('extension/manifest.json', 'manifest version',
     r'"version"\s*:\s*"(?P<v>[^"]+)"'),
    ('extension/background.js', '@version header',
     r'@version\s+(?P<v>\S+)'),
    ('extension/background.js', 'VERSION constant',
     r"const VERSION\s*=\s*'(?P<v>[^']+)'"),
    ('extension/page.js', 'GM bridge info.script.version',
     r"script:\s*\{\s*version:\s*'(?P<v>[^']+)'"),
    ('dashboard/index.html', 'dashboard rail footer',
     r'class="rail-foot">v(?P<v>[^ <]+)'),
    ('dashboard/index.html', 'dashboard status line',
     r'<span class="sl-v">(?P<v>[^<]+)</span>'),
    # The published wheel is a version claim about the wire format, so it is
    # checked like any other site. pyproject reads this same attribute, so
    # there is nothing separate to keep in step.
    ('daedalus_cli/__init__.py', 'package __version__',
     r'__version__\s*=\s*"(?P<v>[^"]+)"'),
]

# The site every other site is compared against.
CANONICAL = ('extension/background.js', 'VERSION constant')
MANIFEST = ('extension/manifest.json', 'manifest version')


def manifest_form(version):
    """Canonical version as the manifest must spell it (0.16.0a -> 0.16.0.1)."""
    m = re.fullmatch(r'(\d+(?:\.\d+)*)([a-z])', version)
    if not m:
        return version
    base, suffix = m.groups()
    return f'{base}.{ord(suffix) - ord("a") + 1}'


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


def collect(staged=False, rev=None):
    """[(path, description, version)] for every site, in SITES order."""
    cache = {}
    found = []
    for path, desc, pattern in SITES:
        if path not in cache:
            cache[path] = read_source(path, staged, rev)
        m = re.search(pattern, cache[path])
        if not m:
            raise SystemExit(
                f'FAIL: no version found for {desc} in {path} — the file changed '
                f'shape and SITES in {__file__} needs updating')
        found.append((path, desc, m.group('v')))
    return found


def check(staged=False, rev=None):
    found = collect(staged, rev)
    canonical = next(v for p, d, v in found if (p, d) == CANONICAL)
    expected = {(p, d): (manifest_form(canonical) if (p, d) == MANIFEST else canonical)
                for p, d, _ in found}

    bad = [(p, d, v) for p, d, v in found if v != expected[(p, d)]]
    if bad:
        where = 'the index' if staged else (f'rev {rev}' if rev else 'the working tree')
        print(f'FAIL: version strings disagree in {where}. '
              f'{CANONICAL[1]} in {CANONICAL[0]} says {canonical!r}:', file=sys.stderr)
        for p, d, v in bad:
            print(f'  {p}: {d} is {v!r}, expected {expected[(p, d)]!r}', file=sys.stderr)
        print('\nBump every site in one pass: '
              'python3 scripts/check_versions.py --set <version>', file=sys.stderr)
        return 1

    print(f'ok: version {canonical} consistent across '
          f'{len(found)} sites in {len({p for p, _, _ in found})} files')
    return 0


def apply(version):
    """Rewrite every site in the working tree to `version`."""
    by_file = {}
    for path, desc, pattern in SITES:
        by_file.setdefault(path, []).append((desc, pattern))

    for path, entries in by_file.items():
        target = REPO / path
        text = target.read_text(encoding='utf-8')
        want = manifest_form(version) if path == MANIFEST[0] else version
        for desc, pattern in entries:
            m = re.search(pattern, text)
            if not m:
                raise SystemExit(f'FAIL: no version found for {desc} in {path}')
            # Replace only the 'v' group so surrounding markup stays byte-identical.
            start, end = m.span('v')
            text = text[:start] + want + text[end:]
        target.write_text(text, encoding='utf-8')
        print(f'set {path} -> {want}')
    return check()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--staged', action='store_true',
                     help='read from the index (what a commit would record)')
    src.add_argument('--rev', help='read from a revision, e.g. HEAD')
    ap.add_argument('--set', dest='set_to', metavar='VERSION',
                    help='rewrite every site in the working tree to VERSION')
    args = ap.parse_args()

    if args.set_to:
        if args.staged or args.rev:
            raise SystemExit('--set rewrites the working tree; drop --staged/--rev')
        return apply(args.set_to)
    return check(staged=args.staged, rev=args.rev)


if __name__ == '__main__':
    raise SystemExit(main())
