#!/usr/bin/env python3
"""Run the regression suite.

    python3 tools/tests/run.py              # everything
    python3 tools/tests/run.py --only=paint # cases whose name contains "paint"

Exit code 0 means every case that could run, passed. A missing Bambu Studio or
Blender is a skip, not a failure. Used by the pre-push hook.
"""
import importlib.util, pathlib, sys

sys.dont_write_bytecode = True   # иначе переписанный тест можно прочитать из кэша

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import harness

for f in sorted(TESTS.glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(f.stem, f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

sys.exit(harness.main(sys.argv))
