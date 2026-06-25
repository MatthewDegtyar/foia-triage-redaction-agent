# Runbook

## Run it

```bash
./run.sh
```
One command: sets up a venv on first run, prints the safety metric, and serves the console at
http://localhost:8000 (it boots already-populated). Pick documents in the left rail → **Run
triage on selected** → review → **Produce package**.

**Transport (no API key):** the model transport is your logged-in **Claude Code CLI**
(DD-013) — run on a machine where you're signed into Claude Code and the LLM judgment layer is
live. Without it, calls fail-safe to escalation and the deterministic safety layer + console +
gate still run (the badge shows "fail-safe"). An `ANTHROPIC_API_KEY` also works but isn't
required.

Manual equivalent: `python -m venv .venv && .venv/bin/pip install -r requirements.txt &&
.venv/bin/python -m backend.context_layer seed && .venv/bin/uvicorn backend.app:app --reload`.

## Add a new terrain / document type (no engineering)

1. Copy a profile: `cp policy/foia_federal.yaml policy/<name>.yaml`, edit `detectors:`,
   `specialists:`, `exemptions:`, and an `eval:` block (gold set + `recall_target` +
   `max_override_rate`).
2. Label a small gold sample for the new class.
3. Dry-run the gate: `python -m evals.promote --profile <name>`
4. Ship if it passes: `python -m evals.promote --profile <name> --promote`
   Otherwise it HOLDs and prints which gate failed.

## Promote / roll back

```bash
python -m evals.promote --profile <name> --promote --actor you   # gated ship
python -m backend.registry status                                # active versions
python -m backend.registry history <name>                        # promotion log
python -m backend.registry rollback <name> <version> you         # instant rollback
```

## Feedback & memory

```bash
python -m backend.feedback <profile>          # rolling officer error rates (per detector)
python -m backend.memory learn <profile>      # distil feedback into durable memories
python -m backend.memory show <profile>       # what the agent has learned
```
The console writes feedback automatically (accept/reject/modify, and `POST /api/flag_missed`
for missed PII). `GET /api/feedback` returns live error rates for the active profile.

## Document sorting & auto-opened reviews (implicit)

Sorting into types and failure-triggered reviews run inside processing — no action needed.

```bash
python -m backend.health summary <profile>    # per-type rollup (counts, escalation, coverage)
python -m backend.health reviews <profile>    # reviews auto-opened by failures
```
`GET /api/health` returns the per-type breakdown + open reviews. A review opens when a type
fails (novel/unrecognized type, escalation spike, zero coverage, high override-rate, or
officer-flagged missed PII) and points you at `evals/promote.py` to adjust + re-ship.

## Accuracy / scale checks

```bash
FOIA_PROFILE=echr_anonymization python -m evals.eval_tab          # deterministic recall vs TAB
FOIA_PROFILE=echr_anonymization FOIA_MODEL=sonnet \
    python -m evals.eval_tab_live --n 20 --loop                   # LLM verify-loop recall
python -m evals.scale --n 1000 --deterministic --workers 16       # throughput/robustness
```
(`eval_tab*` need the TAB dataset built via `python -m evals.build_tab`, which requires
`scikit-learn`; these are dev/CD tools and are excluded from the runtime image.)
