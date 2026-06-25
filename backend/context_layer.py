"""Context layer — a small DB pointing to reference files, injected at agent runtime.

This is the "context engineering" substrate: instead of baking guidance into prompts, the
agent retrieves the authoritative reference material for the *current terrain + exemption*
at runtime and injects it. The DB stores POINTERS (file paths) + metadata; the files hold
the content, so a client can edit guidance without touching code (and the promotion gate
can version it like everything else).

  context/context.db        sqlite: which reference files apply to which (profile, exemption)
  context/files/*.md        the reference content the pointers resolve to

retrieve(profile, exemption) -> a budgeted, injectable context string.
"""
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CTX_DIR = os.path.join(ROOT, "context")
FILES_DIR = os.path.join(CTX_DIR, "files")
DB = os.path.join(CTX_DIR, "context.db")

# Default reference material, materialized on seed() so the layer is self-contained. A
# deployment edits these files (or adds rows pointing to its own) per terrain.
_DEFAULT_FILES = {
    "principles.md": (
        "# Redaction principles (all terrains)\n"
        "- Over-catch is cheaper than a miss: when unsure whether something identifies a "
        "private individual, propose it — the officer prunes.\n"
        "- Government staff acting in their official capacity are generally NOT private "
        "individuals; private third parties are.\n"
        "- Quote text to withhold EXACTLY as it appears so it can be located verbatim.\n"
    ),
    "foia_b6.md": (
        "# FOIA b6 — personal privacy\n"
        "Withhold information whose disclosure would be a clearly unwarranted invasion of "
        "personal privacy: names/contact details of private individuals, and personal "
        "identifiers (SSN, home address, personal phone, DOB). Official agency contacts are "
        "lower-sensitivity; flag but expect the officer to release.\n"
    ),
    "foia_b5.md": (
        "# FOIA b5 — deliberative process\n"
        "Withhold pre-decisional, deliberative discussion; attorney-client advice; attorney "
        "work product. Final decisions and purely factual material are generally NOT b5.\n"
    ),
    "echr_identity.md": (
        "# ECHR anonymization — direct identifiers\n"
        "Withhold the names of natural persons connected to the case (applicants, victims, "
        "relatives, witnesses) and direct identifiers that name them (application numbers when "
        "tied to a named party). Do NOT withhold the names of the Court, judges acting "
        "officially, or respondent States.\n"
    ),
    "echr_quasi.md": (
        "# ECHR anonymization — quasi-identifiers\n"
        "Withhold quasi-identifiers that re-identify the protected person in combination: "
        "specific home/work locations, employer, distinctive demographic details, exact dates "
        "tied to the individual. Generic legal/procedural dates are lower priority.\n"
    ),
    # ── CORPUS (regulations) — the authoritative law the agent references, distinct from the
    # how-to guidelines above. These are reference text, not operating instructions.
    "reg_foia_exemptions.md": (
        "# FOIA exemptions — 5 U.S.C. § 552(b)\n"
        "The nine statutory grounds to withhold:\n"
        "- **(b)(1)** Classified national-defense / foreign-policy information.\n"
        "- **(b)(2)** Records related solely to internal personnel rules and practices.\n"
        "- **(b)(3)** Information exempted from disclosure by another statute.\n"
        "- **(b)(4)** Trade secrets and confidential commercial or financial information.\n"
        "- **(b)(5)** Inter/intra-agency pre-decisional deliberative material; attorney-client "
        "and attorney work-product privileges.\n"
        "- **(b)(6)** Personnel, medical, and similar files whose disclosure would be a clearly "
        "unwarranted invasion of personal privacy.\n"
        "- **(b)(7)** Records compiled for law-enforcement purposes, to the extent disclosure "
        "could: **(A)** interfere with enforcement proceedings; **(B)** deprive a person of a "
        "fair trial; **(C)** be an unwarranted invasion of personal privacy; **(D)** disclose a "
        "confidential source; **(E)** reveal investigative techniques/procedures; **(F)** "
        "endanger an individual's life or physical safety.\n"
        "- **(b)(8)** Financial-institution examination reports.\n"
        "- **(b)(9)** Geological and geophysical information concerning wells.\n"
        "Foreseeable-harm standard: withhold only where disclosure would harm a protected "
        "interest the exemption covers.\n"
    ),
    "reg_frcp_5_2.md": (
        "# Fed. R. Civ. P. 5.2 — Privacy protection for court filings\n"
        "Unless the court orders otherwise, a filing that contains these must be redacted to:\n"
        "- **Social-Security / taxpayer-ID numbers** — last four digits only.\n"
        "- **Birth dates** — year only.\n"
        "- **Names of minors** — initials only.\n"
        "- **Financial-account numbers** — last four digits only.\n"
        "In criminal cases, **home addresses** are additionally limited to the city and state. "
        "The responsibility to redact rests on the filer, not the clerk; an unredacted, unsealed "
        "filing waives protection of the filer's OWN information only.\n"
    ),
    "reg_judicial_privacy.md": (
        "# Judicial Conference privacy policy (record release)\n"
        "Personal identifiers of private parties are withheld; the public docket is released. "
        "Withhold: pro se litigants', defendants', victims', witnesses', and minors' personal "
        "identifiers; cooperating-witness and informant identities and locating details. "
        "Release: the names of judges, magistrate judges, clerks, and counsel of record acting "
        "officially; case numbers; docket numbers; and the official .gov / chambers contacts of "
        "court staff. The distinction is ROLE, not surface form.\n"
    ),
    "reg_foia_foreseeable_harm.md": (
        "# FOIA foreseeable-harm standard + segregability\n"
        "- **Foreseeable harm (FOIA Improvement Act of 2016):** an agency may withhold only if it "
        "reasonably foresees that disclosure would harm an interest the exemption protects, OR "
        "disclosure is prohibited by law. An exemption *could* apply is not enough.\n"
        "- **Segregability (5 U.S.C. § 552(b), final sentence):** any reasonably segregable "
        "non-exempt portion must be released after deleting the exempt parts. Redact the span, "
        "not the document — release everything around it.\n"
    ),
    "reg_privacy_act.md": (
        "# Privacy Act of 1974 (5 U.S.C. § 552a)\n"
        "Governs records about individuals kept in a federal 'system of records' retrievable by a "
        "personal identifier. Disclosure of another person's record without consent is generally "
        "barred; in FOIA this reinforces b6 / b7(C). Withhold third-party personal records "
        "(SSNs, addresses, dates of birth, account and case-file identifiers) absent consent or a "
        "clear public interest that outweighs the privacy intrusion.\n"
    ),
    "reg_frcp_49_1.md": (
        "# Fed. R. Crim. P. 49.1 — Privacy protection in criminal filings\n"
        "The criminal-case analog to Civil Rule 5.2. Redact to: SSN/taxpayer-ID last four digits; "
        "birth year only; a minor's initials; financial-account last four; and the **city and "
        "state only** of a home address. Additional caution for cooperating defendants and "
        "witnesses whose identification could endanger safety (overlaps b7(F)).\n"
    ),
    "reg_ecf_policy.md": (
        "# Judicial Conference policy on electronic case files (remote public access)\n"
        "Because filings are remotely accessible on PACER/CM-ECF, the privacy exposure of an "
        "identifier is national, not local to a courthouse. Treat home addresses, personal "
        "phone numbers, financial-account and government-ID numbers of private parties as "
        "high-sensitivity; the public-record items (docket/case numbers, official court contacts) "
        "remain releasable.\n"
    ),
}

