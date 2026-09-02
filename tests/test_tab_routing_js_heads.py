#!/usr/bin/env python3
"""Method-head spellings on literals and classes must parse exactly.

Each row pairs the node runtime against the guard: a promoter the runtime
reaches is reported, a demoter the parser reads exactly stays silent, and
a body that cannot be read in full is unprovable rather than silent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_SEND = "send('focus-tab', { tab: chromeTab });\n"


def _assert_rows(tmp, name, rows):
    """Assert the (runtime, guard) pair of every row at once."""
    path = Path(tmp) / name
    observed = []
    for label, source, want_runtime, closed in rows:
        runtime, guard = _runtime_and_guard(source, path)
        want_guard = True if closed else runtime
        observed.append((label, runtime, guard, want_runtime, want_guard))
    bad = [row[:3] for row in observed if row[1:3] != row[3:5]]
    assert not bad, bad


def test_smoke_plain_method_is_paired(tmp):
    rows = [('plain', "let send = ordinary;\n"
                      "const m = { go() { send = extCmd; }, plain() {} };\n"
                      "m.go();\n" + _SEND, True, False)]
    _assert_rows(tmp, 'smoke.js', rows)


# Literal method head spellings, with the key each spelling names.
_HEADS = [
    ('identifier', 'go'),
    ('single-quoted', "'go'"),
    ('double-quoted', '"go"'),
    ('escaped-identifier', '\\u0067o'),
    ('computed-literal', "['go']"),
    ('computed-expression', '[k]'),
]


def _literal(head):
    """A method entry of one head spelling plus a plain sibling."""
    return "const m = { " + head + "() { send = %S; }, plain() {} };\n"


def _head_source(head, call, demotes):
    extra = "const k = 'go';\n" if head == '[k]' else ''
    value = 'ordinary' if demotes else 'extCmd'
    seed = 'extCmd' if demotes else 'ordinary'
    return ("let send = " + seed + ";\n" + extra
            + _literal(head).replace('%S', value) + call + _SEND)


def _head_rows(label_prefix, call, idle='void m;\n'):
    """Promoter, demoter and uninvoked rows for one invocation form."""
    rows = []
    for label, head in _HEADS:
        rows.append((label_prefix + label + '-promoter',
                     _head_source(head, call, False), True, False))
        rows.append((label_prefix + label + '-demoter',
                     _head_source(head, call, True), False, False))
        rows.append((label_prefix + label + '-uninvoked-promoter',
                     _head_source(head, idle, False), False, False))
    return rows


def test_literal_method_heads_match_runtime(tmp):
    rows = (_head_rows('literal-dot-', 'm.go();\n')
            + _head_rows('literal-bracket-', "m['go']();\n",
                         idle="void m['plain'];\n"))
    _assert_rows(tmp, 'heads.js', rows)


def _class(body):
    """A class with one accessor/method body plus a plain sibling."""
    return ("class K {\n  " + body + "\n  plain() {}\n}\n"
            "const k = new K();\n")


_ACCESSORS = [
    ('get-dot', 'get', "void k.hook;\n"),
    ('get-bracket', 'get', "void k['hook'];\n"),
    ('set-dot', 'set', "k.hook = 1;\n"),
    ('set-bracket', 'set', "k['hook'] = 1;\n"),
]


def _accessor_body(form, promote):
    if form == 'get':
        inner = 'extCmd' if promote else 'ordinary'
        return 'get hook() { send = ' + inner + '; return 1; }'
    inner = 'extCmd' if promote else 'ordinary'
    return 'set hook(v) { send = ' + inner + '; }'


def test_class_instance_accessors_match_runtime(tmp):
    rows = []
    for label, form, query in _ACCESSORS:
        rows.append(('instance-' + label + '-promoter',
                     "let send = ordinary;\n"
                     + _class(_accessor_body(form, True))
                     + query + _SEND, True, False))
        rows.append(('instance-' + label + '-demoter',
                     "let send = extCmd;\n"
                     + _class(_accessor_body(form, False))
                     + query + _SEND, False, False))
        rows.append(('instance-' + label + '-uninvoked-promoter',
                     "let send = ordinary;\n"
                     + _class(_accessor_body(form, True))
                     + "void k;\n" + _SEND, False, False))
    _assert_rows(tmp, 'instance.js', rows)


def test_class_statics_match_runtime(tmp):
    rows = []
    for label, member, query in (
            ('method-dot', "static go() { send = %V; }", "K.go();\n"),
            ('method-bracket', "static go() { send = %V; }", "K['go']();\n"),
            ('getter-dot', "static get hook() { send = %V; return 1; }",
             "void K.hook;\n"),
            ('getter-bracket',
             "static get hook() { send = %V; return 1; }",
             "void K['hook'];\n"),
            ('setter-dot', "static set hook(v) { send = %V; }",
             "K.hook = 1;\n"),
            ('quoted-name', "static 'go'() { send = %V; }", "K.go();\n")):
        for kind, value, seed in (
                ('promoter', 'extCmd', 'ordinary'),
                ('demoter', 'ordinary', 'extCmd'),
                ('uninvoked-promoter', 'extCmd', 'ordinary')):
            query_text = ('void K;\n' if kind == 'uninvoked-promoter'
                          else query)
            rows.append((
                'static-' + label + '-' + kind,
                "let send = " + seed + ";\n"
                + _class(member.replace('%V', value)) + query_text + _SEND,
                kind != 'uninvoked-promoter' and value == 'extCmd',
                False))
    _assert_rows(tmp, 'statics.js', rows)


_INHERIT_BASE = (
    "class B {\n  %M\n}\nclass K extends B {}\nconst k = new K();\n")


def _inherit_rows(label_prefix, member, query):
    """Promoter/demoter rows resolving through a same-file base class."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            label_prefix + kind,
            "let send = " + seed + ";\n"
            + _INHERIT_BASE.replace('%M', member.replace('%V', value))
            + query + _SEND,
            value == 'extCmd', False))
    return rows


