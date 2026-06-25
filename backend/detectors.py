"""Deterministic PII detectors.

Design stance: the catastrophic FOIA failure is a *false negative* — releasing an
SSN, a home address, a personal phone. So the detectors that guard the worst
disclosures are deterministic regex, not a stochastic model: you do not trust an
LLM to be the only thing standing between a citizen's SSN and a public release.
The LLM handles judgment (responsiveness, contextual exemptions); these patterns
handle the things that can be caught deterministically, and they're tuned to
over-catch — the human officer prunes false positives, which is cheap. A missed
one is not.

Every hit carries its detector name and the exemption it maps to, so the redaction
log is a defensible administrative record, not a black box.
"""
import re
from dataclasses import dataclass


@dataclass
class Hit:
    start: int
    end: int
    text: str
    pii_type: str
    detector: str
    exemption: str          # statutory basis, e.g. "b6"
    confidence: float       # deterministic hits are high-confidence


# The DEFAULT (U.S. federal FOIA) safety-critical class. A terrain profile may add to
# or replace this via its `detectors:` section (see backend/profile.py); when a profile
# omits that section, this set is what runs — so the federal behavior is the baseline.
# Patterns -> (pii_type, exemption, confidence). Ordered; longer/safer first.
DEFAULT_PATTERNS = [
    ("ssn",            r"\b\d{3}-\d{2}-\d{4}\b",                              "b6", 0.99),
    ("ssn_nodash",     r"\b(?<!\d)\d{9}(?!\d)\b",                             "b6", 0.70),
    ("email",          r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b","b6", 0.95),
    ("phone",          r"(?<!\w)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b","b6", 0.90),
    ("phone_local",    r"\b\d{3}[-.\s]\d{4}\b",                                "b6", 0.75),
    ("dob",            r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}\b","b6", 0.85),
    ("credit_card",    r"\b(?:\d[ -]?){13,16}\b",                             "b6", 0.80),
    ("bank_acct",      r"\b(?:acct|account)\s*#?\s*\d{6,}\b",                 "b4", 0.85),
    ("ip_address",     r"\b(?:\d{1,3}\.){3}\d{1,3}\b",                        "b6", 0.60),
    ("street_address", r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){1,3}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Dr|Drive|Ln|Lane|Ct|Court|Way)\b","b6", 0.80),
    ("dl_number",      r"\b[A-Z]{1,2}\d{6,8}\b",                              "b6", 0.55),
]


def compile_patterns(specs: list | None) -> list:
    """Compile a terrain profile's detector specs into the runtime form.

    `specs` is a list of dicts {name, pattern, pii_type?, exemption?, confidence?} from a
    profile YAML, or None to use DEFAULT_PATTERNS. An invalid regex is skipped (not fatal)
    so one bad line in a profile can't take down the whole safety layer.
    """
    if not specs:
        return _compile_tuples(DEFAULT_PATTERNS)
    tuples = []
    for s in specs:
        name = s.get("name") or s.get("pii_type") or "custom"
        tuples.append((name, s["pattern"], s.get("exemption", "b6"), float(s.get("confidence", 0.8))))
    return _compile_tuples(tuples)


def _compile_tuples(tuples: list) -> list:
    out = []
    for (t, p, ex, c) in tuples:
        try:
            out.append((t, re.compile(p), ex, c))
        except re.error:
            continue  # skip malformed pattern; the rest of the layer still runs
    return out


_DEFAULT_COMPILED = _compile_tuples(DEFAULT_PATTERNS)


def detect(text: str, compiled: list | None = None) -> list[Hit]:
    """Run the detector layer. `compiled` comes from compile_patterns(); when omitted the
    built-in federal default set runs, so callers that don't carry a profile (e.g. the
    baseline safety eval) keep working unchanged."""
    cset = compiled if compiled is not None else _DEFAULT_COMPILED
    hits: list[Hit] = []
    for pii_type, rx, exemption, conf in cset:
        for m in rx.finditer(text):
            hits.append(Hit(m.start(), m.end(), m.group(), pii_type, f"regex:{pii_type}", exemption, conf))
    return _dedupe(hits)


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """Drop hits fully contained in a higher-confidence overlapping hit."""
    hits = sorted(hits, key=lambda h: (h.start, -(h.end - h.start), -h.confidence))
    kept: list[Hit] = []
    for h in hits:
        if any(k.start <= h.start and h.end <= k.end and k.confidence >= h.confidence and k is not h
               for k in kept):
            continue
        kept.append(h)
    return sorted(kept, key=lambda h: h.start)
