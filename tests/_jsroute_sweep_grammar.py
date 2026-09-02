"""The grammar half of the tab-routing sweep: what a program is made of.

A sweep program buries a sender assignment in a container body and
reaches it through one route, so the runtime routes exactly when the
body ran before the send. This module holds the tables - key
spellings, member kinds, body forms and containers - and
`_jsroute_sweep` composes them into programs.
"""

RUNTIME_PREFIX = """const calls = [];
const extCmd = (...args) => calls.push(args);
const ordinary = () => undefined;
const chromeTab = 41;
"""
RUNTIME_SUFFIX = (
    "\nconst routed = calls.some(args => args.some(\n"
    "  value => value && value.tab === chromeTab));\n"
    "process.stdout.write(routed ? '1' : '0');\n")
SEND = "send('focus-tab', { tab: chromeTab });\n"
SLOT = '%S'

# Key spellings.  `defn` is how the member key is written where the
# container defines it; `access` is the canonical member access after a
# receiver; `dest` is the name a destructuring pattern can bind, or None
# where no pattern can name the key.


def key_forms(name):
    escaped = f'\\u{ord(name[0]):04x}' + name[1:]
    return [
        ('ident', name, '.' + name, name, 'both'),
        ('quoted', f"'{name}'", '.' + name, name, 'both'),
        ('computed', f"['{name}']", '.' + name, name, 'both'),
        ('computed-expr', f"['{name[0]}' + '{name[1:]}']",
         '.' + name, name, 'both'),
        ('unicode-escape', escaped, '.' + name, name, 'both'),
        ('numeric', '0', '[0]', None, 'both'),
        ('private', '#' + name, '.#' + name, None, 'private'),
    ]


# Member kinds.  Each renders the member text for an object literal or a
# class body, and declares the trigger category a route must match.

class Member:
    def __init__(self, label, trigger, where, render, params=True,
                 recv='instance', proto=True):
        self.label = label
        self.trigger = trigger
        self.where = where           # 'obj', 'class' or 'both'
        self.render = render         # (key, params, body) -> text
        self.params = params         # accepts a parameter list
        self.recv = recv             # 'instance' or 'class'
        self.proto = proto           # lives on the prototype


def _m_method(key, params, body):
    return '%s(%s) { %s }' % (key, params, body)


def _m_async(key, params, body):
    return 'async %s(%s) { %s }' % (key, params, body)


def _m_generator(key, params, body):
    return '* %s(%s) { %s }' % (key, params, body)


def _m_getter(key, params, body):
    return 'get %s() { %s return 1; }' % (key, body)


def _m_setter(key, params, body):
    return 'set %s(v) { %s }' % (key, body)


def _m_static(key, params, body):
    return 'static %s(%s) { %s }' % (key, params, body)


def _m_obj_arrow(key, params, body):
    return '%s: (%s) => { %s }' % (key, params, body)


def _m_obj_function(key, params, body):
    return '%s: function (%s) { %s }' % (key, params, body)


def _m_class_arrow(key, params, body):
    return '%s = (%s) => { %s };' % (key, params, body)


def _m_class_function(key, params, body):
    return '%s = function (%s) { %s };' % (key, params, body)


def _m_ctor(key, params, body):
    return 'constructor(%s) { %s }' % (params, body)


def _m_field_init(key, params, body):
    return '%s = (() => { %s return 1; })();' % (key, body)


MEMBERS = [
    Member('method', 'call', 'both', _m_method),
    Member('async-method', 'call', 'both', _m_async),
    Member('generator-method', 'generator', 'both', _m_generator),
    Member('getter', 'read', 'both', _m_getter, params=False),
    Member('setter', 'write', 'both', _m_setter, params=False),
    Member('obj-arrow-field', 'call', 'obj', _m_obj_arrow, proto=False),
    Member('obj-function-prop', 'call', 'obj', _m_obj_function,
           proto=False),
    Member('class-arrow-field', 'call', 'class', _m_class_arrow,
           proto=False),
    Member('class-function-field', 'call', 'class', _m_class_function,
           proto=False),
    Member('static-method', 'call', 'class', _m_static, recv='class'),
    Member('constructor', 'construct', 'class', _m_ctor),
    Member('class-field-init', 'construct', 'class', _m_field_init,
           params=False, proto=False),
]


# Body forms: what the member body does to reach the sender, and what it
# needs declared before the container.

class Body:
    def __init__(self, label, pre, stmt, params='', needs_arg=False):
        self.label = label
        self.pre = pre               # lines emitted before the container
        self.stmt = stmt             # statements inside the member body
        self.params = params         # parameter list the member needs
        self.needs_arg = needs_arg   # the route must pass the sender


BODIES = [
    Body('direct', [], f'send = {SLOT};'),
    Body('alias', [f'const s0 = {SLOT};'], 'send = s0;'),
    Body('alias-chain',
         [f'const s0 = {SLOT};', 'const s1 = s0;'], 'send = s1;'),
    Body('helper-fn',
         ['function helper0() { send = %s; }' % SLOT], 'helper0();'),
    Body('helper-arrow',
         ['const helper0 = () => { send = %s; };' % SLOT], 'helper0();'),
    Body('member-write',
         ['const bag0 = {};', f'bag0.hook = {SLOT};'],
         'send = bag0.hook;'),
    Body('member-write-computed',
         ['const bag0 = {};', f"bag0['ho' + 'ok'] = {SLOT};"],
         'send = bag0.hook;'),
    Body('param-default', [], 'send = s;', params=f's = {SLOT}'),
    Body('param-arg', [], 'send = s;', params='s', needs_arg=True),
]


