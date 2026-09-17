from src.models import (AgentRegistry, AgentUnavailableError, AttemptStatus, ErrorCode,
                        HumanDecision, InspectionCase, InspectionItem, ItemVerdict, Source,
                        ValidationCode, Verdict, WorkflowConfig)
from src.workflow import InspectionWorkflow

CONTRACT_KEYS = {'case_id', 'status', 'pending_items', 'items', 'rejected_verdicts',
                 'permit_status', 'error', 'trace', 'item_categories'}


class StubAgent:

    def __init__(self, name, responses):
        self._name = name
        self._responses = list(responses)

    def evaluate(self, item):
        payload = self._responses.pop(0) if self._responses else {}
        if isinstance(payload, Exception):
            raise payload
        return ItemVerdict(item_id=item.id, agent=self._name, **payload)


def verdict(value=Verdict.COMPLIANT.value, risk=0.1, section='P2503.5'):
    return {'verdict': value, 'risk_score': risk, 'cited_code_section': section}


def item(item_id='ITM-21', category='foundation', primary='structural_check', secondary=None):
    return InspectionItem(id=item_id, category=category, observation='obs',
                          applicable_code_sections=['P2503.5'],
                          primary_specialist=primary, secondary_specialist=secondary)


def case(items=None, case_id='INS-9001'):
    return InspectionCase(case_id=case_id, items=items or [item()])


def workflow(responses=None, **config):
    responses = responses or {}
    agents = AgentRegistry({
        'structural_check': StubAgent('structural_check',
                                      responses.get('structural_check', [])),
        'systems_check': StubAgent('systems_check', responses.get('systems_check', [])),
    })
    return InspectionWorkflow(agents, WorkflowConfig(**config))


def test_state_matches_output_contract():
    state = workflow({'structural_check': [verdict()]}).start(case())
    assert set(state) == CONTRACT_KEYS
    assert state['case_id'] == 'INS-9001'
    assert state['item_categories'] == {'ITM-21': 'foundation'}


def test_invalid_case_is_rejected_without_calling_agents():
    state = workflow().start(InspectionCase(case_id='', items=[]))
    assert state['status'] == 'failed'
    assert state['error'] == ErrorCode.INVALID_REQUEST.value
    assert state['trace'] == []


def test_duplicate_item_ids_are_invalid():
    state = workflow().start(case([item('ITM-21'), item('ITM-21')]))
    assert state['error'] == ErrorCode.INVALID_REQUEST.value


def test_settled_compliant_case_is_approved():
    state = workflow({'structural_check': [verdict()]}).start(case())
    assert state['status'] == 'complete'
    assert state['permit_status'] == 'approved'
    assert state['items'] == [{'item_id': 'ITM-21', 'final_verdict': 'compliant',
                               'source': Source.AGENT.value}]


def test_non_compliant_alone_settles_and_denies():
    responses = {'structural_check': [verdict(Verdict.NON_COMPLIANT.value, 0.2)]}
    state = workflow(responses).start(case())
    assert state['pending_items'] == []
    assert state['permit_status'] == 'denied'


def test_gated_category_keeps_item_pending():
    state = workflow({'structural_check': [verdict()]},
                     gated_categories=['foundation']).start(case())
    assert state['status'] == 'suspended_pending_human'
    assert state['pending_items'] == ['ITM-21']
    assert state['permit_status'] is None
    assert state['items'][0]['final_verdict'] == Verdict.NEEDS_REVIEW.value


def test_risk_equal_to_threshold_keeps_item_pending():
    state = workflow({'structural_check': [verdict(risk=0.7)]},
                     risk_threshold=0.7).start(case())
    assert state['pending_items'] == ['ITM-21']


def test_disagreeing_specialists_keep_item_pending():
    responses = {
        'structural_check': [verdict(Verdict.COMPLIANT.value)],
        'systems_check': [verdict(Verdict.NON_COMPLIANT.value)],
    }
    state = workflow(responses).start(case([item(secondary='systems_check')]))
    assert state['pending_items'] == ['ITM-21']
    assert state['items'][0]['final_verdict'] == Verdict.NEEDS_REVIEW.value


def test_agreeing_specialists_settle_the_item():
    responses = {'structural_check': [verdict()], 'systems_check': [verdict()]}
    state = workflow(responses).start(case([item(secondary='systems_check')]))
    assert state['pending_items'] == []
    assert state['permit_status'] == 'approved'


def test_trace_records_one_entry_per_attempt_with_agent_name():
    responses = {'structural_check': [AgentUnavailableError('down'), verdict()]}
    state = workflow(responses, max_attempts=2).start(case())
    assert state['trace'] == [
        {'step': 'structural_check', 'item_id': 'ITM-21', 'attempt': 1,
         'status': AttemptStatus.UNAVAILABLE.value},
        {'step': 'structural_check', 'item_id': 'ITM-21', 'attempt': 2,
         'status': AttemptStatus.SUCCESS.value},
    ]
    assert state['permit_status'] == 'approved'


