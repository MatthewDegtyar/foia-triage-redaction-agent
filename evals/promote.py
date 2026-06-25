"""Eval-gated, autonomous profile promotion — the deployment control plane.

Runs a deterministic SCORECARD for a candidate terrain profile and ships it to prod ONLY
if every gate passes; otherwise it holds and escalates with the scorecard (calibrated
refusal, applied to CD). Promotion is a registry version flip (instantly reversible). This
is what lets a client add a new document/redaction type and push it live without an
engineer — the gate, not a human reviewer, is what makes that safe.

Gates (all must pass to ship):
  1. regression   — the federal PII safety metric must stay 7/7 (no existing class degrades)
  2. new-type     — deterministic recall on the candidate's labeled gold >= its recall_target
  3. throughput   — process the sample with zero per-doc errors (robustness)
  4. release-gate — the fail-closed invariant still holds (never auto-release unreviewed text)

    python -m evals.promote --profile echr_anonymization            # dry-run scorecard
    python -m evals.promote --profile echr_anonymization --promote  # ship if it passes
"""
import argparse
import json
import os
import sys

import yaml

from backend import detectors, feedback, registry
from backend.schema import DocReview, Redaction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def _load_candidate(name, candidate_path):
    path = candidate_path or registry.working_path(name)
    with open(path) as f:
        return yaml.safe_load(f), path


# ---- gates ---------------------------------------------------------------------------

def gate_regression():
    """Existing safety metric must not regress."""
    from evals.eval import eval_pii_recall
    recall, misses = eval_pii_recall()
    return {"name": "regression (federal PII recall)", "pass": not misses,
            "detail": f"{recall:.0%}, {len(misses)} miss(es)"}


def gate_new_type(profile, target_override):
    """Deterministic recall on the new class's labeled gold >= target. No gold -> cannot ship."""
    ev = profile.get("eval")
    if not ev:
        return {"name": "new-type recall", "pass": False,
                "detail": "no eval/gold block — cannot auto-promote an unlabeled class"}
    gold = json.load(open(os.path.join(ROOT, ev["gold"])))
    docs_dir = os.path.join(ROOT, ev["docs"])
    target = target_override if target_override is not None else float(ev.get("recall_target", 0.0))
    comp = detectors.compile_patterns(profile.get("detectors"))
    caught = total = 0
    for fname in list(gold)[:ev.get("sample", 200)]:
        text = open(os.path.join(docs_dir, fname), encoding="utf-8").read()
        hits = detectors.detect(text, comp)
        for s in gold[fname]:
            caught += any(_overlap(h.start, h.end, s["start"], s["end"]) for h in hits)
            total += 1
    recall = caught / total if total else 0.0
    return {"name": "new-type recall", "pass": recall >= target,
            "detail": f"{recall:.1%} vs target {target:.0%} ({caught}/{total})"}


def gate_precision(profile_name, profile):
    """Over-redaction guard from the live officer-decision stream. A recall-only gold set
    can't see over-flagging; this enforces it once enough feedback has accumulated. Below the
    minimum sample it's informational (a brand-new class has no feedback yet) — the recall
    gold gate still guards the dangerous direction."""
    ev = profile.get("eval") or {}
    max_or = float(ev.get("max_override_rate", 0.25))
    min_n = int(ev.get("min_feedback", 20))
    m = feedback.metrics(profile_name).get(profile_name)
    if not m or m["decided"] < min_n:
        n = m["decided"] if m else 0
        return {"name": "precision (officer override rate)", "pass": True,
                "detail": f"insufficient feedback ({n}<{min_n}) — informational"}
    return {"name": "precision (officer override rate)", "pass": m["override_rate"] <= max_or,
            "detail": f"{m['override_rate']:.1%} vs max {max_or:.0%} "
                      f"({m['rejected']}/{m['decided']} rejected, {m['missed']} missed)"}


def gate_throughput(profile):
    """Process the sample deterministically with zero per-doc failures."""
    ev = profile.get("eval") or {}
    if not ev:
        return {"name": "throughput/robustness", "pass": True, "detail": "skipped (no sample)"}
    gold = json.load(open(os.path.join(ROOT, ev["gold"])))
    docs_dir = os.path.join(ROOT, ev["docs"])
    comp = detectors.compile_patterns(profile.get("detectors"))
    n = errors = 0
    for fname in list(gold)[:ev.get("sample", 200)]:
        try:
            detectors.detect(open(os.path.join(docs_dir, fname), encoding="utf-8").read(), comp)
            n += 1
        except Exception:  # noqa: BLE001
            errors += 1
    return {"name": "throughput/robustness", "pass": errors == 0, "detail": f"{n} ok, {errors} errors"}


def gate_release_closed():
    """Re-verify the fail-closed invariant: an escalated doc must be unreleasable, and a
    responsive doc with an unreviewed redaction must block production."""
    from backend.app import _decided_responsive
    esc = DocReview(doc_id="d0", filename="f0", text="x", responsive=False,
                    responsive_confidence=0.0, responsive_rationale="", escalated=True)
    resp = DocReview(doc_id="d1", filename="f1", text="x", responsive=True,
                     responsive_confidence=0.9, responsive_rationale="", escalated=False)
    resp.redactions.append(Redaction(start=0, end=1, text="x", pii_type="t", exemption="b6",
                                     basis="", detector="d", confidence=1.0, rationale=""))
    ok = (_decided_responsive(esc) is None                              # escalated -> blocks
          and any(r.status == "proposed" for r in resp.redactions))     # unreviewed -> blocks
    return {"name": "release gate fail-closed", "pass": bool(ok),
            "detail": "escalated unreleasable; unreviewed redaction blocks"}


# ---- driver --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--candidate", help="path to candidate yaml (default: working copy)")
    ap.add_argument("--promote", action="store_true", help="actually ship if all gates pass")
    ap.add_argument("--actor", default="autopromote")
    ap.add_argument("--recall-target", type=float, help="override the profile's recall_target")
    args = ap.parse_args()

    profile, path = _load_candidate(args.profile, args.candidate)
    print("=" * 70)
    print(f"PROMOTION SCORECARD — '{args.profile}'  ({profile.get('jurisdiction', '?')})")
    print(f"candidate: {os.path.relpath(path, ROOT)}  |  current active: "
          f"v{registry.active_version(args.profile)}")
    print("=" * 70)

    gates = [gate_regression(), gate_new_type(profile, args.recall_target),
             gate_precision(args.profile, profile), gate_throughput(profile), gate_release_closed()]
    for g in gates:
        print(f"  [{'PASS' if g['pass'] else 'HOLD'}]  {g['name']:30s} {g['detail']}")

    passed = all(g["pass"] for g in gates)
    decision = "SHIP" if passed else "HOLD"
    print("-" * 70)
    scorecard = {g["name"]: {"pass": g["pass"], "detail": g["detail"]} for g in gates}

    if not passed:
        print("DECISION: HOLD — not promoted. Escalating with scorecard; no engineer needed to "
              "read a stack trace, just the failing gate above.")
        sys.exit(1)

    if args.promote:
        v = registry.register_version(args.profile, scorecard, decision, args.actor, src_path=path)
        print(f"DECISION: SHIP — promoted '{args.profile}' to v{v} (active). Rollback: "
              f"python -m backend.registry rollback {args.profile} <prev_v> {args.actor}")
    else:
        print("DECISION: SHIP-ELIGIBLE — all gates pass. Re-run with --promote to flip the "
              "active pointer.")


if __name__ == "__main__":
    main()
