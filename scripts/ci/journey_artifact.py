"""The committed journey budget as a document: what it may hold, and how
it is written.

Its own module, off `journey_budget.py`, because the budget and the
document are different responsibilities and the first was at its ceiling:
`journey_budget.py` decides what a count may be and this owns the shape a
recorded one arrives in. It is re-exported there, so every existing caller
of `journey_budget.load` / `.render` keeps the spelling it had.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_counters  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position

# `counter` may only name a candidate the counters module will gate on. A
# counter it merely reports — the syscall secondary signal — is
# refused here by name, so a recorded count can never be
# denominated in a quantity the gate does not defend.
COUNTERS = journey_counters.GATE_CANDIDATES
SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / '.github' / 'journey-budget.json'

FIELDS = ('schema_version', 'counter', 'tolerance_pct', 'toolchain',
          'excluded_threads', 'journeys')


def _validated(value):
    if not isinstance(value, dict):
        raise ValueError('the journey budget must be an object')
    unknown = sorted(set(value) - set(FIELDS))
    if unknown:
        raise ValueError(f'unknown field: {unknown[0]}')
    if value.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(
            f'unsupported schema_version: {value.get("schema_version")}')
    counter = value.get('counter')
    if counter is not None and counter not in COUNTERS:
        raise ValueError(f'unknown counter: {counter}')
    tolerance = value.get('tolerance_pct')
    if tolerance is not None and (not isinstance(tolerance, (int, float))
                                  or isinstance(tolerance, bool)
                                  or tolerance < 0):
        raise ValueError('tolerance_pct must be a nonnegative number: '
                         f'{tolerance}')
    toolchain = value.get('toolchain')
    if toolchain is not None and not isinstance(toolchain, dict):
        raise ValueError('toolchain must be an object')
    for field, seen in (toolchain or {}).items():
        if field not in journey_counters.TOOLCHAIN_FIELDS:
            raise ValueError(f'unknown toolchain field: {field}')
        # A blank identity compares unequal to every measured value, so a
        # toolchain that moved on it would read as a match.
        if seen is not None and (not isinstance(seen, str)
                                 or not seen.strip()):
            raise ValueError('a toolchain identity is a non-empty string or '
                             f'null: {field}')
    journeys = value.get('journeys')
    if not isinstance(journeys, dict):
        raise ValueError('journeys must be an object')
    _validated_exclusions(value.get('excluded_threads'), journeys)
    for name, recorded in journeys.items():
        if recorded is None:
            continue
        if not isinstance(recorded, int) or isinstance(recorded, bool) \
                or recorded < 0:
            raise ValueError(
                f'a recorded count must be a nonnegative integer: {name}')
    return value


def _validated_exclusions(value, journeys):
    """The per-journey thread roles this count deliberately does not carry.

    A count that excludes a background thread means something the count
    alone cannot say: the same instructions measured with the front end's
    import included is a different quantity. So the roles are recorded
    beside the counts, one list per journey, and a run whose roles differ
    from the recorded ones compares nothing — a changed gate, not a
    regression.

    Absent is a state, not a shape error: until the first recording of this
    field there is nothing to compare, which is reported rather than
    refused, exactly as an unrecorded toolchain is.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('excluded_threads must be an object')
    for name, roles in value.items():
        # Against the journeys the artefact carries, not against the journey
        # module: a role map naming a journey the document has no count for
        # is stale, and refusing it here is what keeps a rename from leaving
        # a map behind that nothing compares.
        if name not in journeys:
            raise ValueError(
                f'excluded_threads names a journey with no count: {name}')
        if not isinstance(roles, list) or not roles:
            raise ValueError(
                f'excluded_threads names no thread for {name}, which would '
                'say the count carries every thread the profile had')
        if len(set(roles)) != len(roles):
            raise ValueError(f'excluded_threads repeats a role: {name}')
        for role in roles:
            if role not in journey_threads.ROLES:
                raise ValueError(
                    f'unknown excluded thread role: {role} for {name}')
    return value


def load(path=ARTIFACT):
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as error:
        raise ValueError(
            f'cannot read the journey budget: {error}') from None
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'invalid journey budget JSON: {error}') from None
    return _validated(value)


def render(document):
    """The canonical bytes: one journey per line, so a diff reads as a set."""
    _validated(document)
    body = ',\n'.join(
        f'    {json.dumps(name)}: {json.dumps(document["journeys"][name])}'
        for name in sorted(document['journeys']))
    identity = recorded_toolchain(document) or {}
    toolchain = ',\n'.join(
        f'    {json.dumps(field)}: {json.dumps(identity.get(field))}'
        for field in journey_counters.TOOLCHAIN_FIELDS)
    # Absent stays absent: a rendering that invented the block would fail
    # the canonical-rendering control for every artefact recorded before it.
    exclusions = ''
    if document.get('excluded_threads') is not None:
        recorded = document['excluded_threads']
        rows = ',\n'.join(
            f'    {json.dumps(name)}: {json.dumps(recorded[name])}'
            for name in sorted(recorded))
        exclusions = '  "excluded_threads": {\n' + rows + '\n  },\n'
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            f'  "counter": {json.dumps(document.get("counter"))},\n'
            f'  "tolerance_pct": {json.dumps(document.get("tolerance_pct"))},'
            '\n'
            '  "toolchain": {\n'
            f'{toolchain}\n'
            '  },\n'
            f'{exclusions}'
            '  "journeys": {\n'
            f'{body}\n'
            '  }\n'
            '}\n').encode('utf-8')


def recorded_toolchain(document):
    """The recorded identity, or None while no field of it is recorded.

    Every field null is the state before the first baseline, and it is
    reported rather than compared: there is nothing yet to say the
    toolchain still matches.
    """
    recorded = document.get('toolchain') or {}
    return recorded if any(recorded.values()) else None


def recorded_exclusions(document):
    """The recorded per-journey roles, or None before anything is recorded."""
    return document.get('excluded_threads')


def exclusion_diff(recorded, applied):
    """Every journey whose recorded and applied thread roles differ.

    A count measured with a different set of threads excluded is a
    different quantity whatever it reads, so this is a refusal and not a
    comparison — the same rule as a toolchain that moved, and for the same
    reason.
    """
    recorded = recorded or {}
    applied = applied or {}
    return {name: (recorded.get(name), sorted(applied.get(name) or ()))
            for name in sorted(set(recorded) | set(applied))
            if sorted(recorded.get(name) or ()) != sorted(
                applied.get(name) or ())}
