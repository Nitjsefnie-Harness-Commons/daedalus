"""The refusal conditions the frame rule implements, and the control that dies
when each is removed. One row per condition;
``tests/test_cli_arg_audit_conditions.py`` checks this table against the
rule's own source and reports a condition with no row, a row naming another
condition's control, and a row no condition backs. The second field is the
condition's identity - the function it lives in, and the first operand of the
guard that selects it - and the third names the controls, ``test:<name>`` for
one in ``tests/test_cli_arg_audit.py`` and ``plant:<name>`` for a row of
``FRAME_NAMESPACE_PLANTS``. Every control named here was watched go red with
its own condition removed."""
CONDITIONS_PINNED = (
    ('frame_read|isinstance(node, ast.Attribute)',
     'the member set is read off types.FrameType',
     ('test:test_cli_audit_refuses_every_frame_member'
      '_the_interpreter_carries',)),
    ('frame_read|isinstance(node, ast.Subscript)',
     'a subscript is a selection, and the subscript arm reads it',
     ('plant:mapping key, no member selected',)),
    ('frame_read|isinstance(node, ast.Call)',
     'a call is a selection, and the call arm reads it',
     ('test:test_cli_audit_reports_namespace_escapes',)),
    ('_subscript_read|isinstance(node.slice, ast.Constant)',
     'a constant key the audit can read is decided there',
     ('test:test_cli_audit_respects_comprehension_shadowing',)),
    ('_subscript_read|isinstance(node.slice, _RANGE_OR_TUPLE_KEYS)',
     'a range or a tuple key is a position, not a member name',
     ('test:test_cli_audit_refuses_frame_namespaces'
      '_in_the_real_package',)),
    ('_subscript_read|return',
     'a key the audit cannot read names a member all the same',
     ('plant:computed key, concatenation',)),
    ('_call_read|visible',
     'a call naming a constant member is a selection',
     ('plant:callee the audit cannot prove',)),
    ('_call_read|_has_starred(node)',
     'a starred expansion hides the whole argument list',
     ('plant:starred expansion hides the argument list',)),
    ('_call_read|isinstance(node.func, ast.Call)',
     'a callee that is itself a call',
     ('plant:callee the audit cannot see is a call',)),
    ('_call_read|len(node.args) in (2, 3)',
     'a proven getattr whose name is an expression',
     ('plant:getattr whose name is an expression',)),
    ('reads_frame_namespace|origin is not UNPROVEN',
     'a receiver the audit resolved is refused only when it cannot '
     'account for it, which is the frame half and the unproven half '
     'together',
     ('test:test_cli_audit_refuses_a_frame_read_on_a_proven_receiver',
      'test:test_cli_audit_refuses_a_resolved_frame_receiver')),
    ('reads_frame_namespace|return',
     'a frame read is refused at all',
     ('test:test_cli_audit_refuses_a_resolved_frame_receiver',)),
    ('resolve_origin|isinstance(node, ast.Constant)',
     "resolve_origin sees a literal, which is the call arm's price",
     ('test:test_cli_audit_accepts_a_real_call'
      '_naming_the_namespace_key',)),
    ('resolve_origin|isinstance(node, ast.Name)',
     'a name resolves through the scope it is read in',
     ('test:test_cli_audit_requires_builtin_identity',)),
    ('resolve_origin|isinstance(node, ast.Attribute)',
     'an attribute of a value the audit resolved is visible',
     ('test:test_cli_audit_resolver_only_resolves_exact_module_vars',)),
    ('resolve_origin|return',
     'every other expression is unproven, and unproven is the refusal',
     ('plant:class body',)),
    ("_package_roots|('', tree)",
     'the walk starts at the module, not at a callable',
     ('test:test_cli_audit_covers_a_second_package_module',)),
)
