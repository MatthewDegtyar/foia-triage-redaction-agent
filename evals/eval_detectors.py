"""Controlled detector eval — recall on labeled positives + precision on court-text negatives.

Complements the dataset evals: `eval.py` (synthetic dangerous-class recall) and `eval_tab.py`
(real ECHR quasi-identifiers, the model layer's job). This one targets the DETERMINISTIC layer
of the active terrain profile and answers two questions the corpora can't:

  RECALL    — does each detector fire on a known identifier of its type? (the new b6/b4 patterns
              added in DD-016: passport, driver's license, alien A-number, EIN, credit card, IP)
  PRECISION — do the detectors stay SILENT on ordinary court text — docket/case numbers, statute
              cites, ECF refs, bare filing dates, money? (the false positives DD-015/DD-016 fixed)

No network, no model, no heavy deps — runs in CI. Fixtures: evals/fixtures_detectors.json.

    FOIA_PROFILE=uscourts python -m evals.eval_detectors
"""
import json
import os
import sys

from backend import detectors, exemptions

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "fixtures_detectors.json")))


def _hits(text, compiled):
    return detectors.detect(text, compiled)


def main() -> int:
    policy = exemptions.load_policy()
    compiled = detectors.compile_patterns(policy.get("detectors"))
    profile = os.environ.get("FOIA_PROFILE", "foia_federal")

    print("=" * 70)
    print("DETERMINISTIC DETECTOR EVAL — recall (positives) + precision (negatives)")
    print(f"terrain profile: {profile}")
    print("=" * 70)

    # ---- RECALL: each positive must be caught by SOME detector covering the expected span ----
    pos = FIX["positives"]
    caught, missed = 0, []
    by_type_seen, by_type_ok = {}, {}
    for ex in pos:
        t = ex["type"]
        by_type_seen[t] = by_type_seen.get(t, 0) + 1
        s = ex["text"].find(ex["expect"])
        e = s + len(ex["expect"])
        hit = next((h for h in _hits(ex["text"], compiled)
                    if h.start <= e and s <= h.end), None)   # any overlap with the labeled span
        if hit:
            caught += 1
            by_type_ok[t] = by_type_ok.get(t, 0) + 1
        else:
            missed.append((t, ex["expect"]))
    print(f"\nRECALL — labeled identifiers caught: {caught}/{len(pos)} = {caught/len(pos)*100:.0f}%")
    for t in sorted(by_type_seen):
        print(f"    {t:16s} {by_type_ok.get(t,0)}/{by_type_seen[t]}")
    for t, q in missed:
        print(f"    MISS  {t}: {q!r}")

    # ---- PRECISION: negatives are ordinary court text; ANY hit is a false positive ----
    negs = FIX["negatives"]
    fps = []
    for line in negs:
        for h in _hits(line, compiled):
            fps.append((h.detector, h.text.strip(), line[:48]))
    clean = len(negs) - len({line for (_, _, line) in fps})
    print(f"\nPRECISION — court-text lines with NO false positive: {clean}/{len(negs)}")
    for det, txt, ctx in fps:
        print(f"    FALSE POSITIVE [{det}] {txt!r}  in  …{ctx}…")

    ok = (caught == len(pos)) and (not fps)
    print("\n" + "=" * 70)
    print("GATE: PASS — full recall on positives, zero false positives on court text" if ok
          else "GATE: FAIL — see misses / false positives above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
