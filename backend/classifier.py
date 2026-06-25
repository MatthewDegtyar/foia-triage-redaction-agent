"""Implicit document-type sorter (deterministic).

Every document is sorted into a coarse type as it flows through the pipeline — no user
action. The classifier is heuristic regex on structural signatures, NOT a model: the type
label drives the health monitor (per-type failure detection), and a stochastic classifier
in that control-plane role is exactly what this system avoids. A document matching no known
signature is `unknown` — which is itself the signal that a NEW type has appeared and the
analyzing profile may need review.
"""
import re

# (type, signature regex). First match wins; order most-specific first.
_SIGNATURES = [
    ("court_judgment", re.compile(
        r"European Court of Human Rights|the Court (?:held|finds|considers)|"
        r"\bapplication no\.?\s*\d|\bv\.\s+[A-Z][a-z]+\b.*\bjudgment\b|"
        r"UNITED STATES (?:DISTRICT|BANKRUPTCY) COURT|U\.?S\.?\s+COURT OF APPEALS|"
        r"\bORDER (?:ADOPTING|GRANTING|DENYING|DISMISSING|VACATING|ON)\b|"
        r"\bMagistrate Judge\b|\bWrit of Habeas Corpus\b|\bCASE NO\.", re.I)),
    ("email", re.compile(r"^\s*from:\s.+\n(?:.*\n)?\s*to:\s", re.I | re.M)),
    ("memo", re.compile(r"\bMEMORANDUM\b|^\s*(?:to|from|re):\s.+$", re.I | re.M)),
    ("letter", re.compile(r"\bDear\s+[A-Z]|\b(?:Sincerely|Yours (?:sincerely|faithfully)|Regards),", re.I)),
    ("form", re.compile(r"\b(?:Form\s+[A-Z0-9-]{2,}|Section\s+\d+[.:])|_{5,}|\[\s*\]", re.I)),
    ("report", re.compile(r"\b(?:Executive Summary|Findings|Background|Recommendation)s?\b", re.I)),
]


def classify(text: str) -> str:
    """Return a coarse document-type label, or 'unknown' if no signature matches."""
    head = text[:4000]
    for label, rx in _SIGNATURES:
        if rx.search(head):
            return label
    return "unknown"


def known_types() -> list[str]:
    return [t for t, _ in _SIGNATURES]
