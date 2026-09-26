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
  * a second declaration of an already-tabled name — refused, whether
    or not the base already carried a body like it, because the row
    covers the base's COUNT of that name and not the head's.

It reaches that by reading the merge base's OWN definitions, which is a
content comparison; a path list cannot tell a site the branch wrote from
one it did not, and this branch edits fifteen of the twenty-seven files
the JavaScript residue lives in, so a path-scoped boundary would forbid
recording rows for sites that predate it.

A checkout that cannot see a base returns None and the caller MUST
refuse on it. None is "this checkout cannot answer"; an empty set is
"the base resolves and names nothing", and the two are facts about
different things.
"""
import ast
import hashlib
import re
import subprocess
from collections import Counter

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
    """One file at `ref`, or None if that ref has no such file."""
    run = _text_in(root)
    if run(['git', 'cat-file', '-e', f'{ref}:{path}']) is None:
        return None
    return run(['git', 'show', f'{ref}:{path}'])


def introduced_rows(table, read, root, bases=BRANCH_BASES):
    """The rows naming a declaration the base tree does not carry.

    The three shapes of drift this separates, and why a name-keyed or
    file-keyed rule merges them, are in this module's docstring; that
    list is the claim and this is the comparison.

    `read(sources)` turns a `{path: text}` map into `{path: {name:
    Counter of digest}}`. It is the same comparison for both tables, and
    both suites call THIS function, so a mutant that stops it deciding
    anything turns them red.

    None means the base could not be read, and the caller MUST refuse on
    it rather than pass: the boundary is the property every row rests
    on, and a checkout that cannot evaluate it is not evidence that it
    holds.
    """
    merge_base = _merge_base(root, bases)
    head = _git_text(root, ['git', 'rev-parse', 'HEAD']) or ''
    if merge_base is None or merge_base == head.strip():
        # No base, or a base that IS the head: either way there is
        # nothing to compare against, and a tree compared to itself
        # reports "no row excuses a branch-written declaration" for a
        # comparison that could not have failed. Both are the refusal
        # the None arm exists for.
        return None
    paths = sorted({key[0] for key in table})
    head = read({path: text for path in paths
                 if (text := _text_at(root, 'HEAD', path)) is not None})
    base = read({path: text for path in paths
                 if (text := _text_at(root, merge_base, path)) is not None})
    return sorted(key for key in table
                  if _digests_for(head, key) - _digests_for(base, key))


def _digests_for(digests, key):
    """How many times each body was bound to that name in that file."""
    return digests.get(key[0], {}).get(key[1], Counter())


def python_digests(sources):
    """{path: {name: Counter of digest}} over the module-execution
    definitions.

    A COUNTER and not a set, because the count is part of the claim: a
    file that binds one name to one body twice has bound it twice, and a
    set would report one and let the second past.
    """
    digests = {}
    for path, source in sources.items():
        found = {}
        for name, nodes in definition_nodes(_parsed(path, source)).items():
            found[name] = Counter(
                hashlib.sha1(ast.dump(node, include_attributes=False)
                             .encode()).hexdigest() for node in nodes)
        digests[path] = found
    return digests


def js_digests(sources):
    """{path: {name: Counter of digest}} over the JavaScript declarations.

    A counter for the reason the Python side is one, and the JavaScript
    case is the one that matters: this branch exists for byte-identical
    copies, and a second copy of a tabled declaration has the digest the
    base already carries, so a set makes it free and a counter does not.
    """
    digests = {}
    for path, source in sources.items():
        found = {}
        for text, _starts in _js_functions.documents(source, path):
            lines = text.split('\n')
            for item in _js_functions.declarations(text, path):
                start = item.offset - 1
                body = ' '.join(lines[start:start + item.body_lines])
                digest = hashlib.sha1(
                    re.sub(r'\s+', ' ', body).strip().encode()).hexdigest()
                found.setdefault(item.name, Counter())[digest] += 1
        digests[path] = found
    return digests


def _git_in(root):
    """One word per line, for a repository at `root`."""
    def run(argv):
        text = _git_text(root, argv)
        return None if text is None else text.split()
    return run


def _text_in(root):
    """A file's text, for a repository at `root`."""
    def run(argv):
        return _git_text(root, argv)
    return run


def _git_text(root, argv):
    done = subprocess.run(
        argv, cwd=root, capture_output=True, text=True,
        env=_util.child_coverage('scrub'))
    return None if done.returncode else done.stdout
