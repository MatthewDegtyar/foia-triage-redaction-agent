#!/usr/bin/env bash
# Launch the FOIA Triage & Redaction demo. One command — clone the repo and run this.
#
# No API key. The model transport is your logged-in Claude Code CLI (DD-013): run this where
# you're signed into Claude Code and the LLM judgment layer is live. Without it, the
# deterministic safety layer, console, and fail-closed gate still run — nothing breaks.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "==> first run: creating venv + installing deps"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

echo "==> seeding context layer"
.venv/bin/python -m backend.context_layer seed

echo "==> safety eval (deterministic, instant) — the headline metric"
FOIA_DISABLE_MODEL=1 .venv/bin/python -m evals.eval || true   # force deterministic so launch is fast

echo
echo "==> console at http://localhost:8000  (boots already-populated; Ctrl-C to stop)"
# open the browser for the demo (macOS); harmless elsewhere
( sleep 2; command -v open >/dev/null 2>&1 && open http://localhost:8000 ) &
# Live corpus = real federal court-record PDFs (govinfo USCOURTS); terrain = the uscourts profile.
# (The safety eval above runs the federal default; this env is scoped to the server only.)
exec env FOIA_PROFILE=uscourts .venv/bin/uvicorn backend.app:app --reload --port 8000
