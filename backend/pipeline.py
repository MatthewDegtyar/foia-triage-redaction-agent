"""The FOIA triage + redaction pipeline, driven by the agent orchestrator.

Per document:
  deterministic PII detection (safety-critical, model-free)  +
  orchestrator: responsiveness agent, concurrent exemption specialists, reconciler
  -> review package with per-agent provenance and a full audit trail.

Nothing is ever auto-released. Output is a *proposed* package for a human officer.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from . import agents, classifier, detectors, exemptions, health
from .schema import AuditEvent, DocReview, Redaction, ReviewPackage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── PDF intake ────────────────────────────────────────────────────────────────
# Real corpus = born-digital / OCR'd government PDFs (federal court records from
# govinfo's USCOURTS collection). We extract the text layer and run the SAME text
# pipeline — redaction offsets are character offsets into this extracted text, so
# nothing downstream changes. Extraction prefers poppler's `pdftotext` (best layout
# fidelity) and falls back to pure-Python `pypdf` so a fresh clone works without
# system deps.
_PDF_EXTS = (".pdf",)
# One-line CM/ECF docket header stamped on every page: "Case 0:16-cv-… Document 118
# Entered on FLSD Docket 03/27/2023 Page 1 of 2" — boilerplate, not record content.
_ECF_HEADER = re.compile(r"^\s*Case\s+\S+\s+Document\s+\d+.*\bPage\s+\d+\s+of\s+\d+\s*$", re.I)
_PAGE_MARK = re.compile(r"^\s*(?:Page\s+\d+\s+of\s+\d+|\d{1,3})\s*$", re.I)  # margin line/page numbers


def _raw_pdf_text(path: str) -> str:
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-nopgbrk", path, "-"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(f"cannot extract text from {path}: install poppler (pdftotext) or pypdf")


def _clean_court_text(txt: str) -> str:
    """Strip CM/ECF header stamps and stray margin numbers so the released text reads
    like the record, not the docketing chrome. Conservative: only drops boilerplate."""
    out = []
    for line in txt.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if _ECF_HEADER.match(s) or _PAGE_MARK.match(s):
            continue
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def extract_pdf_text(path: str) -> str:
    return _clean_court_text(_raw_pdf_text(path))


def read_doc(path: str) -> str:
    """Read a source document as text — extracting from PDF when needed."""
    if path.lower().endswith(_PDF_EXTS):
        return extract_pdf_text(path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def process_document(orch, doc_id, filename, text, request, policy, pkg, compiled=None) -> DocReview:
    threshold = float(policy.get("escalation_threshold", 0.65))
    det_hits = detectors.detect(text, compiled)

    def audit(actor, **detail):
        pkg.audit.append(AuditEvent(_now(), actor, "agent_step", {"doc": doc_id, **detail}))

    resp, proposals, escalate, notes = orch.review_document(text, request, threshold, len(det_hits), audit)

    review = DocReview(
        doc_id=doc_id, filename=filename, text=text,
        responsive=resp.responsive, responsive_confidence=resp.confidence,
        responsive_rationale="; ".join([resp.rationale] + notes) if notes else resp.rationale,
        escalated=escalate,
    )

    if not resp.responsive and not escalate:
        pkg.audit.append(AuditEvent(_now(), "orchestrator", "skip_nonresponsive", {"doc": doc_id}))
        return review

    # deterministic PII (the safety-critical catch)
    for h in det_hits:
        review.redactions.append(Redaction(
            start=h.start, end=h.end, text=h.text, pii_type=h.pii_type, exemption=h.exemption,
            basis=exemptions.basis_for(h.exemption, policy), detector=h.detector,
            confidence=h.confidence, rationale=f"Deterministic {h.pii_type} pattern match."))

    # contextual exemptions proposed by the specialist agents
    for p in proposals:
        span = exemptions.find_span(text, p.quote)
        if span is None:
            continue
        review.redactions.append(Redaction(
            start=span[0], end=span[1], text=p.quote, pii_type="contextual", exemption=p.exemption,
            basis=exemptions.basis_for(p.exemption, policy), detector=p.agent,
            confidence=p.confidence, rationale=p.rationale))

    pkg.audit.append(AuditEvent(_now(), "orchestrator", "redactions_proposed",
                                {"doc": doc_id, "count": len(review.redactions)}))
    return review


def run(request, docs, policy, request_id="req-001") -> ReviewPackage:
    pkg = ReviewPackage(request_id=request_id, request_text=request,
                        jurisdiction=policy.get("jurisdiction", "unknown"), created_at=_now())
    pkg.audit.append(AuditEvent(_now(), "orchestrator", "intake",
                                {"request_id": request_id, "doc_count": len(docs),
                                 "jurisdiction": policy.get("jurisdiction", "unknown"),
                                 "model_available": agents.llm.available()}))
    profile_name = os.environ.get("FOIA_PROFILE", "foia_federal")
    compiled = detectors.compile_patterns(policy.get("detectors"))
    orch = agents.Orchestrator(policy, compiled, profile_name)
    model_available = agents.llm.available()
    for i, (filename, text) in enumerate(docs):
        review = process_document(orch, f"doc-{i:03d}", filename, text, request, policy, pkg, compiled)
        review.doc_type = classifier.classify(text)          # implicit sort into a type
        det_cov = sum(1 for r in review.redactions if r.detector.startswith("regex:"))
        health.observe(profile_name, review.doc_type, review.escalated, det_cov,
                       review.responsive_confidence, model_available)
        pkg.docs.append(review)

    # implicit, failure-triggered review of the analyzing profile — no user action needed
    counts: dict = {}
    for d in pkg.docs:
        counts[d.doc_type] = counts.get(d.doc_type, 0) + 1
    pkg.audit.append(AuditEvent(_now(), "monitor", "types_sorted", counts))
    for trig in health.evaluate_and_open(profile_name):
        pkg.audit.append(AuditEvent(_now(), "monitor", "review_opened",
                                    {"type": trig["type"], "severity": trig["severity"],
                                     "reasons": trig["reasons"]}))
    return pkg


def load_docs(docs_dir):
    out = []
    for name in sorted(os.listdir(docs_dir)):
        path = os.path.join(docs_dir, name)
        if os.path.isfile(path) and not name.startswith("."):
            out.append((name, read_doc(path)))
    return out
