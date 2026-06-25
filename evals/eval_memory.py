"""Does the MEMORY layer measurably help the LLM — beyond what the context layer already gives?

Honest ablation on the privacy (b6) specialist. The support layers exist only to aid the
model's own reasoning, so we isolate each one:

  A  bare LLM           context.retrieve -> ""   memory.recall -> ""
  B  + context (corpus) context.retrieve -> real memory.recall -> ""      (the shipped baseline)
  C  + context + memory context.retrieve -> real memory.recall -> LESSON  (a targeted lesson)

Probes are short court records with a known gold: PRIVATE parties to withhold + OFFICIALS to
release. We score, per trial, RECALL (private parties caught) and OVER-REDACTION (officials
wrongly withheld). Multiple trials per condition because the model is stochastic — a one-shot
delta is noise. The verdict reports the means AND the spread, and says plainly when a layer
does nothing measurable.

The LESSON in C is the kind of caveat an officer's corrections WOULD teach — but note: the
current learn_from_feedback() only distils *detector over-flag* lessons (about the regex layer,
which the LLM doesn't control), so this measures memory's POTENTIAL via the injection channel,
not today's auto-learner. That gap is itself a finding.

    FOIA_PROFILE=uscourts EVAL_MEM_TRIALS=4 python -m evals.eval_memory
"""
import os
import statistics
import sys

from backend import agents, context_layer, exemptions, memory

TRIALS = int(os.environ.get("EVAL_MEM_TRIALS", "4"))

# Gray-zone rules that the generic context principle ("officials aren't private") does NOT
# supply — the kind of terrain-specific corrections an officer would teach the memory layer.
LESSON = (
    "- A personal name that forms part of the TITLE of a public statute, act, building, road, or "
    "fund (e.g. 'the Dolores Hartman Memorial Highway Act') is a public commemoration — RELEASE it; "
    "withhold only living private individuals named as parties/applicants.\n"
    "- Initials or docket pseudonyms used for a MINOR or victim (e.g. 'J.T.', 'Doe-2') remain "
    "protected identifiers — WITHHOLD them; do not treat them as already anonymized.\n"
    "- A description that singles out ONE identifiable person by role + place + time (a "
    "quasi-identifier) must be withheld even when no name is given.")

PROBES = [
    {   # OVER-REDACTION trap: a public figure embedded in a statute title (should be kept)
        "doc": ("The grant was authorized under the Dolores Hartman Memorial Highway Act. The "
                "actual applicant, a private resident named Trent Boll, requested $40,000 for the "
                "frontage repair."),
        "withhold": ["Trent Boll"],
        "keep": ["Dolores Hartman"],
    },
    {   # RECALL trap: minors referred to by initials / pseudonym (still protected)
        "doc": ("The minor, referred to throughout the transcript as J.T., was interviewed by the "
                "guardian ad litem. A second juvenile, identified only as Doe-2, declined to "
                "provide a statement."),
        "withhold": ["J.T.", "Doe-2"],
        "keep": [],
    },
    {   # RECALL trap: a quasi-identifier — one person singled out by role+place+time, no name
        "doc": ("No name was given for the source, but the filing describes the tipster as the "
                "night-shift janitor at the Belmont courthouse who was hired last winter."),
        "withhold": ["night-shift janitor at the Belmont courthouse"],
        "keep": [],
    },
]


def _matched(gold, proposals):
    """A gold span is 'flagged' if any proposal quote contains it, is contained by it, or
    shares its last token (handles whole-entity vs sub-phrase quoting)."""
    g = gold.lower()
    last = g.split()[-1]
    quotes = [p.quote.lower() for p in proposals if p.quote]
    return any(g in q or q in g or last in q for q in quotes)


def _score(probe, proposals):
    real = [p for p in proposals if p.quote and not p.escalate]
    recall = sum(_matched(w, real) for w in probe["withhold"])
    overred = sum(_matched(k, real) for k in probe["keep"])
    return recall, overred, bool(proposals and proposals[0].escalate)