def test_class_inheritance_match_runtime(tmp):
    rows = (
        _inherit_rows('inherit-getter-', "get hook() { send = %V; }",
                      "void k.hook;\n")
        + _inherit_rows('inherit-static-method-', "static go() { send = %V; }",
                        "K.go();\n")
        + _inherit_rows('inherit-static-getter-',
                        "static get hook() { send = %V; return 1; }",
                        "void K.hook;\n"))
    # A base that is not a same-file class declaration cannot be read.
    rows.append((
        'inherit-unknown-base',
        "let send = ordinary;\nfunction Missing() {}\n"
        "class K extends Missing {}\nconst k = new K();\n"
        "void k.hook;\n" + _SEND, False, True))
    _assert_rows(tmp, 'inherit.js', rows)


def _opaque_source(member, seed, query):
    return ("let send = " + seed + ";\n"
            + _class(member) + query + _SEND)


def test_opaque_class_bodies_match_runtime(tmp):
    """A member the walk cannot read seals the whole class as opaque."""
    rows = []
    for label, member, query in (
            ('static-block',
             "static { send = %V; }", "void K.plain;\n"),
            ('field-initializer',
             "seed = (send = %V, 1);", "void k.plain;\n"),
            ('computed-accessor-unresolvable',
             "get [key]() { send = %V; return 1; }", "void k.hook;\n"),
            ('computed-accessor-resolvable',
             "get [key]() { send = %V; return 1; }", "void k.hook;\n")):
        resolvable = label == 'computed-accessor-resolvable'
        extra = "const key = 'hook';\n" if resolvable else (
            "const key = 'h' + 'ook';\n")
        for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                                  ('demoter', 'ordinary', 'extCmd')):
            expected_closed = not resolvable and kind == 'demoter'
            rows.append((
                'opaque-' + label + '-' + kind,
                extra + _opaque_source(
                    member.replace('%V', value), seed, query),
                value == 'extCmd', expected_closed))
    _assert_rows(tmp, 'opaque.js', rows)


