#!/usr/bin/env python3
"""Focused controls for classic-worker declaration inventory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _worker_declarations import top_level_declarations  # noqa: E402


def _names(source):
    return [name for name, _kind, _start in top_level_declarations(source)]


def test_uninitialized_var_before_block_close_is_global(tmp):
    """ASI at a block close still terminates a worker-global var."""
    del tmp
    assert '_executionContext' in _names("""
if (chrome.runtime) {
  var _executionContext
}
""")


def test_regex_declaration_text_is_not_a_binding(tmp):
    """A regex body is not accepted as a declaration position."""
    del tmp
    assert '_executionContext' not in _names(
        'const harmlessRegex = /var _executionContext;/;')


def test_class_static_block_var_is_not_global(tmp):
    """A var in a class static initialization block stays local."""
    del tmp
    assert '_executionContext' not in _names("""
const harmlessStaticValue = class {
  static {
    var _executionContext;
  }
};
""")


def test_control_named_method_var_is_not_global(tmp):
    """Control-keyword method names still introduce function scopes."""
    del tmp
    for keyword in ('if', 'while', 'for', 'switch', 'with', 'catch'):
        object_source = f"""
const harmlessMethodHolder = {{
  {keyword}() {{ var _executionContext; }},
}};
"""
        class_source = f"""
class HarmlessMethodHolder {{
  {keyword}() {{ var _executionContext; }}
}}
"""
        assert '_executionContext' not in _names(object_source), keyword
        assert '_executionContext' not in _names(class_source), keyword


def test_binding_patterns_and_global_control_flow_vars_are_found(tmp):
    """Every binding pattern and worker-global control-flow var is found."""
    del tmp
    source = """
const {
  property: alias,
  shorthand,
  nested: [first, { deep = fallback }, ...tail],
  [computed]: computedAlias = defaultValue,
  ...rest
} = input, after = 2;
let one = 1, [two, , ...three] = list;
var four;
for (var forBinding of iterable) {
  if (forBinding) break;
}
for (var forInBinding in object) {
  consume(forInBinding);
}
if (condition) {
  var blockBinding = value;
}
if (condition) var unbracedBinding;
while (condition) {
  var whileBinding;
}
if (condition) while (condition) {
  { var deeplyNestedBinding; }
}
do {
  var doBinding;
} while (condition);
{
  var bareBlockBinding;
}
try {
  var tryBinding;
} catch (catchParameter) {
  var catchBinding;
} finally {
  var finallyBinding;
}
switch (value) {
  case 1:
    var switchBinding;
}
"""
    assert _names(source) == [
        'alias', 'shorthand', 'first', 'deep', 'tail',
        'computedAlias', 'rest', 'after', 'one', 'two', 'three',
        'four', 'forBinding', 'forInBinding', 'blockBinding',
        'unbracedBinding', 'whileBinding', 'deeplyNestedBinding',
        'doBinding', 'bareBlockBinding', 'tryBinding', 'catchBinding',
        'finallyBinding', 'switchBinding',
    ]


def test_function_scoped_vars_are_not_worker_globals(tmp):
    """Every supported function shape shields its var declarations."""
    del tmp
    source = """
(function () { var iifeVar; }());
function container() {
  if (condition) { var nestedFunctionVar; }
}
const arrow = () => { var arrowVar; };
class Holder {
  method() { var classMethodVar; }
}
const object = {
  method() { var objectMethodVar; },
};
for (const item of (() => { var iterableVar; return values; })()) {
  consume(item);
}
"""
    assert _names(source) == [
        'container', 'arrow', 'Holder', 'object',
    ]


def test_property_keys_are_not_declarations(tmp):
    """Property names never enter the declaration inventory."""
    del tmp
    source = """
const propertyHolder = {
  propertyKey: 1,
  _executionContext: 2,
};
"""
    assert _names(source) == ['propertyHolder']


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
