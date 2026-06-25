"""Per-document-type health monitor + failure-triggered review.

Runs implicitly inside the pipeline: every processed document is sorted into a type
(classifier.py) and an observation is recorded. When a type's signals breach thresholds, a
review of the *analyzing profile* is opened automatically — the user never has to watch
dashboards or file the ticket. Consistent with the system's stance, a failure OPENS A REVIEW
(an escalation that feeds the eval-gated promotion pipeline); it never silently changes the
profile.

Failure signals (model-independent where possible):
  novel_type     a document type with volume but no known signature -> no tuned handling
  high_escalation a type escalating almost always (only counted when the model was available)
  zero_coverage  a type the deterministic detectors find nothing in (low-severity hint)
  over_redaction profile override-rate too high in the officer feedback (precision)
  missed_pii     officers flagged missed PII (the dangerous direction; always opens a review)

  health/observations.jsonl   per-doc signals
  health/reviews.jsonl        opened reviews (the queue feeding promotion)
"""
import json
import os
import time
from collections import defaultdict

from . import feedback

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
H_DIR = os.path.join(ROOT, "health")
OBS = os.path.join(H_DIR, "observations.jsonl")
REVIEWS = os.path.join(H_DIR, "reviews.jsonl")

_DEFAULTS = {"min_unknown": 3, "esc_threshold": 0.95, "min_vol": 3,
             "max_override": 0.25, "min_feedback": 20}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append(path, rec):
    os.makedirs(H_DIR, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _read(path, profile=None):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if profile is None or r.get("profile") == profile:
                    out.append(r)
    return out


def observe(profile, doc_type, escalated, det_coverage, resp_conf, model_available):
    _append(OBS, {"ts": _now(), "profile": profile, "doc_type": doc_type,
                  "escalated": bool(escalated), "det_coverage": int(det_coverage),
                  "resp_conf": float(resp_conf), "model_available": bool(model_available)})


def summary(profile):
    """Per-type rollup for a profile (+ profile-level feedback)."""
    agg = defaultdict(lambda: {"n": 0, "escalated": 0, "n_model": 0, "esc_model": 0, "coverage0": 0})
    for r in _read(OBS, profile):
        a = agg[r["doc_type"]]
        a["n"] += 1
        a["escalated"] += r["escalated"]
        if r["det_coverage"] == 0:
            a["coverage0"] += 1
        if r["model_available"]:
            a["n_model"] += 1
            a["esc_model"] += r["escalated"]
    types = {}
    for t, a in agg.items():
        types[t] = {"n": a["n"],
                    "escalation_rate": (a["esc_model"] / a["n_model"]) if a["n_model"] else None,
                    "zero_coverage_rate": a["coverage0"] / a["n"] if a["n"] else 0.0,
                    "n_model_available": a["n_model"]}
    fb = feedback.metrics(profile).get(profile, {})
    return {"types": types,
            "feedback": {"override_rate": fb.get("override_rate"), "missed": fb.get("missed", 0),
                         "decided": fb.get("decided", 0)}}


def evaluate(profile, thresholds=None):
    """Return failure triggers: list of {type, severity, reasons[]}."""
    t = {**_DEFAULTS, **(thresholds or {})}
    s = summary(profile)
    triggers = []
    for typ, m in s["types"].items():
        reasons = []
        if typ == "unknown" and m["n"] >= t["min_unknown"]:
            reasons.append(f"{m['n']} docs of unrecognized type — no tuned profile handling")
        if (m["escalation_rate"] is not None and m["n_model_available"] >= t["min_vol"]
                and m["escalation_rate"] >= t["esc_threshold"]):
            reasons.append(f"escalation rate {m['escalation_rate']:.0%} (model available)")
        if m["n"] >= t["min_vol"] and m["zero_coverage_rate"] == 1.0:
            reasons.append("detectors find nothing in this type (zero coverage)")
        if reasons:
            sev = "high" if typ == "unknown" else "medium"
            triggers.append({"type": typ, "severity": sev, "reasons": reasons})
    fb = s["feedback"]
    prof_reasons = []
    if fb["decided"] >= t["min_feedback"] and (fb["override_rate"] or 0) > t["max_override"]:
        prof_reasons.append(f"officer override-rate {fb['override_rate']:.0%} (over-redaction)")
    if (fb["missed"] or 0) > 0:
        prof_reasons.append(f"{fb['missed']} officer-flagged missed PII (under-redaction)")
    if prof_reasons:
        triggers.append({"type": "*", "severity": "high", "reasons": prof_reasons})
    return triggers


def open_review(profile, doc_type, reasons, severity="medium"):
    """Open (or refresh) a review for (profile, doc_type). Deduped while status=open."""
    existing = [r for r in _read(REVIEWS, profile)
                if r["doc_type"] == doc_type and r["status"] == "open"]
    if existing:
        return False  # already open; the queue isn't spammed
    _append(REVIEWS, {"ts": _now(), "profile": profile, "doc_type": doc_type,
                      "severity": severity, "reasons": reasons, "status": "open",
                      "action": f"review profile '{profile}' for type '{doc_type}'; adjust + "
                                f"re-run `python -m evals.promote --profile {profile}`"})
    return True


def open_reviews(profile=None):
    seen, out = set(), []
    for r in _read(REVIEWS, profile):
        k = (r["profile"], r["doc_type"])
        if r["status"] == "open":
            seen.add(k)
        elif k in seen:
            continue
    # last status per (profile,type) wins
    latest = {}
    for r in _read(REVIEWS, profile):
        latest[(r["profile"], r["doc_type"])] = r
    return [r for r in latest.values() if r["status"] == "open"]


def evaluate_and_open(profile, thresholds=None):
    """Implicit driver: evaluate triggers and open reviews. Returns the newly opened ones."""
    opened = []
    for trig in evaluate(profile, thresholds):
        if open_review(profile, trig["type"], trig["reasons"], trig["severity"]):
            opened.append(trig)
    return opened


if __name__ == "__main__":
    import sys
    prof = sys.argv[2] if len(sys.argv) > 2 else "foia_federal"
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary":
        s = summary(prof)
        print(f"profile '{prof}':")
        for t, m in s["types"].items():
            er = f"{m['escalation_rate']:.0%}" if m["escalation_rate"] is not None else "n/a"
            print(f"  {t:16s} n={m['n']:4d} escalation={er} zero_cov={m['zero_coverage_rate']:.0%}")
        print(f"  feedback: {s['feedback']}")
    elif cmd == "reviews":
        for r in open_reviews(prof):
            print(f"  [{r['severity']}] {r['profile']}/{r['doc_type']}: {'; '.join(r['reasons'])}")
