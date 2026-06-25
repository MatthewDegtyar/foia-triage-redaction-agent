"""Multi-agent orchestration for FOIA review.

Each agent is capability-scoped to one job, so its failure surface is bounded to that
one thing — the same forcing-function pattern proven under chaos. A deterministic
orchestrator fans each document out to the specialists concurrently and a deterministic
reconciler cross-checks them. Determinism in the *control plane* is deliberate: you do
not put a stochastic model in charge of whether records get released. The model is in
the scoped judgment nodes; the orchestration and the safety gate are code.

Agents:
  ResponsivenessAgent   — is this document responsive to the request?
  PrivacyAgent (b6/b7C)  — contextual personal privacy a regex can't catch (names of
                           private individuals, sensitive personal narrative)
  DeliberativeAgent (b5) — pre-decisional / attorney-client / work product
  LawEnforcementAgent (b7E) — techniques and procedures
  Reconciler             — deterministic QA: cross-agent disagreement + silent residue
"""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import context_layer, detectors, llm, memory


def _augment(profile: str | None, exemption: str | None, doc: str) -> str:
    """Context-engineer the user turn from the three support layers — the CORPUS (regulations),
    the GUIDELINES (how to apply them), and the officer CORRECTIONS most relevant to this very
    document (RAG) — then the document. All three aid the model's reasoning; none replace it."""
    head = ""
    regs = context_layer.retrieve(profile, exemption, kind="regulation")
    if regs:
        head += f"REGULATIONS (authoritative law for this terrain):\n{regs}\n\n"
    guide = context_layer.retrieve(profile, exemption, kind="guideline")
    if guide:
        head += f"GUIDELINES (how to apply them):\n{guide}\n\n"
    corr = memory.recall_corrections(profile, doc)
    if corr:
        head += ("PRIOR OFFICER CORRECTIONS most relevant to THIS document — the officer overrode "
                 f"the agent on similar text, so weigh these heavily:\n{corr}\n\n")
    caveat = memory.recall(profile, exemption)   # coarse aggregate caveat (e.g. a detector over-flags)
    if caveat:
        head += f"GENERAL LESSONS:\n{caveat}\n\n"
    return head + f"DOCUMENT:\n{doc}"


@dataclass
class Responsiveness:
    responsive: bool
    confidence: float
    rationale: str
    escalate: bool


@dataclass
class Proposal:
    quote: str
    exemption: str
    confidence: float
    rationale: str
    agent: str
    escalate: bool = False   # set on the agent's sentinel proposal when it couldn't decide


class ResponsivenessAgent:
    name = "responsiveness"

    def run(self, request: str, doc: str, threshold: float) -> Responsiveness:
        data = llm.judge(
            "You are a FOIA responsiveness analyst. Decide if the document is responsive "
            "to the request. JSON only: {\"responsive\":bool,\"confidence\":0..1,\"rationale\":\"..\"}. "
            "If genuinely unsure, give low confidence.",
            f"REQUEST:\n{request}\n\nDOCUMENT:\n{doc[:6000]}")
        if data is None:
            return Responsiveness(False, 0.0, "No model determination; escalated.", True)
        conf = float(data.get("confidence", 0.0))
        return Responsiveness(bool(data.get("responsive", False)), conf,
                              data.get("rationale", ""), conf < threshold)


class ExemptionAgent:
    """A scoped exemption specialist, configured from a terrain profile (name, the one
    exemption it owns, and its instruction). Keeping each agent scoped to ONE exemption
    bounds a misfire to 'misjudged its one thing'; defining the roster in config is what
    lets the SAME agent retarget to a new jurisdiction or document domain without code."""

    def __init__(self, name: str, exemption: str, instruction: str, profile_name: str | None = None):
        self.name = name
        self.exemption = exemption
        self.instruction = instruction
        self.profile = profile_name or os.environ.get("FOIA_PROFILE") or "foia_federal"

    def run(self, doc: str, threshold: float) -> list[Proposal]:
        data = llm.judge(
            f"You are an analyst scoped to ONE exemption: {self.exemption}. {self.instruction} "
            "Quote the EXACT text to withhold. JSON only: "
            "{\"proposals\":[{\"quote\":\"..\",\"confidence\":0..1,\"rationale\":\"..\"}]}. "
            "If unsure, omit it rather than guess.",
            _augment(self.profile, self.exemption, doc[:6000]))
        if data is None:
            # couldn't determine -> a single escalation sentinel, not silence
            return [Proposal("", self.exemption, 0.0, "No model determination.", f"agent:{self.name}", True)]
        out = []
        for p in data.get("proposals", []):
            conf = float(p.get("confidence", 0.0))
            if conf < threshold or not p.get("quote"):
                continue
            out.append(Proposal(p["quote"], self.exemption, conf,
                                p.get("rationale", ""), f"agent:{self.name}"))
        return out


