"""Scale-process a directory of real court PDFs through the deterministic layer + classifier.

Throughput/robustness over a large born-digital corpus (govinfo USCOURTS) — the model-free path,
so it runs fast and can't flake. Reports: docs processed, per-type sort, deterministic detector
coverage by pii_type/exemption, and how many documents carry at least one finding. This is the
"process 100 docs" run; the live LLM layer is exercised separately (it's slow per doc by design).

    FOIA_PROFILE=uscourts python -m evals.scale_pdfs [dir]
"""
import json
import os
import sys
import time
from collections import Counter

from backend import classifier, detectors, exemptions, pipeline

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> int:
    docs_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "pdfs_scale")
    if not os.path.isdir(docs_dir):
        print(f"no such dir: {docs_dir}"); return 1
    profile = os.environ.get("FOIA_PROFILE", "foia_federal")
    policy = exemptions.load_policy()
    compiled = detectors.compile_patterns(policy.get("detectors"))

    files = sorted(f for f in os.listdir(docs_dir) if f.lower().endswith(".pdf"))
    types, by_pii, by_exemption = Counter(), Counter(), Counter()
    docs_with_hits = errors = total_chars = total_hits = 0
    t0 = time.time()
    for name in files:
        try:
            text = pipeline.read_doc(os.path.join(docs_dir, name))
        except Exception:  # noqa: BLE001 — a bad PDF must not kill the batch
            errors += 1
            continue
        total_chars += len(text)
        types[classifier.classify(text)] += 1
        hits = detectors.detect(text, compiled)
        if hits:
            docs_with_hits += 1
        total_hits += len(hits)
        for h in hits:
            by_pii[h.pii_type] += 1
            by_exemption[h.exemption] += 1
    dt = time.time() - t0
    n = len(files)

    print("=" * 70)
    print(f"SCALE RUN — deterministic processing of {n} court PDFs   |   profile: {profile}")
    print("=" * 70)
    print(f"  processed       : {n - errors}/{n}   ({errors} unreadable)")
    print(f"  throughput      : {dt:.1f}s total  ·  {n/dt:.1f} docs/s  ·  {dt/max(1,n)*1000:.0f} ms/doc")
    print(f"  extracted text  : {total_chars:,} chars  (~{total_chars//max(1,n):,}/doc)")
    print(f"  docs w/ ≥1 deterministic finding: {docs_with_hits}/{n}   ({total_hits} findings total)")
    print("\n  document types (implicit sort):")
    for t, c in types.most_common():
        print(f"    {t:16s} {c}")
    print("\n  deterministic findings by detector type:")
    for t, c in by_pii.most_common():
        print(f"    {t:18s} {c}")
    print("\n  findings by exemption:")
    for e, c in by_exemption.most_common():
        print(f"    {e:6s} {c}")
    print("\nNote: contextual PII (private names in prose, official-vs-private) is the LLM layer's")
    print("job — not counted here. This is the model-free safety/throughput floor.")

    out = os.path.join(HERE, "scale_pdfs_results.json")
    json.dump({"profile": profile, "docs": n, "errors": errors, "seconds": round(dt, 1),
               "docs_with_findings": docs_with_hits, "total_findings": total_hits,
               "types": dict(types), "by_pii_type": dict(by_pii),
               "by_exemption": dict(by_exemption)}, open(out, "w"), indent=2)
    print(f"\nsaved {os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
