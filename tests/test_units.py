"""Unit tests — the safety-critical layers, exercised directly (no HTTP, no model).

State is isolated per test by the runner (tests/harness.isolate): feedback/memory/context point
at a temp dir and the active profile is `uscourts`.
"""
from backend import (classifier, context_layer, detectors, exemptions, feedback, memory,
                     pipeline, seeding)
from backend.schema import ReviewPackage


def test_detectors_compile_and_catch():
    pol = exemptions.load_policy()
    comp = detectors.compile_patterns(pol.get("detectors"))
    assert len(comp) == len(pol["detectors"]), "every detector pattern should compile"

    def types(s):
        return {h.pii_type for h in detectors.detect(s, comp)}
    assert "ssn" in types("His SSN is 123-45-6789.")
    assert "email" in types("write to jane.doe@example.com please")
    assert "phone" in types("call 512-555-0143 anytime")
    assert "street_address" in types("lives at 4821 Limestone Ridge Road, Austin")
    assert "inmate_no" in types("Pro Se DC#162841 Lowell")
    # the phone-span fix: leading paren is now captured
    phones = [h.text for h in detectors.detect("fax (847) 939-2116 today", comp) if h.pii_type == "phone"]
    assert phones == ["(847) 939-2116"], phones


def test_detectors_no_false_positives_on_court_text():
    pol = exemptions.load_policy()
    comp = detectors.compile_patterns(pol.get("detectors"))
    for neg in ["Case No. 16-cv-61177-BLOOM is before the Court.",
                "THIS CAUSE under 28 U.S.C. § 2254 for habeas relief.",
                "Motion, ECF No. [21], filed on May 9, 2022.",
                "Entered on FLSD Docket 03/27/2023 Page 1 of 2",
                "PageID12025551234 is an internal id"]:
        assert not detectors.detect(neg, comp), f"false positive on: {neg!r}"


def test_classifier_signatures():
    assert classifier.classify("UNITED STATES DISTRICT COURT\nORDER ADOPTING REPORT") == "court_judgment"
    assert classifier.classify("From: a@b.gov\nTo: team\nSubject: hi") == "email"
    assert classifier.classify("nothing structural here at all") == "unknown"


def test_context_layer_guidelines_vs_corpus():
    regs = context_layer.list_items("uscourts", "regulation")
    guides = context_layer.list_items("uscourts", "guideline")
    assert regs and all(r["kind"] == "regulation" for r in regs)
    assert guides and all(g["kind"] == "guideline" for g in guides)
    assert any("5.2" in r["title"] for r in regs), "FRCP 5.2 should be in the corpus"
    reg_ctx = context_layer.retrieve("uscourts", None, kind="regulation")
    guide_ctx = context_layer.retrieve("uscourts", None, kind="guideline")
    assert reg_ctx and guide_ctx and reg_ctx != guide_ctx, "kinds must inject distinct content"


def test_memory_corrections_rag_relevance():
    feedback.record("uscourts", "d", "rejected", exemption="b6", text="Globex Industries")
    fired = memory.recall_corrections("uscourts", "The claim by Globex Industries was sustained.")
    assert "Globex Industries" in fired and "RELEASED" in fired
    assert memory.recall_corrections("uscourts", "A rooftop solar permit in Cedar Park.") == ""


def test_feedback_records_all_correction_kinds():
    feedback.record("uscourts", "d1", "rejected", detector="agent:privacy", exemption="b6", text="X Corp")
    feedback.record("uscourts", "d2", "missed", exemption="b6", text="Jane Roe")
    feedback.record("uscourts", "d3", "reclassified", exemption="b7C", prior_exemption="b6", text="John Poe")
    corr = feedback.corrections("uscourts")
    assert len(corr) == 3 and all(c["text"] for c in corr)
    recl = next(c for c in corr if c["action"] == "reclassified")
    assert recl["prior_exemption"] == "b6"
    assert feedback.metrics("uscourts")["uscourts"]["rejected"] == 1


def test_pipeline_deterministic_detector_redactions():
    pol = exemptions.load_policy()
    comp = detectors.compile_patterns(pol.get("detectors"))
    from backend import agents
    orch = agents.Orchestrator(pol, comp, "uscourts")
    pkg = ReviewPackage(request_id="t", request_text="req", jurisdiction="", created_at="")
    doc = "NOTICE. Serve counsel at jane.doe@example.com regarding the default."
    rev = pipeline.process_document(orch, "d0", "f.txt", doc, "req", pol, pkg, comp)
    assert rev.escalated, "no model -> responsiveness must escalate (fail-safe)"
    emails = [r for r in rev.redactions if r.detector == "regex:email"]
    assert emails and emails[0].text == "jane.doe@example.com"


def test_seeding_party_extraction_skips_orgs():
    pol = exemptions.load_policy()
    doc = ("UNITED STATES DISTRICT COURT\nJOHN DOE,\nPetitioner,\nv.\n"
           "STATE OF FLORIDA,\nRespondent.")
    names = {r.text for r in seeding.court_party_redactions(doc, pol)}
    assert "JOHN DOE" in names
    assert "STATE OF FLORIDA" not in names, "organizations must not be flagged as private"
