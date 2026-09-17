from src.models import (AgentRegistry, AgentUnavailableError, ErrorCode, HumanDecision,
                        InspectionCase, InspectionItem, ItemVerdict, Source, ValidationCode,
                        Verdict, WorkflowConfig)
from src.workflow import InspectionWorkflow


class StubAgent:

    def __init__(self, name, verdicts):
        self._name = name
        self._verdicts = list(verdicts)

    def evaluate(self, item):
        payload = self._verdicts.pop(0) if self._verdicts else {}
        if isinstance(payload, Exception):
            raise payload
        return ItemVerdict(item_id=item.id, agent=self._name, **payload)


def compliant(section='R502.3'):
    return {'verdict': Verdict.COMPLIANT.value, 'risk_score': 0.1,
            'cited_code_section': section}


def item(item_id='item-1', category='structural_framing', specialists=('structural_check',)):
    return InspectionItem(id=item_id, category=category, observation='obs',
                          applicable_code_sections=['R502.3'], specialists=list(specialists))


def case(items=None, case_id='case-1'):
    return InspectionCase(case_id=case_id, items=items or [item()])


def workflow(verdicts, **config):
    agents = AgentRegistry({
        'structural_check': StubAgent('structural_check', verdicts.get('structural_check', [])),
        'systems_check': StubAgent('systems_check', verdicts.get('systems_check', [])),
    })
    return InspectionWorkflow(agents, WorkflowConfig(**config))


def test_invalid_case_fails_fast():
    state = workflow({}).start(InspectionCase(case_id='', items=[]))
    assert state.status == 'failed'
    assert state.error == ErrorCode.INVALID_REQUEST.value


def test_duplicate_item_ids_rejected():
    state = workflow({}).start(case([item('dup'), item('dup')]))
    assert state.error == ErrorCode.INVALID_REQUEST.value


def test_all_compliant_approves_permit():
    state = workflow({'structural_check': [compliant()]}).start(case())
    assert state.status == 'complete'
    assert state.permit_status == 'approved'
    assert state.items[0]['source'] == Source.AGENT.value


def test_non_compliant_item_suspends_for_human():
    verdicts = {'structural_check': [{'verdict': Verdict.NON_COMPLIANT.value,
                                      'risk_score': 0.4, 'cited_code_section': 'R502.3'}]}
    state = workflow(verdicts).start(case())
    assert state.status == 'suspended_pending_human'
    assert state.pending_items == ['item-1']
    assert state.permit_status is None


def test_gated_category_always_suspends():
    state = workflow({'structural_check': [compliant()]},
                     gated_categories=['structural_framing']).start(case())
    assert state.pending_items == ['item-1']


def test_risk_above_threshold_suspends():
    verdicts = {'structural_check': [{'verdict': Verdict.COMPLIANT.value, 'risk_score': 0.9,
                                      'cited_code_section': 'R502.3'}]}
    state = workflow(verdicts, risk_gate_threshold=0.7).start(case())
    assert state.pending_items == ['item-1']


def test_ungrounded_citation_is_rejected_then_retried():
    verdicts = {'structural_check': [
        {'verdict': Verdict.COMPLIANT.value, 'risk_score': 0.1,
         'cited_code_section': 'E3601.6'},
        compliant(),
    ]}
    state = workflow(verdicts, max_retries=1).start(case())
    assert state.rejected_verdicts[0]['code'] == ValidationCode.UNGROUNDED_CITATION.value
    assert state.permit_status == 'approved'


def test_out_of_range_risk_is_rejected():
    verdicts = {'structural_check': [
        {'verdict': Verdict.COMPLIANT.value, 'risk_score': 4.2,
         'cited_code_section': 'R502.3'},
        compliant(),
    ]}
    state = workflow(verdicts, max_retries=1).start(case())
    assert state.rejected_verdicts[0]['code'] == ValidationCode.RISK_OUT_OF_RANGE.value


