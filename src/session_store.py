from src.models import ErrorCode, Verdict


def new_suspension(case_id, item_states):
    items = []
    pending = []
    for entry in item_states:
        items.append({
            'item_id': entry['item_id'],
            'final_verdict': entry['final_verdict'],
            'source': entry['source'],
        })
        if entry.get('pending'):
            pending.append(entry['item_id'])
    return {
        'case_id': case_id,
        'status': 'suspended_pending_human',
        'pending_items': pending,
        'items': items,
        'rejected_verdicts': [],
        'permit_status': None,
        'error': None,
        'trace': [],
        'item_categories': {},
    }


def pending_items(state):
    return list(state.get('pending_items') or [])


def invalid_case_state(case_id):
    return {
        'case_id': case_id,
        'status': 'failed',
        'pending_items': [],
        'items': [],
        'rejected_verdicts': [],
        'permit_status': None,
        'error': ErrorCode.INVALID_REQUEST.value,
        'trace': [],
        'item_categories': {},
    }


def trace_entry(step, item_id, attempt, status):
    return {'step': step, 'item_id': item_id, 'attempt': attempt, 'status': status}


def calculate_permit_status(items):
    if any(entry['final_verdict'] == Verdict.NON_COMPLIANT.value for entry in items):
        return 'denied'
    return 'approved'