# Every entry syntax a literal or class body may hold, as one promoting
# entry beside a plain sibling: (label, carrier, entry, invoke, prelude).
# A `closed` demoter is fail-closed: the member is not modelled, so the
# lookup answers unprovable instead of proving silence.
_TABLE = [
    ('identifier-method', 'literal', 'go() { send = %V; }', 'm.go();\n', ''),
    ('quoted-method', 'literal', "'go'() { send = %V; }", 'm.go();\n', ''),
    ('double-quoted-method', 'literal', '"go"() { send = %V; }',
     'm.go();\n', ''),
    ('escaped-method', 'literal', '\\u0067o() { send = %V; }',
     'm.go();\n', ''),
    ('computed-literal-key', 'literal', "['go']() { send = %V; }",
     'm.go();\n', ''),
    ('computed-expression-key', 'literal', '[k]() { send = %V; }',
     'm.go();\n', "const k = 'go';\n"),
    ('numeric-key', 'literal', '0() { send = %V; }', 'm[0]();\n', ''),
    ('getter', 'literal', 'get hook() { send = %V; return 1; }',
     'void m.hook;\n', ''),
    ('setter', 'literal', 'set hook(v) { send = %V; }', 'm.hook = 1;\n', ''),
    ('async-method', 'literal', 'async go() { send = %V; }',
     'm.go();\n', ''),
    ('generator', 'literal', '*go() { send = %V; }',
     'for (const item of m.go()) {}\n', ''),
    ('async-generator', 'literal', 'async *go() { send = %V; }',
     'const it = m.go();\nit.next();\n', ''),
    ('spread', 'literal', '...src', 'm.go();\n',
     'const src = { go() { send = %V; } };\n'),
    ('shorthand-property', 'literal', 'go', 'm.go();\n',
     'const go = () => { send = %V; };\n'),
    ('data-property', 'literal', 'go: () => { send = %V; }',
     'm.go();\n', ''),
    ('static-method', 'class', 'static go() { send = %V; }',
     'K.go();\n', ''),
]


def _table_source(seed, carrier, entry, prelude, value):
    if carrier == 'literal':
        body = ("const m = { " + entry.replace('%V', value)
                + ", plain() {} };\n")
    else:
        body = _class(entry.replace('%V', value))
    return ("let send = " + seed + ";\n" + prelude.replace('%V', value)
            + body)


def test_entry_syntax_table_matches_runtime(tmp):
    """No entry syntax stays guard-silent while its promotion runs."""
    rows = []
    for label, carrier, entry, invoke, prelude in _TABLE:
        for kind, value, seed, run in (
                ('promoter', 'extCmd', 'ordinary', True),
                ('demoter', 'ordinary', 'extCmd', False),
                ('uninvoked-promoter', 'extCmd', 'ordinary', False)):
            idle = ('void m;\n' if carrier == 'literal'
                    else 'void k;\nvoid K;\n')
            closed = kind == 'demoter' and label in (
                'numeric-key', 'async-generator')
            rows.append((f'table-{label}-{kind}',
                         _table_source(seed, carrier, entry, prelude, value)
                         + (idle if kind == 'uninvoked-promoter' else invoke)
                         + _SEND, run, closed))
    for label, entry, query in (
            ('private-method', '#go() { send = %V; }', 'void k.plain;\n'),
            ('class-field', 'seed = (send = %V, 1);', 'void k.plain;\n'),
            ('static-block', 'static { send = %V; }', 'void K.plain;\n')):
        rows.append((
            'table-' + label + '-promoter',
            _table_source('ordinary', 'class', entry, '', 'extCmd')
            + query + _SEND,
            label != 'private-method', False))
        rows.append((
            'table-' + label + '-demoter',
            _table_source('extCmd', 'class', entry, '', 'ordinary')
            + query + _SEND,
            label == 'private-method', label != 'private-method'))
    _assert_rows(tmp, 'table.js', rows)


