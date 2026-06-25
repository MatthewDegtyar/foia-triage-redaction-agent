"""Demo boot staging — lands the console 'in medias res' (DD-014 / DD-015).

Boot-only. The deterministic seed runs model-free for a fast launch, so it (a) stages the
representative mix an employer should see at a glance — a few docs the agent triaged that
AWAIT REVIEW, a couple already cleared, a queue still untouched — and (b) reproduces the
privacy specialist's contextual private-litigant redactions deterministically from the court
caption, which a live 'run agent' re-derives with the model.
"""
import os
import re

from . import exemptions, intake, runtime
from .schema import Redaction

# Caption shape in a federal court order:  "PEDRO ORTIZ,\n  Petitioner,"
_PARTY_ROLE = (r"(?:Petitioner|Movant|Plaintiff|Defendant|Respondent|Appellant|Appellee|"
               r"Debtor|Claimant)s?")
_PARTY_RX = re.compile(r"(?m)^([A-Z][A-Z.'\-]+(?:\s+[A-Z][A-Z.'\-]+){1,3})\s*,\s*\n\s*"
                       + _PARTY_ROLE + r"\s*[,.]")
# Names that are organizations / officials acting officially -> NOT private individuals.
_ORG_RX = re.compile(r"\b(?:UNITED STATES|SECRETARY|DEPARTMENT|FLORIDA|STATE|COUNTY|CITY|"
                     r"COMPANY|LLC|L\.L\.C|INC|CORP|N\.A|BANK|ASSOCIATION|COMMISSION|BUREAU|"
                     r"ATTORNEY GENERAL|DIRECTOR|COMMISSIONER|BOARD|SECURITIES|AGENCY|"
                     r"SERVICES|SYSTEMS|GROUP|TRUST|AMERICA|WARDEN)\b")


def court_party_redactions(text, policy):
    """Stage the contextual b6 call the privacy SPECIALIST makes — withholding a PRIVATE
    litigant's name (and every later mention) while releasing judges and counsel."""
    reds, seen = [], set()
    for m in _PARTY_RX.finditer(text):
        name = m.group(1).strip().rstrip(",")
        if _ORG_RX.search(name) or name in seen:
            continue
        seen.add(name)
        # withhold the caption form AND every case-insensitive mention in the body
        for occ in re.finditer(re.escape(name), text, re.I):
            reds.append(Redaction(
                start=occ.start(), end=occ.end(), text=occ.group(), pii_type="private_individual",
                exemption="b6", basis=exemptions.basis_for("b6", policy),
                detector="specialist:privacy", confidence=0.88,
                rationale=("Private litigant named in the record; withheld under the court's "
                           "privacy policy. Judges, court staff, and counsel of record are released."),
                status="proposed"))
    return reds


def seed_court_redactions(pkg, docs):
    """Add the staged private-party redactions to the given docs (boot demo only)."""
    policy = exemptions.load_policy()
    for d in docs:
        existing = {(r.start, r.end) for r in d.redactions}
        for r in court_party_redactions(d.text, policy):
            if (r.start, r.end) not in existing:
                d.redactions.append(r)
        d.redactions.sort(key=lambda r: r.start)


def arrange_demo_spread(pkg):
    """Land the demo 'in medias res': a few docs AWAITING REVIEW (the focal work), a couple
    already cleared, and a queue still untouched. The deterministic seed escalates everything,
    so we stage the representative mix here."""
    if pkg is None:
        return
    processed = [d for d in pkg.docs if d.agent_run]
    # stage the privacy specialist's contextual name redactions on the docs in play (model-free boot)
    seed_court_redactions(pkg, processed[:4])
    for d in processed[:3]:                          # NEEDS ATTENTION — agent assessed responsive;
        d.escalated = False                          # the redactions it found await the officer
        d.responsive = True
    if len(processed) >= 4:                          # CLEARED — reviewed + applied
        d = processed[3]
        d.escalated = False
        d.officer_responsive = True
        for r in d.redactions:
            r.status, r.decided_by, r.decided_at = "accepted", "officer", runtime.now()
    if len(processed) >= 5:                          # CLEARED — withheld (non-responsive)
        d = processed[4]
        d.escalated = False
        d.officer_responsive = False


def seed_on_startup():
    """Boot already-populated, mid-shift: 4 untouched docs in Unreviewed, 3 the agent triaged
    that need review, and 2 cleared — so the whole workflow reads at a glance."""
    os.environ["FOIA_DISABLE_MODEL"] = "1"
    try:
        intake.intake_raw_docs(intake.intake_docs(9, None))            # 9 raw -> Unreviewed
        tail = [d.doc_id for d in runtime.STATE["package"].docs[4:]]   # triage the last 5 (deterministic)
        intake.run_agent_on(tail, runtime.new_job())
        arrange_demo_spread(runtime.STATE["package"])                  # 3 need review, 2 cleared, 4 unreviewed
    except Exception:  # noqa: BLE001
        pass
    finally:
        os.environ.pop("FOIA_DISABLE_MODEL", None)
