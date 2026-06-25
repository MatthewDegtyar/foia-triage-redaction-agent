# Design decisions (ADR log)

Short, dated rationale for the choices that shape this system. Each entry: the decision,
why, and the trade-off accepted.

---

### DD-001 — Deterministic catch for the dangerous class
**Decision:** The safety-critical PII (SSN, address, phone, DOB, account, email) is found by
regex detectors, not a model.
**Why:** The catastrophic failure here is silent — releasing an SSN throws no error. You do
not put a stochastic system as the only thing between a private identifier and a public
release.
**Trade-off:** Regex over-catches; the officer prunes. Recall ≫ precision by design.

### DD-002 — Calibrated refusal (single fail-safe transport)
**Decision:** All model access goes through `llm.judge`, which returns `None` on no
transport / parse failure / error; every agent treats `None` as "escalate to a human."
**Why:** In FOIA, guessing is the failure mode. One choke point makes the safe default
impossible to forget.
**Trade-off:** Lower automation when the model is unavailable — accepted.

### DD-003 — Release gate fails closed
**Decision:** `produce` releases nothing while any escalation is unresolved or any redaction
on a releasable doc is unreviewed (HTTP 409).
**Why:** Detection without enforcement still discloses. Found in integration testing: the
gate originally failed *open*.
**Trade-off:** No partial releases — accepted; partial review is exactly the risk.

### DD-004 — Terrain is configuration, not code
**Decision:** Detectors, specialists, exemption schedule, and thresholds live in a YAML
**terrain profile**; the same code runs federal FOIA, a state PIA, or court-record
anonymization by swapping a file (`FOIA_PROFILE`).
**Why:** A generalist agent must retarget across document domains without engineering.
**Trade-off:** A profile is data and can be wrong; the promotion gate (DD-007/008) guards it.

### DD-005 — Scoped specialists, deterministic control plane
**Decision:** Each LLM specialist owns exactly one exemption; the orchestrator and the
reconciler are deterministic code, not a model.
**Why:** A misfire is bounded to "misjudged its one thing." You don't put a stochastic
system in charge of *whether* records get released or in charge of the net that checks it.
**Trade-off:** Less emergent flexibility than a single autonomous agent — intentional.

### DD-006 — The verify-loop is recall-monotonic
**Decision:** The agentic verify-loop (`VerifyLoopAgent`) only *adds* verified spans; the
precision/prune pass is opt-in (`prune: false` default).
**Why:** Measured: an auto-prune step deleted true PERSON spans (recall 63%→43%). In an
over-catch domain scored on recall, dropping a true span is the dangerous direction.
**Trade-off:** Over-redaction handled elsewhere (DD-008), not by the loop.

### DD-007 — Eval-gated autonomous promotion; deploy = config flip
**Decision:** A versioned **registry** holds profiles (working copy = staging, active
version = prod). `promote.py` runs a deterministic scorecard and ships only if every gate
passes, else holds and escalates. Promotion is an atomic, reversible version-pointer flip.
**Why:** Lets a client add a new doc type and push it live without engineering — the *gate*,
not a human reviewer, is what makes that safe. Because profiles are data, rollback is instant.
**Trade-off:** A brand-new class can't ship until it has labeled gold (fails closed).

### DD-008 — Precision gate from the officer-feedback stream
**Decision:** The console's accept/reject/modify/flag-missed actions are a live labeled
stream; the promotion scorecard enforces an officer override-rate ceiling (over-redaction),
complementing the recall gold gate.
**Why:** A recall-only gate is blind to over-flagging; both error directions must gate a
deploy.
**Trade-off:** Precision gate is informational until enough feedback accrues — accepted.

