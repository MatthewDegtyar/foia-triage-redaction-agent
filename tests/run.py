"""Zero-dependency test harness — validates the system end-to-end, deterministically.

    python -m tests.run            # run everything
    python -m tests.run -v         # + per-test lines
    python -m tests.run units      # only tests/test_units.py

No pytest. Discovers `test_*` functions in tests/test_*.py and runs each with FULLY ISOLATED
state (temp dirs for the feedback/memory/context/health stores) and FOIA_DISABLE_MODEL=1, so a
test never touches demo data, no model is called, and the run can't flake. The API tests drive
the real FastAPI app via Starlette's TestClient (firing the startup seed against isolated
state). Exit code 0 iff everything passes.
"""
import importlib
import shutil
import sys
import traceback

from tests.harness import isolate

TEST_MODULES = ["tests.test_units", "tests.test_api"]


def main() -> int:
    verbose = "-v" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    mods = [m for m in TEST_MODULES if not only or any(o in m for o in only)]

    tests = []
    for modname in mods:
        mod = importlib.import_module(modname)
        for name in sorted(dir(mod)):
            if name.startswith("test_") and callable(getattr(mod, name)):
                tests.append((modname.split(".")[-1], name, getattr(mod, name)))

    passed, failed = 0, []
    print(f"running {len(tests)} tests (isolated state · model disabled)\n")
    for mod, name, fn in tests:
        tmp = isolate()
        try:
            fn()
            passed += 1
            if verbose:
                print(f"  PASS  {mod}.{name}")
        except Exception as e:  # noqa: BLE001
            failed.append((mod, name, traceback.format_exc()))
            print(f"  FAIL  {mod}.{name}: {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {len(failed)} failed, {len(tests)} total")
    for mod, name, tb in failed:
        print(f"\n--- {mod}.{name} ---\n{tb.rstrip()}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
