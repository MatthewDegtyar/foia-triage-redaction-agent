# Architecture

A FOIA triage & redaction agent that proposes responsiveness calls and exemption-based
redactions for a human records officer — and never releases anything itself. The reliability
*is* the product: in this domain the catastrophic failure is silent (a released SSN looks
like a finished job), so every layer is built so the safe outcome is the default.

## Layers (request → release)

```
                          terrain profile (policy/<name>.yaml)
                          detectors + specialists + exemptions + thresholds + eval targets
                                              │  (selected by FOIA_PROFILE / registry)
                                              ▼
 documents ──► PIPELINE ──► per document:
                              1. DETECTORS (deterministic regex)        ── safety-critical PII
                              2. ORCHESTRATOR (deterministic control plane)
                                   • ResponsivenessAgent     (1 scoped LLM call)
                                   • exemption SPECIALISTS    (concurrent, 1 exemption each)
                                       └─ single-shot  or  VerifyLoopAgent (agentic, tool-grounded)
                                   • RECONCILER               (deterministic QA / escalation)
                                   each LLM turn is CONTEXT-ENGINEERED:
                                       + context layer (reference files for terrain/exemption)
                                       + memory layer  (lessons learned from past feedback)
                              ▼
                           REVIEW PACKAGE (proposed redactions + per-item provenance + audit)
                              ▼
 OFFICER CONSOLE ──► resolve responsiveness · accept/reject/modify · flag-missed
                       └─ every action ──► AUDIT LOG  and  FEEDBACK STREAM (live labels)
                              ▼
                           RELEASE GATE (produce): FAILS CLOSED
                              releases nothing while any escalation is open or any redaction unreviewed
```

## Components

| Module | Role |
|---|---|
| `backend/detectors.py` | Deterministic PII detectors; patterns from the active profile (federal default baked in). The safety-critical layer (DD-001). |
| `backend/agents.py` | The orchestration: `ResponsivenessAgent`, scoped `ExemptionAgent` / agentic `VerifyLoopAgent`, deterministic `Reconciler`, `Orchestrator`. Each LLM turn is augmented with context + memory (DD-005/006/010/011). |
| `backend/llm.py` | Single fail-safe transport: API key → SDK, else logged-in Claude Code CLI, else `None`→escalate (DD-002). Call counter for cost. |
| `backend/exemptions.py` | Terrain-profile loader (`FOIA_PROFILE`, registry-aware) + quote→offset resolution. |
| `backend/pipeline.py` | PDF/text intake (`read_doc`/`load_docs`); drives the orchestrator per doc; assembles the review package + audit trail. |
| `backend/app.py` | FastAPI **route handlers only**: intake, run_agent, status, decision, reclassify, add_redaction, flag_missed, resolve, feedback, health, produce (fails closed, DD-003). |
| `backend/runtime.py` | Shared runtime state + primitives — one process-wide package/job behind a re-entrant lock; paths, id/job sequences, `ensure_pkg`. |
| `backend/intake.py` | Document intake off the source pool + the async, per-doc-status agent-run job (DD-014). |
| `backend/seeding.py` | Demo boot staging — lands the console 'in medias res', re-deriving the privacy specialist's calls model-free (DD-014/DD-015). |
| `backend/api_models.py` | Pydantic request bodies for the API surface (pure schema). |
| `backend/registry.py` | Versioned profile registry: staging (working copy) vs prod (active version), promote/rollback, promotion log (DD-007). |
| `backend/feedback.py` | Officer-decision stream → rolling error rates (DD-008) **+ `corrections()`**: the removed/added/reclassified spans (with text) the runtime RAG learns from (DD-017). |
| `backend/context_layer.py` | SQLite `(profile, exemption)`→reference files, split by `kind`: **Corpus** (regulations) + **Guidelines** (how-to), retrieved per-`kind` and injected at runtime (DD-010/017). |
| `backend/memory.py` | Agent memory: aggregate caveats (DD-011) **+ `recall_corrections`** — lexical RAG that injects the officer corrections most relevant to the document being assessed (DD-017). |
| `backend/classifier.py` | Deterministic document-type sorter (heuristic signatures); `unknown` = a novelty signal (DD-012). |
| `backend/health.py` | Per-type health monitor; auto-opens a review of the analyzing profile on failure, feeding promotion (DD-012). |
| `console/index.html` | Records-officer review console — markup only; `styles.css` + `app.js` served alongside (no-cache). |
| `evals/` | Eval suite (below) — also the gates the promotion scorecard reuses. |
| `policy/*.yaml` | Terrain profiles; `_versions/` snapshots; `registry.json`. |

## The eval suite (and how it doubles as the deploy gate)

| Eval | Measures | Reused as gate |
|---|---|---|
| `evals/eval.py` | Federal PII recall (the safety metric), no API key | **regression gate** |
| `evals/eval_detectors.py` | Controlled detector eval — recall on labeled positives + **precision** on court-text negatives (the DD-016 identifiers; DD-015/016 FP traps as regression guards), no deps | detector recall + precision |
| `evals/eval_pii.py` | Scale recall vs AI4Privacy `pii-masking-200k` (per PII class), profile-aware; needs `datasets`+net, skips gracefully | independent-corpus recall |
| `evals/eval_tab.py` | Deterministic recall vs the Text Anonymization Benchmark, profile-aware | new-type recall |
| `evals/eval_tab_live.py` | LLM-layer recall (single-shot / `--loop`), full-doc chunking | LLM-layer recall (planned) |
| `evals/eval_tab_loop_trace.py` | Within-run proof the verify-loop lifts recall monotonically | — |
| `evals/scale.py` | Throughput / robustness over a large doc set (`--deterministic`) | throughput gate |
| `evals/promote.py` | **The scorecard**: regression · new-type recall · precision (feedback) · throughput · fail-closed → ship/hold | the gate itself |

## Deployment / promotion pipeline (self-service, eval-gated)

The **trigger** is implicit: as documents are processed they're sorted into types and a
per-type health monitor (`health.py`) opens a review automatically when a type fails (novel
type, escalation spike, over-redaction, officer-flagged misses) — surfaced at `GET /api/health`.

```
detect new type → author candidate profile (staging working copy) → label a gold sample
   → promote.py scorecard ─ all gates pass? ── yes ─► register version + flip active pointer (prod)
                                              └ no ──► HOLD + escalate with scorecard (no engineer)
   → officer feedback streams in → memory learns → precision gate enforces over-redaction
   → rollback = flip pointer to a prior version (instant)
```

Autonomy is bounded: author/test/canary freely; **promote to prod only within the
deterministic gate, else escalate** (DD-007). Because a profile is data, every promotion is a
reversible config flip recorded in the registry log — the deployment is itself an
administrative record.

## Running surface

A launchable repo, not a container (DD-009): `./run.sh` sets up a venv, prints the safety
metric, and serves the console at :8000 (which boots already-populated). The model transport
is the operator's logged-in Claude Code CLI — no API key (DD-013). See `docs/RUNBOOK.md`.
