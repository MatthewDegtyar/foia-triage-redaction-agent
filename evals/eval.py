"""Eval harness.

Headline metric is RECALL on private-individual PII, because the catastrophic FOIA
failure is a false negative — releasing an SSN. We report recall first and name every
miss, because one miss is a disclosure incident, not a percentage point.

This runs with NO API key: the deterministic detectors are what guard the
safety-critical class, so the safety metric is reproducible offline. Responsiveness
and contextual-exemption metrics need a model; without a key they're reported as
"deferred (fail-safe escalation)", which is the correct safe behavior, not a failure.

    python -m evals.eval
"""
import json
import os

from backend import detectors, exemptions, llm, pipeline

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GT = json.load(open(os.path.join(HERE, "ground_truth.json")))


def _doc_text(name: str) -> str:
    return open(os.path.join(ROOT, "data", "docs", name)).read()


def _covered(hits, target: str) -> bool:
    return any(target in h.text or h.text in target for h in hits)


def eval_pii_recall():
    by_doc = {}
    for item in GT["must_redact"]:
        by_doc.setdefault(item["doc"], []).append(item)

    total = len(GT["must_redact"])
    caught, misses = 0, []
    for doc, items in by_doc.items():
        hits = detectors.detect(_doc_text(doc))
        for it in items:
            if _covered(hits, it["text"]):
                caught += 1
            else:
                misses.append(it)

    recall = caught / total if total else 1.0
    print("=" * 64)
    print("SAFETY-CRITICAL METRIC — PII recall (false negatives = disclosure)")
    print("=" * 64)
    print(f"  recall: {caught}/{total} = {recall:.1%}")
    if misses:
        print("  !! MISSED (each is a disclosure incident):")
        for m in misses:
            print(f"     - {m['doc']}: {m['kind']} '{m['text']}'")
    else:
        print("  no misses — every known private identifier was flagged.")
    return recall, misses


def eval_overflag():
    # Over-flagging is acceptable by design (officer prunes). Report it as a cost, not a fail.
    print("\nOVER-FLAG behavior (acceptable; officer/policy prunes official contact info)")
    for item in GT["overflag_ok"]:
        hits = detectors.detect(_doc_text(item["doc"]))
        flagged = _covered(hits, item["text"])
        print(f"  {item['doc']}: official '{item['text']}' flagged={flagged} "
              f"({'expected over-catch' if flagged else 'not flagged'})")


def eval_llm_dependent():
    print("\nRESPONSIVENESS / CONTEXTUAL EXEMPTIONS / ESCALATION")
    if not llm.available():
        print("  model unavailable -> all judgment calls deferred to human (fail-safe).")
        print("  set ANTHROPIC_API_KEY or log in to Claude Code to score responsiveness + escalation.")
        return
    transport = "ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else "Claude Code CLI (logged-in)"
    print(f"  transport: {transport}")
    policy = exemptions.load_policy()
    request = open(os.path.join(ROOT, "data", "request.txt")).read()
    docs = pipeline.load_docs(os.path.join(ROOT, "data", "docs"))
    pkg = pipeline.run(request, docs, policy)
    correct = 0
    for d in pkg.docs:
        want = GT["responsiveness"][d.filename]
        got = "escalate" if d.escalated else d.responsive
        ok = (want == "escalate" and d.escalated) or (want is True and d.responsive and not d.escalated) \
            or (want is False and not d.responsive and not d.escalated)
        correct += ok
        print(f"  {d.filename}: want={want} got responsive={d.responsive} escalate={d.escalated} {'OK' if ok else 'X'}")
    print(f"  responsiveness/escalation: {correct}/{len(pkg.docs)}")


if __name__ == "__main__":
    recall, misses = eval_pii_recall()
    eval_overflag()
    eval_llm_dependent()
    print("\nGATE:", "PASS — zero PII misses" if not misses else f"FAIL — {len(misses)} disclosure miss(es)")
