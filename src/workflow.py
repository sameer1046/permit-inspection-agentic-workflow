from src import session_store
from src.models import (AgentUnavailableError, ErrorCode, Source, ValidationCode, Verdict,
                        WorkflowState)

SPECIALIST_AGENTS = ('structural_check', 'systems_check')

SEVERITY = {
    Verdict.COMPLIANT.value: 0,
    Verdict.NEEDS_REVIEW.value: 1,
    Verdict.NON_COMPLIANT.value: 2,
}

HUMAN_SOURCES = {
    'approve': Source.HUMAN_APPROVED.value,
    'reject': Source.HUMAN_REJECTED.value,
    'defer': Source.HUMAN_DEFERRED.value,
}

HUMAN_VERDICTS = {
    'approve': Verdict.COMPLIANT.value,
    'reject': Verdict.NON_COMPLIANT.value,
    'defer': Verdict.NEEDS_REVIEW.value,
}


class InspectionWorkflow:

    def __init__(self, agents, config):
        self._agents = agents
        self._config = config

    def start(self, case):
        if not self._is_valid_case(case):
            return WorkflowState(case_id=getattr(case, 'case_id', None), status='failed',
                                 error=ErrorCode.INVALID_REQUEST.value)

        state = WorkflowState(case_id=case.case_id, status='running')
        item_states = []

        for item in case.items:
            state.item_categories[item.id] = item.category
            verdicts = []
            for name in self._specialists_for(item):
                agent = self._agents.get(name)
                if agent is None:
                    session_store.trace(state, 'agent_missing', item.id, name)
                    continue
                try:
                    verdict = self._run_with_retries(agent, item, state)
                except AgentUnavailableError as exc:
                    state.status = 'failed'
                    state.error = str(exc)
                    session_store.trace(state, 'agent_unavailable', item.id, name)
                    return state
                if verdict is not None:
                    verdicts.append(verdict)

            item_states.append(self._resolve_item(item, verdicts, state))

        return self._finalize(state, item_states)

    def resume(self, state, decisions):
        if state is None or state.status != 'suspended_pending_human':
            return state

        pending = set(session_store.pending_items(state))
        by_item = {d.item_id: d.decision for d in (decisions or [])}
        item_states = []

        for entry in state.items:
            item_id = entry['item_id']
            decision = by_item.get(item_id)
            if item_id not in pending:
                item_states.append({**entry, 'pending': False})
                continue
            if decision not in HUMAN_VERDICTS:
                item_states.append({**entry, 'pending': True})
                continue
            session_store.trace(state, 'human_decision', item_id, decision)
            item_states.append({
                'item_id': item_id,
                'final_verdict': HUMAN_VERDICTS[decision],
                'source': HUMAN_SOURCES[decision],
                'pending': decision == 'defer',
            })

        return self._finalize(state, item_states)

    def _finalize(self, state, item_states):
        pending = [entry['item_id'] for entry in item_states if entry.get('pending')]
        resolved = session_store.new_suspension(state.case_id, item_states)
        resolved.rejected_verdicts = state.rejected_verdicts
        resolved.trace = state.trace
        resolved.item_categories = state.item_categories
        if pending:
            resolved.status = 'suspended_pending_human'
            resolved.permit_status = None
            return resolved
        resolved.status = 'complete'
        resolved.pending_items = []
        resolved.permit_status = self._compute_permit_status(resolved)
        session_store.trace(resolved, 'permit_decision', None, resolved.permit_status)
        return resolved

    def _specialists_for(self, item):
        declared = getattr(item, 'specialists', None) or []
        specialists = [name for name in declared if name in SPECIALIST_AGENTS]
        return specialists or ['structural_check']

    def _run_with_retries(self, agent, item, state):
        attempts = max(0, int(getattr(self._config, 'max_retries', 0))) + 1
        for attempt in range(attempts):
            verdict = agent.evaluate(item)
            code = self._validate_verdict(verdict, item)
            if code is None:
                session_store.trace(state, 'verdict_accepted', item.id, verdict.agent)
                return verdict
            state.rejected_verdicts.append({
                'item_id': item.id,
                'agent': verdict.agent,
                'attempt': attempt + 1,
                'code': code,
                'verdict': verdict.verdict,
                'risk_score': verdict.risk_score,
                'cited_code_section': verdict.cited_code_section,
            })
            session_store.trace(state, 'verdict_rejected', item.id, code)
        return None

    def _validate_verdict(self, verdict, item):
        if verdict.verdict not in SEVERITY:
            return ValidationCode.MALFORMED_VERDICT.value
        if not isinstance(verdict.risk_score, (int, float)) or isinstance(verdict.risk_score, bool):
            return ValidationCode.RISK_OUT_OF_RANGE.value
        if not 0.0 <= float(verdict.risk_score) <= 1.0:
            return ValidationCode.RISK_OUT_OF_RANGE.value
        cited = verdict.cited_code_section
        if cited is not None and cited not in (item.applicable_code_sections or []):
            return ValidationCode.UNGROUNDED_CITATION.value
        return None

    def _resolve_item(self, item, verdicts, state):
        if not verdicts:
            session_store.trace(state, 'escalated_no_valid_verdict', item.id)
            return {'item_id': item.id, 'final_verdict': Verdict.NEEDS_REVIEW.value,
                    'source': Source.AGENT.value, 'pending': True}

        worst = max(verdicts, key=lambda v: SEVERITY[v.verdict])
        risk = max(float(v.risk_score) for v in verdicts)
        threshold = float(getattr(self._config, 'risk_gate_threshold', 1.0))
        gated = item.category in (getattr(self._config, 'gated_categories', None) or [])

        pending = (worst.verdict != Verdict.COMPLIANT.value or gated or risk >= threshold)
        if pending:
            session_store.trace(state, 'gated_for_human', item.id,
                                f'verdict={worst.verdict} risk={risk} gated={gated}')
        return {'item_id': item.id, 'final_verdict': worst.verdict,
                'source': Source.AGENT.value, 'pending': pending}

    def _compute_permit_status(self, state):
        hard_block = set(getattr(self._config, 'hard_block_categories', None) or [])
        non_compliant = [entry for entry in state.items
                         if entry['final_verdict'] == Verdict.NON_COMPLIANT.value]
        if any(state.item_categories.get(entry['item_id']) in hard_block
               for entry in non_compliant):
            return 'denied'
        if non_compliant:
            return 'conditional'
        return 'approved'

    def _is_valid_case(self, case):
        if case is None or not getattr(case, 'case_id', None):
            return False
        items = getattr(case, 'items', None)
        if not items:
            return False

        item_ids = set()
        for item in items:
            item_id = getattr(item, 'id', None)
            if not item_id or item_id in item_ids:
                return False
            item_ids.add(item_id)
        return True
