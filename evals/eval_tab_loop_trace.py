"""Within-run A/B for the verify-loop: score the SAME run at its initial turn (single-shot
equivalent) vs. after the monotonic expand turns. Because both stages come from one run over
the same documents, the PERSON cross-run variance that muddies a two-arm A/B is eliminated —
any delta is the loop's own contribution.

    FOIA_PROFILE=echr_anonymization FOIA_MODEL=sonnet \
        python -m evals.eval_tab_loop_trace --n 20 --max-iters 2
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


def _recall(per_doc_spans):
    by_ent = defaultdict(lambda: [0, 0])
    caught = total = 0
    for fname, spans in per_doc_spans.items():
        for s in GT[fname]:
            hit = any(_overlap(p0, p1, s["start"], s["end"]) for (p0, p1) in spans)
            by_ent[s["entity_type"]][0] += hit
            by_ent[s["entity_type"]][1] += 1
            caught += hit
            total += 1
    return caught, total, by_ent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=6000)
    ap.add_argument("--overlap", type=int, default=500)
    ap.add_argument("--max-iters", type=int, default=2)
    args = ap.parse_args()

    policy = exemptions.load_policy()
    threshold = float(policy.get("escalation_threshold", 0.65))
    comp = detectors.compile_patterns(policy.get("detectors"))
    specialists = [agents.VerifyLoopAgent(s.name, s.exemption, s.instruction, comp, args.max_iters, prune=False)
                   for s in agents.build_specialists(policy)]
    files = list(GT.keys())[:args.n]
    model = os.environ.get("FOIA_MODEL", "claude-sonnet-4-6")

    print("=" * 70)
    print(f"VERIFY-LOOP within-run trace — {len(files)} ECHR cases | model: {model} | "
          f"max_iters={args.max_iters} | recall-monotonic (prune=off)")
    print("=" * 70)

    def locate(base, chunk, quotes):
        out = set()
        for q in quotes:
            idx = chunk.find(q)
            if idx >= 0:
                out.add((base + idx, base + idx + len(q)))
        return out

    def work(fname):
        text = open(os.path.join(DOCS, fname), encoding="utf-8").read()
        init, final = set(), set()
        for base, chunk in _chunks(text, args.chunk, args.overlap):
            for sp in specialists:
                _, stages = sp.run_traced(chunk, threshold)
                if not stages:
                    continue
                init |= locate(base, chunk, stages[0])
                final |= locate(base, chunk, stages[-1])
        return fname, init, final

    llm.CALL_COUNT = 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(work, files))
    wall = time.perf_counter() - t0

    init_spans = {f: s for f, s, _ in results}
    final_spans = {f: s for f, _, s in results}
    ic, it, iby = _recall(init_spans)
    fc, ft, fby = _recall(final_spans)

    print(f"  overall recall — initial: {ic}/{it} = {ic/it:.1%}   ->   final: {fc}/{ft} = {fc/ft:.1%}   "
          f"(+{(fc-ic)/it*100:.1f} pts, +{fc-ic} spans)")
    print("\n  by entity_type      initial -> final")
    for k in sorted(iby, key=lambda k: -iby[k][1]):
        ic2, it2 = iby[k]
        fc2 = fby[k][0]
        flag = "  <== loop added" if fc2 > ic2 else ("  (lost)" if fc2 < ic2 else "")
        print(f"    {k:9s}: {ic2/it2:5.1%} -> {fc2/it2:5.1%}{flag}")
    print(f"\n  model calls: {llm.CALL_COUNT}  |  wall: {wall:.1f}s")
    print("  monotonic guarantee:", "HELD" if fc >= ic else "VIOLATED")
    print("=" * 70)


if __name__ == "__main__":
    main()
