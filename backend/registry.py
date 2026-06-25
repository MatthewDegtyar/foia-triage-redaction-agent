"""Versioned terrain-profile registry + promotion log.

A profile is DATA, not code, so a deploy is an atomic version-pointer flip — instantly
reversible, no engineering release. The registry separates two states:

  working copy   policy/<name>.yaml            <- staging; the client edits this freely
  active version policy/_versions/<name>.vN.yaml <- prod; what load_policy() serves

`promote` snapshots the working copy as the next version and flips the active pointer,
recording the scorecard + decision. `rollback` flips the pointer to a prior snapshot. Every
promotion/rollback is logged — the deployment is itself an administrative record.
"""
import json
import os
import shutil
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_DIR = os.path.join(_HERE, "..", "policy")
VERS_DIR = os.path.join(POLICY_DIR, "_versions")
REGISTRY = os.path.join(POLICY_DIR, "registry.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load() -> dict:
    if os.path.exists(REGISTRY):
        with open(REGISTRY) as f:
            return json.load(f)
    return {"profiles": {}, "log": []}


def _save(reg: dict) -> None:
    os.makedirs(VERS_DIR, exist_ok=True)
    with open(REGISTRY, "w") as f:
        json.dump(reg, f, indent=2)


def working_path(name: str) -> str:
    return os.path.join(POLICY_DIR, f"{name}.yaml")


def _version_path(name: str, v: int) -> str:
    return os.path.join(VERS_DIR, f"{name}.v{v}.yaml")


def active_version(name: str):
    return _load()["profiles"].get(name, {}).get("active")


def active_path(name: str) -> str:
    """The path prod serves: the active versioned snapshot, or the working copy if the
    profile has never been promoted (so unregistered profiles behave exactly as before)."""
    v = active_version(name)
    return _version_path(name, v) if v else working_path(name)


def register_version(name: str, scorecard: dict, decision: str, actor: str, src_path: str | None = None) -> int:
    """Promotion write: snapshot the candidate working copy as the next version and activate
    it. Returns the new version number."""
    reg = _load()
    os.makedirs(VERS_DIR, exist_ok=True)
    prof = reg["profiles"].setdefault(name, {"active": None, "versions": []})
    v = (max(prof["versions"]) + 1) if prof["versions"] else 1
    shutil.copy(src_path or working_path(name), _version_path(name, v))
    prof["versions"].append(v)
    prev = prof["active"]
    prof["active"] = v
    reg["log"].append({"ts": _now(), "profile": name, "action": "promote", "from": prev,
                       "version": v, "decision": decision, "actor": actor, "scorecard": scorecard})
    _save(reg)
    return v


def rollback(name: str, to_version: int, actor: str) -> None:
    reg = _load()
    prof = reg["profiles"].get(name)
    if not prof or to_version not in prof["versions"]:
        raise ValueError(f"no version {to_version} for profile '{name}'")
    prev = prof["active"]
    prof["active"] = to_version
    reg["log"].append({"ts": _now(), "profile": name, "action": "rollback", "from": prev,
                       "version": to_version, "actor": actor})
    _save(reg)


def history(name: str | None = None) -> list:
    return [e for e in _load()["log"] if name is None or e["profile"] == name]


def status() -> dict:
    return _load()["profiles"]


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        for name, p in status().items():
            print(f"  {name}: active v{p['active']}  (versions {p['versions']})")
    elif cmd == "history":
        for e in history(sys.argv[2] if len(sys.argv) > 2 else None):
            extra = e.get("decision", "")
            print(f"  {e['ts']}  {e['action']:8s} {e['profile']} v{e.get('from')}->v{e['version']} "
                  f"by {e['actor']} {extra}")
    elif cmd == "rollback":
        rollback(sys.argv[2], int(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "operator")
        print(f"rolled back {sys.argv[2]} -> v{sys.argv[3]}")