def test_unread_sibling_entries_match_runtime(tmp):
    """An entry whose key is unread never answers for another key."""
    rows = []
    for label, entry in (
            ('method-sibling', "go() { send = %V; }"),
            ('data-sibling', "go: () => { send = %V; }")):
        for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                                  ('demoter', 'ordinary', 'extCmd')):
            rows.append((
                'unread-' + label + '-' + kind,
                "let send = " + seed + ";\n"
                "function pick() { return 'zz'; }\n"
                "const kk = pick();\n"
                "const m = { " + entry.replace('%V', value)
                + ", [kk]: () => {} };\n"
                "m.go();\n" + _SEND,
                value == 'extCmd', False))
    _assert_rows(tmp, 'unread.js', rows)


def test_class_accessor_pairs_match_runtime(tmp):
    """A form-mismatched member never proves a sibling accessor absent."""
    rows = []
    for label, body, query in (
            ('get-then-set', "get hook() { return 1; }\n"
             "  set hook(v) { send = %V; }",
             "const k = new K();\nk.hook = 1;\n"),
            ('get-then-set-read', "get hook() { send = %V; return 1; }\n"
             "  set hook(v) {}",
             "const k = new K();\nvoid k.hook;\n"),
            ('set-then-get', "set hook(v) {}\n"
             "  get hook() { send = %V; return 1; }",
             "const k = new K();\nvoid k.hook;\n"),
            ('method-then-get', "hook() {}\n"
             "  get hook() { send = %V; return 1; }",
             "const k = new K();\nvoid k.hook;\n"),
            ('static-pair', "static get hook() { return 1; }\n"
             "  static set hook(v) { send = %V; }",
             "K.hook = 1;\n"),
            ('quoted-pair', "get 'hook'() { return 1; }\n"
             "  set 'hook'(v) { send = %V; }",
             "const k = new K();\nk['hook'] = 1;\n")):
        for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                                  ('demoter', 'ordinary', 'extCmd')):
            rows.append((
                'pair-' + label + '-' + kind,
                "let send = " + seed + ";\n"
                + "class K {\n  " + body.replace('%V', value)
                + "\n}\n" + query + _SEND,
                value == 'extCmd', False))
    _assert_rows(tmp, 'pairs.js', rows)


def test_duplicate_members_last_wins(tmp):
    """The last member with a key answers, as JavaScript resolves it."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'dup-class-' + kind,
            "let send = " + seed + ";\n"
            + _class("get hook() { return 1; }\n"
                     "  get hook() { send = %V; return 1; }".replace(
                         '%V', value))
            + "void k.hook;\n" + _SEND,
            value == 'extCmd', False))
        rows.append((
            'dup-literal-' + kind,
            "let send = " + seed + ";\n"
            "const m = { go() {}, go() { send = " + value + "; },"
            " plain() {} };\n"
            "m.go();\n" + _SEND,
            value == 'extCmd', False))
    _assert_rows(tmp, 'dup.js', rows)


def test_computed_key_initializers_match_runtime(tmp):
    """A computed key counts as known only for a whole-literal constant."""
    rows = []
    for label, prelude, member, query in (
            ('literal-concat', "const k = 'g' + 'o';\n",
             "const m = { [k]() { send = %V; }, plain() {} };\n",
             'm.go();\n'),
            ('class-concat', "const key = 'h' + 'ook';\n",
             "class K {\n  get [key]() { send = %V; return 1; }\n"
             "  plain() {}\n}\nconst k = new K();\n",
             'void k.hook;\n')):
        for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                                  ('demoter', 'ordinary', 'extCmd')):
            rows.append((
                'computed-' + label + '-' + kind,
                "let send = " + seed + ";\n" + prelude
                + member.replace('%V', value) + query + _SEND,
                value == 'extCmd', kind == 'demoter'))
    _assert_rows(tmp, 'computed.js', rows)


def test_later_declared_class_statics_match_runtime(tmp):
    """A static call inside a function resolves a class declared later."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'later-static-' + kind,
            "let send = " + seed + ";\n"
            "function later() { K.go(); }\n"
            "class K {\n  static go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "later();\n" + _SEND,
            value == 'extCmd', False))
    _assert_rows(tmp, 'later.js', rows)


