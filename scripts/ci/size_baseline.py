#!/usr/bin/env python3
"""A ratchet on module size, pointing the opposite way to the coverage one.

Nothing stopped a module that was already too large from taking on one more
responsibility, and the largest files here got that way a few lines at a time
with every individual commit looking reasonable.

The rule is not a line limit. A file over its ceiling is listed in BASELINE at
the size it was measured and may not exceed it; every other file must stay
under the ceiling. Growing a listed file is allowed, because some fixes
genuinely belong in it, but only by editing its recorded number in the same
commit — where a reviewer sees the monolith getting bigger instead of
inferring it from a diffstat.

Shrinking is the direction this exists to reward, so it costs the author
nothing: `--tighten` follows a file down, and CI runs it beside the coverage
ratchet. Without that the bound would stay where the file used to be and the
lines could come back for free, which is the same slack the coverage floor had
when its recorded measurement went stale.

  python3 scripts/ci/size_baseline.py            # report violations, exit 1 on any
  python3 scripts/ci/size_baseline.py --tighten  # follow shrunk files down

`--tighten` never raises a number and never adds an entry: growth stays a
decision somebody makes and a reviewer sees.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

# A new module has no reason to start large. Production code gets the tighter
# number because a test module legitimately carries many small independent
# cases, while production code that size is carrying responsibilities.
PRODUCTION_CEILING = 500
TEST_CEILING = 700

# Issues #97 and #129 are the standing work to empty this table.
BASELINE = {
    'server.py': 1970,
    'tests/_pyroute.py': 701,
    'tests/_pyroute_state.py': 712,
    'tests/test_tab_routing.py': 717,
    'tests/test_mcp_server.py': 1723,
    'tests/test_cli.py': 1268,
    'tests/test_bridge_results.py': 1343,
}

_ENTRY = re.compile(r"^(?P<indent>\s*)'(?P<path>[^']+)': (?P<size>\d+),\s*$")


def ceiling_for(rel):
    """The ceiling that applies to `rel`."""
    return TEST_CEILING if rel.startswith('tests/') else PRODUCTION_CEILING


def tracked_sizes(root=ROOT):
    """Every tracked .py/.js the policy covers, with its line count.

    `examples/` is excluded for the same reason the linters exclude it: those
    files are worked examples read start to finish, not modules anyone
    imports.
    """
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '*.py', '*.js'],
        capture_output=True, check=True, timeout=30)
    sizes = {}
    for raw in listed.stdout.split(b'\0'):
        if not raw:
            continue
        rel = raw.decode('utf-8', 'surrogateescape')
        if rel.startswith('examples/'):
            continue
        path = root / rel
        if not path.is_file():
            continue
        with open(path, 'rb') as handle:
            sizes[rel] = sum(1 for _ in handle)
    return sizes


def violations(sizes, baseline=None):
    """Everything wrong with `sizes` under the policy, as {kind: {...}}.

    `grown` is a file past its recorded size, `over` one above its ceiling
    with no entry excusing it, `missing` an entry naming a file that is gone,
    and `graduated` one that is back under its ceiling and should be deleted.
    """
    baseline = BASELINE if baseline is None else baseline
    return {
        'grown': {rel: (sizes[rel], recorded)
                  for rel, recorded in baseline.items()
                  if rel in sizes and sizes[rel] > recorded},
        'over': {rel: (count, ceiling_for(rel))
                 for rel, count in sizes.items()
                 if rel not in baseline and count > ceiling_for(rel)},
        'missing': sorted(rel for rel in baseline if rel not in sizes),
        'graduated': {rel: (sizes[rel], ceiling_for(rel))
                      for rel in baseline
                      if rel in sizes and sizes[rel] <= ceiling_for(rel)},
    }


def tightened(text, sizes, baseline=None):
    """`text` with every shrunk entry followed down, or None to leave it.

    An entry whose file is back under its ceiling is removed outright, because
    a ceiling nobody is held to is worse than no entry at all.
    """
    baseline = BASELINE if baseline is None else baseline
    out = []
    changed = False
    for line in text.splitlines(keepends=True):
        match = _ENTRY.match(line)
        if match is None:
            out.append(line)
            continue
        rel = match.group('path')
        if rel not in baseline or rel not in sizes:
            out.append(line)
            continue
        recorded, current = int(match.group('size')), sizes[rel]
        if current <= ceiling_for(rel):
            changed = True          # graduated: the entry goes away
            continue
        if current < recorded:
            out.append(f"{match.group('indent')}'{rel}': {current},\n")
            changed = True
            continue
        out.append(line)
    return ''.join(out) if changed else None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tighten', action='store_true',
                    help='follow shrunk files down instead of reporting')
    args = ap.parse_args()

    sizes = tracked_sizes()
    if args.tighten:
        text = SELF.read_text(encoding='utf-8')
        rewritten = tightened(text, sizes)
        if rewritten is None:
            print('no module shrank below its recorded size')
            return 0
        SELF.write_text(rewritten, encoding='utf-8')
        print('tightened the module size baseline')
        return 0

    found = violations(sizes)
    if not any(found.values()):
        print(f'{len(sizes)} tracked modules within the size policy')
        return 0
    for kind, detail in found.items():
        if detail:
            print(f'{kind}: {detail}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