def test_unknown_verdict_is_malformed():
    verdicts = {'structural_check': [
        {'verdict': 'looks_fine', 'risk_score': 0.1, 'cited_code_section': 'R502.3'},
        compliant(),
    ]}
    state = workflow(verdicts, max_retries=1).start(case())
    assert state.rejected_verdicts[0]['code'] == ValidationCode.MALFORMED_VERDICT.value


def test_exhausted_retries_escalate_to_human():
    bad = {'verdict': 'nope', 'risk_score': 0.1, 'cited_code_section': 'R502.3'}
    state = workflow({'structural_check': [bad, bad]}, max_retries=1).start(case())
    assert state.status == 'suspended_pending_human'
    assert state.items[0]['final_verdict'] == Verdict.NEEDS_REVIEW.value
    assert len(state.rejected_verdicts) == 2


def test_cross_trade_item_takes_worst_verdict():
    items = [item('item-1', 'fire_protection', ('structural_check', 'systems_check'))]
    verdicts = {
        'structural_check': [compliant()],
        'systems_check': [{'verdict': Verdict.NON_COMPLIANT.value, 'risk_score': 0.8,
                           'cited_code_section': 'R502.3'}],
    }
    state = workflow(verdicts).start(case(items))
    assert state.items[0]['final_verdict'] == Verdict.NON_COMPLIANT.value


def test_agent_unavailable_fails_the_run():
    state = workflow({'structural_check': [AgentUnavailableError('down')]}).start(case())
    assert state.status == 'failed'
    assert state.error == 'down'


def test_resume_applies_human_decisions():
    verdicts = {'structural_check': [{'verdict': Verdict.NON_COMPLIANT.value,
                                      'risk_score': 0.4, 'cited_code_section': 'R502.3'}]}
    flow = workflow(verdicts)
    state = flow.start(case())
    resumed = flow.resume(state, [HumanDecision(item_id='item-1', decision='approve')])
    assert resumed.status == 'complete'
    assert resumed.permit_status == 'approved'
    assert resumed.items[0]['source'] == Source.HUMAN_APPROVED.value


def test_deferred_item_stays_pending_across_rounds():
    verdicts = {'structural_check': [{'verdict': Verdict.NON_COMPLIANT.value,
                                      'risk_score': 0.4, 'cited_code_section': 'R502.3'}]}
    flow = workflow(verdicts)
    state = flow.resume(flow.start(case()),
                        [HumanDecision(item_id='item-1', decision='defer')])
    assert state.status == 'suspended_pending_human'
    assert state.pending_items == ['item-1']
    assert state.items[0]['source'] == Source.HUMAN_DEFERRED.value


def test_partial_round_keeps_undecided_items_pending():
    items = [item('item-1'), item('item-2')]
    bad = {'verdict': Verdict.NON_COMPLIANT.value, 'risk_score': 0.4,
           'cited_code_section': 'R502.3'}
    flow = workflow({'structural_check': [bad, bad]})
    state = flow.resume(flow.start(case(items)),
                        [HumanDecision(item_id='item-1', decision='approve')])
    assert state.pending_items == ['item-2']


def test_hard_block_category_denies_permit():
    items = [item('item-1', 'fire_protection', ('systems_check',))]
    verdicts = {'systems_check': [{'verdict': Verdict.NON_COMPLIANT.value, 'risk_score': 0.5,
                                   'cited_code_section': 'R502.3'}]}
    flow = workflow(verdicts, hard_block_categories=['fire_protection'])
    state = flow.resume(flow.start(case(items)),
                        [HumanDecision(item_id='item-1', decision='reject')])
    assert state.permit_status == 'denied'


def test_soft_non_compliance_is_conditional():
    verdicts = {'structural_check': [{'verdict': Verdict.NON_COMPLIANT.value,
                                      'risk_score': 0.4, 'cited_code_section': 'R502.3'}]}
    flow = workflow(verdicts, hard_block_categories=['fire_protection'])
    state = flow.resume(flow.start(case()),
                        [HumanDecision(item_id='item-1', decision='reject')])
    assert state.permit_status == 'conditional'


def test_resume_on_complete_state_is_a_noop():
    flow = workflow({'structural_check': [compliant()]})
    state = flow.start(case())
    assert flow.resume(state, []) is state
