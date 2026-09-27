#!/usr/bin/env python3
"""Check and tighten the set of names a new tests module may not bind.

The mutable policy state is the ``.github/reserved-test-names.json``
document: every reserved name, the limb that owns it, and the module or
modules that own it. The committed form is generated from the tree. The
three limbs are the ones ``tests/_reserved_names.py`` derives from the
recognisers the two name-bound controls already use, so an entry is added
or dropped by tightening, and a name that is stale is a rule nobody
enforces. ``tests/test_reserved_test_names.py`` fails when the committed
document and a fresh derivation disagree, and prints the remedy a
refusal carries. That suite is also this script's CI coverage: no
workflow step names it, so the staleness check runs as
``test_reserved_test_names.py``
``::test_the_committed_set_is_what_the_rules_derive``:

  The committed set is generated and never edited by hand:
  python3 scripts/ci/reserved_names.py --tighten

  python3 scripts/ci/reserved_names.py
  python3 scripts/ci/reserved_names.py --tighten
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ARTIFACT = ROOT / '.github' / 'reserved-test-names.json'
_SCHEMA_VERSION = 1
_DERIVATION = None

STALE_REMEDY = (
    'The committed set is generated and never edited by hand: '
    'python3 scripts/ci/reserved_names.py --tighten')


def _derivation():
    """The tests-side derivation, imported on demand and kept.

    Imported inside the function rather than at module scope, so a tree
    that does not carry `tests/_reserved_names.py` -- the shape a bare
    copy of this script is run in -- is the one-line refusal `main`
    prints rather than a traceback before it starts. That is the only
    direction this script reaches out of its own directory, so it is
    also the only one that can fail here.

    Kept after the first success because `_validated` asks for the limb
    names once per (name, limb) PAIR it reads -- the call sits in the
    inner limb loop -- each a function call over a module `sys.modules`
    has already memoised. Timed over seven rounds, the two shapes differ
    by less than the run-to-run spread of the validation itself, so this
    is a tidiness fix and not a speed claim, and no count is given here
    for the reason `test_helper_reimplementation.py` leaves its
    population out: a count in prose is a claim somebody has to
    reproduce.
    """
    global _DERIVATION
    if _DERIVATION is None:
        import _reserved_names
        _DERIVATION = _reserved_names
    return _DERIVATION


def _limbs():
    return set(_derivation().LIMBS)


def _validated(value, label='reserved names'):
    """The document this script writes, or a refusal naming what is wrong."""
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    unknown = sorted(set(value) - {'schema_version', 'names'})
    if unknown:
        raise ValueError(f'unknown field: {unknown[0]}')
    if 'names' not in value:
        raise ValueError('missing field: names')
    schema = value.get('schema_version')
    if schema != _SCHEMA_VERSION:
        raise ValueError(f'unsupported schema_version: {schema}')
    if not isinstance(value['names'], dict):
        raise ValueError('names must be an object')
    for name, entry in sorted(value['names'].items()):
        if not isinstance(name, str) or not name:
            raise ValueError('a name must be a nonempty string')
        if not isinstance(entry, dict) or not entry:
            raise ValueError(f'a name must map to an object of limbs: {name}')
        for limb, owners in sorted(entry.items()):
            if limb not in _limbs():
                raise ValueError(f'unknown limb: {limb}')
            if (not isinstance(owners, list)
                    or not all(isinstance(one, str) for one in owners)):
                raise ValueError(f'owners must be module paths: {name}')
    return value


def load(path=ARTIFACT):
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as error:
        raise ValueError(f'cannot read reserved names: {error}') from None
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'invalid reserved names JSON: {error}') from None
    return _validated(value)


def _line(name, entry):
    limbs = ', '.join(
        f'{json.dumps(limb)}: {json.dumps(list(entry[limb]))}'
        for limb in _derivation().LIMBS if limb in entry)
    return f'    {json.dumps(name)}: {{{limbs}}}'


def render(document):
    """The canonical bytes: one name per line, so a diff reads as a set."""
    _validated(document)
    body = ',\n'.join(_line(name, entry)
                      for name, entry in sorted(document['names'].items()))
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            '  "names": {\n'
            f'{body}\n'
            '  }\n'
            '}\n').encode('utf-8')


def document(sources=None):
    """The document a fresh derivation of `sources` gives."""
    derived = _derivation().reserved(sources)
    return {
        'schema_version': _SCHEMA_VERSION,
        'names': {name: {limb: list(owners) for limb, owners in entry.items()}
                  for name, entry in derived.items()},
    }


def violations(committed, derived):
    """The names the committed document and a fresh derivation disagree on.

    `absent` is derived and not committed, `stale` is committed and no
    longer derived, and `owners` is a name both carry with a different
    owner set -- a module renamed or a second module taking a name the
    first owned alone.
    """
    old = committed['names']
    new = derived['names']
    return {
        'absent': sorted(set(new) - set(old)),
        'stale': sorted(set(old) - set(new)),
        'owners': sorted(name for name in set(old) & set(new)
                         if old[name] != new[name]),
    }


def tracked_sources(root):
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', 'tests/*.py'],
        capture_output=True, text=True, check=True)
    paths = listed.stdout.splitlines()
    if not paths:
        raise ValueError(f'git ls-files named no tests module under {root}')
    return {name: (Path(root) / name).read_text(encoding='utf-8')
            for name in paths}


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tighten', action='store_true',
                        help='rewrite the committed set from the tree')
    parser.add_argument('--tree', type=Path, default=ROOT,
                        help='the checkout whose tracked tests tree to read')
    parser.add_argument('--artifact', type=Path, default=ARTIFACT,
                        help='the committed set to read or write')
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        derived = document(tracked_sources(args.tree))
        if args.tighten:
            payload = render(derived)
            if (args.artifact.exists()
                    and args.artifact.read_bytes() == payload):
                print('the reserved set is already current')
                return 0
            args.artifact.parent.mkdir(parents=True, exist_ok=True)
            # The sibling ratchets publish through `thresholds`, so a
            # `--tighten` killed part way through truncates nothing.
            import thresholds
            thresholds.publish(args.artifact, payload)
            print(f'tightened the reserved set: {len(derived["names"])} names')
            return 0
        found = violations(load(args.artifact), derived)
        if not any(found.values()):
            print(f'{len(derived["names"])} reserved names match the '
                  'committed set')
            return 0
        for kind, rows in found.items():
            if rows:
                print(f'{kind}: {rows}', file=sys.stderr)
        print(STALE_REMEDY, file=sys.stderr)
        return 1
    except (AssertionError, ImportError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