def test_scoped_class_declarations_match_runtime(tmp):
    """Same-named classes in different scopes are not decidable."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'shadow-inner-function-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  static go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "K.go();\n"
            "function f() { class K {\n  static go() {}\n"
            "  plain() {} } }\n" + _SEND,
            kind == 'promoter', True))
        rows.append((
            'shadow-inner-block-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  static go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "K.go();\n"
            "{ class K {\n  static go() {}\n  plain() {} } }\n" + _SEND,
            kind == 'promoter', True))
        rows.append((
            'shadow-inner-instance-getter-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  get hook() { send = " + value
            + "; return 1; }\n  plain() {} }\n"
            "const k = new K();\nvoid k.hook;\n"
            "function f() { class K {\n  get hook() { return 1; }\n"
            "  plain() {} } }\n" + _SEND,
            kind == 'promoter', True))
        rows.append((
            'shadow-outer-later-top-level-' + kind,
            "let send = " + seed + ";\n"
            "function f() { class K {\n  static go() { send = " + value
            + "; }\n  plain() {} }\n  K.go(); }\n"
            "f();\n"
            "class K {\n  static go() {}\n  plain() {} }\n" + _SEND,
            kind == 'promoter', True))
        rows.append((
            'call-after-both-inner-shadow-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  static go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "function f() { class K {\n  static go() {}\n"
            "  plain() {} } }\n"
            "K.go();\n" + _SEND,
            kind == 'promoter', True))
        rows.append((
            'extends-base-shadowed-inner-' + kind,
            "let send = " + seed + ";\n"
            "class B {\n  static go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "class K extends B {}\n"
            "function f() { class B {\n  static go() {}\n"
            "  plain() {} } }\n"
            "K.go();\n" + _SEND,
            kind == 'promoter', True))
    _assert_rows(tmp, 'scoped.js', rows)


def test_bare_class_fields_match_runtime(tmp):
    """A bare field is an own property: it shadows the same-key member."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'bare-field-shadows-getter-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  hook;\n"
            "  get hook() { send = " + value + "; return 1; }\n"
            "  plain() {}\n}\n"
            "const k = new K();\nvoid k.hook;\n" + _SEND,
            kind == 'demoter', False))
        rows.append((
            'bare-static-field-shadows-static-getter-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  static hook;\n"
            "  static get hook() { send = " + value + "; return 1; }\n"
            "  plain() {}\n}\n"
            "void K.hook;\n" + _SEND,
            kind == 'demoter', False))
        rows.append((
            'bare-field-shadows-method-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n  go;\n  go() { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "const k = new K();\ntry { k.go(); } catch (e) {}\n" + _SEND,
            kind == 'demoter', False))
        rows.append((
            'base-bare-field-shadows-derived-getter-' + kind,
            "let send = " + seed + ";\n"
            "class B {\n  hook;\n  plain() {}\n}\n"
            "class K extends B {\n"
            "  get hook() { send = " + value + "; return 1; }\n"
            "  plain() {}\n}\n"
            "const k = new K();\nvoid k.hook;\n" + _SEND,
            kind == 'demoter', False))
        rows.append((
            'base-bare-field-shadows-derived-setter-' + kind,
            "let send = " + seed + ";\n"
            "class B {\n  hook;\n  plain() {}\n}\n"
            "class K extends B {\n"
            "  set hook(v) { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "const k = new K();\nk.hook = 1;\n" + _SEND,
            kind == 'demoter', False))
    _assert_rows(tmp, 'barefields.js', rows)


