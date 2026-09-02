"""Whole JavaScript programs sweeping the tab-routing guard's blind
spots.

Each program is the frame `tests/test_tab_routing_js.py` runs: a
prefix defining `extCmd`/`ordinary`/`chromeTab`, a generated body, and
a suffix reporting whether a recorded call carried the tab. Two
directions are emitted per grammar point. The promoter seeds `send`
with `ordinary` and the container body writes `extCmd`, so the runtime
routes exactly when the body ran before the send; the demoter is the
twin, seeding `extCmd` and writing `ordinary`.

The guard may over-report - a container it cannot follow is a taint -
but a program the runtime routes and the guard passes is a false
green, which is what the sweep exists to find.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _jsroute_sweep_grammar import (  # noqa: E402
    BODIES, CONTAINERS, MEMBERS, RUNTIME_PREFIX, RUNTIME_SUFFIX, SEND,
    SLOT, key_forms)

# Routes.  A template is rendered with `{r}` (receiver expression),
# `{a}` (canonical member access), `{p}` (property name), `{d}` (a name
# a pattern may bind) and `{arg}` (the sender, where the body needs it
# handed in).  `needs` restricts a route to keys that supply what it
# spells out.


class Route:
    def __init__(self, label, trigger, tmpl, needs=(),
                 implicit_args=False):
        self.label = label
        self.trigger = trigger
        self.tmpl = tmpl
        self.needs = set(needs)
        # A tag call hands the member its own first argument, so a
        # body that reads parameter one cannot ride on it.
        self.implicit_args = implicit_args


CALL_ROUTES = [
    Route('direct-call', 'call', '{r}{a}({arg});'),
    Route('bracket-call', 'call', "{r}['{p}']({arg});", ('ident-prop',)),
    Route('computed-expr-call', 'call',
          "{r}['{p0}' + '{p1}']({arg});", ('split-prop',)),
    Route('optional-call', 'call', '{r}{a}?.({arg});'),
    Route('optional-chain-call', 'call', '{r}?.{pdot}({arg});',
          ('ident-prop',)),
    Route('call-method', 'call', '{r}{a}.call({r}{argc});'),
    Route('apply-method', 'call', '{r}{a}.apply({r}, [{arg}]);'),
    Route('bind-then-call', 'call', '{r}{a}.bind({r}){argp};'),
    Route('fn-proto-call-call', 'call',
          'Function.prototype.call.call({r}{a}, {r}{argc});'),
    Route('reflect-apply', 'call',
          'Reflect.apply({r}{a}, {r}, [{arg}]);'),
    Route('value-then-call', 'call',
          'const f0 = {r}{a};\nf0({arg});'),
    Route('value-paren-call', 'call',
          'const f0 = ({r}{a});\n(f0)({arg});'),
    Route('paren-receiver-value', 'call',
          'const f0 = ({r}){a};\nf0({arg});'),
    Route('helper-arrow-arg', 'call',
          'const run0 = (f) => f({arg});\nrun0({r}{a});'),
    Route('helper-fn-arg', 'call',
          'function run0(f) {{ f({arg}); }}\nrun0({r}{a});'),
    Route('helper-default-param', 'call',
          'function run0(f = {r}{a}) {{ f({arg}); }}\nrun0();'),
    Route('destructured-param', 'call',
          'const run0 = ({{ {d} }}) => {d}({arg});\nrun0({r});',
          ('dest',)),
    Route('nested-destructured-param', 'call',
          'const run0 = ({{ inner: {{ {d} }} }}) => {d}({arg});\n'
          'run0({{ inner: {r} }});', ('dest',)),
    Route('destructured-value', 'call',
          'const {{ {d} }} = {r};\n{d}({arg});', ('dest',)),
    Route('destructured-default', 'call',
          'const {{ {d} = null }} = {r};\n{d}({arg});', ('dest',)),
    Route('array-element-call', 'call', '[{r}][0]{a}({arg});'),
    Route('comma-call', 'call', '(0, {r}{a})({arg});'),
    Route('chain-one-hop', 'call',
          'const w0 = {{ m: {r} }};\nw0.m{a}({arg});'),
    Route('chain-two-hops', 'call',
          'const w0 = {{ a: {{ m: {r} }} }};\nw0.a.m{a}({arg});'),
    Route('chain-three-hops', 'call',
          'const w0 = {{ a: {{ b: {{ m: {r} }} }} }};\n'
          'w0.a.b.m{a}({arg});'),
    Route('optional-chain-hop', 'call',
          'const w0 = {{ m: {r} }};\nw0?.m?.{pdot}({arg});',
          ('ident-prop',)),
    Route('tagged-template', 'call', '{r}{a}`x`;',
          implicit_args=True),
    Route('proxy-then-call', 'call',
          'const p0 = new Proxy({r}, {{}});\np0{a}({arg});'),
    Route('for-in-call', 'call',
          'for (const k0 in {r}) {{ {r}[k0]({arg}); }}'),
    Route('sibling-this-call', 'call', None),
]

READ_ROUTES = [
    Route('direct-read', 'read', 'void {r}{a};'),
    Route('bracket-read', 'read', "void {r}['{p}'];", ('ident-prop',)),
    Route('computed-expr-read', 'read',
          "void {r}['{p0}' + '{p1}'];", ('split-prop',)),
    Route('optional-read', 'read', 'void {r}?.{pdot};', ('ident-prop',)),
    Route('typeof-read', 'read', 'void typeof {r}{a};'),
    Route('equality-read', 'read', 'if ({r}{a} == 1) {{ void 0; }}'),
    Route('template-read', 'read', 'void `${{{r}{a}}}`;'),
    Route('destructured-read', 'read',
          'const {{ {d} }} = {r};\nvoid {d};', ('dest',)),
    Route('nested-destructured-read', 'read',
          'const {{ inner: {{ {d} }} }} = {{ inner: {r} }};\n'
          'void {d};', ('dest',)),
    Route('spread-read', 'read', 'void {{ ...{r} }};'),
    Route('object-assign-read', 'read',
          'const o0 = {{}};\nObject.assign(o0, {r});'),
    Route('json-read', 'read', 'void JSON.stringify({r});'),
    Route('for-in-read', 'read',
          'for (const k0 in {r}) {{ void {r}[k0]; }}'),
    Route('in-operator', 'read', "void ('{p}' in {r});", ('ident-prop',)),
    Route('delete-operator', 'read', 'void (delete {r}{a});'),
    Route('array-element-read', 'read', 'void [{r}][0]{a};'),
    Route('chain-one-hop-read', 'read',
          'const w0 = {{ m: {r} }};\nvoid w0.m{a};'),
    Route('chain-two-hops-read', 'read',
          'const w0 = {{ a: {{ m: {r} }} }};\nvoid w0.a.m{a};'),
    Route('proxy-then-read', 'read',
          'const p0 = new Proxy({r}, {{}});\nvoid p0{a};'),
    Route('sibling-this-read', 'read', None),
]

_ASSIGN_OPS = ['=', '+=', '-=', '*=', '/=', '%=', '**=', '<<=', '>>=',
               '>>>=', '&=', '|=', '^=', '||=', '&&=', '??=']

WRITE_ROUTES = [Route(f'assign-{op}', 'write', '{r}{a} %s 1;' % op)
                for op in _ASSIGN_OPS] + [
    Route('bracket-assign', 'write', "{r}['{p}'] = 1;", ('ident-prop',)),
    Route('bracket-shift-assign', 'write',
          "{r}['{p}'] <<= 1;", ('ident-prop',)),
    Route('prefix-increment', 'write', '++{r}{a};'),
    Route('postfix-increment', 'write', '{r}{a}++;'),
    Route('prefix-decrement', 'write', '--{r}{a};'),
    Route('object-destructure-assign', 'write',
          '({{ {p}: {r}{a} }} = {{ {p}: 1 }});', ('ident-prop',)),
    Route('array-destructure-assign', 'write', '[{r}{a}] = [1];'),
    Route('for-of-assign-target', 'write',
          'for ({r}{a} of [1]) {{ void 0; }}'),
    Route('object-assign-write', 'write',
          "Object.assign({r}, {{ '{p}': 1 }});", ('ident-prop',)),
    Route('chain-one-hop-write', 'write',
          'const w0 = {{ m: {r} }};\nw0.m{a} = 1;'),
    Route('proxy-then-write', 'write',
          'const p0 = new Proxy({r}, {{}});\np0{a} = 1;'),
    Route('sibling-this-write', 'write', None),
]

CONSTRUCT_ROUTES = [
    Route('new-parens', 'construct', 'new {r}({arg});'),
    Route('new-no-parens', 'construct', 'new {r};'),
    Route('new-paren-callee', 'construct', 'new ({r})({arg});'),
    Route('new-bound-const', 'construct',
          'const k0 = new {r}({arg});\nvoid k0;'),
    Route('new-bound-let', 'construct',
          'let k0;\nk0 = new {r}({arg});\nvoid k0;'),
    Route('new-in-literal', 'construct',
          'const w0 = {{ k: new {r}({arg}) }};\nvoid w0;'),
    Route('new-passed-to-helper', 'construct',
          'const run0 = (o) => o;\nvoid run0(new {r}({arg}));'),
    Route('new-array-element', 'construct',
          'void [new {r}({arg})][0];'),
    Route('reflect-construct', 'construct',
          'Reflect.construct({r}, [{arg}]);'),
    Route('extends-then-new', 'construct',
          'class D0 extends {r} {{}}\nnew D0({arg});'),
    Route('new-then-spread', 'construct',
          'void {{ ...new {r}({arg}) }};'),
]

GENERATOR_ROUTES = [
    Route('generator-next', 'generator', '{r}{a}().next();'),
    Route('generator-spread', 'generator', 'void [...{r}{a}()];'),
    Route('generator-for-of', 'generator',
          'for (const v0 of {r}{a}()) {{ void v0; }}'),
    Route('generator-uncalled', 'generator', 'void {r}{a};'),
]

ROUTES = (CALL_ROUTES + READ_ROUTES + WRITE_ROUTES + CONSTRUCT_ROUTES
          + GENERATOR_ROUTES)


# Contexts and order.

CONTEXTS = ['top-level', 'in-called-function', 'in-block',
            'after-shadow', 'with-decoys', 'asi-block']
ORDERS = ['before', 'after', 'never']
DIRECTIONS = ['promoter', 'demoter']


def _indent(lines):
    return ['  ' + line if line else line for line in lines]


def apply_context(context, body_lines):
    """Wrap the container-and-route lines in the chosen context."""
    if context == 'top-level':
        return list(body_lines)
    if context == 'in-called-function':
        return (['function ctx0() {'] + _indent(body_lines)
                + ['}', 'ctx0();'])
    if context == 'in-block':
        return ['{'] + _indent(body_lines) + ['}']
    if context == 'after-shadow':
        return (['function shadow0() { let send = ordinary; void send; }',
                 'shadow0();'] + list(body_lines))
    if context == 'with-decoys':
        return (['// send = extCmd; m.go(); new K();',
                 'const note0 = "send = extCmd";',
                 '/* set hook(v) { send = extCmd; } */',
                 'void note0;'] + list(body_lines))
    if context == 'asi-block':
        # The hazard belongs to the route, not to the whole program: the
        # route drops its terminator and a block follows it.
        return list(body_lines)
    raise ValueError(context)


# Grammar point -> source text.

class Point:
    __slots__ = ('container', 'member', 'key', 'route', 'body',
                 'context', 'order', 'direction')

    def __init__(self, container, member, key, route, body, context,
                 order, direction):
        self.container = container
        self.member = member
        self.key = key
        self.route = route
        self.body = body
        self.context = context
        self.order = order
        self.direction = direction

    def family(self):
        return (self.container.label, self.route.label, self.body.label,
                self.context)

    def as_dict(self):
        return {'container': self.container.label,
                'member': self.member.label, 'key': self.key[0],
                'route': self.route.label, 'body': self.body.label,
                'context': self.context, 'order': self.order,
                'direction': self.direction}


def _sibling_route(member, key, spelling):
    """A private or numeric key reached from a sibling method."""
    access = key[2]
    if member.trigger == 'call':
        inner = f'this{access}();'
    elif member.trigger == 'read':
        inner = f'void this{access};'
    elif member.trigger == 'write':
        inner = f'this{access} = 1;'
    else:
        return None
    prefix = 'static ' if member.recv == 'class' else ''
    return '%sreach0() { %s }' % (prefix, inner), inner


def compatible(point):
    """Reject grammar points that cannot produce a running program."""
    c, m, k, r, b = (point.container, point.member, point.key,
                     point.route, point.body)
    if m.where not in (c.holds, 'both'):
        return False
    if c.holds == 'class' and m.where == 'obj':
        return False
    if c.holds == 'obj' and m.where == 'class':
        return False
    if k[4] == 'private' and c.holds != 'class':
        return False
    if k[4] == 'private' and c.proto_only:
        # private methods are installed on instances, never on the
        # prototype object a proto receiver names
        return False
    if k[4] == 'private' and m.label in ('constructor',):
        return False
    if m.label == 'constructor' and k[0] != 'ident':
        return False
    if m.trigger == 'construct':
        if not c.constructs:
            return False
    elif c.constructs:
        return False
    if (c.recv_kind == 'class' and m.recv != 'class'
            and m.trigger != 'construct'):
        return False
    if c.recv_kind != 'class' and m.recv == 'class':
        return False
    if c.proto_only and not m.proto:
        return False
    if c.descriptor:
        # A descriptor names its accessor `get`/`set`, so the key
        # spelling is fixed and only accessors belong there.
        if m.label not in ('getter', 'setter') or k[0] != 'ident':
            return False
    if m.trigger != r.trigger:
        return False
    if r.tmpl is None:
        # sibling-this routes only exist where the container is a class.
        if c.holds != 'class' or c.constructs:
            return False
    if b.needs_arg and (r.tmpl is None or not any(
            tag in r.tmpl for tag in ('{arg}', '{argc}', '{argp}'))):
        return False
    if b.needs_arg and not m.params:
        return False
    if b.params and not m.params:
        return False
    if b.params and r.implicit_args:
        return False
    if 'dest' in r.needs and k[3] is None:
        return False
    if 'ident-prop' in r.needs and not k[3]:
        return False
    if 'split-prop' in r.needs and (not k[3] or len(k[3]) < 2):
        return False
    if k[0] == 'private' and r.tmpl is not None:
        return False
    if k[0] == 'private' and r.tmpl is None:
        return True
    if r.tmpl is None and k[0] != 'private':
        # a sibling reach is the private key's only route
        return False
    return True


def render(point):
    """Render a grammar point to complete JavaScript source."""
    c, m, k, r, b = (point.container, point.member, point.key,
                     point.route, point.body)
    spelling = 'extCmd' if point.direction == 'promoter' else 'ordinary'
    seed = 'ordinary' if point.direction == 'promoter' else 'extCmd'
    body_stmt = b.stmt.replace(SLOT, spelling)
    params = b.params.replace(SLOT, spelling)
    if c.descriptor:
        member_text = ('get() { %s return 1; }' % body_stmt
                       if m.trigger == 'read'
                       else 'set(v) { %s }' % body_stmt)
    else:
        member_text = m.render(k[1], params, body_stmt)
    sibling = None
    if r.tmpl is None:
        sibling = _sibling_route(m, k, spelling)
        if sibling is None:
            return None
        member_text = member_text + ' ' + sibling[0]
    lines, recv = c.build(member_text)
    if r.tmpl is None:
        prefix = 'K.' if m.recv == 'class' else recv + '.'
        route_lines = [prefix + 'reach0();']
    else:
        arg = spelling if b.needs_arg else ''
        fields = {
            'r': recv, 'a': k[2], 'p': k[3] or '0',
            'pdot': k[3] or '0',
            'p0': (k[3] or '0')[:1], 'p1': (k[3] or '0')[1:],
            'd': k[3] or 'x0', 'arg': arg,
            'argc': (', ' + arg) if arg else '',
            'argp': f'({arg})'}
        route_lines = r.tmpl.format(**fields).split('\n')
    if point.context == 'asi-block' and route_lines:
        if route_lines[-1].endswith(';'):
            route_lines[-1] = route_lines[-1][:-1]
        route_lines = route_lines + ['{ void 0; }']
    pre = [line.replace(SLOT, spelling) for line in b.pre]
    send_line = SEND.rstrip('\n')
    if point.order == 'before':
        full = pre + lines + route_lines + [send_line]
    elif point.order == 'after':
        full = pre + lines + [send_line] + route_lines
    else:
        full = pre + lines + [send_line]
    text = '\n'.join(apply_context(point.context, full)) + '\n'
    return f'let send = {seed};\n' + text


def program(point):
    source = render(point)
    if source is None:
        return None
    return RUNTIME_PREFIX + source + RUNTIME_SUFFIX


# Sampling.

def _default_key(member):
    name = 'hook' if member.trigger in ('read', 'write') else 'go'
    return key_forms(name)[0]


def _key_variants(member):
    name = 'hook' if member.trigger in ('read', 'write') else 'go'
    return key_forms(name)


def _triples():
    """Every container/member/route combination that can run."""
    found = []
    for container in CONTAINERS:
        for member in MEMBERS:
            key = _default_key(member)
            for route in ROUTES:
                point = Point(container, member, key, route, BODIES[0],
                              'top-level', 'before', 'promoter')
                if compatible(point):
                    found.append((container, member, key, route))
    return found


def sample_points(seed, count, anchors=200):
    """Three tiers: an exhaustive shape sweep, one-axis variation over a
    sampled set of anchors, and a random fill of the full product."""
    rng = random.Random(seed)
    points = []
    seen = set()

    def keep(point):
        signature = (point.container.label, point.member.label,
                     point.key[0], point.route.label, point.body.label,
                     point.context, point.order, point.direction)
        if signature in seen or not compatible(point):
            return False
        seen.add(signature)
        points.append(point)
        return True

    triples = _triples()
    for container, member, key, route in triples:
        for order in ORDERS:
            for direction in DIRECTIONS:
                keep(Point(container, member, key, route, BODIES[0],
                           'top-level', order, direction))
    chosen = list(triples)
    rng.shuffle(chosen)
    for container, member, key, route in chosen[:anchors]:
        variants = [(k, BODIES[0], 'top-level')
                    for k in _key_variants(member)[1:]]
        variants += [(key, b, 'top-level') for b in BODIES[1:]]
        variants += [(key, BODIES[0], c) for c in CONTEXTS[1:]]
        for variant_key, body, context in variants:
            for direction in DIRECTIONS:
                keep(Point(container, member, variant_key, route, body,
                           context, 'before', direction))
    keys_go = key_forms('go')
    keys_hook = key_forms('hook')
    attempts = 0
    limit = max(count * 200, 400000)
    while len(points) < count and attempts < limit:
        attempts += 1
        member = rng.choice(MEMBERS)
        container = rng.choice(CONTAINERS)
        pool = (keys_hook if member.trigger in ('read', 'write')
                else keys_go)
        keep(Point(container, member, rng.choice(pool),
                   rng.choice(ROUTES), rng.choice(BODIES),
                   rng.choice(CONTEXTS), rng.choice(ORDERS),
                   rng.choice(DIRECTIONS)))
    return points
