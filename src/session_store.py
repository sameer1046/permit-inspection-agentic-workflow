import dataclasses

from src.models import TraceEvent, WorkflowState


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
    return WorkflowState(
        case_id=case_id,
        status='suspended_pending_human',
        pending_items=pending,
        items=items,
        rejected_verdicts=[],
        permit_status=None,
        error=None,
        trace=[],
        item_categories={},
    )


def pending_items(state):
    return list(state.pending_items)


def copy_state(state, **changes):
    return dataclasses.replace(state, **changes)


def trace(state, step, item_id=None, detail=None):
    state.trace.append(dataclasses.asdict(TraceEvent(step=step, item_id=item_id, detail=detail)))
    return state
