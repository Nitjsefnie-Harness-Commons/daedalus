"""Reading a workflow's structure without a YAML library.

Not a suite itself — run_tests.py only loads `test_*.py`.

The suites here are stdlib only, and PyYAML is not a dependency of this
project. What the workflow tests need of a parse is narrow: reach one key on
one named job and see the value the runner would receive, so that a policy
pinned on `jobs.speed.environment` cannot be satisfied by the same key
written on some other job.
"""


def job_scalar(workflow, job, key):
    """The value `key` holds on `job`, as the runner would receive it.

    Reads a plain scalar and a folded or literal block scalar, including
    YAML's rule that a more-indented line inside a folded block keeps its
    newline rather than folding to a space. `job` is a top-level key of
    `jobs:` and `key` one of its own keys, so a key written on a different
    job is not found here. None when `job` does not declare `key`.
    """
    lines = workflow.splitlines()
    if f'  {job}:' not in lines:
        return None
    body = lines[lines.index(f'  {job}:') + 1:]
    for number, line in enumerate(body):
        if line.strip() and not line.startswith('    '):
            return None
        name, colon, rest = line.partition(':')
        if not colon or name != f'    {key}':
            continue
        header = rest.strip()
        if header[:1] not in ('>', '|'):
            return header
        block = []
        for content in body[number + 1:]:
            if content.strip() and not content.startswith('     '):
                break
            block.append(content)
        while block and not block[-1].strip():
            block.pop()
        indent = min(len(c) - len(c.lstrip()) for c in block if c.strip())
        parts = [c[indent:] if c.strip() else '' for c in block]
        text = parts[0]
        for before, part in zip(parts, parts[1:]):
            fold = (header[:1] == '>' and before[:1] not in ('', ' ')
                    and part[:1] not in ('', ' '))
            text += (' ' if fold else '\n') + part
        return text if header.endswith('-') else text + '\n'
    return None
