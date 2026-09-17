import json
from pathlib import Path

import pandas as pd
import streamlit as st
from openai import OpenAIError

from src.agents.structural_check_agent import StructuralCheckAgent
from src.agents.systems_check_agent import SystemsCheckAgent
from src.llm import OpenAIInspectionClient
from src.models import (AgentRegistry, HumanDecision, InspectionCase, InspectionItem,
                        WorkflowConfig)
from src.workflow import InspectionWorkflow

st.set_page_config(page_title='Permit Inspection Sign-Off Workflow', layout='wide')
DATA_DIR = Path(__file__).resolve().parent / 'data'


@st.cache_data
def load_cases():
    try:
        with open(DATA_DIR / 'cases.json', 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return []


def to_case(raw_case):
    items = [InspectionItem(**item) for item in raw_case['items']]
    return InspectionCase(case_id=raw_case['case_id'], items=items)


st.title('Permit Inspection Sign-Off Workflow')
st.caption('Ridgeview County building department - field inspection to permit decision')

raw_cases = load_cases()

with st.sidebar:
    st.header('Configuration')
    all_categories = sorted({item['category'] for raw_case in raw_cases
                             for item in raw_case['items']})
    gated_categories = st.multiselect(
        'Gated categories', all_categories,
        default=['electrical_rough_in'] if 'electrical_rough_in' in all_categories else [])
    hard_block_categories = st.multiselect(
        'Hard-block categories', all_categories,
        default=['fire_protection'] if 'fire_protection' in all_categories else [])
    risk_gate_threshold = st.slider('Risk gate threshold', 0.0, 1.0, 0.7, 0.05)
    max_retries = st.number_input('Max retries', min_value=0, max_value=3, value=1)

if not raw_cases:
    st.warning('No cases found in data/cases.json.')
else:
    options = [raw_case['case_id'] for raw_case in raw_cases]
    selected_case_id = st.selectbox('Inspection case', options)
    selected_case = next(rc for rc in raw_cases if rc['case_id'] == selected_case_id)
    st.subheader('Inspection observations')
    st.dataframe(pd.DataFrame(selected_case['items'])[['id', 'category', 'observation']])

    if st.button('Run inspection'):
        config = WorkflowConfig(
            gated_categories=gated_categories,
            risk_gate_threshold=risk_gate_threshold,
            hard_block_categories=hard_block_categories,
            max_retries=int(max_retries),
        )
        try:
            with st.spinner('Running inspection...'):
                agents = AgentRegistry({
                    'structural_check': StructuralCheckAgent(
                        OpenAIInspectionClient('prompts/structural_check.txt')),
                    'systems_check': SystemsCheckAgent(
                        OpenAIInspectionClient('prompts/systems_check.txt')),
                })
                state = InspectionWorkflow(agents, config).start(to_case(selected_case))
        except NotImplementedError:
            st.error('InspectionWorkflow.start() is not implemented yet.')
        except OpenAIError:
            st.error('The inspection model is unavailable. '
                     'Set OPENAI_API_KEY and try again.')
        else:
            st.session_state['state'] = state
            st.session_state['config'] = config
            st.session_state['agents'] = agents

state = st.session_state.get('state')
if state is not None:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric('Status', state.status)
    with col2:
        st.metric('Permit status', state.permit_status or '-')
    with col3:
        st.metric('Error', state.error or '-')

    if state.items:
        st.subheader('Item outcomes')
        st.dataframe(pd.DataFrame(state.items))

    if state.rejected_verdicts:
        st.subheader('Rejected verdicts')
        st.dataframe(pd.DataFrame(state.rejected_verdicts))

    if state.trace:
        st.subheader('Trace')
        st.dataframe(pd.DataFrame(state.trace))

    if state.status == 'suspended_pending_human':
        st.subheader('Inspector decision panel')
        st.caption('Simulates the on-call inspector clearing pending items over the shift.')
        decisions_by_item = {}
        for item_id in state.pending_items:
            decisions_by_item[item_id] = st.selectbox(
                f'Decision for {item_id}',
                ['no decision yet', 'approve', 'reject', 'defer'],
                key=f'decision_{item_id}',
            )
        if st.button('Submit inspector decisions for this round'):
            decisions = [
                HumanDecision(item_id=item_id, decision=decision)
                for item_id, decision in decisions_by_item.items()
                if decision != 'no decision yet'
            ]
            workflow = InspectionWorkflow(st.session_state['agents'],
                                          st.session_state['config'])
            st.session_state['state'] = workflow.resume(state, decisions)
            st.rerun()