def test_exhausted_specialist_escalates_to_human():
    responses = {'structural_check': [AgentUnavailableError('down'),
                                      AgentUnavailableError('down')]}
    state = workflow(responses, max_attempts=2).start(case())
    assert state['status'] == 'suspended_pending_human'
    assert state['items'][0]['final_verdict'] == Verdict.NEEDS_REVIEW.value
    assert [entry['status'] for entry in state['trace']] == ['unavailable', 'unavailable']
    assert state['rejected_verdicts'] == []


def test_unknown_verdict_is_recorded_as_malformed():
    responses = {'structural_check': [verdict('looks_fine'), verdict()]}
    state = workflow(responses, max_attempts=2).start(case())
    assert state['rejected_verdicts'] == [
        {'item_id': 'ITM-21', 'agent': 'structural_check', 'attempt': 1,
         'code': ValidationCode.MALFORMED_VERDICT.value}]
    assert state['trace'][0]['status'] == AttemptStatus.MALFORMED.value


def test_out_of_range_risk_is_recorded():
    responses = {'structural_check': [verdict(risk=4.2), verdict()]}
    state = workflow(responses, max_attempts=2).start(case())
    assert state['rejected_verdicts'][0]['code'] == ValidationCode.RISK_OUT_OF_RANGE.value


def test_inapplicable_code_section_is_recorded():
    responses = {'structural_check': [verdict(section='E3601.6'), verdict()]}
    state = workflow(responses, max_attempts=2).start(case())
    assert state['rejected_verdicts'][0]['code'] == ValidationCode.UNGROUNDED_CITATION.value


def test_null_citation_is_allowed():
    state = workflow({'structural_check': [verdict(section=None)]}).start(case())
    assert state['rejected_verdicts'] == []
    assert state['permit_status'] == 'approved'


def test_resume_applies_human_decisions():
    flow = workflow({'structural_check': [verdict()]}, gated_categories=['foundation'])
    resumed = flow.resume(flow.start(case()),
                          [HumanDecision(item_id='ITM-21', decision='approve')])
    assert resumed['status'] == 'complete'
    assert resumed['permit_status'] == 'approved'
    assert resumed['items'][0]['source'] == Source.HUMAN_APPROVED.value


def test_human_rejection_denies_the_permit():
    flow = workflow({'structural_check': [verdict()]}, gated_categories=['foundation'])
    resumed = flow.resume(flow.start(case()),
                          [HumanDecision(item_id='ITM-21', decision='reject')])
    assert resumed['permit_status'] == 'denied'
    assert resumed['items'][0]['source'] == Source.HUMAN_REJECTED.value


def test_deferred_item_remains_pending():
    flow = workflow({'structural_check': [verdict()]}, gated_categories=['foundation'])
    resumed = flow.resume(flow.start(case()),
                          [HumanDecision(item_id='ITM-21', decision='defer')])
    assert resumed['status'] == 'suspended_pending_human'
    assert resumed['pending_items'] == ['ITM-21']
    assert resumed['items'][0]['source'] == Source.HUMAN_DEFERRED.value


def test_resume_does_not_call_agents_again():
    items = [item('ITM-21'),
             item('ITM-22', category='plumbing_rough_in', primary='systems_check')]
    responses = {'structural_check': [verdict()], 'systems_check': [verdict()]}
    flow = workflow(responses, gated_categories=['foundation'])
    state = flow.start(case(items))
    trace_before = list(state['trace'])
    resumed = flow.resume(state, [HumanDecision(item_id='ITM-21', decision='approve')])
    assert resumed['trace'] == trace_before


def test_decisions_for_settled_items_are_ignored():
    items = [item('ITM-21'),
             item('ITM-22', category='plumbing_rough_in', primary='systems_check')]
    responses = {'structural_check': [verdict()], 'systems_check': [verdict()]}
    flow = workflow(responses, gated_categories=['foundation'])
    resumed = flow.resume(flow.start(case(items)), [
        HumanDecision(item_id='ITM-21', decision='approve'),
        HumanDecision(item_id='ITM-22', decision='reject'),
    ])
    settled = next(e for e in resumed['items'] if e['item_id'] == 'ITM-22')
    assert settled['source'] == Source.AGENT.value
    assert resumed['permit_status'] == 'approved'


def test_undecided_pending_item_survives_the_round():
    items = [item('ITM-21'), item('ITM-22', primary='systems_check', category='foundation')]
    responses = {'structural_check': [verdict()], 'systems_check': [verdict()]}
    flow = workflow(responses, gated_categories=['foundation'])
    resumed = flow.resume(flow.start(case(items)),
                          [HumanDecision(item_id='ITM-21', decision='approve')])
    assert resumed['pending_items'] == ['ITM-22']


def test_failed_case_is_not_processed_on_resume():
    flow = workflow()
    failed = flow.start(InspectionCase(case_id='', items=[]))
    assert flow.resume(failed, [HumanDecision(item_id='ITM-21', decision='approve')]) is failed


def test_resume_on_complete_case_is_a_noop():
    flow = workflow({'structural_check': [verdict()]})
    state = flow.start(case())
    assert flow.resume(state, []) is state
