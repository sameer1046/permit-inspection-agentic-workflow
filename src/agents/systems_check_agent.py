from src.models import ItemVerdict


class SystemsCheckAgent:

    def __init__(self, llm_client):
        self._llm = llm_client

    def evaluate(self, item):
        raw = self._llm.respond(item)
        if not isinstance(raw, dict) or 'verdict' not in raw or 'risk_score' not in raw:
            return ItemVerdict(item_id=item.id, agent='systems_check', verdict=None,
                               risk_score=None, cited_code_section=None)
        return ItemVerdict(
            item_id=item.id,
            agent='systems_check',
            verdict=raw.get('verdict'),
            risk_score=raw.get('risk_score'),
            cited_code_section=raw.get('cited_code_section'),
        )
