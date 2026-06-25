"""Scale recall eval for the deterministic detector layer on AI4Privacy `pii-masking-200k`.

An independent, large PII corpus (~209k samples, 54 classes) — orthogonal to our synthetic
gold and to TAB. It answers: do the format-based detectors generalize beyond our own data to
someone else's PII? We stream English rows, map AI4Privacy's labels to our detector types, and
measure RECALL per class (did some detector cover the labeled span — i.e. would it be redacted).

Honest scoping:
  • This dataset has NO passport / driver's-license / alien-registration classes, so those
    DD-016 detectors are validated only by the controlled fixture (`eval_detectors.py`).
  • Several detectors are deliberately CONTEXTUAL for court text (dob/financial_acct need a cue
    word; street_address needs <number+name+suffix>). On free-form prose their recall is lower
    BY DESIGN — the precision trade DD-015/DD-016 made. The per-class split shows exactly that.
  • AI4Privacy is multilingual/synthetic; we filter to English. Formats still skew international,
    so this is a coverage signal, not a court-accuracy claim.

    EVAL_PII_N=5000 FOIA_PROFILE=uscourts python -m evals.eval_pii

Requires: `datasets` (pip) + network. Skips gracefully (exit 0) if unavailable.
"""
import os
import sys
from collections import defaultdict
from itertools import islice

from backend import detectors, exemptions

# AI4Privacy label -> our detector pii_type (only the classes our deterministic layer targets)
LABEL_MAP = {
    "EMAIL": "email",
    "SSN": "ssn",
    "PHONENUMBER": "phone",
    "CREDITCARDNUMBER": "credit_card",
    "IPV4": "ip_address",
    "DOB": "dob",
    "STREET": "street_address",
    "SECONDARYADDRESS": "street_address",
    "ACCOUNTNUMBER": "financial_acct",
}
CONTEXTUAL = {"dob", "financial_acct", "street_address"}   # recall-traded-for-precision by design


def main() -> int:
    try:
        from datasets import load_dataset
    except Exception:  # noqa: BLE001
        print("SKIP: `datasets` not installed (pip install datasets). "
              "Fixture eval (eval_detectors.py) covers these detectors offline.")
        return 0

    n_target = int(os.environ.get("EVAL_PII_N", "5000"))
    policy = exemptions.load_policy()
    compiled = detectors.compile_patterns(policy.get("detectors"))
    profile = os.environ.get("FOIA_PROFILE", "foia_federal")

    try:
        ds = load_dataset("ai4privacy/pii-masking-200k", split="train", streaming=True)
    except Exception as e:  # noqa: BLE001 — offline / rate-limited
        print(f"SKIP: could not load AI4Privacy ({type(e).__name__}). Fixture eval covers these offline.")
        return 0

    seen = defaultdict(int)        # labeled spans of a mapped class
    covered = defaultdict(int)     # ...that a detector overlapped
    rows = 0
    for r in islice(ds, n_target):
        if r.get("language") != "en":
            continue
        rows += 1
        text = r["source_text"]
        hits = detectors.detect(text, compiled)
        for m in r["privacy_mask"]:
            cls = LABEL_MAP.get(m["label"])
            if not cls:
                continue
            seen[cls] += 1
            s, e = m["start"], m["end"]
            if any(h.start < e and s < h.end for h in hits):   # span/hit overlap
                covered[cls] += 1

    print("=" * 70)
    print("AI4PRIVACY pii-masking-200k — deterministic detector RECALL by class")
    print(f"terrain profile: {profile}   |   english rows scored: {rows}")
    print("=" * 70)
    tot_s = tot_c = 0
    fmt_s = fmt_c = 0
    for cls in sorted(seen):
        s, c = seen[cls], covered[cls]
        tot_s += s; tot_c += c
        tag = "  (contextual — recall traded for precision)" if cls in CONTEXTUAL else ""
        if cls not in CONTEXTUAL:
            fmt_s += s; fmt_c += c
        print(f"    {cls:16s} {c:5d}/{s:<5d} = {c/s*100 if s else 0:5.1f}%{tag}")
    if tot_s:
        print(f"\n    ALL mapped classes : {tot_c}/{tot_s} = {tot_c/tot_s*100:.1f}%")
    if fmt_s:
        print(f"    FORMAT-based only  : {fmt_c}/{fmt_s} = {fmt_c/fmt_s*100:.1f}%   <- the headline")
    print("\nNote: passport / driver's-license / alien-registration have NO class here —")
    print("      validated by the controlled fixture (evals/eval_detectors.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