def _run_condition(agent, ctx_on, mem_text):
    context_layer.retrieve = (lambda p, e=None: _REAL_CTX(p, e)) if ctx_on else (lambda p, e=None: "")
    memory.recall = lambda p, e=None, limit=5: mem_text
    rec_tot = wh_tot = over_tot = keep_tot = fails = 0
    per_trial = []
    for probe in PROBES:
        wh_tot += len(probe["withhold"]); keep_tot += len(probe["keep"])
        for _ in range(TRIALS):
            props = agent.run(probe["doc"], 0.65)
            r, o, failed = _score(probe, props)
            rec_tot += r; over_tot += o; fails += failed
            per_trial.append((r / len(probe["withhold"]), o))
    n = len(PROBES) * TRIALS
    recall = rec_tot / (wh_tot * TRIALS)
    overred_per_probe = over_tot / n
    recall_spread = [round(x[0], 2) for x in per_trial]
    return {"recall": recall, "overred_per_probe": overred_per_probe,
            "overred_total": over_tot, "keep_opportunities": keep_tot * TRIALS,
            "fails": fails, "recall_per_trial": recall_spread}


_REAL_CTX = context_layer.retrieve   # capture before we monkeypatch


def main() -> int:
    profile = os.environ.get("FOIA_PROFILE", "foia_federal")
    if not agents.llm.available():
        print("SKIP: model unavailable (need Claude Code logged in or ANTHROPIC_API_KEY).")
        return 0
    policy = exemptions.load_policy()
    spec = next((s for s in (policy.get("specialists") or []) if s["exemption"] == "b6"), None)
    instr = spec["instruction"] if spec else "Withhold private individuals' names and identifiers."
    agent = agents.ExemptionAgent("privacy", "b6", instr, profile)

    print("=" * 72)
    print(f"MEMORY ABLATION — privacy(b6) specialist | profile={profile} | "
          f"{len(PROBES)} probes × {TRIALS} trials")
    print("=" * 72)
    conds = [("A bare LLM         ", False, ""),
             ("B +context (corpus)", True, ""),
             ("C +context +memory ", True, LESSON)]
    results = {}
    for label, ctx_on, mem in conds:
        r = _run_condition(agent, ctx_on, mem)
        results[label.strip()[0]] = r
        print(f"\n{label}")
        print(f"    recall (private caught):      {r['recall']*100:5.1f}%   per-trial {r['recall_per_trial']}")
        print(f"    over-redaction (officials):   {r['overred_total']}/{r['keep_opportunities']} wrongly withheld "
              f"({r['overred_per_probe']:.2f}/probe)")
        if r["fails"]:
            print(f"    model no-determination trials: {r['fails']}")

    a, bb, c = results["A"], results["B"], results["C"]
    print("\n" + "=" * 72)
    print("VERDICT (brutally honest)")
    print(f"  corpus layer (A→B): recall {a['recall']*100:.0f}%→{bb['recall']*100:.0f}%, "
          f"over-redaction {a['overred_total']}→{bb['overred_total']}")
    print(f"  memory layer (B→C): recall {bb['recall']*100:.0f}%→{c['recall']*100:.0f}%, "
          f"over-redaction {bb['overred_total']}→{c['overred_total']}")
    d_rec = (c["recall"] - bb["recall"]) * 100
    d_over = bb["overred_total"] - c["overred_total"]
    if abs(d_rec) < 8 and d_over <= 0:
        print("  → MEMORY shows NO measurable improvement over context here (within model noise).")
    else:
        print(f"  → MEMORY moved recall {d_rec:+.0f} pts and cut over-redaction by {d_over} "
              f"(of {bb['overred_total']}).")
    print("  Caveat: today's learn_from_feedback() only learns DETECTOR over-flag lessons, which")
    print("  the LLM can't act on — so this is memory's POTENTIAL via the injection channel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
