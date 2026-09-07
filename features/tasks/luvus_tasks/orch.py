"""Native ORCH coordination."""

def coordinate(store, host, record):
    from .operations import claim_orch, link_orch
    from .handover import save_record
    if not record.get('orch_enabled'):
        return record
    if not record.get('orch_id'):
        record.update(link_orch(store, host, record, paths=record.get('orch_paths', []), deps=record.get('orch_deps', [])))
    claim_orch(store, host, record)
    record.pop('orch_error', None)
    save_record(store, record)
    return record


def show(store, host, reveal=False):
    """Compatibility entrypoint; task presentation belongs to native ORCH."""
    return None


def try_show(store, host, record=None, reveal=False):
    return ''
