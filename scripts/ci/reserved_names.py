#!/usr/bin/env python3
"""Check and tighten the set of names a new tests module may not bind.

The mutable policy state is the ``.github/reserved-test-names.json``
document: every reserved name, the limb that owns it, the module or
modules that own it, and the tests modules the derivation covered. The
committed form is generated from the tree, and the verdict is asked only
about the modules it names, because an exhaustive document is not a
question two branches can both answer (see ``violations``). The
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
_SCHEMA_VERSION = 2
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
        # The main config excludes `tests/`, where this module lives, so
        # pyright cannot resolve a name that `sys.path` above resolves at
        # runtime. The suppression states that property of the config; an
        # unresolvable import is `Any` to pyright either way.
        import _reserved_names  # pyright: ignore[reportMissingImports]
        _DERIVATION = _reserved_names
    return _DERIVATION


def _limbs():
    return set(_derivation().LIMBS)


def _validated(value, label='reserved names'):
    """The document this script writes, or a refusal naming what is wrong."""
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    unknown = sorted(set(value) - {'schema_version', 'names', 'modules'})
    if unknown:
        raise ValueError(f'unknown field: {unknown[0]}')
    if 'names' not in value:
        raise ValueError('missing field: names')
    schema = value.get('schema_version')
    if schema != _SCHEMA_VERSION:
        raise ValueError(f'unsupported schema_version: {schema}')
    if 'modules' not in value:
        raise ValueError('missing field: modules')
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
    modules = value['modules']
    if not isinstance(modules, list):
        raise ValueError('modules must be a list of module paths')
    for module in modules:
        if not isinstance(module, str) or not module:
            raise ValueError('a covered module must be a nonempty string')
    if len(set(modules)) != len(modules):
        raise ValueError('a module is covered twice')
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
    """The canonical bytes: one name and one module per line, so a diff
    reads as a set."""
    _validated(document)
    body = ',\n'.join(_line(name, entry)
                      for name, entry in sorted(document['names'].items()))
    scope = ',\n'.join(f'    {json.dumps(module)}'
                       for module in sorted(document['modules']))
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            '  "names": {\n'
            f'{body}\n'
            '  },\n'
            '  "modules": ['
            + (f'\n{scope}\n  ' if scope else '')
            + ']\n'
            '}\n').encode('utf-8')


def document(sources=None):
    """The document a fresh derivation of `sources` gives."""
    derived = _derivation().reserved(sources)
    return {
        'schema_version': _SCHEMA_VERSION,
        'names': {name: {limb: list(owners) for limb, owners in entry.items()}
                  for name, entry in derived.items()},
        'modules': sorted({owner for entry in derived.values()
                           for owners in entry.values() for owner in owners}),
    }


def _owners(entry):
    return {owner for owners in entry.values() for owner in owners}


def violations(committed, derived, tracked):
    """The names the committed document and a fresh derivation disagree on.

    `absent` is derived and not committed, `stale` is committed and no
    longer derived, `owners` is a name both carry with a different owner
    set -- a module renamed or a second module taking a name the first
    owned alone -- and `scope` is an owner the document records in a
    module it does not list as covered.

    THE VERDICT IS SCOPED TO WHAT THE DOCUMENT COVERED. A derivation
    over the whole tree is not a question two branches can both answer:
    each derives against a base the other has moved past, so each
    document is exact for what its own run saw and the MERGE is where
    the equality first fails, on `main`, with no branch left to fix it
    (issue 1266). `tracked` is the tests modules the checkout carries,
    and `covered` is the document's own `modules` intersected with it,
    so a module that has since been deleted is out of scope until the
    document absorbs the deletion.

    Each kind is a violation only when it is a fact about the covered
    modules: `absent` when every module that now binds the name is
    covered, `stale` when a recorded owner is still on disk (a module
    that merely stopped binding a name exists, so over-claiming stays a
    violation), and `owners` when every recorded owner is covered. A
    module outside the scope contributes a name the document owes
    nothing about, and it cannot be an owner the document records --
    which is what `scope` is for, and the one hole the scoping opens.
    """
    old = committed['names']
    new = derived['names']
    listed = set(committed['modules'])
    covered = listed & set(tracked)
    return {
        'absent': sorted(name for name in set(new) - set(old)
                         if _owners(new[name]) <= covered),
        'stale': sorted(name for name in set(old) - set(new)
                        if _owners(old[name]) & set(tracked)),
        'owners': sorted(name for name in set(old) & set(new)
                         if old[name] != new[name]
                         and _owners(old[name]) <= covered),
        'scope': sorted({owner for entry in old.values()
                         for owner in _owners(entry) - listed}),
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
        sources = tracked_sources(args.tree)
        derived = document(sources)
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
            print(f'covered modules: {len(derived["modules"])} of '
                  f'{len(sources)} tracked; a name bound by a module outside '
                  'that list is out of the committed set\'s scope')
            return 0
        found = violations(load(args.artifact), derived, set(sources))
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
