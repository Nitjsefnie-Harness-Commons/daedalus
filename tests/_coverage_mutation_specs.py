"""Every mutation the coverage-guard sweeps are held to.

Not a suite itself — run_tests.py only loads `test_*.py`.

A row is (name, target file key, replacements, the child program that
must start failing). tests/_mutation_sweep.py runs them; the tables were
split out of the four suites that grew them, each of which another suite
imported whole to reach the rows. The data a table reads to build its own
rows moved with it, so the suite and this module read one copy of it. The
two tables that once shared a name in different suites keep distinct ones
here: `_SCOPE_*` is the scope-binding suite's, `_DEST_*` the root
provenance suite's.
"""
import ast


_MATCH_INVOKE = (
    "__import__('test_bash_resolver_scan')."
    "test_match_captures_shadow_outer_bindings('.')")
_WALRUS_INVOKE = (
    "__import__('test_bash_resolver_scan')."
    'test_comprehension_walrus_binds_in_containing_scope(None)')
_BASH_MUTATION_SPECS = (
    ('MatchAs scope', 'scopes', ((
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, "
        "ast.MatchStar)):\n",
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchStar)):\n"),),
     _MATCH_INVOKE),
    ('MatchStar scope', 'scopes', ((
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, "
        "ast.MatchStar)):\n",
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs)):\n"),),
     _MATCH_INVOKE),
    ('MatchMapping scope', 'scopes', ((
        "    if isinstance(node, ast.MatchMapping):\n"
        "        return {node.rest} if node.rest else set()\n", ""),),
     _MATCH_INVOKE),
    ('Bash walrus binding', 'bash', ((
        "    elif isinstance(node, ast.NamedExpr):\n"
        "        targets, value = [node.target], node.value\n", ""),),
     _WALRUS_INVOKE),
    ('Bash walrus binding scope', 'bash', ((
        "            if isinstance(node, ast.NamedExpr):\n"
        "                scope = _containing_binding_scope("
        "scope, self.parents)\n", ""),), _WALRUS_INVOKE),
)


_CACHE_INVOKE = (
    'import tempfile; from pathlib import Path; '
    'import test_static_guard_regressions as regression; '
    'scratch = tempfile.TemporaryDirectory(); ')
_CACHE_MUTATIONS = (
    ('mutation children start cache-free', 'runner',
     (("            clear_bytecode(root)\n", ''),),
     _CACHE_INVOKE + 'regression.test_mutation_gate_clears_caches_before_'
     'every_child(Path(scratch.name)); scratch.cleanup()'),
    ('nested mutation children inherit bytecode prevention', 'runner',
     (("                    **os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}),\n",
       "                    **os.environ, "
       "'PYTHONDONTWRITEBYTECODE': ''}),\n"),),
     _CACHE_INVOKE + 'regression.test_mutation_gate_rejects_a_cached_'
     'equivalent_edit(Path(scratch.name)); scratch.cleanup()'),
    ('bytecode cleanup descends through the copied tree', 'owned',
     (("    for cache in root.rglob('__pycache__'):\n",
       "    for cache in root.glob('__pycache__'):\n"),),
     _CACHE_INVOKE + 'regression.test_mutation_gate_clears_caches_before_'
     'every_child(Path(scratch.name)); scratch.cleanup()'),
    ('bytecode cleanup refuses checkout destinations', 'owned',
     (("        raise ValueError(\n"
       "            f'clear_bytecode root lies inside the checkout: {root}')"
       "\n", "        pass\n"),),
     _CACHE_INVOKE + 'regression.test_bytecode_cleanup_refuses_checkout_'
     'paths(Path(scratch.name)); scratch.cleanup()'),
    ('bytecode cleanup is a control-owned writer', 'calls',
     (("                   ('_owned_writes', 'clear_bytecode'): ('root', 0)}",
       "                   }"),),
     _CACHE_INVOKE + 'regression.test_bytecode_cleanup_refuses_checkout_'
     'paths(Path(scratch.name)); scratch.cleanup()'),
    ('mutation environment may defer its helper import', 'calls',
     (("    ('_util', 'child_coverage'),\n", ''),),
     _CACHE_INVOKE + 'regression.test_bytecode_cleanup_refuses_checkout_'
     'paths(Path(scratch.name)); scratch.cleanup()'),
    ('mutation children skip site initialization', 'runner',
     (("[sys.executable, '-B', '-S', '-c', program]",
       "[sys.executable, '-B', '-c', program]"),),
     _CACHE_INVOKE + 'regression.test_mutation_gate_rejects_a_cached_'
     'equivalent_edit(Path(scratch.name)); scratch.cleanup()'),
    ('mutation children verify site isolation', 'runner',
     (('                "assert sys.flags.no_site, '
       "'site initialization enabled'\\n\"\n", ''),),
     _CACHE_INVOKE + 'regression.test_mutation_gate_refuses_site_'
     'initialization(Path(scratch.name)); scratch.cleanup()'),
)

