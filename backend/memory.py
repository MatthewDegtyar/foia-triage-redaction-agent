"""Agent memory layer — persistent, learned state the agent carries across runs.

Distinct from the context layer (static, authoritative reference files): memory is *dynamic*
and *earned*. It learns from the officer-decision feedback stream — e.g. "in this terrain,
detector X over-flags, so be precise" — and recalls the relevant lessons into the agent at
runtime. Closes the loop: feedback -> memory -> runtime context -> better proposals.

  memory/memory.db   sqlite: (profile, scope, key, kind) -> content + weight + updated_at

learn_from_feedback(profile)  distils the feedback stream into memories (idempotent upsert).
recall(profile, exemption)    -> an injectable "what we've learned" string.
"""
import os
import re
import sqlite3
import time

from . import feedback

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
MEM_DIR = os.path.join(ROOT, "memory")
DB = os.path.join(MEM_DIR, "memory.db")

# how much over-flagging before it becomes a remembered caveat
_OVERFLAG_RATE = 0.40
_OVERFLAG_MIN = 10


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _conn():
    os.makedirs(MEM_DIR, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS memories (
        profile TEXT, scope TEXT, key TEXT, kind TEXT, content TEXT, weight REAL, updated_at TEXT,
        PRIMARY KEY (profile, scope, key, kind))""")
    return c


def remember(profile, scope, key, kind, content, weight=1.0):
    c = _conn()
    with c:
        c.execute("INSERT OR REPLACE INTO memories "
                  "(profile, scope, key, kind, content, weight, updated_at) VALUES (?,?,?,?,?,?,?)",
                  (profile, scope, key, kind, content, float(weight), _now()))
    c.close()


def learn_from_feedback(profile: str) -> int:
    """Distil the live feedback stream into durable memories. Currently: detectors whose
    officer override-rate is high become 'over-flags here' caveats the agent recalls."""
    m = feedback.metrics(profile).get(profile)
    if not m:
        return 0
    learned = 0
    for det, c in m.get("by_detector", {}).items():
        if c["decided"] >= _OVERFLAG_MIN and (c["override_rate"] or 0) >= _OVERFLAG_RATE:
            remember(profile, "detector", det, "over_flag",
                     f"'{det}' over-flags in this terrain (officers rejected "
                     f"{c['rejected']}/{c['decided']} = {c['override_rate']:.0%}). Be precise; "
                     f"do not propose this class loosely.", weight=c["override_rate"])
            learned += 1
    return learned


def recall(profile: str | None, exemption: str | None = None, limit: int = 5) -> str:
    """Injectable 'what we've learned' for this terrain, strongest lessons first."""
    if not os.path.exists(DB):
        return ""
    c = _conn()
    rows = c.execute(
        "SELECT content FROM memories WHERE profile IN (?, '*') ORDER BY weight DESC LIMIT ?",
        (profile or "*", limit)).fetchall()
    c.close()
    return "\n".join(f"- {r[0]}" for r in rows)


# ── Corrections-RAG: retrieve the officer corrections most RELEVANT to the doc at hand ──────
_STOP = {"the", "and", "for", "that", "this", "with", "from", "was", "are", "his", "her", "its",
         "not", "but", "has", "had", "who", "all", "any", "out", "ref", "via", "per", "inc",
         "llc", "case", "court", "order", "filed", "page", "docket", "document", "motion"}


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 3 and t not in _STOP}


def _lesson(r: dict, text: str) -> str:
    ex, prior = r.get("exemption") or "?", r.get("prior_exemption") or "?"
    if r["action"] == "rejected":
        return f'Officer RELEASED "{text}" — do not over-withhold this kind here.'
    if r["action"] == "reclassified":
        return f'Officer reclassified "{text}": {prior} → {ex} (use {ex}).'
    return f'Officer WITHHELD "{text}" as {ex} — the agent had missed it; catch this kind.'


def recall_corrections(profile: str | None, doc_text: str, k: int = 5,
                       min_coverage: float = 0.5) -> str:
    """Lexical RAG over the officer-correction stream: rank past corrections by how much of their
    span's distinctive words appear in THIS document, return the top-k as directional lessons.
    No model, no embeddings — deterministic word overlap. '' when nothing is relevant."""
    doc = _tokens(doc_text)
    if not doc:
        return ""
    scored, seen = [], set()
    for r in reversed(feedback.corrections(profile)):   # newest first -> dedupe keeps the latest
        text = (r.get("text") or "").strip()
        key = (text.lower(), r["action"])
        if not text or key in seen:
            continue
        span = _tokens(text)
        if not span:
            continue
        coverage = len(span & doc) / len(span)          # fraction of the span's words present in the doc
        if coverage >= min_coverage:
            seen.add(key)
            scored.append((coverage, len(span & doc), _lesson(r, text[:90])))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return "\n".join(f"- {lesson}" for _, _, lesson in scored[:k])


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    prof = sys.argv[2] if len(sys.argv) > 2 else "foia_federal"
    if cmd == "learn":
        n = learn_from_feedback(prof)
        print(f"learned {n} memory(ies) for '{prof}' from feedback")
    elif cmd == "show":
        c = _conn()
        rows = c.execute("SELECT profile, scope, key, kind, content, weight FROM memories "
                         "WHERE profile IN (?, '*') ORDER BY weight DESC", (prof,)).fetchall()
        if not rows:
            print(f"no memories for '{prof}' yet.")
        for r in rows:
            print(f"  [{r[5]:.2f}] {r[0]}/{r[1]}/{r[2]} ({r[3]}): {r[4]}")
        c.close()