# Containers.  `build` maps the rendered member text to the lines that
# define the container and the receiver expression a route reaches it
# through.  `holds` is the member syntax family the container accepts.

class Container:
    def __init__(self, label, holds, build, recv_kind='instance',
                 proto_only=False, constructs=False,
                 descriptor=False):
        self.label = label
        self.holds = holds           # 'obj' or 'class'
        self.build = build
        self.recv_kind = recv_kind
        self.proto_only = proto_only
        self.constructs = constructs
        self.descriptor = descriptor


def _obj_containers():
    def simple(text):
        return (['const m = { %s };' % text], 'm')

    def later(text):
        return (['let m;', 'm = { %s };' % text], 'm')

    def factory_arrow(text):
        return (['const mk0 = () => ({ %s });' % text,
                 'const m = mk0();'], 'm')

    def factory_fn(text):
        return (['function mk0() { return { %s }; }' % text,
                 'const m = mk0();'], 'm')

    def iife(text):
        return (['const m = (() => ({ %s }))();' % text], 'm')

    def spread(text):
        return (['const src0 = { %s };' % text,
                 'const m = { ...src0 };'], 'm')

    def assign(text):
        return (['const m = {};',
                 'Object.assign(m, { %s });' % text], 'm')

    def destructured(text):
        return (['const { ...m } = { %s };' % text], 'm')

    def inline(text):
        return ([], '({ %s })' % text)

    def array_elem(text):
        return (['const m = [{ %s }][0];' % text], 'm')

    def proxy(text):
        return (['const m = new Proxy({ %s }, {});' % text], 'm')

    def define_property(text):
        return (['const m = {};',
                 "Object.defineProperty(m, 'hook',",
                 '  { %s });' % text], 'm')

    return [
        Container('obj-literal', 'obj', simple),
        Container('obj-later-bound', 'obj', later),
        Container('factory-arrow', 'obj', factory_arrow),
        Container('factory-function', 'obj', factory_fn),
        Container('iife', 'obj', iife),
        Container('spread-copy', 'obj', spread),
        Container('object-assign-target', 'obj', assign),
        Container('destructured-copy', 'obj', destructured),
        Container('inline-literal', 'obj', inline),
        Container('array-element', 'obj', array_elem),
        Container('proxy-wrapped', 'obj', proxy),
        Container('define-property', 'obj', define_property,
                  descriptor=True),
    ]


def _class_containers():
    def decl(text):
        return (['class K { %s }' % text, 'const m = new K();'], 'm')

    def decl_noparens(text):
        return (['class K { %s }' % text, 'const m = new K;'], 'm')

    def decl_paren_callee(text):
        return (['class K { %s }' % text, 'const m = new (K)();'], 'm')

    def expr(text):
        return (['const K = class { %s };' % text,
                 'const m = new K();'], 'm')

    def expr_named(text):
        return (['const K = class Inner { %s };' % text,
                 'const m = new K();'], 'm')

    def later(text):
        return (['class K { %s }' % text, 'let m;', 'm = new K();'], 'm')

    def extends_base(text):
        return (['class B { %s }' % text, 'class K extends B {}',
                 'const m = new K();'], 'm')

    def proto(text):
        return (['class K { %s }' % text, 'const m = K.prototype;'], 'm')

    def static_recv(text):
        return (['class K { %s }' % text], 'K')

    def static_expr(text):
        return (['const K = class { %s };' % text], 'K')

    def inline_new(text):
        return (['class K { %s }' % text], 'new K()')

    def ctor_decl(text):
        return (['class K { %s }' % text], 'K')

    def ctor_expr(text):
        return (['const K = class { %s };' % text], 'K')

    def ctor_extends(text):
        return (['class B { %s }' % text, 'class K extends B {}'], 'K')

    return [
        Container('class-decl-new', 'class', decl),
        Container('class-new-no-parens', 'class', decl_noparens),
        Container('class-new-paren-callee', 'class', decl_paren_callee),
        Container('class-expression', 'class', expr),
        Container('class-expression-named', 'class', expr_named),
        Container('class-later-bound', 'class', later),
        Container('class-extends-base', 'class', extends_base),
        Container('class-prototype', 'class', proto, proto_only=True),
        Container('class-static-recv', 'class', static_recv,
                  recv_kind='class'),
        Container('class-static-expr', 'class', static_expr,
                  recv_kind='class'),
        Container('class-inline-new', 'class', inline_new),
        Container('class-decl-ctor', 'class', ctor_decl,
                  recv_kind='class', constructs=True),
        Container('class-expr-ctor', 'class', ctor_expr,
                  recv_kind='class', constructs=True),
        Container('class-extends-ctor', 'class', ctor_extends,
                  recv_kind='class', constructs=True),
    ]


CONTAINERS = _obj_containers() + _class_containers()