### DD-009 — Ship as a launchable repo, not a container  (revised)
**Decision:** The project is run by cloning the repo and executing `./run.sh` (venv setup →
safety eval → console at :8000). No Docker.
**History / why revised:** This started as a single container image with a prod/debug
`APP_MODE` toggle. That broke against DD-013: the model transport is the operator's *logged-in
Claude Code CLI*, and a slim container can't borrow a host login (macOS keeps the token in the
keychain). So the container could only ever run the deterministic layer — it added build/
mount friction without delivering the live judgment layer. A plain repo launched locally *is*
the live path (it runs where you're already signed into Claude Code), and it's the lower-
friction thing for someone evaluating the work.
**Trade-off:** Less "runs identically anywhere" than a container. Accepted: the audience runs
Claude Code locally; `./run.sh` is one command; the seam to containerize later (for a keyed
deployment) is still clean. Kept as a *decision* with its reversal visible, not silently
dropped.

### DD-010 — Context layer (DB of file pointers, injected at runtime)
**Decision:** A small SQLite DB maps `(profile, exemption)` to reference files; at agent
runtime the relevant files are read and injected as authoritative context.
**Why:** Context engineering for accuracy without baking guidance into code; a client edits
guidance files, not prompts.
**Trade-off:** Another store to seed — done idempotently on boot.

### DD-011 — Agent memory layer (learned state, recalled at runtime)
**Decision:** A SQLite-backed memory distils the feedback stream into durable lessons (e.g.
"detector X over-flags here") and recalls them into the agent at runtime.
**Why:** Closes the loop feedback → memory → runtime context → better proposals; the agent
improves across runs without retraining.
**Trade-off:** Memory is heuristic and earned, not ground truth — kept advisory (injected as
guidance, never as a hard rule).

### DD-012 — Implicit type sorting + failure-triggered review
**Decision:** Every document is sorted into a type by a deterministic classifier as it
flows through the pipeline; a per-type health monitor watches failure signals (novel type,
escalation spike, zero coverage, override-rate, officer-flagged misses) and **auto-opens a
review of the analyzing profile** when a threshold breaks — all inside `pipeline.run`, no
user action.
**Why:** The operator shouldn't have to notice that a new document type appeared or that the
system is mis-analyzing one. The classifier is deterministic (a stochastic classifier in
this control-plane role is the thing the system avoids), and a failure **opens a review**
that feeds the eval-gated promotion pipeline — it never silently re-tunes the profile.
**Trade-off:** The classifier is coarse heuristics (a court letter with a "Re:" line may sort
as a memo); the type label drives monitoring, not redaction, so coarse is acceptable. An LLM
classifier could refine labels later without touching the deterministic trigger logic.

### DD-014 — Triage is an async job with per-document status
**Decision:** `POST /api/process` starts a background job and returns immediately; the work
runs with limited concurrency, updating each document's state (queued → processing → done)
under a lock. The console polls `GET /api/status` and shows a progress bar + per-doc chips,
rendering documents as they complete.
**Why:** Live model calls take ~30s/doc, so a synchronous POST just hung with no feedback.
The natural FOIA workflow is to **review documents as they land**, not wait for the whole
batch — so per-document status (not just a batch spinner) is the right granularity.
**Trade-off:** A little shared-state plumbing (a lock; a retry in `/api/status` for the
serialize-vs-append race). Accepted; the package is small and the UX win is large.

### DD-013 — Transport is the operator's logged-in Claude Code, not a managed API key
**Decision:** The model transport is the locally-installed, logged-in **Claude Code CLI** —
no `ANTHROPIC_API_KEY` is expected, in dev OR prod. `llm.judge` uses the CLI when no key is
set; a key still works but is never required. The deterministic safety layer, the console,
and the fail-closed gate run with **zero credentials**; only the LLM judgment layer needs
Claude Code, and its absence degrades safely to escalation.
**Why:** This is a portfolio/demo system whose audience already runs Claude Code. Forcing a
key to provision is friction and a secret to manage; assuming Claude Code lets anyone clone
and `./run.sh` to see the system work, with the live judgment layer active simply by running
where they're signed in. No secrets in the repo or CI.
**Trade-off:** None at the demo's scale. (This constraint is also *why* the project ships as a
repo rather than a container — DD-009 — since a slim container can't borrow a host's Claude
Code login.) A real keyed deployment would set `ANTHROPIC_API_KEY`, which the same `llm.judge`
path already supports.