class VerifyLoopAgent:
    """A tool-grounded propose -> verify -> critique/expand -> revise loop, scoped to ONE
    exemption. This is the genuinely *agentic* node: the model acts (proposes), the loop
    driver runs DETERMINISTIC tools and feeds their results back as observations, and the
    model decides what to add/drop on the next turn — repeating until it converges.

    Tools (executed by the driver, not invoked freely by the model — auditable by design):
      locate(quote)   : exact offset or None  -> unlocatable / phantom spans are dropped
      uncovered()     : detector hits not yet proposed -> grounds the 'what did you miss' turn

    Output is boosted on two axes:
      recall    : an exhaustive re-scan turn ('every distinct entity, incl. mentioned once'),
                  grounded by the deterministic candidate list, surfaces misses one shot leaves.
      precision : a 'what should NOT be withheld' pass drops over-redactions (e.g. a public
                  authority or an official acting officially).

    Bounded: max_iters turns and convergence (a turn that adds no new located span stops the
    loop). Same fail-safe as the single-shot agent: a null first determination -> escalate.
    """

    def __init__(self, name, exemption, instruction, compiled=None, max_iters=2, prune=False,
                 profile_name=None):
        self.name = name
        self.exemption = exemption
        self.instruction = instruction
        self.compiled = compiled            # compiled detector set for the uncovered() tool
        self.max_iters = max(1, max_iters)
        self.profile = profile_name or os.environ.get("FOIA_PROFILE") or "foia_federal"
        # prune=False keeps the loop RECALL-MONOTONIC: it only ADDS verified spans, never
        # auto-removes one. In an over-catch domain, dropping a true span is the dangerous
        # direction; the officer prunes false positives. Enable prune only when optimizing
        # precision against a measured precision target.
        self.prune = prune

    def _propose(self, d):
        return llm.judge(
            f"You are an analyst scoped to ONE exemption: {self.exemption}. {self.instruction} "
            "Quote the EXACT text to withhold. JSON only: "
            "{\"proposals\":[{\"quote\":\"..\",\"confidence\":0..1,\"rationale\":\"..\"}]}. "
            "If unsure, omit it rather than guess.",
            _augment(self.profile, self.exemption, d))

    def _critique(self, d, current, candidates):
        cur = "; ".join(current) or "(none yet)"
        cand = "; ".join(candidates) or "(none)"
        remove_clause = (
            ",\"remove\":[\"<an already-marked quote that should NOT be withheld, e.g. a public "
            "authority or official acting officially>\"]" if self.prune else "")
        remove_ask = (" Also flag any already-marked quote that should NOT be withheld."
                      if self.prune else "")
        return llm.judge(
            f"You are reviewing a colleague's withholding pass for ONE exemption: {self.exemption}. "
            f"{self.instruction}\n"
            f"ALREADY MARKED (do NOT repeat these): {cur}\n"
            f"DETECTOR-FLAGGED but not yet marked (adjudicate each): {cand}\n"
            "Re-read the DOCUMENT and find ADDITIONAL spans of this class that were MISSED. Be "
            "exhaustive: every DISTINCT item, including entities mentioned only once." + remove_ask +
            " JSON only: {\"add\":[{\"quote\":\"..\",\"confidence\":0..1,\"rationale\":\"..\"}]" +
            remove_clause + "}. Quote add-items EXACTLY from the document. Empty lists if none.",
            _augment(self.profile, self.exemption, d))

    def _uncovered(self, d, current):
        """Tool: deterministic detector hits not already covered by a current proposal."""
        if not self.compiled:
            return []
        out = []
        for h in detectors.detect(d, self.compiled):
            if not any(h.text in q or q in h.text for q in current):
                out.append(h.text)
        return out[:30]

    def run_traced(self, doc: str, threshold: float):
        """Core loop. Returns (proposals, stages) where stages[i] is the located-quote list
        after turn i — stages[0] is the single-shot equivalent, stages[-1] the final. The
        eval scores each stage to show the loop's lift WITHIN one run (no cross-run variance).
        """
        d = doc[:6000]
        data = self._propose(d)
        if data is None:
            sentinel = [Proposal("", self.exemption, 0.0, "No model determination.", f"agent:{self.name}", True)]
            return sentinel, None

        located: dict[str, tuple[float, str]] = {}   # quote -> (confidence, rationale)

        def take(props) -> int:
            added = 0
            for p in props if isinstance(props, list) else []:
                if not isinstance(p, dict):
                    continue
                q = p.get("quote")
                if not isinstance(q, str) or not q or q in located:
                    continue
                conf = float(p.get("confidence", 0.0))
                if conf < threshold or d.find(q) < 0:   # locate() tool: drop unlocatable
                    continue
                located[q] = (conf, p.get("rationale", ""))
                added += 1
            return added

        take(data.get("proposals", []))
        stages = [list(located)]
        for _ in range(self.max_iters - 1):
            crit = self._critique(d, list(located), self._uncovered(d, list(located)))
            if crit is None:
                break
            if self.prune:                              # opt-in precision pass (not monotonic)
                for item in crit.get("remove", []) or []:
                    q = item.get("quote") if isinstance(item, dict) else item
                    if isinstance(q, str):
                        located.pop(q, None)
            added = take(crit.get("add", []))
            stages.append(list(located))
            if added == 0 and not self.prune:           # converged: nothing new to add
                break

        props = [Proposal(q, self.exemption, c, r, f"agent:{self.name}") for q, (c, r) in located.items()]
        return props, stages

    def run(self, doc: str, threshold: float) -> list[Proposal]:
        props, _ = self.run_traced(doc, threshold)
        return props


