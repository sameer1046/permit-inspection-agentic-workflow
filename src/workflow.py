from src import session_store
from src.models import AgentUnavailableError, AttemptStatus, Source, ValidationCode, Verdict

VALID_VERDICTS = (Verdict.COMPLIANT.value, Verdict.NON_COMPLIANT.value,
                  Verdict.NEEDS_REVIEW.value)

HUMAN_OUTCOMES = {
    'approve': (Verdict.COMPLIANT.value, Source.HUMAN_APPROVED.value, False),
    'reject': (Verdict.NON_COMPLIANT.value, Source.HUMAN_REJECTED.value, False),
    'defer': (Verdict.NEEDS_REVIEW.value, Source.HUMAN_DEFERRED.value, True),
}


class InspectionWorkflow:

    def __init__(self, agents, config):
        self._agents = agents
        self._config = config

    def start(self, case):
        if not self._is_valid_case(case):
            return session_store.invalid_case_state(getattr(case, 'case_id', None))

        trace = []
        rejected = []
        categories = {}
        item_states = []

        for item in case.items:
            categories[item.id] = item.category
            verdicts = []
            failed = False
            for name in self._specialists_for(item):
                verdict = self._run_with_retries(name, item, trace, rejected)
                if verdict is None:
                    failed = True
                else:
                    verdicts.append(verdict)
            item_states.append(self._resolve_item(item, verdicts, failed))

        return self._finalize(case.case_id, item_states, trace, rejected, categories)

    def resume(self, state, decisions):
        if not state or state.get('status') != 'suspended_pending_human':
            return state

        pending = set(session_store.pending_items(state))
        by_item = {d.item_id: d.decision for d in (decisions or [])}
        item_states = []

        for entry in state['items']:
            item_id = entry['item_id']
            decision = by_item.get(item_id)
            if item_id not in pending:
                item_states.append({**entry, 'pending': False})
            elif decision in HUMAN_OUTCOMES:
                final_verdict, source, still_pending = HUMAN_OUTCOMES[decision]
                item_states.append({'item_id': item_id, 'final_verdict': final_verdict,
                                    'source': source, 'pending': still_pending})
            else:
                item_states.append({**entry, 'pending': True})

        return self._finalize(state['case_id'], item_states, state['trace'],
                              state['rejected_verdicts'], state['item_categories'])

    def _finalize(self, case_id, item_states, trace, rejected, categories):
        state = session_store.new_suspension(case_id, item_states)
        state['trace'] = trace
        state['rejected_verdicts'] = rejected
        state['item_categories'] = categories
        if not state['pending_items']:
            state['status'] = 'complete'
            state['permit_status'] = session_store.calculate_permit_status(state['items'])
        return state

    def _specialists_for(self, item):
        names = [item.primary_specialist, item.secondary_specialist]
        return [name for name in names if name]

    def _run_with_retries(self, name, item, trace, rejected):
        agent = self._agents.get(name)
        attempts = max(1, int(getattr(self._config, 'max_attempts', 1)))

        for attempt in range(1, attempts + 1):
            if agent is None:
                trace.append(session_store.trace_entry(name, item.id, attempt,
                                                       AttemptStatus.UNAVAILABLE.value))
                continue
            try:
                verdict = agent.evaluate(item)
            except AgentUnavailableError:
                trace.append(session_store.trace_entry(name, item.id, attempt,
                                                       AttemptStatus.UNAVAILABLE.value))
                continue
            code = self._validate_verdict(verdict, item)
            if code is None:
                trace.append(session_store.trace_entry(name, item.id, attempt,
                                                       AttemptStatus.SUCCESS.value))
                return verdict
            trace.append(session_store.trace_entry(name, item.id, attempt,
                                                   AttemptStatus.MALFORMED.value))
            rejected.append({'item_id': item.id, 'agent': name, 'attempt': attempt,
                             'code': code})
        return None

    def _validate_verdict(self, verdict, item):
        if verdict is None or verdict.verdict not in VALID_VERDICTS:
            return ValidationCode.MALFORMED_VERDICT.value
        risk = verdict.risk_score
        if isinstance(risk, bool) or not isinstance(risk, (int, float)):
            return ValidationCode.RISK_OUT_OF_RANGE.value
        if not 0.0 <= float(risk) <= 1.0:
            return ValidationCode.RISK_OUT_OF_RANGE.value
        cited = verdict.cited_code_section
        if cited is not None and cited not in (item.applicable_code_sections or []):
            return ValidationCode.UNGROUNDED_CITATION.value
        return None

    def _resolve_item(self, item, verdicts, failed):
        gated = item.category in (getattr(self._config, 'gated_categories', None) or [])
        threshold = float(getattr(self._config, 'risk_threshold', 1.0))
        disagree = len({v.verdict for v in verdicts}) > 1
        risky = any(float(v.risk_score) >= threshold for v in verdicts)

        if failed or disagree or gated or risky or not verdicts:
            return {'item_id': item.id, 'final_verdict': Verdict.NEEDS_REVIEW.value,
                    'source': Source.AGENT.value, 'pending': True}
        return {'item_id': item.id, 'final_verdict': verdicts[0].verdict,
                'source': Source.AGENT.value, 'pending': False}

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
