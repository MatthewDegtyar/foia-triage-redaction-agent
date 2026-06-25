"""Terrain-profile loading and quote->span resolution.

A "terrain profile" is one YAML that fully describes a deployment's terrain: the
exemption schedule, the escalation threshold, the deterministic `detectors:` (the
safety-critical class for this domain), and the scoped LLM `specialists:`. Swapping
profiles is how the SAME agent retargets across jurisdictions and document domains
(federal FOIA, a state PIA, court-record anonymization, ...) with no code change.

Select the active profile with the FOIA_PROFILE env var (a path, or a bare name
resolved against policy/<name>.yaml); default is the federal FOIA schedule.
"""
import os

import yaml

from . import registry

_POLICY_DIR = os.path.join(os.path.dirname(__file__), "..", "policy")
_DEFAULT = os.path.join(_POLICY_DIR, "foia_federal.yaml")


def _resolve(path: str | None) -> str:
    path = path or os.environ.get("FOIA_PROFILE")
    if not path:
        return _DEFAULT
    if os.path.sep in path or path.endswith(".yaml"):
        return path                                    # explicit path (e.g. a candidate)
    # bare name -> the PROMOTED (active) version from the registry; falls back to the working
    # copy policy/<name>.yaml when the profile has never been promoted.
    return registry.active_path(path)


def load_policy(path: str | None = None) -> dict:
    """Load the active terrain profile (a.k.a. policy). Sections beyond the exemption
    schedule (detectors, specialists) are optional; absent ones fall back to the federal
    defaults baked into detectors.py / agents.py."""
    with open(_resolve(path)) as f:
        return yaml.safe_load(f)


def basis_for(code: str, policy: dict) -> str:
    ex = policy.get("exemptions", {}).get(code)
    return ex["basis"] if ex else f"({code})"


def find_span(text: str, quote: str) -> tuple[int, int] | None:
    """Map an LLM-quoted string back to exact offsets so redactions are verifiable.
    If the quote can't be located verbatim, return None -> the proposal is dropped
    rather than applied to the wrong place (no phantom redactions)."""
    if not quote:
        return None
    idx = text.find(quote)
    if idx < 0:
        return None
    return idx, idx + len(quote)
