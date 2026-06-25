"""FastAPI surface for the FOIA review console — route handlers only.

The heavier concerns live in focused modules: shared runtime state + primitives in
`runtime.py`, document intake and the agent-run job in `intake.py`, demo boot staging in
`seeding.py`, and request bodies in `api_models.py`. This file wires those into HTTP.

Endpoints:
  GET  /                 -> the officer review console (+ /app.js, /styles.css)
  GET  /api/docs         -> source pool + document types in the stream
  POST /api/intake       -> bring in the next batch of RAW documents (land Unreviewed)
  POST /api/run_agent    -> triage selected/all Unreviewed docs (async; poll /api/status)
  GET  /api/status       -> live job progress + the accumulated package
  GET  /api/package      -> current package
  GET  /api/examples     -> curated redaction corpus (criteria + before/after)
  POST /api/decision     -> officer accepts/rejects/modifies one redaction (audited)
  POST /api/reclassify   -> change the exemption a redaction is withheld under
  POST /api/add_redaction-> officer highlight-to-redact (recorded as a missed-PII signal)
  POST /api/flag_missed  -> officer flags PII the agent missed (recall signal)
  POST /api/resolve      -> officer resolves a doc's responsiveness (clears an escalation)
  GET  /api/feedback     -> rolling officer-decision error rates (promotion gate)
  GET  /api/health       -> implicit per-type health + auto-opened reviews
  POST /api/produce      -> apply accepted redactions, return redacted docs + log (fails closed)
"""
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from . import (classifier, context_layer, examples, exemptions, feedback, health, intake,
               pipeline, runtime, seeding)
from .api_models import (AddRedaction, Decision, IntakeReq, Missed, Reclassify, Resolve, RunReq)
from .schema import AuditEvent, Redaction

app = FastAPI(title="FOIA Triage & Redaction")

# Never cache the console bundle — fast-iterating demo; a stale asset silently mis-renders
# state (e.g. raw docs categorized by an old reviewState). No etag, so no 304 reuse.
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
             "Pragma": "no-cache", "Expires": "0"}


def _asset(name: str, media_type: str) -> Response:
    content = open(os.path.join(runtime.CONSOLE_DIR, name), encoding="utf-8").read()
    return Response(content, media_type=media_type, headers=_NO_CACHE)


def _pkg():
    """The current package or a 404 — every officer action needs one."""
    pkg = runtime.STATE["package"]
    if pkg is None:
        raise HTTPException(404, "no package")
    return pkg


def _doc(pkg, doc_id):
    """Locate a document in the package or a 400."""
    doc = next((x for x in pkg.docs if x.doc_id == doc_id), None)
    if doc is None:
        raise HTTPException(400, "bad doc")
    return doc


@app.get("/")
def console():
    return HTMLResponse(open(os.path.join(runtime.CONSOLE_DIR, "index.html"), encoding="utf-8").read(),
                        headers=_NO_CACHE)


@app.get("/app.js")
def app_js():
    return _asset("app.js", "application/javascript")


@app.get("/styles.css")
def styles_css():
    return _asset("styles.css", "text/css")


# ── intake + triage ─────────────────────────────────────────────────────────────
@app.get("/api/docs")
def list_docs():
    """The source pool + the document types available to stream (drives the type selector)."""
    out, types = [], {}
    for name in sorted(os.listdir(runtime.DOCS_DIR)):
        p = os.path.join(runtime.DOCS_DIR, name)
        if os.path.isfile(p) and not name.startswith("."):
            text = pipeline.read_doc(p)
            t = classifier.classify(text)
            types[t] = types.get(t, 0) + 1
            out.append({"filename": name, "doc_type": t})
    return {"documents": out, "types": sorted(types)}


@app.post("/api/intake")
def api_intake(req: IntakeReq | None = None):
    """Bring in the next batch of RAW documents from the stream (instant; they land Unreviewed)."""
    n = max(1, min(12, req.n if req else 3))
    added = intake.intake_raw_docs(intake.intake_docs(n, req.types if req else None))
    if added:   # officer brought records into the package — part of the administrative record
        runtime.STATE["package"].audit.append(AuditEvent(
            runtime.now(), "officer", "documents_intake",
            {"count": len(added), "doc_ids": [d.doc_id for d in added],
             "filenames": [d.filename for d in added],
             "types": req.types if req else None}))
    return {"intake": len(added)}


@app.post("/api/run_agent")
def run_agent(req: RunReq | None = None):
    """Run the triage agent on selected (or all) Unreviewed documents — async; poll /api/status."""
    pkg = _pkg()
    want = set(req.doc_ids) if (req and req.doc_ids) else None
    ids = [d.doc_id for d in pkg.docs if not d.agent_run and (want is None or d.doc_id in want)]
    if not ids:
        return {"job_id": None, "running": 0}
    job = runtime.new_job()
    # log the officer's decision to triage these documents (the agent's own steps are logged separately)
    pkg.audit.append(AuditEvent(runtime.now(), "officer", "agent_run_requested",
                                {"count": len(ids), "doc_ids": ids,
                                 "scope": "selected" if want is not None else "all_unreviewed"}))
    threading.Thread(target=intake.run_agent_on, args=(ids, job), daemon=True).start()
    return {"job_id": job["id"], "running": len(ids)}


