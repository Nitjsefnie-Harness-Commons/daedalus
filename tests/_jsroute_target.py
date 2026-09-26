"""The resolution record every JavaScript routing helper reports."""


def _target(status, binding=None, body=None, member=None, name=None,
            source=None, form=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member, 'name': name, 'source': source, 'form': form}
