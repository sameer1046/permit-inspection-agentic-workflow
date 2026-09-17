from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    COMPLIANT = 'compliant'
    NON_COMPLIANT = 'non_compliant'
    NEEDS_REVIEW = 'needs_review'


class Source(str, Enum):
    AGENT = 'agent'
    HUMAN_APPROVED = 'human_approved'
    HUMAN_REJECTED = 'human_rejected'
    HUMAN_DEFERRED = 'human_deferred'


class AttemptStatus(str, Enum):
    SUCCESS = 'success'
    UNAVAILABLE = 'unavailable'
    MALFORMED = 'malformed'


class ValidationCode(str, Enum):
    UNGROUNDED_CITATION = 'UNGROUNDED_CITATION'
    RISK_OUT_OF_RANGE = 'RISK_OUT_OF_RANGE'
    MALFORMED_VERDICT = 'MALFORMED_VERDICT'


class ErrorCode(str, Enum):
    INVALID_REQUEST = 'INVALID_REQUEST'


class AgentUnavailableError(Exception):
    pass


@dataclass
class InspectionItem:
    id: str
    category: str
    observation: str
    applicable_code_sections: list = field(default_factory=list)
    primary_specialist: Optional[str] = None
    secondary_specialist: Optional[str] = None


@dataclass
class InspectionCase:
    case_id: str
    items: list = field(default_factory=list)


@dataclass
class ItemVerdict:
    item_id: str
    agent: str
    verdict: Optional[str] = None
    risk_score: Optional[float] = None
    cited_code_section: Optional[str] = None


@dataclass
class HumanDecision:
    item_id: str
    decision: str


@dataclass
class WorkflowConfig:
    gated_categories: list = field(default_factory=list)
    risk_threshold: float = 0.7
    max_attempts: int = 2


class AgentRegistry:

    def __init__(self, agents):
        self._agents = dict(agents)

    def get(self, name):
        return self._agents.get(name)

    def names(self):
        return list(self._agents)