@app.get("/api/status")
def status():
    """Live job progress + the accumulated package (per-doc statuses drive the UI queue)."""
    for _ in range(4):   # package lists may be mutated by workers mid-serialize; retry
        try:
            with runtime.LOCK:
                job = dict(runtime.STATE.get("job") or {})
                pkg = runtime.STATE["package"].to_dict() if runtime.STATE["package"] else None
            return {"job": job, "package": pkg}
        except RuntimeError:
            continue
    return {"job": dict(runtime.STATE.get("job") or {}), "package": None}


@app.get("/api/package")
def get_package():
    return _pkg().to_dict()


@app.get("/api/examples")
def get_examples():
    """The curated redaction corpus — criteria + before/after, including challenging cases."""
    return examples.corpus()


# ── support layers (viewable; the same content the agent reasons over at runtime) ─────────
@app.get("/api/guidelines")
def get_guidelines():
    """GUIDELINES — how we redact: the rule files for the active terrain + worked before/after cases."""
    prof = runtime.STATE.get("profile")
    return {"profile": prof, "guidelines": context_layer.list_items(prof, "guideline"),
            "examples": examples.corpus()}


@app.get("/api/corpus")
def get_corpus():
    """CORPUS — the regulations/authorities the agent references for the active terrain."""
    prof = runtime.STATE.get("profile")
    return {"profile": prof, "corpus": context_layer.list_items(prof, "regulation")}


@app.get("/api/learned")
def get_learned():
    """LEARNED — officer corrections the agent now tracks + RAGs against (removed/added/reclassified)."""
    prof = runtime.STATE.get("profile")
    out = [{"action": r["action"], "text": r.get("text"), "exemption": r.get("exemption"),
            "prior_exemption": r.get("prior_exemption"), "detector": r.get("detector"),
            "ts": r.get("ts"), "doc": r.get("doc")}
           for r in reversed(feedback.corrections(prof))]   # newest first
    return {"profile": prof, "corrections": out, "count": len(out)}


# ── officer actions ──────────────────────────────────────────────────────────────
@app.post("/api/decision")
def decision(d: Decision):
    pkg = _pkg()
    doc = _doc(pkg, d.doc_id)
    if d.redaction_index >= len(doc.redactions):
        raise HTTPException(400, "bad redaction")
    red = doc.redactions[d.redaction_index]
    red.status = d.action
    red.decided_by = d.officer
    red.decided_at = runtime.now()
    if d.action == "modified" and d.new_text is not None:
        red.text = d.new_text
    pkg.audit.append(AuditEvent(runtime.now(), d.officer, f"redaction_{d.action}",
                                {"doc": d.doc_id, "exemption": red.exemption, "idx": d.redaction_index}))
    # feed the live label stream (precision/over-redaction signal + the RAG source);
    # carry the span text so a 'rejected' (officer removed it) can be matched to future docs
    feedback.record(runtime.STATE.get("profile"), d.doc_id, d.action, detector=red.detector,
                    exemption=red.exemption, officer=d.officer, text=red.text)
    return {"ok": True}


@app.post("/api/reclassify")
def reclassify(rq: Reclassify):
    """Officer changes the exemption a redaction is withheld under (b6 / b5 / b7E / b4)."""
    pkg = _pkg()
    doc = _doc(pkg, rq.doc_id)
    if rq.redaction_index >= len(doc.redactions):
        raise HTTPException(400, "bad redaction")
    policy = exemptions.load_policy()
    r = doc.redactions[rq.redaction_index]
    prior = r.exemption                       # capture before overwrite — it's the correction
    r.exemption = rq.exemption
    r.basis = exemptions.basis_for(rq.exemption, policy)
    pkg.audit.append(AuditEvent(runtime.now(), "officer", "redaction_reclassified",
                                {"doc": rq.doc_id, "idx": rq.redaction_index,
                                 "from": prior, "exemption": rq.exemption}))
    # a reclassify is a correction the runtime RAG learns from (this span is {new} not {prior})
    feedback.record(runtime.STATE.get("profile"), rq.doc_id, "reclassified", detector=r.detector,
                    exemption=rq.exemption, prior_exemption=prior, text=r.text)
    return {"ok": True}


