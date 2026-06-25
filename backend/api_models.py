"""Request bodies for the API surface (app.py). Pure schema — no state, no logic."""
from pydantic import BaseModel


class IntakeReq(BaseModel):
    n: int = 3                           # how many documents to bring in
    types: list[str] | None = None       # restrict the stream to these document types


class RunReq(BaseModel):
    doc_ids: list[str] | None = None     # None/empty -> run on ALL unprocessed documents


class Decision(BaseModel):
    doc_id: str
    redaction_index: int
    action: str                          # accepted | rejected | modified
    officer: str = "officer"
    new_text: str | None = None          # for modify


class Missed(BaseModel):
    doc_id: str
    text: str
    pii_type: str = "unknown"
    exemption: str | None = None
    officer: str = "officer"


class AddRedaction(BaseModel):
    doc_id: str
    start: int
    end: int
    text: str
    exemption: str = "b6"
    officer: str = "officer"


class Reclassify(BaseModel):
    doc_id: str
    redaction_index: int
    exemption: str


class Resolve(BaseModel):
    doc_id: str
    responsive: bool
    officer: str = "officer"
