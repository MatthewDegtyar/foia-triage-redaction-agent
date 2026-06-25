"""A curated corpus of redaction examples — the agent's "answer key".

Each example is a small document with its CORRECT redactions hand-labeled: what to withhold,
the exemption, and *why*; plus, where instructive, what NOT to withhold (false-positive
traps). The corpus doubles as documentation (the criteria the agent applies) and as a
demonstration of genuinely hard cases — contextual PII, official-vs-private judgment,
deliberative material, and look-alike traps — that a naive regex pass gets wrong.

Served at /api/examples; rendered in the console's "Examples" view. Independent of the live
model, so it always renders the before/after correctly.
"""

# The criteria the agent applies, shown as a legend.
CRITERIA = [
    {"code": "b6", "label": "Personal privacy (PII)",
     "look_for": "SSN, home address, personal phone, DOB, personal email, account numbers, "
                 "and the names/identifying details of PRIVATE individuals."},
    {"code": "b5", "label": "Deliberative process",
     "look_for": "Pre-decisional recommendations, attorney-client advice, work product — even "
                 "when it contains no PII."},
    {"code": "b4", "label": "Confidential commercial",
     "look_for": "Trade secrets and confidential commercial or financial information — e.g. an "
                 "applicant's account/invoice numbers tied to a private party."},
    {"code": "b7C", "label": "Law-enforcement privacy",
     "look_for": "Names/identifying details of suspects, targets, or third parties appearing in "
                 "law-enforcement records (a heightened privacy interest beyond ordinary b6)."},
    {"code": "b7D", "label": "Confidential source",
     "look_for": "The identity of a confidential informant or source — a name, a CI number, or "
                 "distinguishing facts that would reveal one."},
    {"code": "b7E", "label": "Law-enforcement technique",
     "look_for": "Investigative techniques/procedures whose disclosure risks circumvention."},
    {"code": "b7F", "label": "Personal safety",
     "look_for": "Locating details whose disclosure could endanger an individual's life or "
                 "physical safety — e.g. a threatened witness's whereabouts."},
    {"code": "—", "label": "Do NOT withhold",
     "look_for": "Government staff acting in their official capacity, public case/permit numbers, "
                 "and purely factual, already-public material."},
]

