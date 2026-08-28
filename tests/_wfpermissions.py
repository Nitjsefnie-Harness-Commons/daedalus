"""Permission assertions shared by the workflow contract mutations."""
from _yamlread import job_mapping


def assert_diff_coverage_permissions(workflow):
    permissions = job_mapping(workflow, 'diff-coverage', 'permissions')
    assert permissions == {'contents': 'read'}, (
        f'unsafe decoded permissions: {permissions!r}')


def assert_permissions_mutation_refused(workflow):
    try:
        assert_diff_coverage_permissions(workflow)
    except AssertionError as error:
        assert 'unsafe decoded permissions' in str(error), str(error)
        return
    raise AssertionError('widened decoded permissions were accepted')
