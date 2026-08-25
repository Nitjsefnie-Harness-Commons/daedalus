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
    jobs = next((number for number, line in enumerate(lines)
                 if line.startswith('jobs:')
                 and (not line[5:].strip()
                      or line[5:].lstrip().startswith('#'))), None)
    if jobs is None:
        return None
    body_end = len(lines)
    for number, line in enumerate(lines[jobs + 1:], jobs + 1):
        if (line.strip() and not line.lstrip().startswith('#')
                and not line.startswith((' ', '\t'))):
            body_end = number
            break
    body = lines[jobs + 1:body_end]
    job_indent = next((len(line) - len(line.lstrip()) for line in body
                       if line.strip() and not line.lstrip().startswith('#')),
                      None)
    if job_indent is None:
        return None
    job_name = ' ' * job_indent + job + ':'
    job_start = next((number for number, line in enumerate(body)
                      if line == job_name
                      or line.startswith(job_name + ' #')), None)
    if job_start is None:
        return None
    job_end = len(body)
    for number, line in enumerate(body[job_start + 1:], job_start + 1):
        if line.strip() and not line.lstrip().startswith('#'):
            indent = len(line) - len(line.lstrip())
            if indent <= job_indent:
                job_end = number
                break
    job_body = body[job_start + 1:job_end]
    key_indent = next((len(line) - len(line.lstrip()) for line in job_body
                       if line.strip() and not line.lstrip().startswith('#')),
                      None)
    if key_indent is None:
        return None
    key_name = ' ' * key_indent + key + ':'
    for number, line in enumerate(job_body):
        name, colon, rest = line.partition(':')
        if not colon or name != key_name[:-1]:
            continue
        header = rest.strip()
        if header[:1] not in ('>', '|'):
            return header
        block = []
        for content in job_body[number + 1:]:
            if (content.strip()
                    and len(content) - len(content.lstrip()) <= key_indent):
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
