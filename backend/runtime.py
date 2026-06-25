"""Shared runtime state and primitives for the API layer.

One process-wide ReviewPackage + job, guarded by a re-entrant lock. Kept in a single
leaf module so the route handlers (app.py), the intake/job driver (intake.py), and the
demo seeder (seeding.py) all read and mutate the SAME state with no import cycles.
"""
import os
import threading
from datetime import datetime, timezone

from . import exemptions
from .schema import ReviewPackage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
# The live corpus: real born-digital government PDFs (federal court records, govinfo USCOURTS).
# Override with FOIA_CORPUS to point at the legacy synthetic .txt set (data/docs) or another dir.
DOCS_DIR = os.environ.get("FOIA_CORPUS", os.path.join(DATA, "pdfs"))
CONSOLE_DIR = os.path.join(ROOT, "console")

STATE: dict = {"package": None, "job": None,
               "profile": os.environ.get("FOIA_PROFILE", "foia_federal")}
LOCK = threading.RLock()   # re-entrant: helpers acquire it while a caller already holds it

_DOC_SEQ = 0       # globally-unique doc ids across the intake stream
_JOB_SEQ = 0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def active_profile() -> str:
    """The active terrain, re-read from the env each call (an operator may switch between runs)
    and cached on STATE so the feedback/health surfaces report against the right profile."""
    STATE["profile"] = os.environ.get("FOIA_PROFILE", "foia_federal")
    return STATE["profile"]


def request_text() -> str:
    return open(os.path.join(DATA, "request.txt"), encoding="utf-8").read()


def next_doc_id() -> str:
    global _DOC_SEQ
    with LOCK:
        _DOC_SEQ += 1
        return f"doc-{_DOC_SEQ:04d}"


def ensure_pkg() -> ReviewPackage:
    if STATE["package"] is None:
        policy = exemptions.load_policy()
        STATE["package"] = ReviewPackage(request_id="req-001", request_text=request_text(),
                                         jurisdiction=policy.get("jurisdiction", "unknown"),
                                         created_at=now())
    return STATE["package"]


def new_job() -> dict:
    global _JOB_SEQ
    with LOCK:
        _JOB_SEQ += 1
        job = {"id": _JOB_SEQ, "status": "running", "total": 0, "done": 0,
               "order": [], "statuses": {}, "started": now()}
        STATE["job"] = job
    return job
