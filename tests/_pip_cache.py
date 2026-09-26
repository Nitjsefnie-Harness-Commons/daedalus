"""The tests workflow's pip-cache job table.

Not a suite itself — run_tests.py only loads `test_*.py`.

`_CACHE_JOBS` moved here out of tests/test_ci_pip_cache.py, which
tests/test_cache_action_releases.py imported for it: that suite's release
verifier cross-checks the workflow's real pins against the length of this
table, so one spelling of the job list has to reach both.
"""

_CACHE_JOBS = (
    # (job, the key's python component, the step a save must follow, the
    # step the restore must precede).
    ('suites', '${{ matrix.python }}', 'Run every suite',
     'Install the test dependencies and project'),
    ('coverage-matrix', '${{ matrix.python }}', 'Measure',
     'Install the coverage toolchain and the project'),
    ('coverage', '3.13', 'Install the coverage toolchain and the project',
     'Install the coverage toolchain and the project'),
    ('pycodestyle', '3.13', 'pycodestyle', 'Install linters'),
    ('pylint', '3.13', 'pylint', 'Install linters and import dependencies'),
    ('pyright', '3.13', 'pyright',
     'Install the type checker and the dependencies it resolves'),
)