# (profile, exemption, kind, title, path, priority). profile/exemption '*' = applies to all.
# kind: "guideline" (how we redact) | "regulation" (the law the agent references — the Corpus).
_SEED = [
    ("*", "*", "guideline", "Redaction principles", "principles.md", 1),
    ("foia_federal", "b6", "guideline", "FOIA b6 personal privacy", "foia_b6.md", 10),
    ("foia_federal", "b5", "guideline", "FOIA b5 deliberative", "foia_b5.md", 10),
    ("echr_anonymization", "direct_id", "guideline", "ECHR direct identifiers", "echr_identity.md", 10),
    ("echr_anonymization", "quasi_id", "guideline", "ECHR quasi-identifiers", "echr_quasi.md", 10),
    # Corpus (regulations) — referenced authoritatively per terrain.
    ("foia_federal", "*", "regulation", "FOIA exemptions (5 U.S.C. § 552(b))", "reg_foia_exemptions.md", 5),
    ("foia_federal", "*", "regulation", "FOIA foreseeable-harm + segregability", "reg_foia_foreseeable_harm.md", 4),
    ("foia_federal", "*", "regulation", "Privacy Act of 1974", "reg_privacy_act.md", 3),
    ("uscourts", "*", "regulation", "Fed. R. Civ. P. 5.2", "reg_frcp_5_2.md", 5),
    ("uscourts", "*", "regulation", "Fed. R. Crim. P. 49.1", "reg_frcp_49_1.md", 5),
    ("uscourts", "*", "regulation", "Judicial Conference privacy policy", "reg_judicial_privacy.md", 4),
    ("uscourts", "*", "regulation", "Foreseeable-harm + segregability", "reg_foia_foreseeable_harm.md", 4),
    ("uscourts", "*", "regulation", "E-filing remote-access privacy policy", "reg_ecf_policy.md", 3),
]


