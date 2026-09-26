"""The refusal conditions the frame rule implements, and the control that dies
when each is removed. One row per condition; the suite named for this module
checks the table against the rule's own source and reports a condition with no
row, a row naming another condition's control, and a row no condition backs.
The second field is the condition's identity: the function it lives in and the
operand of the guard selecting it - each operand of a guard several conditions
are split across has its own, a guard taken whole is named by its first
operand, an unguarded refusal by ``return``, a walk root by what it yields.
The third names the control, ``test:<name>`` in ``tests/test_cli_arg_audit.py``
and ``plant:<name>`` in ``FRAME_NAMESPACE_PLANTS``. Every control named here
was watched go red with its own condition removed.

What no row here is worth reading as: ``frame_read``'s attribute arm and the
member test inside it cannot be told apart by any control in the tree - two
separate them from the rest. Six shapes of refusal reach neither this table nor
the control, and each is named in the suite beside the code that misses it: a
refusal delegated to a function the walk's scope does not name, a refusal
written inside one, a refusal raised rather than returned, one raised through a
helper, one inside a loop, one under ``try``/``except`` and one under ``with``.

The row key is the code's own text, so a behaviour-preserving rewrite of a
guard's boolean algebra - pushing a negation through, reordering operands - is
a red asking for a renamed row: the text the key names has changed and the
decision has not. The cost is accepted rather than argued; what a normaliser
would buy and lose is in the pull request.

Its reach, stated so the numbers follow from the rules. Domain one: every row
in this table against every OTHER control the tree offers - the 31 the audit
suite's runner collects and the 21 plant rows, 52 in all - which is 20 x 51 =
1020 cells; a cell is detected when that control does not survive the row's own
condition removal, and 249 of the 1020 are, 24%. Domain two: the 190 unordered
pairs of rows, exchanging the two rows' named controls and nothing else; the
exchange is unnoticed when each row's control survives under the OTHER row's
condition, which is 91 of the 190; the other 99 are caught, in both directions
for 14 of them and in exactly one for 85. Both domains grow with the tree, so
the figures measure it rather than bounding anything. Re-derived by running the
control over every condition against every control; the script is named in the
pull request."""
CONDITIONS_PINNED = (
    ('frame_read|isinstance(node, ast.Attribute)',
     'an attribute read is a selection the rule reads a member from',
     ('test:test_cli_audit_refuses_a_resolved_frame_receiver',)),
    ('frame_read|node.attr in FRAME_SURFACE',
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
    ('_subscript_read|key is not None',
     'a key it can read is a member, a key it cannot is refused',
     ('plant:class body',)),
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
     'a proven getattr whose name is an expression; the first conjunct, so '
     'removing it takes the other three with it',
     ('plant:getattr whose name is an expression',)),
    ('reads_frame_namespace|origin is not UNPROVEN',
     'a name a local scope binds is unproven, and unproven is refused',
     ('test:test_cli_audit_refuses_a_frame_read'
      '_on_a_proven_receiver',)),
    ('reads_frame_namespace|not isinstance(origin, types.FrameType)',
     'a receiver resolved to a live frame is refused on that account',
     ('test:test_cli_audit_refuses_a_resolved_frame_receiver',)),
    ('reads_frame_namespace|return',
     'a frame read is refused at all',
     ('plant:computed key, a bound name',)),
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
     ('test:test_cli_audit_refuses_reflective_namespace_access',)),
    ("_package_roots|('', tree)",
     'the walk starts at the module, not at a callable',
     ('test:test_cli_audit_covers_a_second_package_module',)),
)
