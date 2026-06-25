"""API integration tests — drive the real FastAPI app through TestClient.

Each `with client()` fires the startup seed (deterministic, against isolated state), so these
validate the whole stack: boot arrangement, the support-layer endpoints, officer actions →
audit + feedback, and the fail-closed release gate (both directions).
"""
from collections import Counter

from tests.harness import client


def _bucket(doc):
    if not doc["agent_run"]:
        return "unreviewed"
    if doc["officer_responsive"] is True:
        return "cleared"
    if doc["officer_responsive"] is False:
        return "nonresponsive"
    return "attention"


def test_boot_lands_in_medias_res():
    with client() as c:
        docs = c.get("/api/status").json()["package"]["docs"]
        assert len(docs) == 9
        b = Counter(_bucket(d) for d in docs)
        assert (b["unreviewed"], b["attention"], b["cleared"], b["nonresponsive"]) == (4, 3, 1, 1), dict(b)


def test_support_layer_endpoints():
    with client() as c:
        corpus = c.get("/api/corpus").json()
        assert corpus["corpus"] and any("5.2" in r["title"] for r in corpus["corpus"])
        guidelines = c.get("/api/guidelines").json()
        assert guidelines["guidelines"] and guidelines["examples"]["examples"]
        learned = c.get("/api/learned").json()
        assert "corrections" in learned and "count" in learned


def test_intake_adds_docs_and_audits():
    with client() as c:
        before = len(c.get("/api/status").json()["package"]["docs"])
        assert c.post("/api/intake", json={"n": 2}).json()["intake"] == 2
        d = c.get("/api/status").json()["package"]
        assert len(d["docs"]) == before + 2
        assert any(a["action"] == "documents_intake" for a in d["audit"])


def test_run_agent_is_audited():
    with client() as c:
        unrev = next(x for x in c.get("/api/status").json()["package"]["docs"] if not x["agent_run"])
        r = c.post("/api/run_agent", json={"doc_ids": [unrev["doc_id"]]}).json()
        assert r["running"] == 1
        audit = c.get("/api/status").json()["package"]["audit"]
        assert any(a["action"] == "agent_run_requested" for a in audit)


def test_decision_records_audit_and_feedback():
    from backend import feedback
    with client() as c:
        doc = next(x for x in c.get("/api/status").json()["package"]["docs"]
                   if x["agent_run"] and x["officer_responsive"] is None and x["redactions"])
        text = doc["redactions"][0]["text"]
        c.post("/api/decision", json={"doc_id": doc["doc_id"], "redaction_index": 0, "action": "rejected"})
        audit = c.get("/api/status").json()["package"]["audit"]
        assert any(a["action"] == "redaction_rejected" for a in audit)
        rejected = [r for r in feedback.corrections("uscourts") if r["action"] == "rejected"]
        assert any(r["text"] == text for r in rejected), "reject must carry the span text"


def test_reclassify_records_correction_with_prior():
    from backend import feedback
    with client() as c:
        doc = next(x for x in c.get("/api/status").json()["package"]["docs"]
                   if x["agent_run"] and x["redactions"])
        prior = doc["redactions"][0]["exemption"]
        new = "b7C" if prior != "b7C" else "b6"
        c.post("/api/reclassify", json={"doc_id": doc["doc_id"], "redaction_index": 0, "exemption": new})
        recl = [r for r in feedback.corrections("uscourts") if r["action"] == "reclassified"]
        assert recl and recl[-1]["prior_exemption"] == prior and recl[-1]["exemption"] == new


def test_produce_fails_closed_then_releases():
    with client() as c:
        # boot has responsive docs with open (proposed) redactions -> production blocked
        blocked = c.post("/api/produce")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["docs_with_open_redactions"]
        # review every proposed redaction on every doc, then production opens
        for d in c.get("/api/status").json()["package"]["docs"]:
            for i, r in enumerate(d["redactions"]):
                if r["status"] == "proposed":
                    c.post("/api/decision", json={"doc_id": d["doc_id"], "redaction_index": i, "action": "accepted"})
        ok = c.post("/api/produce")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["cleared_for_release"] and body["released"] >= 1
        # every released doc's redactions are burned in
        assert all("[REDACTED:" in doc["redacted_text"] or not doc["redaction_log"]
                   for doc in body["documents"])
