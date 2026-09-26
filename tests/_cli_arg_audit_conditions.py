"""The ledger of refusal conditions the frame rule implements: one row per
condition, the control that dies when it is removed, and where that control
lives. Carried in a module of its own so a reader of the rule meets the table
beside the rule and the resolver's own file carries no ledger of it."""
# Every refusal condition the rule carries, the control that fails when the
# condition is removed, and where that control lives. One row per condition,
# and every row was verified by removing that condition and watching that
# control go red. The test file is 675 of 700 and this file is 700 of 700, so
# the ledger lives here where a reader of the rule meets it.
CONDITIONS_PINNED = (
    ('C1', 'the member set is read off types.FrameType',
     'refuses_every_frame_member_the_interpreter_carries'),
    ('C2', 'a receiver resolved to a live frame is refused on that account',
     'refuses_a_resolved_frame_receiver'),
    ('C3', 'a frame read is refused at all', 'five controls, C1-C11 aside'),
    ('C4', 'the call arm: a callee that is itself a call',
     'the plant row "callee the audit cannot see is a call"'),
    ('C5', 'the call arm: a starred expansion',
     'the plant row "starred expansion hides the argument list"'),
    ('C6', 'the call arm: a proven getattr whose name is an expression',
     'the plant row "getattr whose name is an expression"'),
    ('C7', "resolve_origin sees a literal, which is the call arm's price",
     'accepts_a_real_call_naming_the_namespace_key'),
    ('C8', 'the walk starts at the module, not at a callable',
     'covers_a_second_package_module'),
    ('C9', 'a name a local scope binds is unproven',
     'refuses_a_frame_read_on_a_proven_receiver'),
    ('C10', 'the subscript arm REFUSES a key it cannot read',
     'four plant rows, "computed key, ..."'),
    ('C11', 'the subscript arm EXEMPTS a range or a tuple key',
     'refuses_frame_namespaces_in_the_real_package, 11 slices'),
)
