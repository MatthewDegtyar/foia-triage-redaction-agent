"""LIVE accuracy eval — does the LLM specialist layer catch the contextual identifiers
the regex layer can't (names, identifying narrative) on the TAB/ECHR terrain?

Runs the ECHR-profile scoped specialists (identity=direct_id, quasi=quasi_id) over a
sample of TAB cases through the live Claude Code transport, resolves each proposed quote
to exact offsets (verbatim or it's dropped — no phantom spans), and scores RECALL against
the expert labels, with PERSON broken out (the LLM-only class).

Honest scope: specialists see the first 6000 chars of each case (the transport window), so
LLM-reachable recall is also reported over spans that fall inside that window.

    FOIA_PROFILE=echr_anonymization FOIA_MODEL=haiku python -m evals.eval_tab_live --n 20
"""
import argparse
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from backend import agents, detectors, exemptions, llm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GT = json.load(open(os.path.join(HERE, "ground_truth_tab.json")))
DOCS = os.path.join(ROOT, "data_tab", "docs")


def _overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def _chunks(text, size, overlap):
    """Sliding window over the FULL document so no span is out of reach. Each chunk is
    <= the transport window; yields (base_offset, chunk_text) so quotes resolve to true
    full-document offsets. Overlap avoids cutting an entity at a boundary."""
    if len(text) <= size:
        yield 0, text
        return
    step = max(1, size - overlap)
    base = 0
    while base < len(text):
        yield base, text[base:base + size]
        if base + size >= len(text):
            break
        base += step


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--chunk", type=int, default=6000)
    ap.add_argument("--overlap", type=int, default=500)
    ap.add_argument("--loop", action="store_true",
                    help="use the tool-grounded VerifyLoopAgent (propose->verify->revise)")
    ap.add_argument("--max-iters", type=int, default=2)
    args = ap.parse_args()

    policy = exemptions.load_policy()
    threshold = float(policy.get("escalation_threshold", 0.65))
    model = os.environ.get("FOIA_MODEL", "claude-sonnet-4-6")
    files = list(GT.keys())[:args.n]

    base = agents.build_specialists(policy)
    if args.loop:
        comp = detectors.compile_patterns(policy.get("detectors"))
        specialists = [agents.VerifyLoopAgent(s.name, s.exemption, s.instruction, comp, args.max_iters)
                       for s in base]
        mode = f"LOOP (max_iters={args.max_iters})"
    else:
        specialists = base
        mode = "single-shot"

    print("=" * 70)
    print(f"LIVE TAB EVAL — {mode} — {len(files)} ECHR cases (full-doc chunking)")
    print(f"profile: {policy.get('jurisdiction')}  |  model: {model}")
    print(f"specialists: {[(s.name, s.exemption) for s in specialists]}  |  "
          f"chunk={args.chunk} overlap={args.overlap}")
    print("=" * 70)
    llm.CALL_COUNT = 0

    def work(fname):
        text = open(os.path.join(DOCS, fname), encoding="utf-8").read()
        spans = set()   # dedupe across overlapping chunks
        for base, chunk in _chunks(text, args.chunk, args.overlap):
            for sp in specialists:
                for p in sp.run(chunk, threshold):
                    if p.escalate or not p.quote:
                        continue
                    idx = chunk.find(p.quote)   # resolve within the chunk -> true offset
                    if idx >= 0:
                        spans.add((base + idx, base + idx + len(p.quote)))
        return fname, text, sorted(spans)

    by_ent = defaultdict(lambda: [0, 0])        # entity_type -> [caught, total]
    must_caught = must_total = n_props = 0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(work, files))
    wall = time.perf_counter() - t0

    for fname, text, spans in results:
        n_props += len(spans)
        for s in GT[fname]:
            hit = any(_overlap(p0, p1, s["start"], s["end"]) for (p0, p1) in spans)
            by_ent[s["entity_type"]][0] += hit
            by_ent[s["entity_type"]][1] += 1
            must_caught += hit
            must_total += 1

    print(f"  LLM proposals located (verbatim): {n_props}")
    print(f"  overall must-mask recall: {must_caught}/{must_total} = {must_caught/max(must_total,1):.1%}")
    print("\n  by entity_type:")
    for k, (c, t) in sorted(by_ent.items(), key=lambda kv: -kv[1][1]):
        print(f"    {k:9s}: {c}/{t} = {c/max(t,1):.1%}")
    pc, pt = by_ent.get("PERSON", [0, 0])
    print(f"\n  PERSON (the LLM-only class, full doc): {pc}/{pt} = {pc/max(pt,1):.1%}")
    print(f"  model calls: {llm.CALL_COUNT}  |  wall: {wall:.1f}s for {len(files)} docs "
          f"({len(files)/wall:.2f} docs/s)")
    print("=" * 70)


if __name__ == "__main__":
    main()