def test_side_mismatch_lookups_match_runtime(tmp):
    """A static lookup is never answered by an instance member, and vice
    versa."""
    rows = [
        ('instance-getter-for-static-lookup',
         "let send = ordinary;\n"
         + _class("get hook() { send = extCmd; return 1; }")
         + "void K.hook;\n" + _SEND, False, False),
        ('static-getter-for-instance-lookup',
         "let send = ordinary;\n"
         + _class("static get hook() { send = extCmd; return 1; }")
         + "void k.hook;\n" + _SEND, False, False),
    ]
    _assert_rows(tmp, 'sides.js', rows)


def test_unknown_lookup_keys_match_runtime(tmp):
    """A lookup whose key is unknown reports while a member could match."""
    rows = []
    for kind, value in (('promoter', 'extCmd'), ('demoter', 'ordinary')):
        rows.append((
            'unknown-key-class-setter-' + kind,
            "let send = " + ('extCmd' if kind == 'demoter' else 'ordinary')
            + ";\n"
            "function key() { return '#hook'; }\n"
            "class K {\n  set '#hook'(v) { send = " + value + "; }\n"
            "  plain() {}\n}\n"
            "const obj = new K();\nobj[key()] = 1;\n" + _SEND,
            kind == 'promoter', kind == 'demoter'))
    rows.append((
        'unknown-key-misses-private-member',
        "let send = ordinary;\n"
        "function key() { return 'x'; }\n"
        "class K {\n  set #x(v) { send = extCmd; }\n"
        "  plain() {}\n}\n"
        "const obj = new K();\nobj[key()] = 1;\n" + _SEND,
        False, False))
    _assert_rows(tmp, 'unknownkeys.js', rows)


def test_reachable_private_calls_match_runtime(tmp):
    """A private method call through the class name is resolved."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'private-call-' + kind,
            "let send = " + seed + ";\n"
            "class K {\n"
            "  static #hidden() { send = " + value + "; }\n"
            "  static go() { K.#hidden(); }\n"
            "  plain() {}\n}\n"
            "K.go();\n" + _SEND,
            kind == 'promoter', False))
    rows.append(('private-call-uninvoked',
                 "let send = ordinary;\n"
                 "class K {\n"
                 "  static #hidden() { send = extCmd; }\n"
                 "  static go() { K.#hidden(); }\n"
                 "  plain() {}\n}\n"
                 "void K;\n" + _SEND, False, False))
    _assert_rows(tmp, 'private.js', rows)


def test_constructor_members_seal_opaque(tmp):
    """A class whose constructor defines members is not provably absent."""
    rows = []
    for kind, value, seed in (('promoter', 'extCmd', 'ordinary'),
                              ('demoter', 'ordinary', 'extCmd')):
        rows.append((
            'constructor-' + kind,
            "let send = " + seed + ";\n"
            + _class("constructor() { this.go = () => { send = " + value
                     + "; }; }")
            + "k.go();\n" + _SEND,
            value == 'extCmd', kind == 'demoter'))
    _assert_rows(tmp, 'ctor.js', rows)


def test_asi_bare_field_is_recorded(tmp):
    """The walk records an ASI-terminated bare field as a data member.

    The end-to-end shadow shape stays false green until the call scanner
    stops crediting a following method head as invoked, so this pins the
    walk limb directly.
    """
    del tmp
    from types import SimpleNamespace
    import _jsread
    from _jsroute_keys import _class_members

    text = ("let send = ordinary;\n"
            "class K {\n  hook\n  get hook() { send = extCmd; }\n"
            "  plain() {}\n}\n")
    mask = _jsread.js_mask(text)
    pairs = {}
    stack = []
    for pos, ch in enumerate(mask):
        if ch in '([{':
            stack.append(pos)
        elif ch in ')]}':
            if stack:
                pairs[stack.pop()] = pos + 1
    receiver = SimpleNamespace(
        mask=mask, text=text, pair_end=pairs, computed_key=lambda key: None)
    opening = mask.index('{', text.index('class K'))
    close = pairs[opening]
    members = _class_members(receiver, opening, close)
    assert members is not None, members
    assert any(member[0] is False and member[1] == 'data'
               and member[2] == 'hook' for member in members), members


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsheads_')


if __name__ == '__main__':
    raise SystemExit(main())
