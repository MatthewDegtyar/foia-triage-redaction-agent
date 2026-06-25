"""Low-level Claude transport for the agent layer.

Everything that talks to the model goes through here, so the fail-safe is in ONE
place: no transport, an unparseable reply, or an error all return None, and every
agent treats None as "escalate to a human" rather than guessing.

Two transports, tried in order:
  1. ANTHROPIC_API_KEY + the anthropic SDK (production).
  2. The locally-installed, logged-in Claude Code CLI (`claude -p`) — for a demo on a
     machine where the operator is already signed into Claude Code, so the judgment
     layer runs with NO API key. Subscription auth, headless, one turn, no tools.
With neither, judge() returns None and the agents escalate — the safe default.
"""
import json
import os
import shutil
import subprocess
import tempfile

MODEL = os.environ.get("FOIA_MODEL", "claude-sonnet-4-6")
_CLI_TIMEOUT = int(os.environ.get("FOIA_CLI_TIMEOUT", "120"))

# Count of model invocations actually issued (not no-transport short-circuits), so cost of
# the agentic loop can be reported honestly. Increments under the GIL; fine for our use.
CALL_COUNT = 0


def _claude_cli() -> str | None:
    """Path to the Claude Code CLI, if installed (used only when no API key is set)."""
    return shutil.which("claude")


def available() -> bool:
    """A model transport exists: an API key, or a logged-in Claude Code CLI. The
    FOIA_DISABLE_MODEL kill-switch forces the deterministic path (used for the instant,
    key-free startup pre-seed)."""
    if os.environ.get("FOIA_DISABLE_MODEL"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or _claude_cli() is not None


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001
        return None


def _parse_json(raw: str | None):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[4:].strip() if raw.lower().startswith("json") else raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                return json.loads(raw[s:e + 1])
            except Exception:  # noqa: BLE001
                return None
        return None


def _judge_sdk(system: str, user: str, max_tokens: int):
    global CALL_COUNT
    client = _client()
    if client is None:
        return None
    CALL_COUNT += 1
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return _parse_json("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))
    except Exception:  # noqa: BLE001
        return None


def _judge_cli(system: str, user: str):
    """Route through the logged-in Claude Code CLI. Headless, single turn, replacing the
    default agentic system prompt with ours; run from a neutral cwd so it doesn't pick up
    project context. The CLI prints a JSON envelope; the model's text is its `result`."""
    global CALL_COUNT
    cli = _claude_cli()
    if cli is None:
        return None
    CALL_COUNT += 1
    try:
        proc = subprocess.run(
            [cli, "-p", user,
             "--system-prompt", system,
             "--model", MODEL,
             "--max-turns", "1",
             "--output-format", "json"],
            capture_output=True, text=True, timeout=_CLI_TIMEOUT,
            cwd=tempfile.gettempdir(),
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error"):
            return None
        return _parse_json(envelope.get("result"))
    except Exception:  # noqa: BLE001
        return None


def judge(system: str, user: str, max_tokens: int = 1200) -> dict | None:
    """Return parsed JSON, or None if no transport is available / the reply is unparseable.
    Prefers the API key when present, else the logged-in Claude Code CLI."""
    if os.environ.get("FOIA_DISABLE_MODEL"):
        return None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _judge_sdk(system, user, max_tokens)
    return _judge_cli(system, user)