def _conn():
    os.makedirs(CTX_DIR, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS context_items (
        profile TEXT, exemption TEXT, kind TEXT, title TEXT, path TEXT, priority INTEGER,
        PRIMARY KEY (profile, exemption, path))""")
    return c


def seed():
    """Idempotently materialize default reference files and register their pointers."""
    os.makedirs(FILES_DIR, exist_ok=True)
    for name, body in _DEFAULT_FILES.items():
        p = os.path.join(FILES_DIR, name)
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(body)
    c = _conn()
    with c:
        for row in _SEED:
            c.execute("INSERT OR REPLACE INTO context_items "
                      "(profile, exemption, kind, title, path, priority) VALUES (?,?,?,?,?,?)", row)
    c.close()


def retrieve(profile: str | None, exemption: str | None, budget_chars: int = 1800,
             kind: str | None = None) -> str:
    """Injectable reference context for (profile, exemption), highest priority first, within
    a char budget. `kind` ('guideline' | 'regulation') filters the layer; None = both. Returns
    '' if the DB/files are absent — a graceful no-op."""
    if not os.path.exists(DB):
        return ""
    c = _conn()
    rows = c.execute(
        "SELECT title, path FROM context_items WHERE profile IN (?, '*') AND exemption IN (?, '*') "
        "AND (kind = ? OR ? IS NULL) ORDER BY priority DESC",
        (profile or "*", exemption or "*", kind, kind)).fetchall()
    c.close()
    out, used, seen = [], 0, set()
    for title, path in rows:
        fp = os.path.join(FILES_DIR, path)
        if path in seen or not os.path.exists(fp):   # a file may be registered for several exemptions
            continue
        seen.add(path)
        body = open(fp).read().strip()
        chunk = f"## {title}\n{body}"
        if used + len(chunk) > budget_chars:
            chunk = chunk[: max(0, budget_chars - used)]
        out.append(chunk)
        used += len(chunk)
        if used >= budget_chars:
            break
    return "\n\n".join(out)


def list_items(profile: str | None, kind: str | None = None) -> list:
    """Full (un-budgeted) reference items for the viewer endpoints — [{title, kind, body}],
    deduped by file, highest priority first."""
    if not os.path.exists(DB):
        return []
    c = _conn()
    rows = c.execute(
        "SELECT title, kind, path, priority FROM context_items WHERE profile IN (?, '*') "
        "AND (kind = ? OR ? IS NULL) ORDER BY priority DESC, title",
        (profile or "*", kind, kind)).fetchall()
    c.close()
    out, seen = [], set()
    for title, k, path, _ in rows:
        fp = os.path.join(FILES_DIR, path)
        if path in seen or not os.path.exists(fp):
            continue
        seen.add(path)
        out.append({"title": title, "kind": k, "body": open(fp).read().strip()})
    return out


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    if cmd == "seed":
        seed()
        print(f"seeded context db at {os.path.relpath(DB, ROOT)} ({len(_SEED)} pointers, "
              f"{len(_DEFAULT_FILES)} files)")
    elif cmd == "list":
        c = _conn()
        for r in c.execute("SELECT profile, exemption, title, path FROM context_items ORDER BY profile"):
            print(f"  {r[0]:20s} {r[1]:10s} {r[2]}  -> files/{r[3]}")
        c.close()
    elif cmd == "retrieve":
        print(retrieve(sys.argv[2] if len(sys.argv) > 2 else None,
                       sys.argv[3] if len(sys.argv) > 3 else None))
