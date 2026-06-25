"""Document intake and the agent-run job.

`intake_docs`/`intake_raw_docs` pull documents off the source pool into the package as RAW
(Unreviewed) records — fast, model-free. `run_agent_on` triages selected docs in place: the
slow model calls, with bounded concurrency and per-document status driving the UI queue
(DD-014). Nothing here is auto-released; output is always a *proposed* package.
"""
from concurrent.futures import ThreadPoolExecutor

from . import agents, classifier, detectors, exemptions, health, pipeline, runtime
from .schema import AuditEvent, DocReview

_INTAKE_SEQ = 0    # cycles the demo pool to simulate a continuous arrival of documents


def intake_docs(n, types):
    """Pull the next n documents off the (cycling) source pool — the continuous arrival of
    public-records documents. Each gets a unique filename so the stream never repeats an id."""
    pool = pipeline.load_docs(runtime.DOCS_DIR)
    if types:
        tset = set(types)
        pool = [(fn, t) for (fn, t) in pool if classifier.classify(t) in tset]
    if not pool:
        return []
    global _INTAKE_SEQ
    out = []
    for _ in range(n):
        fn, text = pool[_INTAKE_SEQ % len(pool)]
        _INTAKE_SEQ += 1
        base, _, ext = fn.rpartition(".")
        out.append((f"{base or fn}-{_INTAKE_SEQ:03d}.{ext or 'txt'}", text))
    return out


def intake_raw_docs(docs):
    """Append RAW (un-triaged) documents to the package — they land in Unreviewed, untouched
    by the agent. Fast: classification is the only work; no model is called here."""
    runtime.active_profile()
    with runtime.LOCK:
        pkg = runtime.ensure_pkg()
        added = []
        for fn, text in docs:
            rev = DocReview(doc_id=runtime.next_doc_id(), filename=fn, text=text, responsive=False,
                            responsive_confidence=0.0, responsive_rationale="", escalated=False,
                            doc_type=classifier.classify(text), agent_run=False)
            pkg.docs.append(rev)
            added.append(rev)
    return added


def run_agent_on(doc_ids, job):
    """Run the triage agent on the specified RAW documents, updating each in place. Per-doc
    status drives the UI; concurrency is bounded. This is where the (slow) model calls happen."""
    profile_name = runtime.active_profile()
    policy = exemptions.load_policy()
    request = runtime.request_text()
    compiled = detectors.compile_patterns(policy.get("detectors"))
    orch = agents.Orchestrator(policy, compiled, profile_name)
    model_available = agents.llm.available()
    pkg = runtime.STATE["package"]
    ids = list(doc_ids)
    with runtime.LOCK:
        job["model_available"] = model_available
        job["total"] = len(ids)
        job["order"] = ids
        job["statuses"] = {i: "queued" for i in ids}
    by_id = {d.doc_id: d for d in pkg.docs}

    def work(doc_id):
        d = by_id.get(doc_id)
        if d is None:
            return
        with runtime.LOCK:
            job["statuses"][doc_id] = "processing"
        try:
            rev = pipeline.process_document(orch, doc_id, d.filename, d.text, request, policy, pkg, compiled)
            rev.doc_type = classifier.classify(d.text)
            rev.agent_run = True
            det_cov = sum(1 for r in rev.redactions if r.detector.startswith("regex:"))
            health.observe(profile_name, rev.doc_type, rev.escalated, det_cov,
                           rev.responsive_confidence, model_available)
            with runtime.LOCK:
                idx = next((k for k, x in enumerate(pkg.docs) if x.doc_id == doc_id), None)
                if idx is not None:
                    pkg.docs[idx] = rev
                job["statuses"][doc_id] = "done"
                job["done"] += 1
        except Exception:  # noqa: BLE001 — isolate a bad doc; the batch continues
            with runtime.LOCK:
                job["statuses"][doc_id] = "error"
                job["done"] += 1

    with ThreadPoolExecutor(max_workers=2) as ex:   # fewer concurrent CLI calls -> fewer flakes
        list(ex.map(work, ids))

    for trig in health.evaluate_and_open(profile_name):
        pkg.audit.append(AuditEvent(runtime.now(), "monitor", "review_opened",
                                    {"type": trig["type"], "reasons": trig["reasons"]}))
    with runtime.LOCK:
        job["status"] = "done"
        job["finished"] = runtime.now()