_SCOPE_INVOKE = (
    'import test_coverage_scope_bindings as binding_suite; '
    'binding_suite.test_import_bindings_do_not_rebind_root_'
    'spellings(None)')
_SCOPE_MUTATIONS = (
    ('a star import refuses every root spelling', 'scopes',
     (("    if _ALL_NAMES in shadowed_names:\n        return False\n",
       ""),), _SCOPE_INVOKE),
    ('a rebound proof name is unprovable', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = set()\n"),), _SCOPE_INVOKE),
    ('Path is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'str', 'ROOT', _ALL_NAMES})\n"),),
     _SCOPE_INVOKE),
    ('str is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'Path', 'ROOT', _ALL_NAMES})\n"),),
     _SCOPE_INVOKE),
    ('a canonical import is exact', 'scopes',
     (("            and (node.module, alias.name) == "
       "_CANONICAL_MEMBERS.get(bound))\n",
       "            and bound in _CANONICAL_MEMBERS)\n"),),
     _SCOPE_INVOKE),
    ('a relative import is not canonical', 'scopes',
     (("    return (not node.level\n", "    return (True\n"),),
     _SCOPE_INVOKE),
    ('scope shadows carry the unprovable names', 'scopes',
     (("    shadows[tree].update(unprovable)\n", ""),),
     _SCOPE_INVOKE),
    ('module shadows carry the unprovable names', 'scopes',
     (("    names.update(unprovable)\n", ""),),
     _SCOPE_INVOKE),
    ('the _util.ROOT arm needs an owner', 'scopes',
     (("        return '_util' in owners\n", "        return True\n"),),
     _SCOPE_INVOKE),
    ('an unbound ROOT is unprovable', 'scopes',
     (("    if not facts.root_assignments and not root_imported:\n"
       "        names.add('ROOT()')\n", ""),), _SCOPE_INVOKE),
    ('owner imports never prove constructors or builtins', 'scopes',
     (("    if isinstance(node, ast.Import):\n        return False\n",
       "    if isinstance(node, ast.Import):\n"
       "        return alias.name in _ROOT_MODULES\n"),),
     _SCOPE_INVOKE),
    ('proof shadows distinguish calls from owner attributes', 'scopes',
     (("                              if bound in {'Path', 'str', 'ROOT'} "
       "else bound)"
       "\n", "                              if False else bound)\n"),),
     _SCOPE_INVOKE),
    ('str calls consult their import proof shadow', 'scopes',
     (("        return ('str()' not in shadowed_names\n"
       "                and _is_root_spelling(node.args[0], "
       "shadowed_names, owners))\n",
       "        return _is_root_spelling(node.args[0], "
       "shadowed_names, owners)\n"),),
     _SCOPE_INVOKE),
    ('Path assignments consult their import proof shadow', 'scopes',
     (("    return (not {'Path', 'Path()'} & shadowed_names\n",
       "    return (not {'Path'} & shadowed_names\n"),),
     _SCOPE_INVOKE),
    ('a constructor import retires an owner', 'scopes',
     (("                        and alias.name in _ROOT_MODULES):\n",
       "                        and alias.name in _ROOT_MODULES) "
       "and not _canonical_import(node, alias, bound):\n"),),
     _SCOPE_INVOKE),
    ('a literal ROOT import establishes provenance', 'scopes',
     (("                if canonical and bound == 'ROOT':\n"
       "                    root_imported = True\n", ""),),
     _SCOPE_INVOKE),
    ('ROOT is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'Path', 'str', _ALL_NAMES})\n"),),
     _SCOPE_INVOKE),
    ('ROOT provenance requires the literal member', 'scopes',
     (("        return (alias.name == 'ROOT'\n"
       "                and alias.asname in (None, alias.name))\n",
       "        return True\n"),), _SCOPE_INVOKE),
    ('ROOT provenance allows a same-name alias', 'scopes',
     (("        return (alias.name == 'ROOT'\n"
       "                and alias.asname in (None, alias.name))\n",
       "        return alias.name == 'ROOT' and alias.asname is None\n"),),
     _SCOPE_INVOKE),
    ('local imports do not taint module proofs', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = set().union(*imports.values())\n"),),
     _SCOPE_INVOKE),
    ('chdir sees local import shadows', 'guard',
     (("                                       scopes[node], "
       "self.scope_shadows,\n",
       "                                       tree, self.scope_shadows,\n"
       ),), _SCOPE_INVOKE),
    ('imports shadow their containing scope', 'scopes',
     (("        shadows[scope].update(imports.get(node, ()))\n",
       "        shadows[tree].update(imports.get(node, ()))\n"),),
     _SCOPE_INVOKE),
    ('local imports still shadow local launches', 'scopes',
     (("        shadows[scope].update(imports.get(node, ()))\n",
       ""),), _SCOPE_INVOKE),
    ('an assignment cannot erase a ROOT import shadow', 'scopes',
     (("            and not _other_root_bindings(facts, root_values)\n",
       ""),), 'import test_coverage_root_provenance as root_suite; '
     'root_suite._assert_root_binding_site(17)'),
)

_DECLARED_FORMS = (
    ('ROOT', 'from _repo import ROOT', 'ROOT = other', 'ROOT'),
    ('str', 'str = str', 'from helpers import str', 'str(ROOT)'),
    ('Path', 'from pathlib import Path', 'from helpers import Path', 'ROOT'),
)
_DERIVATION = 'ROOT = Path(__file__).resolve().parents[1]\n'

_ROOT_BINDING_FORMS = (
    ('for', 'for ROOT in items:\n    pass\n'),
    ('walrus', '(ROOT := other)\n'),
    ('with', 'with manager as ROOT:\n    pass\n'),
    ('except', 'try:\n    pass\nexcept Exception as ROOT:\n    pass\n'),
    ('match-as', 'match value:\n    case ROOT:\n        pass\n'),
    ('match-star', 'match value:\n    case [*ROOT]:\n        pass\n'),
    ('match-rest', 'match value:\n    case {**ROOT}:\n        pass\n'),
    ('comprehension', '[ROOT for ROOT in items]\n'),
    ('del', 'del ROOT\n'),
    ('augmented', 'ROOT += other\n'),
    ('function', 'def ROOT():\n    pass\n'),
    ('async-function', 'async def ROOT():\n    pass\n'),
    ('class', 'class ROOT:\n    pass\n'),
    ('parameter', 'def f(ROOT):\n    pass\n'),
    ('vararg', 'def f(*ROOT):\n    pass\n'),
    ('kwarg', 'def f(**ROOT):\n    pass\n'),
    ('import', 'import helpers as ROOT\n'),
    ('from-alias', 'from helpers import other as ROOT\n'),
    ('same-name-alias', 'from helpers import ROOT as ROOT\n'),
    ('mixed-import', 'from helpers import ROOT, other as ROOT\n'),
    ('star-import', 'from helpers import *\n'),
    ('uninitialized-annotation', 'ROOT: object\n'),
    ('unaccepted-assignment', 'ROOT = other\n'),
)

_PERMITTED_BINDINGS = (
    ('owner', 'ROOT', 'local_store', 'def unused():\n    ROOT = other\n'),
    ('constructor', 'ROOT', 'local_store',
     'def unused():\n    ROOT = other\n'),
) + tuple(('owner', 'Path', name, binding) for name, binding in (
    ('parameter', 'def unused(Path):\n    pass\n'),
    ('vararg', 'def unused(*Path):\n    pass\n'),
    ('kwarg', 'def unused(**Path):\n    pass\n'),
    ('local_store', 'def unused():\n    Path = other\n'),
    ('comprehension', '[Path for Path in items]\n'),
    ('module_store', 'Path = other\n'),
    ('module_annotation', 'Path: object\n'),
    ('global_store', 'def unused():\n    global Path\n    Path = other\n'),
))
_DERIVATIONS = {
    'owner': 'import _util\nROOT = _util.ROOT\n',
    'constructor': 'from pathlib import Path\n' + _DERIVATION,
}

_DEST_INVOKE = 'import test_coverage_root_provenance as root_suite; '
_ROUTING = (
    "    return _routed_bindings(shadows, destinations)\n",
    "    return shadows\n")
_DEST_MUTATIONS = (
    ('global shadows reach explicit cwd', 'scopes', (_ROUTING,),
     _DEST_INVOKE + 'root_suite.test_declared_bindings_reach_'
     'explicit_cwd(None)'),
    ('global shadows reach chdir', 'scopes', (_ROUTING,),
     _DEST_INVOKE + 'root_suite.test_declared_bindings_reach_chdir(None); '
     "root_suite._assert_declared_destination('global', 1)"),
    ('global import shadows reach root assignments', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = imports[tree]\n"),),
     _DEST_INVOKE + 'root_suite.test_declared_bindings_reach_'
     'explicit_cwd(None)'),
    ('global destinations are shared', 'scopes',
     (("    destinations = {scope: dict.fromkeys(names, module)\n",
       "    destinations = {scope: {}\n"),),
     _DEST_INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None)'),
    ('nonlocal destinations are shared', 'scopes',
     (("            destinations[scope][name] = module "
       "if target is None else target\n",
       "            destinations[scope][name] = scope\n"),),
     _DEST_INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None)'),
) + tuple(
    (f'{declaration} {name} routes its proof role', 'scopes',
     (("            target = destinations[scope].get("
       "name.removesuffix('()'), scope)\n",
       "            target = scope\n" if name == 'ROOT' else
       "            target = destinations[scope].get(name, scope)\n"),),
     _DEST_INVOKE + f'root_suite._assert_declared_destination('
     f'{declaration!r}, {form})')
    for declaration in ('global', 'nonlocal')
    for form, (name, _, _, _) in enumerate(_DECLARED_FORMS))


_NAME_BINDING = (
    "    if (isinstance(node, ast.Name)\n"
    "            and isinstance(node.ctx, (ast.Store, ast.Del))):\n"
    "        return {node.id}\n")
_DEFINITION_BINDING = (
    "    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef,\n                         *_TYPE_PARAMETERS)):\n")
_PATTERN_BINDING = (
    "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, "
    "ast.MatchStar)):\n")
_SITE_MUTANTS = {
    'name': (_NAME_BINDING, ''),
    'scope': ("                if self.destinations[scope].get('ROOT', scope) "
              "is self.tree\n", "                if True\n"),
    'annotation': ("                and node not in annotations}\n",
                   "                }\n"),
    'del': (_NAME_BINDING, _NAME_BINDING.replace(
        '(ast.Store, ast.Del)', 'ast.Store')),
    'parameter': ("    if isinstance(node, ast.arg):\n"
                  "        return {node.arg}\n", ''),
    'import': ("    if isinstance(node, (ast.Import, ast.ImportFrom)):\n"
               "        return {_import_bound_name(node, alias) "
               "for alias in node.names}\n", ''),
    'same-name-alias': (
        "        return (alias.name == 'ROOT'\n"
        "                and alias.asname in (None, alias.name))\n",
        "        return alias.name == 'ROOT' and alias.asname is None\n"),
    'star-import': ("        if not _bound_names(node) "
                    "& {'ROOT', _ALL_NAMES}:\n",
                    "        if 'ROOT' not in _bound_names(node):\n"),
    'match-rest': ("    if isinstance(node, ast.MatchMapping):\n"
                   "        return {node.rest} if node.rest else set()\n", ''),
    'unaccepted-assignment': (
        "            and all(_is_repository_root_binding(value, owners, "
        "names)\n"
        "                    for value in root_values.values())):\n",
        "            ):\n"),
}
_SITE_MUTANTS.update(
    (name, (needle, needle.replace(kind + ', ', '').replace(', ' + kind, '')))
    for name, needle, kind in (
        ('function', _DEFINITION_BINDING, 'ast.FunctionDef'),
        ('async-function', _DEFINITION_BINDING, 'ast.AsyncFunctionDef'),
        ('class', _DEFINITION_BINDING, 'ast.ClassDef'),
        ('except', _PATTERN_BINDING, 'ast.ExceptHandler'),
        ('match-as', _PATTERN_BINDING, 'ast.MatchAs'),
        ('match-star', _PATTERN_BINDING, 'ast.MatchStar')))
_SITE_KINDS = {
    'comprehension': 'scope', 'parameter': 'scope',
    'vararg': 'scope', 'kwarg': 'scope',
    'uninitialized-annotation': 'annotation',
    'from-alias': 'import', 'same-name-alias': 'same-name-alias',
    'mixed-import': 'import',
}
_DEST_MUTATIONS += tuple(
    (f'ROOT census includes {name}', 'scopes',
     (_SITE_MUTANTS[_SITE_KINDS.get(
         name, name if name in _SITE_MUTANTS else 'name')],),
     _DEST_INVOKE + f'root_suite._assert_root_binding_site({form})')
    for form, (name, _) in enumerate(_ROOT_BINDING_FORMS)) + (
        ('ROOT census excludes accepted assignment targets', 'scopes',
         (("        if node in assignments:\n            continue\n", ''),),
         _DEST_INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census allows literal imports', 'scopes',
         (("                or _canonical_import(node, alias, 'ROOT')\n",
           "                or False\n"),),
         _DEST_INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census ignores nonbinding nodes', 'scopes',
         (("        if not _bound_names(node) & {'ROOT', _ALL_NAMES}:\n"
           "            continue\n", ''),),
         _DEST_INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census gates the assignment exemption', 'scopes',
         (("            and not _other_root_bindings(facts, root_values)\n",
           ''),),
         _DEST_INVOKE + 'root_suite.test_every_root_binding_site_'
         'must_be_accepted(None)'),
)


_DEST_MUTATIONS += ((
    ('type parameters enter the shared shadow census', 'scopes',
     (("                         *_TYPE_PARAMETERS)):\n",
       "                         )):\n"),),
     _DEST_INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('root proofs use annotation scopes', 'scopes',
     (("def _evaluation_scopes(tree, type_scopes=True):\n",
       "def _evaluation_scopes(tree, type_scopes=False):\n"),),
     _DEST_INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('parameters enter their binding scope', 'scopes',
     (("            scoped.append((argument, binding_scope))\n", ''),),
     _DEST_INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None); '
     'import test_coverage_name_bindings as names; '
     'names.test_every_grammar_binding_removes_only_its_builtin_'
     'exemption(None)'),
) if hasattr(ast, 'TypeVar') else ())

_DEST_MUTATIONS += ((
    ('scoped shadows read the binding census', 'scopes',
     (("            shadows[scope].update(_bound_names(node))\n",
       "            pass\n"),),
     _DEST_INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('aggregate shadows read the binding census', 'scopes',
     (("    names = set().union(*(_bound_names(node) "
       "for node in memo_nodes(tree)\n"
       "                          if not isinstance(node, (ast.Import,\n"
       "                                                   ast.ImportFrom))))"
       "\n", "    names = set()\n"),),
     _DEST_INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
) if hasattr(ast, 'TypeVar') else ())

_DEST_MUTATIONS += (
    ('ROOT assignments use their destination scope', 'scopes',
     (("                if target in self.root_scope_nodes}\n",
       "                }\n"),),
     _DEST_INVOKE + 'root_suite.test_local_root_bindings_leave_the_module_'
     'root_alone(None)'),
    ('ROOT census resolves global destinations', 'scopes',
     (("                if self.destinations[scope].get('ROOT', scope) "
       "is self.tree\n", "                if scope is self.tree\n"),),
     _DEST_INVOKE + 'root_suite.test_local_root_bindings_leave_the_module_'
     'root_alone(None)'),
)

_DEST_MUTATIONS += (
    ('ROOT imports preserve the owner role', 'scopes',
     (("                              if bound in {'Path', 'str', 'ROOT'} "
       "else bound)\n",
       "                              if bound in {'Path', 'str'} "
       "else bound)\n"),),
     _DEST_INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
    ('absent bare ROOT leaves the owner role alone', 'scopes',
     (("        names.add('ROOT()')\n", "        names.add('ROOT')\n"),),
     _DEST_INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
    ('bare ROOT consults its proof marker', 'scopes',
     (("        return 'ROOT()' not in shadowed_names\n",
       "        return True\n"),),
     _DEST_INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
)


_DEST_MUTATIONS += (
    ('owner derivations ignore Path shadows', 'scopes',
     (("            and '_util' not in names\n",
       "            and not {'Path', 'Path()', '_util'} & names\n"),),
     'import test_coverage_scope_bindings as scope; '
     'scope.test_owner_derivation_does_not_depend_on_path(None)'),
)


_DEST_MUTATIONS += (
    ('chdir keeps aggregate shadows beside scoped imports', 'guard',
     (("                                   node.args[0], self.shadowed_names\n"
       "                                   | _visible_scope_shadows(\n",
       "                                   node.args[0], "
       "_visible_scope_shadows(\n"),),
     'import test_coverage_scope_bindings as scope; '
     'scope.test_chdir_keeps_aggregate_binding_refusals(None)'),
)


_DEST_MUTATIONS += tuple(
    (f'permitted {name} transitions through {consumer}', 'scopes',
     (("                if target in self.root_scope_nodes}\n",
       "                }\n") if name == 'ROOT' else
      ("            and '_util' not in names\n",
       "            and not {'Path', 'Path()', '_util'} & names\n"),),
     _DEST_INVOKE + f'root_suite._assert_permitted_transitions('
     f'{name!r}, {consumer!r})')
    for name in ('ROOT', 'Path') for consumer in ('cwd', 'chdir'))
_DEST_MUTATIONS += tuple(
    (f'permitted transitions retain refusing twins through {consumer}',
     'scopes', (("    if any(isinstance(part, ast.Name) "
                 "and part.id in shadowed_names\n"
                 "           for part in ast.walk(node)):\n"
                 "        return False\n", ''),),
     _DEST_INVOKE + f'root_suite._assert_permitted_transitions('
     f"'ROOT', {consumer!r})")
    for consumer in ('cwd', 'chdir'))

_DECORATOR_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_decorated_definition_refuses_a_hidden_launcher(None)')
# One needle, one row: the issue-620 argument table, the launching table,
# the opaque-callee table and the query table all reach the argument arm,
# so splitting them across rows would pin one change twice and read as
# more changes than there are.
_ARGUMENT_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_cwd_less_call_argument_refuses_a_hidden_launcher('
    'None); form_suite.test_a_launching_callee_refuses_a_hidden_'
    'launcher(None); form_suite.test_an_opaque_callee_refuses_a_hidden_'
    'launcher(None); form_suite.test_a_query_shape_stays_refused(None)')
# The enumeration this branch removed, put back where it was. The
# opaque-callee rows are what catch it: their callee names carry no
# information, so no list can be tuned to satisfy them.
_ENUMERATION_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_an_opaque_callee_refuses_a_hidden_launcher(None); '
    'form_suite.test_a_query_shape_stays_refused(None)')
_LAMBDA_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_lambda_default_refuses_a_hidden_launcher(None)')
_CLEAN_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_launcher_free_headers_and_arguments_stay_clean(None)')

# The decorator half of the header set, removed on its own. Narrowing it to
# FunctionDef alone must turn the async and class rows red, which is what
# says the two forms are reached by this arm and not by the argument arm.
_DECORATORS = (
    "    if isinstance(node, _DECORATED_FORMS):\n", "    if False:\n")
_DECORATED_FORMS = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef,)\n")
# The two halves of that set removed on their own, so the async row and the
# class row are each shown to be reached by this arm rather than by the
# argument arm that sat behind them.
_DECORATED_NO_ASYNC = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef, ast.ClassDef)\n")
_DECORATED_NO_CLASS = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef)\n")
# A lambda default binds its parameter as surely as a def's, so the header
# set that routes it must still carry Lambda.
_HEADER_FORMS = (
    "_HEADER_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,\n"
    "                 ast.Lambda)\n",
    "_HEADER_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)\n")
_ARGUMENTS = (
    "def _call_argument_parts(value):\n"
    '    """Every value a call\'s arguments carry, starred forms '
    'included."""\n'
    "    arguments = [*value.args,\n"
    "                 *(keyword.value for keyword in value.keywords)]\n"
    "    for argument in arguments:\n"
    "        yield from _carried_parts(argument)\n",
    "def _call_argument_parts(value):\n    yield from ()\n")
# The enumeration this branch removed, reinstated in front of the arm. The
# list holds the callee and keyword names the case tables name, so it is
# the strongest form of the mistake: a gate tuned to satisfy every row.
# A gate like this one satisfies the tables, which is why the tables are
# not the whole defence — the runtime probes reject it behaviourally,
# because a gate naming every name here must also name to_thread,
# callback and finalize, and the next name written is not on its list.
_ENUMERATION = (
    "def _unfollowable_launcher_bindings(tree, facts):\n",
    "_INVOKING = frozenset({'call', 'partial', 'map', 'Thread',\n"
    "                       'submit', 'register', 'side_effect'})\n"
    "\n"
    "\n"
    "def _invoking_callee(node):\n"
    "    function = node.func\n"
    "    if isinstance(function, ast.Attribute):\n"
    "        name = function.attr\n"
    "    elif isinstance(function, ast.Name):\n"
    "        name = function.id\n"
    "    else:\n"
    "        name = ''\n"
    "    return name in _INVOKING or any(\n"
    "        keyword.arg in _INVOKING for keyword in node.keywords)\n"
    "\n"
    "\n"
    "def _unfollowable_launcher_bindings(tree, facts):\n")
# A bare module name is a launcher only where a launch method is read off
# it. Judging it in an argument position too must turn the module handed
# to patch.object rows red.
# The arm this row edits, anchored as a whole block ending at a line
# break. A needle that stops short of the newline is a prefix of the
# module-name row's, and a prefix's uniqueness is inherited from the
# longer string rather than from the text this mutation changes.
_ENUMERATION_ARM = (
    "        if (isinstance(node, ast.Call)\n"
    "                and not _has_cwd_control(node)\n"
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or _carries_launch_value(_call_argument_parts("
    "node),\n"
    "                                              facts))):\n",
    "        if (isinstance(node, ast.Call)\n"
    "                and not _has_cwd_control(node)\n"
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or (_invoking_callee(node)\n"
    "                         and _carries_launch_value(\n"
    "                             _call_argument_parts(node), facts)))):\n")
_MODULE_IN_ARGUMENT = (
    "                     or _carries_launch_value(_call_argument_parts("
    "node),\n"
    "                                              facts))):\n",
    "                     or _carries_launcher(_call_argument_parts(node),\n"
    "                                         facts))):\n")
_STARRED = (
    "    elif isinstance(value, ast.Starred):\n"
    "        yield from _carried_parts(value.value)\n",
    "    elif isinstance(value, ast.Starred):\n        pass\n")

_UNFOLLOWABLE_MUTATIONS = (
    ('decorator list', 'bindings', (_DECORATORS,), _DECORATOR_INVOKE),
    ('decorated forms', 'bindings', (_DECORATED_FORMS,), _DECORATOR_INVOKE),
    ('decorated forms without async', 'bindings', (_DECORATED_NO_ASYNC,),
     _DECORATOR_INVOKE),
    ('decorated forms without class', 'bindings', (_DECORATED_NO_CLASS,),
     _DECORATOR_INVOKE),
    ('lambda header form', 'bindings', (_HEADER_FORMS,), _LAMBDA_INVOKE),
    ('call arguments', 'bindings', (_ARGUMENTS,), _ARGUMENT_INVOKE),
    ('callee-name enumeration', 'bindings',
     (_ENUMERATION, _ENUMERATION_ARM), _ENUMERATION_INVOKE),
    ('module name in argument', 'bindings', (_MODULE_IN_ARGUMENT,),
     _CLEAN_INVOKE),
    ('starred call argument', 'bindings', (_STARRED,), _ARGUMENT_INVOKE),
)
