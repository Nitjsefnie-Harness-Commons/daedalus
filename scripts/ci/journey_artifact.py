"""The committed journey budget as a document: what it may hold, and how
it is written.

Its own module, off `journey_budget.py`, because the budget and the
document are different responsibilities and the first was at its ceiling:
`journey_budget.py` decides what a count may be and this owns the shape a
recorded one arrives in. It is bound into that module by name rather than
reached through a forward, so a name the gates use has one owner.
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

# `thread_bands` is the record made before a thread's role was read from
# what it executed: the `Ir` thresholds that used to decide which role a
# thread fell in. Nothing compares it and no re-baseline writes it — the
# signatures below replaced it as the table a count is classified under —
# and it stays in the schema only so a document recorded before that change
# still loads until it is re-recorded.
FIELDS = ('schema_version', 'counter', 'tolerance_pct', 'toolchain',
          'excluded_threads', 'thread_bands', 'thread_signatures', 'shas',
          'journeys')

# A sha is a content hash, not a magnitude: it says what a journey RENDERED,
# not how much it cost, so it is comparable across machines and across
# toolchains, and a run's own value is the value to record.
SHA_HEX = 64


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
    _validated_bands(value.get('thread_bands'))
    _validated_signatures(value.get('thread_signatures'))
    _validated_shas(value.get('shas'), journeys)
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


def _validated_bands(value):
    """The `Ir` thresholds a count was recorded under, before roles came
    from identity.

    A role is no longer read from a thread's total, so these numbers decide
    nothing and no gate compares them; `thread_signatures` is the table that
    does. The shape is still checked — a document carrying this field has to
    carry a sane one — so a half-written artefact is refused rather than
    loaded and re-rendered, and a re-baseline drops the field outright.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('thread_bands must be an object')
    for role, threshold in value.items():
        if role not in journey_threads.ROLES:
            raise ValueError(f'unknown thread band: {role}')
        if not isinstance(threshold, int) or isinstance(threshold, bool) \
                or threshold <= 0:
            raise ValueError(
                f'a thread band must be a positive integer: {role} = '
                f'{threshold}')
    return value


def _validated_signatures(value):
    """The function names that put a thread in each signature-decided role.

    `excluded_threads` records the ROLES a journey leaves out while the
    table that PUTS a thread in one of them is guarded nowhere, so a run
    that read a profile by different symbols would compare a count taken
    under one classifier against counts taken under another. That is why
    this field exists, and it is why it replaced the `Ir` bands rather than
    sitting beside them: the bands stopped deciding a role when identity
    replaced size, so a gate comparing them would be comparing a field
    nothing reads.

    Absent is the state, not a shape error, exactly as for the roles: until
    the first recording of this field there is nothing to compare, which is
    reported rather than refused.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('thread_signatures must be an object')
    for role, names in value.items():
        if role not in journey_threads.SIGNATURES:
            raise ValueError(f'unknown thread signature: {role}')
        if not isinstance(names, list) or not names:
            raise ValueError(
                f'thread_signatures names no symbol for {role}, so that '
                'thread carries a signature nothing can match')
        if len(set(names)) != len(names):
            raise ValueError(f'thread_signatures repeats a symbol: {role}')
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f'a signature symbol is a non-empty string: {role} = '
                    f'{name!r}')
    return value


def _validated_shas(value, journeys):
    """The sha256 each journey's rendering had when its count was recorded.

    A recorded count is a statement about a journey, and a journey that
    renders differently is a different journey: without this the only sha
    comparison is across the rounds of ONE measurement, which at one round
    can never fire, and a changed rendering would be compared against the
    old counts and read as a regression or a saving.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('shas must be an object')
    for name, seen in value.items():
        if name not in journeys:
            raise ValueError(f'shas names a journey with no count: {name}')
        if not isinstance(seen, str) or not seen.strip():
            raise ValueError(f'a recorded sha is a non-empty string: {name}')
        # The length and the hex loop read the value AS STORED, not stripped:
        # stripping the first and not the second let a padded sha validate
        # and then report as a change rather than a format refusal.
        if len(seen) != SHA_HEX or \
                any(character not in '0123456789abcdef'
                    for character in seen):
            raise ValueError(
                f'a recorded sha is {SHA_HEX} lowercase hex characters: '
                f'{name} = {seen!r}')
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
    # Absent stays absent: a rendering that invented a block would fail
    # the canonical-rendering control for every artefact recorded before it.
    blocks = ''
    for field in ('excluded_threads', 'thread_bands', 'thread_signatures',
                  'shas'):
        if document.get(field) is None:
            continue
        rows = ',\n'.join(
            f'    {json.dumps(name)}: {json.dumps(document[field][name])}'
            for name in sorted(document[field]))
        blocks += (f'  {json.dumps(field)}: {{\n' + rows
                   + '\n  },\n')
    return ('{\n'
            f'  "schema_version": {document["schema_version"]},\n'
            f'  "counter": {json.dumps(document.get("counter"))},\n'
            f'  "tolerance_pct": {json.dumps(document.get("tolerance_pct"))},'
            '\n'
            '  "toolchain": {\n'
            f'{toolchain}\n'
            '  },\n'
            f'{blocks}'
            '  "journeys": {\n'
            f'{body}\n'
            '  }\n'
            '}\n').encode('utf-8')


def budget_of(document, name):
    """The count `name` may reach: its record plus the tolerance.

    Lives here rather than beside the policy because the summary renders the
    same number the gate compares against, and two copies of that
    arithmetic would drift into a table disagreeing with the verdict above
    it.
    """
    recorded = document['journeys'].get(name)
    if recorded is None:
        return None
    tolerance = document.get('tolerance_pct') or 0.0
    return recorded * (1 + tolerance / 100.0)


def recorded_toolchain(document):
    """The recorded identity, or None while no field of it is recorded.

    Every field null is the state before the first baseline, and it is
    reported rather than compared: there is nothing yet to say the
    toolchain still matches.
    """
    recorded = document.get('toolchain') or {}
    return recorded if any(recorded.values()) else None


def sha_diff(recorded, measured):
    """Every journey whose recorded sha this measurement does not carry.

    The measured side is ONE SHA PER ROUND and the recorded side is the one
    the counts were taken on, so what is compared is the value the rounds
    agree on. A measurement whose rounds disagree is refused here as well
    rather than compared against whichever of them a set happened to pick —
    and both states are one line, because a sha is a content hash and
    nothing about it is a magnitude.
    """
    recorded = recorded or {}
    measured = measured or {}
    differs = {}
    for name in sorted(set(recorded) | set(measured)):
        seen = measured.get(name) or []
        agreed = _as_set(seen)
        was = _as_set(recorded.get(name))
        if agreed != was:
            differs[name] = (recorded.get(name), sorted(agreed))
    return differs


def _as_set(value):
    """One sha, or the several a set of rounds reported, as a set.

    The recorded side is one string by the schema, but a caller holding a
    measurement's own map has a list, and building a set from one of those
    and not the other would be a crash rather than a comparison.
    """
    return set(value) if isinstance(value, list) else {value}


def map_diff(recorded, measured):
    """Every key whose recorded and measured value differ, both ways.

    A count measured against a different map is a different quantity, so
    this is a refusal and not a comparison. A key one side did not record
    differs too, which is what makes the never-recorded state refuse rather
    than fall through.
    """
    recorded = recorded or {}
    measured = measured or {}
    return {key: (recorded.get(key), measured.get(key))
            for key in sorted(set(recorded) | set(measured))
            if recorded.get(key) != measured.get(key)}


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