### DD-015 — Real born-digital PDFs as the live corpus; a `uscourts` terrain proves the swap
**Decision:** The live corpus is **real, born-digital government PDFs** — federal court
records (orders, R&Rs, habeas dispositions) from GPO's **govinfo USCOURTS** collection,
curated to ≤4 pages with a clean text layer. `pipeline.read_doc` extracts text (poppler
`pdftotext`, falling back to `pypdf`); the extracted text flows through the *unchanged* text
pipeline — redaction offsets are character offsets into it. A new **`uscourts` terrain
profile** retargets the detectors/specialists for the court domain, and the server runs on it
(`FOIA_PROFILE=uscourts`); `FOIA_CORPUS` still points back to the synthetic `data/docs` set.
**Why:** A portfolio demo on hand-written synthetic text under-sells the work. Real records
exercise the parts that matter — PDF extraction, multi-page layout, real institutional
language, and the *terrain* thesis (DD-004): moving to court records is a profile + a corpus
dir, **no engine change**. It also surfaced the real lesson — the federal detectors over-fire
on court text (every filing date read as a DOB, "2 The Court" as a street address, chambers
`.gov` emails as private). The `uscourts` profile fixes that deterministically (DOB needs a
contextual cue; "Court" dropped as a street suffix) and leaves the hard call — *private
litigant vs. judge/clerk/counsel of record* — to the b6 specialist, validated live: on a real
garnishment R&R the agent withheld the defendant and an affected third party while releasing
the District Judge, the Magistrate Judge, counsel, and the case number.
**Why court records over a PII-rich set:** federal filings are deliberately PII-light (Fed. R.
Civ. P. 5.2 makes filers pre-redact SSNs/DOBs/account numbers), and they are already public
record — so the demo runs on genuinely-public text and never republishes private PII. The
vivid raw-identifier showcase (SSN/address/DOB/deliberative) stays in the synthetic, harm-free
**Examples** corpus. Because the deterministic boot runs model-free, the in-medias-res board
stages the privacy specialist's contextual name redactions from each caption (`_court_party_
redactions`); a live "run agent" re-derives them with the model.
**Trade-off:** Extraction adds a system dep (poppler) with a pure-Python fallback (`pypdf`);
court orders show fewer raw-format hits than the synthetic set, by design. Accepted — the
realism and the demonstrated terrain-swap are worth more than a denser redaction count.

### DD-016 — Redaction taxonomy widened to the law-enforcement exemptions + more identifiers
**Decision:** The court terrain now carries the full law-enforcement privacy schedule — **b7C**
(LE personal privacy), **b7D** (confidential source), **b7F** (physical safety) — alongside the
existing b7E, and the deterministic detector set gains the identifiers FRCP 5.2 + standard DLP
name but the profile lacked: **taxpayer ID/EIN, passport, driver's license, alien-registration
A-number, brand-prefixed credit card, IPv4**. Two new *scoped* specialists (DD-005) propose the
genuinely contextual LE calls — `confidential_source` → b7D, `safety` → b7F. The console picker
renders all seven exemptions from its single `EXEMPTIONS` source of truth.
**Why:** The live corpus skews criminal/habeas (one is a terrorism case), where confidential-
source and witness-safety calls are exactly what a real reviewer makes — modeling only b6/b5/b7E
under-represented the domain. The new detectors are kept precise (passport/DL require a
contextual cue; the card pattern is brand-prefixed) so they don't reintroduce the court-text
false positives DD-015 fixed — verified zero FPs on the corpus.
**Why b7C is manual-only:** b7C (LE personal privacy) overlaps heavily with the b6 privacy
specialist, and proposals are merged without cross-specialist span dedupe — an auto b7C
specialist would double-flag every name. So b7C lives in the schedule + picker for the officer
to *reclassify* a b6 name when the record is law-enforcement, rather than as a second proposer.
**Trade-off:** Two more specialists = more model calls per live "run agent" (boot is model-free,
so the in-medias-res state is unchanged). Exemptions 8/9 (bank-exam, well data) and 7(A)/(B)
remain out of scope — irrelevant to this terrain.

### DD-017 — Three explicit support layers: Corpus, Guidelines, and Corrections-RAG
**Decision:** The model's three reasoning aids are now distinct, each **viewable in the console**
*and* **injected at runtime** by `agents._augment` (in order): **CORPUS** — the regulations/law
for the terrain (`context_layer`, `kind="regulation"`: FOIA §552(b) schedule, FRCP 5.2, Judicial
Conference policy); **GUIDELINES** — the how-we-redact rules (`kind="guideline"` `.md` files, with
the worked before/after **Examples** folded in); and **CORRECTIONS** — the officer overrides most
relevant to *this* document, retrieved by lexical RAG (`memory.recall_corrections`). The console's
"Examples" button became **Guidelines**, plus new **Corpus** and **Learned** views
(`/api/guidelines`, `/api/corpus`, `/api/learned`).
**Why:** Past officer decisions — when a redaction had to be **removed** (`rejected`), **added**
(`missed`), or **reclassified** — are the richest signal the system has, but it was barely used: the
only thing learned was an aggregate "detector over-flags" caveat, `reclassify` wasn't recorded, and
reject/accept records didn't even carry the span text. Now every correction is logged with its
text (`feedback.record` + `corrections()`), and at runtime the agent retrieves the corrections whose
distinctive words overlap the document it's assessing and weighs them — closing the loop
*feedback → learn → RAG → better proposal* with no separate training step.
**Why lexical RAG (not embeddings):** corrections are short, concrete spans where word-overlap is a
strong signal; it's deterministic, ships in-repo, and keeps the zero-credential design (no embedding
model/vector store). Verified: a recorded "released «Acme Corporation»" surfaces on a doc that names
Acme and stays silent on an unrelated doc; the prior ablation (DD-memo eval) showed the injection
channel measurably moves recall.
**Trade-off:** Lexical retrieval misses paraphrase/synonymy an embedding would catch — acceptable for
named spans; an embedding backend could slot in behind the same `recall_corrections` seam later.
Corrections + context files stay live/un-versioned (promotion still snapshots only the policy YAML).

### DD-018 — A dep-free, isolated test harness (no pytest)
**Decision:** `tests/run.py` is a tiny runner that discovers `test_*` functions and executes each
with **fully isolated state** — temp dirs for the feedback/memory/context/health stores and
`FOIA_DISABLE_MODEL=1` — printing a pass/fail summary (`python -m tests.run [-v]`). Unit tests hit
the safety layers directly; API tests drive the real app through Starlette's `TestClient` (firing
the startup seed against the isolated state), covering the boot arrangement, the support-layer
endpoints, officer-action → audit + feedback wiring, and the fail-closed release gate **both
directions** (409 blocked → review all → 200 released).
**Why no pytest:** the project's ethos is clone-and-run with zero credentials and minimal deps; the
evals are already plain `python -m evals.X` scripts, and `TestClient` ships with FastAPI. A 60-line
runner keeps the suite installable-free and CI-safe, and the determinism (no model) means it never
flakes. Isolation via monkeypatching each store's module-global path keeps tests from ever touching
the curated demo data (the Learned corrections, the context DB).
**Companion refactor:** extracted `_pkg()` / `_doc()` helpers in `app.py` (deduping the 404/lookup
boilerplate across seven handlers) and fixed the phone detector's span so a leading `(` is captured
(`\b` → `(?<!\w)`), without re-introducing letter-adjacent false positives — both now guarded by tests.
**Trade-off:** less ecosystem tooling than pytest (no fixtures/parametrize/plugins). Accepted at this
size; the runner is small enough to extend, and a `pytest` shim could wrap the same `test_*` functions
later with no rewrite.
