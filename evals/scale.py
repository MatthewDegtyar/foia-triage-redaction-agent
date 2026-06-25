"""Scale / robustness harness — does the WHOLE pipeline survive a large document set?

This is a throughput + robustness test, not an accuracy eval (eval.py owns the safety
metric). It drives the real pipeline (deterministic detectors + the multi-agent
orchestrator + audit trail) over N documents and reports:

  - throughput (docs/sec, wall time) and per-doc latency
  - PII detector hits (the safety-critical layer) across the batch
  - escalation / responsive / non-responsive breakdown
  - per-document errors, isolated so one bad doc can't abort the run
  - release-gate behavior (it must fail closed while escalations are open)

    python -m evals.scale --n 20                 # live judgment layer (Claude Code login)
    python -m evals.scale --n 200 --offset 20
    python -m evals.scale --n 1000 --deterministic --workers 16   # model-free, fast

--deterministic forces the model-free fail-safe path (every judgment escalates), which
exercises all the plumbing at full speed and zero model cost.
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

from backend import agents, detectors, exemptions, llm, pipeline
from backend.schema import AuditEvent, ReviewPackage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUEST = ("All agency records — communications, memoranda, reports, budgets and "
           "personnel matters — responsive to public-records request FOIA-SCALE-TEST.")


def _now() -> str:
    # wall-clock not needed for correctness; audit timestamps are informational here
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--docs", default=os.path.join(ROOT, "data_scale", "docs"))
    ap.add_argument("--deterministic", action="store_true",
                    help="force model-free fail-safe path (no model calls)")
    ap.add_argument("--workers", type=int, default=1,
                    help="doc-level concurrency (each doc also fans out specialists)")
    args = ap.parse_args()

    if args.deterministic:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        agents.llm._claude_cli = lambda: None   # disable the CLI transport too

    policy = exemptions.load_policy()
    all_docs = pipeline.load_docs(args.docs)
    batch = all_docs[args.offset:args.offset + args.n]

    transport = ("none (deterministic fail-safe)" if not llm.available()
                 else ("ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY")
                       else "Claude Code CLI (logged-in)"))
    print("=" * 70)
    print(f"SCALE TEST — {len(batch)} docs (offset {args.offset}), workers={args.workers}")
    print(f"transport: {transport}")
    print("=" * 70)

    pkg = ReviewPackage(request_id="scale-001", request_text=REQUEST,
                        jurisdiction=policy.get("jurisdiction", "unknown"), created_at=_now())
    compiled = detectors.compile_patterns(policy.get("detectors"))
    orch = agents.Orchestrator(policy, compiled)

    stats = {"ok": 0, "errors": 0, "responsive": 0, "nonresponsive": 0,
             "escalated": 0, "redactions": 0, "det_pii": 0}
    errors = []
    latencies = []

    def work(item):
        i, (filename, text) = item
        t0 = time.perf_counter()
        try:
            rev = pipeline.process_document(orch, f"doc-{i:05d}", filename, text, REQUEST, policy, pkg, compiled)
            return i, rev, None, time.perf_counter() - t0
        except Exception as e:  # noqa: BLE001 — robustness: isolate per-doc failure
            return i, None, f"{type(e).__name__}: {e}", time.perf_counter() - t0

    t_start = time.perf_counter()
    items = list(enumerate(batch))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for i, rev, err, dt in ex.map(work, items):
            done += 1
            latencies.append(dt)
            if err:
                stats["errors"] += 1
                errors.append((i, err))
            else:
                stats["ok"] += 1
                pkg.docs.append(rev)
                if rev.escalated:
                    stats["escalated"] += 1
                elif rev.responsive:
                    stats["responsive"] += 1
                else:
                    stats["nonresponsive"] += 1
                stats["redactions"] += len(rev.redactions)
            if done % 25 == 0 or done == len(items):
                rate = done / (time.perf_counter() - t_start)
                print(f"  ...{done}/{len(items)}  ({rate:.1f} docs/s)")
    wall = time.perf_counter() - t_start

    # release-gate check: with open escalations, produce() must fail closed.
    unresolved = [d.doc_id for d in pkg.docs if d.escalated]
    gate_blocks = bool(unresolved) or any(
        any(r.status == "proposed" for r in d.redactions)
        for d in pkg.docs if d.responsive and not d.escalated)

    print("-" * 70)
    print(f"  processed:      {stats['ok']}/{len(batch)}   errors: {stats['errors']}")
    print(f"  escalated:      {stats['escalated']}")
    print(f"  responsive:     {stats['responsive']}")
    print(f"  non-responsive: {stats['nonresponsive']}")
    print(f"  redactions proposed (incl. deterministic PII): {stats['redactions']}")
    print(f"  audit events:   {len(pkg.audit)}")
    print(f"  wall time:      {wall:.1f}s   ({stats['ok']/wall:.1f} docs/s)")
    if latencies:
        sl = sorted(latencies)
        print(f"  per-doc latency: avg {sum(sl)/len(sl):.2f}s  "
              f"p50 {sl[len(sl)//2]:.2f}s  p95 {sl[int(len(sl)*0.95)]:.2f}s  max {sl[-1]:.2f}s")
    print(f"  RELEASE GATE:   {'BLOCKS (fails closed) — correct' if gate_blocks else '!! OPEN — BUG'}")
    if errors:
        print("  first errors:")
        for i, e in errors[:5]:
            print(f"    doc {i}: {e}")
    print("=" * 70)
    print("SCALE GATE:", "PASS" if stats["errors"] == 0 and gate_blocks else "FAIL")


if __name__ == "__main__":
    main()
