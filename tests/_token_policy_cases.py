"""Whole-source grammar cells for the extension token policy."""


KEYWORDS = ('await case delete do else in instanceof new of return throw '
            'typeof void yield').split()


def grammar_cases():
    """Each row names a lexical decision, its state, and a source verdict."""
    rows = []

    def pair(name, state, source, refused=False):
        rows.append((name + '-healthy', state, refused, source))
        rows.append((name + '-read', state, True,
                     source + '; console.log(config.token);'))

    for operand in ('value', '12', '1.2e3', "'value'", '"value"',
                    '`value`', '/x/', '[1]', '(1)', 'value++', 'value--'):
        pair('division-' + operand, 'division',
             f"const n = {operand} / /'/.source.length")
    for word in KEYWORDS:
        for dot in ('.', '?.', './* gap */'):
            pair('property-' + word + dot, 'division',
                 f"const n = ({{{word}: 1}}){dot}{word} / /'/.source.length")
    for lead in ('', 'void 0;', '{', 'const x =', 'const x = (',
                 'const x = [', 'const x = [1,', 'const x = true ?'):
        tail = (' : 0' if lead.endswith('?') else
                ')' if lead.endswith('(') else
                ']' if '[' in lead else '}' if lead == '{' else '')
        pair('regex-' + lead, 'regex', lead + " /'/.source" + tail)
    for operator in ('+', '-', '*', '/', '%', '**', '&&', '||', '??',
                     '&', '|', '^', '==', '!=', '<', '>', '<<', '>>'):
        pair('operator-' + operator, 'regex',
             f"const x = 1 {operator} /'/.source.length")
    for word in ('return', 'throw', 'void', 'typeof', 'delete', 'new',
                 'in', 'instanceof', 'case', 'do', 'else'):
        source = {
            'case': "switch (1) {case /'/.source: break;}",
            'do': "do /'/.test('x'); while (false)",
            'else': "if (false) {} else /'/.test('x')",
            'in': "const x = 'x' in /'/",
            'instanceof': "const x = 1 instanceof /'/",
        }.get(word, word + " /'/.source")
        pair('keyword-' + word, 'regex', source)
    for word in ('await', 'yield', 'of'):
        pair('contextual-' + word, 'unresolved', word + ' / 2', True)
    for head in ('if (true)', 'while (false)', 'for (;;)', 'with ({})'):
        pair('control-' + head, 'regex', head + " /'/.test('x')")
    for head in ('obj.if(1)', 'obj?.while(1)', '(function(){})'):
        pair('call-' + head, 'division', head + " / /'/.source.length")
    pair('brace', 'unresolved', 'const n = {} / 2', True)
    pair('function-brace', 'unresolved', 'const n = function(){} / 2', True)
    for end in ('\n', '\r', '\r\n', '\u2028', '\u2029'):
        pair('line-comment-' + repr(end), 'comment', '// ready' + end)
        pair('block-comment-' + repr(end), 'comment',
             '/* console.log(config.token);' + end + '*/')
        pair('template-line-' + repr(end), 'template',
             '`console.log(config.token);' + end + '`')
        pair('continuation-' + repr(end), 'string', "'ready\\" + end + "'")
        pair('regex-line-' + repr(end), 'unresolved', '/abc' + end, True)
    for text in ("'console.log(config.token)'", '"config.token"',
                 "'it\\'s config.token'", r'"escaped\\\\config.token"',
                 '`config.token`', r'`\${config.token}`',
                 '`outer ${`inner ${1}`} tail`',
                 '/* /\' console.log(config.token); */',
                 r'/[\]/\x27]/', r'/\//', '/config.token/g'):
        pair('literal-' + text, 'literal', text)
    for text in ('/* unfinished', "'unfinished", '"unfinished',
                 '`unfinished', '`unfinished ${1', '/unfinished',
                 '/unfinished' + chr(92), r'const caf\u00e9 = 1'):
        pair('unfinished-' + text, 'unresolved', text, True)
    for tag in ('console.log', '(console).log', '(console.log)',
                'console[method]', 'unknown', '(0, console.log)'):
        for value, refused in (('config.token', True),
                               ('config.token.slice(0, 8)', False),
                               ('config.tabId', False)):
            rows.append(('tag-' + tag + value, 'template arguments', refused,
                         tag + '`${' + value + '}`;'))
    for source in ('(console).log(config.token);',
                   '((console)).log(config.token);',
                   'console.log?.(config.token);',
                   'console.log + config.token;',
                   'console.log = config.token;'):
        rows.append(('invocation-' + source, 'arguments/refused', True,
                     source))
        rows.append(('invocation-safe-' + source, 'arguments', False,
                     source.replace('config.token', 'config.tabId')))
    for key, refused in ((r'\x74oken', True), (r'\u0074oken', True),
                         (r'\u{74}oken', True), (r'\164oken', True),
                         (r'\x74abId', False), (r'\u0074abId', False)):
        rows.append(('key-' + key, 'key decoding', refused,
                     f"console.log(config['{key}']);"))
    return rows


def boundary_cases():
    rows = []
    for end in ('\n', '\r', '\r\n', '\u2028', '\u2029'):
        for lead in ('var value', 'break label', 'continue label',
                     'debugger', 'value', 'import value from "module"'):
            rows.append((True, lead + end + '/x/;'))
            rows.append((True, lead + end + "/'/; console.log(config.token);"))
        rows.extend([
            (False, 'const value = 2;' + end + '/x/;'),
            (False, '// ready' + end),
            (True, '// ready' + end + 'console.log(config.token);'),
            (False, "'é😀';" + end + "console.log(config['tabId']);"),
            (True, "'é😀';" + end + "console.log(config['token']);"),
            (True, 'value /*' + end + '*/ /x/;'),
        ])
    rows.extend([
        (True, "for await (const x of xs) /'/; console.log(config.token);"),
        (True, "of / /'/;\n'closed'; console.log(config.token);"),
        (False, 'console.log`config.token`;'),
        (False, 'const x = `${config.token}`;'),
        (False, 'console.log`raw ${config.tabId} raw`;'),
        (True, 'console.log`raw ${`${config.token}`} raw`;'),
        (False, 'console.log`raw ${`${config.tabId}`} raw`;'),
    ])
    return rows


def lexical_context_cases():
    rows = []
    for source in ("const x = [... /'/.source];",
                   "console.log(... /'/.source);",
                   "class C extends /'/.constructor {}",
                   "export default /'/.source;",
                   "const x = () => /'/.source;",
                   "const x = ! /'/.source;",
                   "const x = ~ /'/.source;",
                   "console.log(/[[]/.source);",
                   "const obj = {default: 1, extends: 1}; "
                   "obj.default / /'/.source.length;",
                   "const obj = {default: 1, extends: 1}; "
                   "obj.extends / /'/.source.length;"):
        rows.append((False, source))
        rows.append((True, source + ' console.log(config.token);'))
    for opening in ('<!--', '-->'):
        rows.append((True, opening + " /*\nconsole.log(config.token);\n// */"))
        rows.append((True, opening + ' ready\n'))
    rows.append((False, 'let n = 2; const x = n-->0;'))
    return rows