@app.post("/api/add_redaction")
def add_redaction(a: AddRedaction):
    """Officer manually flags additional text to withhold (highlight-to-redact in the modal).
    Added as an already-accepted redaction, audited, and recorded as a recall (missed-PII)
    signal so the feedback loop learns where the agent under-redacted."""
    pkg = _pkg()
    doc = _doc(pkg, a.doc_id)
    if not a.text or a.end <= a.start:
        raise HTTPException(400, "bad selection")
    policy = exemptions.load_policy()
    # a highlight candidate until the doc is marked responsive (then it's applied/blacked)
    status_ = "accepted" if doc.officer_responsive is True else "proposed"
    doc.redactions.append(Redaction(
        start=a.start, end=a.end, text=a.text, pii_type="manual", exemption=a.exemption,
        basis=exemptions.basis_for(a.exemption, policy), detector="officer", confidence=1.0,
        rationale="Manually flagged by the reviewing officer.", status=status_,
        decided_by=a.officer, decided_at=runtime.now()))
    pkg.audit.append(AuditEvent(runtime.now(), a.officer, "redaction_added",
                                {"doc": a.doc_id, "exemption": a.exemption, "text": a.text}))
    feedback.record(runtime.STATE.get("profile"), a.doc_id, "missed", exemption=a.exemption,
                    officer=a.officer, pii_type="manual", text=a.text)
    return {"ok": True}


@app.post("/api/flag_missed")
def flag_missed(m: Missed):
    """Officer flags PII the agent MISSED — the recall (false-negative) signal the gold set
    can't supply live. Recorded to the feedback stream and the audit log."""
    pkg = _pkg()
    feedback.record(runtime.STATE.get("profile"), m.doc_id, "missed", exemption=m.exemption,
                    officer=m.officer, pii_type=m.pii_type, text=m.text)
    pkg.audit.append(AuditEvent(runtime.now(), m.officer, "redaction_missed",
                                {"doc": m.doc_id, "pii_type": m.pii_type, "text": m.text}))
    return {"ok": True}


@app.post("/api/resolve")
def resolve(rq: Resolve):
    """Officer resolves a doc's responsiveness (clears an escalation). The agent advises;
    the human determines — and only a determined doc can ever be released."""
    pkg = _pkg()
    doc = _doc(pkg, rq.doc_id)
    doc.officer_responsive = rq.responsive
    doc.escalated = False
    pkg.audit.append(AuditEvent(runtime.now(), rq.officer, "responsiveness_resolved",
                                {"doc": rq.doc_id, "responsive": rq.responsive}))
    return {"ok": True}


# ── monitoring surfaces ──────────────────────────────────────────────────────────
@app.get("/api/feedback")
def get_feedback():
    """Rolling officer-decision error rates for the active profile (feeds the promotion gate)."""
    return feedback.metrics(runtime.STATE.get("profile"))


@app.get("/api/health")
def get_health():
    """Implicit per-type health for the active profile, plus any auto-opened reviews. The
    document sort and the failure triggers run during processing — this just surfaces them."""
    prof = runtime.STATE.get("profile")
    return {"profile": prof, "by_type": health.summary(prof)["types"],
            "open_reviews": health.open_reviews(prof)}


# ── release gate ─────────────────────────────────────────────────────────────────
def _decided_responsive(doc) -> bool | None:
    """True/False = determined; None = still unresolved (must not be released)."""
    if doc.officer_responsive is not None:
        return doc.officer_responsive
    if doc.escalated:
        return None
    return doc.responsive


@app.post("/api/produce")
def produce():
    pkg = _pkg()

    # Fail CLOSED. Production is blocked if anything is unresolved or unreviewed, and
    # when blocked we produce nothing and log a block — never a release.
    unresolved = [d.doc_id for d in pkg.docs if _decided_responsive(d) is None]
    open_redactions = {
        d.doc_id: [i for i, r in enumerate(d.redactions) if r.status == "proposed"]
        for d in pkg.docs if _decided_responsive(d) is True
    }
    blocked_docs = [doc_id for doc_id, idxs in open_redactions.items() if idxs]

    if unresolved or blocked_docs:
        pkg.audit.append(AuditEvent(runtime.now(), "officer", "production_blocked",
                                    {"unresolved_responsiveness": unresolved,
                                     "docs_with_open_redactions": blocked_docs}))
        raise HTTPException(status_code=409, detail={
            "error": "release_blocked",
            "message": "Nothing was produced. Resolve every escalation and review every "
                       "redaction on releasable documents before release.",
            "unresolved_responsiveness": unresolved,
            "docs_with_open_redactions": blocked_docs,
        })

    # Cleared: apply accepted/modified redactions on releasable docs only.
    out = []
    for doc in pkg.docs:
        if _decided_responsive(doc) is not True:
            continue
        applied = [r for r in doc.redactions if r.status in ("accepted", "modified")]
        text = doc.text
        for r in sorted(applied, key=lambda r: r.start, reverse=True):
            text = text[:r.start] + f"[REDACTED:{r.exemption}]" + text[r.end:]
        out.append({
            "filename": doc.filename,
            "redacted_text": text,
            "redaction_log": [
                {"exemption": r.exemption, "basis": r.basis, "rationale": r.rationale,
                 "detector": r.detector, "confidence": r.confidence, "decided_by": r.decided_by}
                for r in applied
            ],
        })
    pkg.audit.append(AuditEvent(runtime.now(), "officer", "produced_package", {"docs": len(out)}))
    return {"documents": out, "released": len(out), "cleared_for_release": True}


# ── boot ─────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def _seed_on_startup():
    seeding.seed_on_startup()
