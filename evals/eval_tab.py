"""Accuracy eval on the TAB corpus (1000 ECHR court cases, real expert labels).

Reports RECALL of the DETERMINISTIC detector layer against TAB's must-mask spans,
matched by character-offset overlap, broken down by identifier_type and entity_type.

Why the breakdown matters: TAB's labels are ~94% QUASI-identifiers (names, dates,
places) — the job of the LLM PrivacyAgent (b6), not the regex layer. The deterministic
detectors target a narrow dangerous class (SSN/phone/email/address/DOB/account). This
eval shows exactly where the regex layer has coverage and where the model layer is
required, instead of one misleading aggregate number.

    python -m evals.eval_tab
"""
import json
import os
from collections import defaultdict

from backend import detectors, exemptions

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GT = json.load(open(os.path.join(HERE, "ground_truth_tab.json")))
DOCS = os.path.join(ROOT, "data_tab", "docs")

# Use the active terrain profile's deterministic layer (FOIA_PROFILE env var). Defaults to
# federal; set FOIA_PROFILE=echr_anonymization to score the ECHR-retargeted detectors.
_POLICY = exemptions.load_policy()
_COMPILED = detectors.compile_patterns(_POLICY.get("detectors"))


def _overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def main():
    by_idt = defaultdict(lambda: [0, 0])          # identifier_type -> [caught, total]
    by_ent = defaultdict(lambda: [0, 0])          # entity_type -> [caught, total]
    det_hits_total = det_hits_on_gt = 0

    for fname, spans in GT.items():
        text = open(os.path.join(DOCS, fname), encoding="utf-8").read()
        hits = detectors.detect(text, _COMPILED)
        det_hits_total += len(hits)
        for s in spans:
            caught = any(_overlap(h.start, h.end, s["start"], s["end"]) for h in hits)
            by_idt[s["identifier_type"]][0] += caught
            by_idt[s["identifier_type"]][1] += 1
            by_ent[s["entity_type"]][0] += caught
            by_ent[s["entity_type"]][1] += 1
        for h in hits:
            if any(_overlap(h.start, h.end, s["start"], s["end"]) for s in spans):
                det_hits_on_gt += 1

    total_caught = sum(v[0] for v in by_idt.values())
    total = sum(v[1] for v in by_idt.values())
    print("=" * 70)
    print(f"TAB ACCURACY EVAL — deterministic detector recall on {len(GT)} ECHR cases")
    print(f"terrain profile: {_POLICY.get('jurisdiction', 'unknown')}")
    print("=" * 70)
    print(f"  overall must-mask recall: {total_caught}/{total} = {total_caught/total:.1%}")
    print("\n  by identifier_type:")
    for k, (c, t) in sorted(by_idt.items()):
        print(f"    {k:6s}: {c}/{t} = {c/t:.1%}")
    print("\n  by entity_type:")
    for k, (c, t) in sorted(by_ent.items(), key=lambda kv: -kv[1][1]):
        print(f"    {k:9s}: {c}/{t} = {c/t:.1%}")
    print(f"\n  detector hits: {det_hits_total} total, {det_hits_on_gt} land on a labeled span")
    print("=" * 70)
    print("Reading: the regex layer covers the narrow dangerous class it targets; TAB's")
    print("name/date/place identifiers (the QUASI bulk) are the LLM privacy agent's job.")


if __name__ == "__main__":
    main()
