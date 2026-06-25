"""Schema for the review package + audit log.

The audit log is not optional plumbing — FOIA determinations are appealable, so
every responsiveness call and every redaction has to carry its basis. This shape
IS the administrative record / Vaughn-style justification.
"""
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Redaction:
    start: int
    end: int
    text: str
    pii_type: str            # ssn | email | deliberative | ...
    exemption: str           # b6 | b7C | b5 | ...
    basis: str               # human-readable statutory basis
    detector: str            # regex:ssn | llm | ...
    confidence: float
    rationale: str
    status: str = "proposed" # proposed | accepted | rejected | modified
    decided_by: str | None = None
    decided_at: str | None = None


@dataclass
class DocReview:
    doc_id: str
    filename: str
    text: str
    responsive: bool
    responsive_confidence: float
    responsive_rationale: str
    escalated: bool                       # agent declined to decide -> human must
    redactions: list[Redaction] = field(default_factory=list)
    officer_responsive: bool | None = None  # human determination; None = not yet decided
    doc_type: str = "unknown"             # implicitly classified type (email | memo | court_judgment | ...)
    agent_run: bool = True                # has the triage agent run? raw intake = False


@dataclass
class AuditEvent:
    at: str
    actor: str               # "agent" | officer id
    action: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewPackage:
    request_id: str
    request_text: str
    jurisdiction: str
    created_at: str
    docs: list[DocReview] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
