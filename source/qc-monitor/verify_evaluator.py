"""Save every actual test attempt, including failures and source identities."""

import io
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from records import PROJECT, ROOT, code_identity, save_json, sha256, utc_now


def main():
    started = utc_now()
    suite = unittest.defaultTestLoader.discover(str(PROJECT), "test_*.py")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    path = ROOT / "evidence/evaluator" / ("tests-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".json")
    save_json(path, {"started_utc": started, "finished_utc": utc_now(), "python": sys.version,
                    "platform": platform.platform(), "code": code_identity(),
                    "independent_expected_sha256": sha256(PROJECT/"evaluator_expected.json"),
                    "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                    "skipped": len(result.skipped), "success": result.wasSuccessful(), "output": stream.getvalue()})
    print(stream.getvalue())
    print(path)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
