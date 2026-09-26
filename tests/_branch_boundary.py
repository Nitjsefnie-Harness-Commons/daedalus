"""Whether an allowance row excuses a declaration the branch wrote.

The property every row in both tables rests on, and the one comparison
that decides it. A row is a claim about ONE DECLARATION, so the
comparison is over declarations and never over names and never over a
list of the files the branch touched. Three shapes of drift come apart
under it, and a name-keyed or file-keyed rule merges them:

  * a site that predates the branch, in a file the branch edits for an
    unrelated reason — excused, because the base carries the identical
    declaration;
  * a site the branch MOVED — excused, because it is the same
    declaration under a different line;
  * a second, NEW declaration of an already-tabled name — refused,
    because no row was ever written for it.

It reaches that by reading the merge base's OWN definitions, which is a
content comparison. A path list cannot tell a site the branch wrote from
one it did not, and it cost this branch five renames in test files it
was not asked to touch before the form changed. `tests/` carries 71
files with a Python row and 27 with a JavaScript one, and this branch
edits fifteen of the twenty-seven, so a path-scoped boundary would have
forbidden recording rows for sites that predate it.

A checkout that cannot see a base returns None and the caller MUST
refuse on it. None is "this checkout cannot answer"; an empty set is
"the base resolves and names nothing", and the two are different facts
about different things. Before this refusal existed, both boundary tests
took the None arm with a bare `return` — and the only CI job that runs
the suites checks out at depth 1, so the control was green there and had
evaluated nothing.
"""
import ast
import hashlib
import re
import subprocess

import _js_functions
import _util
from _helper_binds import definition_nodes

BRANCH_BASES = ('origin/main', 'main')


def _parsed(path, source):
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise AssertionError(
            f'tests module does not parse: {path}: {exc}') from exc


def _merge_base(root, bases=BRANCH_BASES):
    """The merge base with the first of `bases` that resolves, or None.

    A developer checkout resolves one of them and a CI checkout that has
    fetched the base resolves the same one, so both read the same tree.
    None means THIS CHECKOUT CANNOT SEE A BASE, which is a different
    fact from "the base resolves and names nothing"; the caller has to
    treat the first as a refusal and may treat the second as an answer.
    """
    run = _text_in(root)
    for base in bases:
        merge_base = run(['git', 'merge-base', 'HEAD', base])
        if merge_base and merge_base.strip():
            return merge_base.strip()
    return None


def _text_at(root, ref, path):
    """One file's text at `ref`, or None if that ref has no such file."""
    run = _text_in(root)
    if run(['git', 'cat-file', '-e', f'{ref}:{path}']) is None:
        return None
    return run(['git', 'show', f'{ref}:{path}'])


def introduced_rows(table, read, root, bases=BRANCH_BASES):
    """The rows naming a declaration the base tree does not carry.

    A row is a claim about ONE DECLARATION, so the comparison is over
    declarations and never over names or over a file list. Three shapes
    of drift come apart under it, and a name-keyed or file-keyed rule
    merges them:

      * a site that predates the branch, in a file the branch edits for
        an unrelated reason — excused, because the base carries the
        identical declaration;
      * a site the branch MOVED — excused, because it is the same
        declaration under a different line;
      * a second, NEW declaration of an already-tabled name — refused,
        because no row was ever written for it.

    `read(sources)` turns a `{path: text}` map into `{path: {name: set
    of declaration digests}}`. It is the same comparison for both
    tables, and both boundary tests below call THIS function, so a
    mutant that stops it deciding anything turns both suites red.

    None means the base could not be read, and the caller MUST refuse on
    it rather than pass. The boundary is the property every row in both
    tables rests on, and a checkout that cannot evaluate it is not
    evidence that the property holds.
    """
    merge_base = _merge_base(root, bases)
    if merge_base is None:
        return None
    paths = sorted({key[0] for key in table})
    head = read({path: text for path in paths
                 if (text := _text_at(root, 'HEAD', path)) is not None})
    base = read({path: text for path in paths
                 if (text := _text_at(root, merge_base, path)) is not None})
    return sorted(key for key in table
                  if _digests_for(head, key) - _digests_for(base, key))


def _digests_for(digests, key):
    """The declaration digests one row names, on one side of the diff."""
    return digests.get(key[0], {}).get(key[1], set())


def python_digests(sources):
    """{path: {name: {digest}}} over the module-execution definitions.

    The digest is the definition's own AST, so two declarations agree
    exactly when they are the same definition and disagree the moment
    either its body or its signature moves.
    """
    digests = {}
    for path, source in sources.items():
        found = {}
        for name, nodes in definition_nodes(_parsed(path, source)).items():
            found[name] = {hashlib.sha1(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest() for node in nodes}
        digests[path] = found
    return digests


def js_digests(sources):
    """{path: {name: {digest}}} over the JavaScript declarations.

    The digest is the declaration's own body with its whitespace
    normalised, for the reason the Python side digests the AST: a moved
    line must not read as a new declaration and an edited body must.
    """
    digests = {}
    for path, source in sources.items():
        found = {}
        for text, _starts in _js_functions.documents(source, path):
            lines = text.split('\n')
            for item in _js_functions.declarations(text, path):
                # `offset` is the line the `function` keyword or the
                # `const` sits on, counted from one, and `body_lines`
                # spans that line to the closing brace.
                start = item.offset - 1
                body = ' '.join(lines[start:start + item.body_lines])
                found.setdefault(item.name, set()).add(hashlib.sha1(
                    re.sub(r'\s+', ' ', body).strip().encode()).hexdigest())
        digests[path] = found
    return digests


def _git_in(root):
    """A `run` yielding one word per line, for a repository at `root`."""
    def run(argv):
        text = _git_text(root, argv)
        return None if text is None else text.split()
    return run


def _text_in(root):
    """A `run` yielding a file's text, for a repository at `root`."""
    def run(argv):
        return _git_text(root, argv)
    return run


def _git_text(root, argv):
    done = subprocess.run(
        argv, cwd=root, capture_output=True, text=True,
        env=_util.child_coverage('scrub'))
    return None if done.returncode else done.stdout