# Each redaction: quote (verbatim), exemption, label, why. Each trap: quote, why (NOT withheld).
EXAMPLES = [
    {
        "id": "contact-card", "title": "Applicant contact in an email", "difficulty": "standard",
        "doc_type": "email",
        "text": ("From: Dana Whitfield <dana.whitfield@agency.example.gov>\n"
                 "To: Permit Review Team\nSubject: Cedar Park solar permit CP-SOLAR-2231\n\n"
                 "Logging the applicant contact. Primary applicant is Marcus Reedy, SSN "
                 "123-45-6789, cell 512-555-0143, personal email marcus.reedy@gmail.com, "
                 "residence 4821 Limestone Ridge Road, Cedar Park. DOB 04/17/1979."),
        "redactions": [
            {"quote": "Marcus Reedy", "exemption": "b6", "label": "private individual",
             "why": "Name of a private applicant (not a government employee)."},
            {"quote": "123-45-6789", "exemption": "b6", "label": "SSN", "why": "Social Security number."},
            {"quote": "512-555-0143", "exemption": "b6", "label": "personal phone", "why": "Personal cell."},
            {"quote": "marcus.reedy@gmail.com", "exemption": "b6", "label": "personal email",
             "why": "Private individual's personal email."},
            {"quote": "4821 Limestone Ridge Road", "exemption": "b6", "label": "home address",
             "why": "Residential address of a private individual."},
            {"quote": "04/17/1979", "exemption": "b6", "label": "DOB", "why": "Date of birth."},
        ],
        "skip": [
            {"quote": "dana.whitfield@agency.example.gov", "why":
             "Government staff acting officially — released, not withheld."},
            {"quote": "CP-SOLAR-2231", "why": "Public permit number — not personal."},
        ],
        "note": "The baseline: direct identifiers caught deterministically; the agent keeps the "
                "official .gov address and the public permit number.",
    },
    {
        "id": "pii-in-prose", "title": "PII buried in narrative (no labels)", "difficulty": "challenging",
        "doc_type": "letter",
        "text": ("Dear Review Board,\n\nI am writing about my neighbor's permit. The applicant, "
                 "Priya Vance, lives over on Cypress Creek — number 1140 — and mentioned she was "
                 "born the same spring as my daughter, April of 1986. You can usually catch her on "
                 "her cell, five-one-two, five-five-five, oh-one-eight-eight.\n\nRegards,\nA concerned resident"),
        "redactions": [
            {"quote": "Priya Vance", "exemption": "b6", "label": "private individual",
             "why": "Named private third party."},
            {"quote": "1140", "exemption": "b6", "label": "home address",
             "why": "Address given in prose ('Cypress Creek — number 1140'); a regex keyed to "
                    "'<number> <Street> St/Rd' misses it."},
            {"quote": "April of 1986", "exemption": "b6", "label": "DOB",
             "why": "Date of birth written as narrative, not MM/DD/YYYY."},
            {"quote": "five-one-two, five-five-five, oh-one-eight-eight", "exemption": "b6",
             "label": "personal phone", "why": "Phone number spelled out in words — no digit pattern to match."},
        ],
        "skip": [],
        "note": "Why the model layer exists: none of these match a digit/format pattern. Catching "
                "them needs reading comprehension, not regex.",
    },
    {
        "id": "official-vs-private", "title": "Official vs. private individual", "difficulty": "challenging",
        "doc_type": "memo",
        "text": ("MEMORANDUM\nFrom: Lee Ortiz, Permit Desk (lee.ortiz@agency.example.gov)\n\n"
                 "Re: complaint intake. The complainant, Sarah Boyd (sarah.boyd@outlook.com, "
                 "512-555-0170), alleges the contractor cut corners. I (Lee, ext. 4471) logged it "
                 "and will route to inspections."),
        "redactions": [
            {"quote": "Sarah Boyd", "exemption": "b6", "label": "private individual",
             "why": "Private complainant."},
            {"quote": "sarah.boyd@outlook.com", "exemption": "b6", "label": "personal email", "why": "Private email."},
            {"quote": "512-555-0170", "exemption": "b6", "label": "personal phone", "why": "Private phone."},
        ],
        "skip": [
            {"quote": "Lee Ortiz", "why": "Agency staff named in official capacity — released."},
            {"quote": "lee.ortiz@agency.example.gov", "why": "Official work email — released."},
            {"quote": "ext. 4471", "why": "Office extension of an official — released."},
        ],
        "note": "Same surface form (a name, an email, a phone) — opposite decision. The distinction "
                "is *role*, which only context reveals.",
    },
    {
        "id": "deliberative", "title": "Deliberative recommendation (no PII)", "difficulty": "challenging",
        "doc_type": "memo",
        "text": ("MEMORANDUM — Cedar Park solar permit CP-SOLAR-2231\n\n"
                 "Factual: the setback measures 18 feet; code requires 15.\n\n"
                 "Pre-decisional (NOT for release): staff lean toward DENIAL on traffic-study "
                 "grounds, but this has not been briefed to the Director and the recommendation may "
                 "change. Hold internal until the Feb 28 review."),
        "redactions": [
            {"quote": "staff lean toward DENIAL on traffic-study grounds, but this has not been "
                      "briefed to the Director and the recommendation may change",
             "exemption": "b5", "label": "deliberative", "why":
             "Pre-decisional staff recommendation — withheld under b5 even though it contains no PII."},
        ],
        "skip": [
            {"quote": "the setback measures 18 feet; code requires 15", "why":
             "Purely factual and final — released. b5 protects the deliberation, not the facts."},
        ],
        "note": "Not every redaction is privacy. The hard part is separating the protected "
                "*recommendation* from the releasable *facts* in the same memo.",
    },
    {
        "id": "lookalike-trap", "title": "Look-alike trap (precision)", "difficulty": "challenging",
        "doc_type": "report",
        "text": ("INTERNAL REVIEW — case 36110/97, permit CP-SOLAR-2231, parcel ID 5829-114-002.\n\n"
                 "Throughput for Q1 was 412 permits. The applicant's account number on file is "
                 "acct #774413820. Invoice total 4,471.00."),
        "redactions": [
            {"quote": "acct #774413820", "exemption": "b4", "label": "account number",
             "why": "Confidential account number tied to the applicant (b4/b6)."},
        ],
        "skip": [
            {"quote": "36110/97", "why": "Public case number — not personal."},
            {"quote": "5829-114-002", "why": "Public parcel ID — a property record, not private."},
            {"quote": "412", "why": "An aggregate statistic — not an identifier."},
            {"quote": "4,471.00", "why": "An invoice amount — not an identifier."},
        ],
        "note": "Over-redaction is its own failure. Four ID-shaped numbers; only one is private. A "
                "trigger-happy redactor blacks out the whole document and buries the public record.",
    },
    {
        "id": "quasi-identifier", "title": "Quasi-identifier (identity by combination)", "difficulty": "challenging",
        "doc_type": "report",
        "text": ("The complaint was filed by the only female structural engineer in the Cedar Park "
                 "field office, who transferred from the Austin district last March. No name was "
                 "given, but the description is specific enough to identify her."),
        "redactions": [
            {"quote": "the only female structural engineer in the Cedar Park field office, who "
                      "transferred from the Austin district last March",
             "exemption": "b6", "label": "quasi-identifier", "why":
             "No direct identifier, yet the combination of role + office + transfer date singles out "
             "one person — a re-identification risk the privacy analysis must catch."},
        ],
        "skip": [],
        "note": "The hardest class: no SSN, no name — identity emerges from the *combination* of "
                "details. Pure pattern matching is blind to it.",
    },
    {
        "id": "lawenf-technique", "title": "Investigative technique (no PII)", "difficulty": "challenging",
        "doc_type": "report",
        "text": ("ORDER ON MOTION TO SUPPRESS\n\nThe agents located the handset by deploying a "
                 "cell-site simulator configured to force re-registration at fifteen-minute "
                 "intervals across the target sector — a method the Bureau does not disclose "
                 "publicly. The motion to suppress is DENIED."),
        "redactions": [
            {"quote": ("deploying a cell-site simulator configured to force re-registration at "
                       "fifteen-minute intervals across the target sector"),
             "exemption": "b7E", "label": "investigative technique",
             "why": "Non-public surveillance method; disclosing the how would let a target evade it."},
        ],
        "skip": [
            {"quote": "The motion to suppress is DENIED", "why":
             "The court's holding is public — b7E protects the method, not the outcome."},
        ],
        "note": "b7E withholds the *operational detail*, not the result. The order's ruling is "
                "released; the step-by-step that would defeat the technique is not.",
    },
    {
        "id": "lawenf-privacy", "title": "Third party in a law-enforcement record", "difficulty": "challenging",
        "doc_type": "memo",
        "text": ("INVESTIGATIVE SUMMARY\n\nSpecial Agent Dana Cole interviewed a neighbor, "
                 "Marcus Reedy, who is not a subject of this investigation but was present during "
                 "the search. He declined to give a further statement."),
        "redactions": [
            {"quote": "Marcus Reedy", "exemption": "b7C", "label": "third party (LE record)",
             "why": "A private third party named in a law-enforcement record — a heightened privacy "
                    "interest under 7(C), beyond an ordinary b6 name."},
        ],
        "skip": [
            {"quote": "Special Agent Dana Cole", "why":
             "A law-enforcement officer acting in an official capacity — released."},
        ],
        "note": "Same name shape, law-enforcement context: the third party shifts from b6 to b7C, "
                "while the agent acting officially stays released.",
    },
    {
        "id": "confidential-source", "title": "Confidential informant", "difficulty": "challenging",
        "doc_type": "memo",
        "text": ("AFFIDAVIT IN SUPPORT OF SEARCH WARRANT\n\nProbable cause rests in part on "
                 "information from a confidential informant, CI-2231, a former associate of the "
                 "defendant who has provided reliable information in three prior prosecutions. "
                 "CI-2231 reported the transaction on March 3."),
        "redactions": [
            {"quote": "CI-2231", "exemption": "b7D", "label": "confidential source",
             "why": "A code that identifies a confidential informant — withholding it protects the "
                    "source's identity."},
            {"quote": "a former associate of the defendant", "exemption": "b7D",
             "label": "source-identifying detail",
             "why": "A distinguishing fact that, combined with the rest, would reveal who the source is."},
        ],
        "skip": [
            {"quote": "has provided reliable information in three prior prosecutions", "why":
             "The reliability assertion supporting probable cause is releasable — it does not, alone, "
             "identify the source."},
        ],
        "note": "b7D guards the *identity*, including the breadcrumb facts — a CI number plus "
                "'former associate' can re-identify a source as surely as a name.",
    },
    {
        "id": "witness-safety", "title": "Witness under threat (safety)", "difficulty": "challenging",
        "doc_type": "letter",
        "text": ("VICTIM/WITNESS NOTICE\n\nCooperating witness Priya Vance has relocated; her "
                 "residence at 1140 Cypress Creek Road, Apt 4 must not appear on the public docket "
                 "given documented threats from the defendant's associates. Reach her only through "
                 "the U.S. Attorney's Office, 500 Courthouse Way."),
        "redactions": [
            {"quote": "Priya Vance", "exemption": "b7F", "label": "witness (safety)",
             "why": "A cooperating witness under documented threat — identifying details withheld to "
                    "protect physical safety under 7(F), not merely privacy."},
            {"quote": "1140 Cypress Creek Road, Apt 4", "exemption": "b7F", "label": "whereabouts (safety)",
             "why": "Home address whose disclosure could endanger the witness."},
        ],
        "skip": [
            {"quote": "500 Courthouse Way", "why":
             "The U.S. Attorney's Office is an official location — released."},
        ],
        "note": "b7F turns on *danger*, not ordinary privacy: the documented threat is the trigger, "
                "so the witness's whereabouts are withheld while the courthouse address is released.",
    },
]


def _redact(text, redactions):
    """Produce the released ('after') text by replacing each redaction span with a marker."""
    out = text
    for r in redactions:
        out = out.replace(r["quote"], f"[█ {r['exemption']}]")
    return out


def corpus():
    items = []
    for ex in EXAMPLES:
        items.append({**ex, "after": _redact(ex["text"], ex["redactions"])})
    return {"criteria": CRITERIA, "examples": items}
