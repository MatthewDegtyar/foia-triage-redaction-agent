"""Officer-decision feedback loop.

The review console's accept / reject / modify / flag-missed actions are a continuous LABELED
stream — the officer is, in effect, hand-labeling the agent's output in the course of normal
work. This module persists those decisions and derives rolling error rates per terrain
profile, which the promotion gate reads:

  reject  on a proposed redaction  -> over-redaction        (precision signal / false positive)
  accept / modify                  -> confirmed             (true positive)
  flag-missed (PII the agent didn't catch) -> under-redaction (recall signal / false negative)

The recall-only gold set can't see over-redaction; this is the precision arm that complements
it, plus a live miss signal. Persisted as JSONL so metrics accumulate across sessions.
"""
import json
import os
import threading
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
STORE = os.path.join(ROOT, "feedback", "decisions.jsonl")
_LOCK = threading.Lock()

_LABEL_ACTIONS = ("accepted", "rejected", "modified", "missed")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record(profile, doc, action, detector=None, exemption=None, officer="officer",
           pii_type=None, text=None, prior_exemption=None):
    """Append one officer decision to the feedback log. `text` is the span the officer acted on
    (carry it on every correction so the runtime RAG can match it to new documents);
    `prior_exemption` is set on a reclassify (the exemption the redaction moved away from)."""
    rec = {"ts": _now(), "profile": profile or "unknown", "doc": doc, "action": action,
           "detector": detector, "exemption": exemption, "officer": officer,
           "pii_type": pii_type, "text": text, "prior_exemption": prior_exemption}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with _LOCK, open(STORE, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _read(profile=None):
    if not os.path.exists(STORE):
        return []
    rows = []
    with open(STORE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if profile is None or r.get("profile") == profile:
                rows.append(r)
    return rows


def _rates(c: dict) -> dict:
    decided = c["accepted"] + c["rejected"] + c["modified"]
    return {**c, "decided": decided,
            "override_rate": (c["rejected"] / decided) if decided else None,
            "confirm_rate": ((c["accepted"] + c["modified"]) / decided) if decided else None}


def metrics(profile=None) -> dict:
    """Rolling counts + override/confirm rates, keyed by profile (and by detector within)."""
    by_prof = defaultdict(lambda: {a: 0 for a in _LABEL_ACTIONS})
    by_det = defaultdict(lambda: defaultdict(lambda: {a: 0 for a in _LABEL_ACTIONS}))
    for r in _read(profile):
        a = r.get("action")
        if a not in _LABEL_ACTIONS:
            continue
        by_prof[r["profile"]][a] += 1
        by_det[r["profile"]][r.get("detector") or "(unknown)"][a] += 1
    out = {}
    for prof, c in by_prof.items():
        out[prof] = _rates(c)
        out[prof]["by_detector"] = {d: _rates(cc) for d, cc in by_det[prof].items()}
    return out


def labels(profile) -> dict:
    """Derived label set: confirmed (positive) and rejected (negative) spans, plus misses."""
    pos, neg, missed = [], [], []
    for r in _read(profile):
        if r.get("action") in ("accepted", "modified"):
            pos.append(r)
        elif r.get("action") == "rejected":
            neg.append(r)
        elif r.get("action") == "missed":
            missed.append(r)
    return {"positive": pos, "negative": neg, "missed": missed}


# The corrections the runtime RAG learns from: a span the officer had to REMOVE (rejected),
# ADD (missed), or RECLASSIFY. Only records that carry the span `text` are usable for matching.
_CORRECTION_ACTIONS = ("rejected", "missed", "reclassified")


def corrections(profile) -> list:
    """Officer corrections (newest last) that carry a span — the source for `recall_corrections`."""
    return [r for r in _read(profile)
            if r.get("action") in _CORRECTION_ACTIONS and (r.get("text") or "").strip()]


if __name__ == "__main__":
    import sys
    prof = sys.argv[1] if len(sys.argv) > 1 else None
    m = metrics(prof)
    if not m:
        print("no feedback recorded yet.")
    for p, c in m.items():
        orr = f"{c['override_rate']:.1%}" if c["override_rate"] is not None else "n/a"
        print(f"{p}: decided={c['decided']} accepted={c['accepted']} rejected={c['rejected']} "
              f"modified={c['modified']} missed={c['missed']}  override_rate={orr}")
        for d, cc in c["by_detector"].items():
            o = f"{cc['override_rate']:.0%}" if cc["override_rate"] is not None else "n/a"
            print(f"    {d:22s} decided={cc['decided']:3d} override={o} missed={cc['missed']}")