# The DEFAULT (federal FOIA) specialist roster. A profile's `specialists:` section
# replaces this; when omitted, this is what runs.
DEFAULT_SPECIALISTS_SPEC = [
    {"name": "privacy", "exemption": "b6",
     "instruction": ("Withhold contextual personal privacy a pattern match can't catch: names "
                     "and identifying details of PRIVATE individuals (not government staff acting "
                     "in their official capacity), and sensitive personal narrative.")},
    {"name": "deliberative", "exemption": "b5",
     "instruction": "Withhold pre-decisional/deliberative discussion, attorney-client advice, and work product."},
    {"name": "lawenf", "exemption": "b7E",
     "instruction": "Withhold law-enforcement techniques and procedures whose disclosure risks circumvention."},
]


def build_specialists(policy: dict | None = None, compiled=None, profile_name=None):
    """Instantiate the specialist roster for a terrain profile, or the federal default.

    A spec with `loop: true` becomes a tool-grounded VerifyLoopAgent (optionally `max_iters`)
    — the agentic, accuracy-boosting verifier; otherwise a single-shot ExemptionAgent. `loop`
    is opt-in per agent because the loop costs extra model calls (a review-mode lever, not the
    throughput default). `compiled` is the detector set the loop's uncovered() tool uses;
    `profile_name` keys the context + memory injected at runtime."""
    specs = (policy or {}).get("specialists") or DEFAULT_SPECIALISTS_SPEC
    out = []
    for s in specs:
        if s.get("loop"):
            out.append(VerifyLoopAgent(s["name"], s["exemption"], s["instruction"], compiled,
                                       int(s.get("max_iters", 2)), bool(s.get("prune", False)),
                                       profile_name=profile_name))
        else:
            out.append(ExemptionAgent(s["name"], s["exemption"], s["instruction"],
                                      profile_name=profile_name))
    return out


class Reconciler:
    """Deterministic QA pass — the consistency-auditor. Runs with no model, because the
    safety net must not depend on the thing it's checking."""
    name = "reconciler"

    def review(self, responsive: bool, resp_escalate: bool, det_hit_count: int,
               specialist_escalations: list[str]) -> tuple[bool, list[str]]:
        notes, escalate = [], False
        if resp_escalate:
            escalate = True; notes.append("responsiveness undetermined")
        if specialist_escalations:
            escalate = True; notes.append("specialists undetermined: " + ",".join(specialist_escalations))
        # silent-residue guard: a 'non-responsive' doc that nonetheless contains PII is
        # suspicious — never let it pass on the agent's word alone.
        if not responsive and not resp_escalate and det_hit_count > 0:
            escalate = True
            notes.append(f"non-responsive doc holds {det_hit_count} PII hit(s); verify classification")
        return escalate, notes


class Orchestrator:
    def __init__(self, policy: dict | None = None, compiled=None, profile_name=None):
        self.responsiveness = ResponsivenessAgent()
        self.specialists = build_specialists(policy, compiled, profile_name)
        self.reconciler = Reconciler()

    def review_document(self, text: str, request: str, threshold: float, det_hit_count: int,
                        audit) -> tuple[Responsiveness, list[Proposal], bool, list[str]]:
        resp = self.responsiveness.run(request, text, threshold)
        audit("responsiveness", responsive=resp.responsive, confidence=resp.confidence, escalate=resp.escalate)

        # concurrent fan-out across scoped specialists
        proposals: list[Proposal] = []
        escalations: list[str] = []
        with ThreadPoolExecutor(max_workers=len(self.specialists)) as ex:
            results = list(ex.map(lambda a: (a.name, a.run(text, threshold)), self.specialists))
        for name, props in results:
            esc = [p for p in props if p.escalate]
            real = [p for p in props if not p.escalate]
            if esc:
                escalations.append(name)
                audit(f"agent:{name}", outcome="escalated")
            else:
                audit(f"agent:{name}", outcome="proposed", count=len(real))
            proposals.extend(real)

        escalate, notes = self.reconciler.review(resp.responsive, resp.escalate, det_hit_count, escalations)
        audit("reconciler", escalate=escalate, notes=notes)
        return resp, proposals, escalate, notes
