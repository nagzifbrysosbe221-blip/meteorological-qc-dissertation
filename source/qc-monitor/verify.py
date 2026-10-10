"""Run offline fixtures and save the actual test output, including any failures."""

import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from records import ROOT, code_identity, save_json, utc_now


def main():
    started = utc_now()
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), "test_*.py")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    path = ROOT / "evidence" / "data-foundation" / (
        "tests-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    save_json(path, {"started_utc": started, "finished_utc": utc_now(), "code": code_identity(),
                     "python": sys.version, "test_kind": "synthetic_correctness_fixtures",
                     "tests_run": result.testsRun, "failures": len(result.failures),
                     "errors": len(result.errors), "skipped": len(result.skipped),
                     "success": result.wasSuccessful(), "output": stream.getvalue()})
    print(stream.getvalue())
    print(path)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
