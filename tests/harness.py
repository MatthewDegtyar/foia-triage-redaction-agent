"""Shared test fixtures: isolated state + a TestClient. Imported by run.py and the test modules."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def isolate():
    """Point every file-writing layer at a fresh temp dir; force deterministic + court terrain;
    reset the in-process package. Returns the temp dir to clean up afterward."""
    os.environ["FOIA_DISABLE_MODEL"] = "1"
    os.environ["FOIA_PROFILE"] = "uscourts"
    tmp = tempfile.mkdtemp(prefix="foia-test-")
    from backend import context_layer, feedback, health, memory, runtime
    feedback.STORE = os.path.join(tmp, "decisions.jsonl")
    memory.MEM_DIR, memory.DB = tmp, os.path.join(tmp, "memory.db")
    context_layer.CTX_DIR = tmp
    context_layer.FILES_DIR = os.path.join(tmp, "files")
    context_layer.DB = os.path.join(tmp, "context.db")
    health.H_DIR = tmp
    health.OBS = os.path.join(tmp, "observations.jsonl")
    health.REVIEWS = os.path.join(tmp, "reviews.jsonl")
    context_layer.seed()                       # materialize corpus/guidelines into the temp dir
    runtime.STATE["package"] = None
    runtime.STATE["job"] = None
    return tmp


def client():
    """A TestClient whose `with` block fires the app's startup seed against the isolated state."""
    from fastapi.testclient import TestClient
    from backend import app as appmod
    return TestClient(appmod.app)
