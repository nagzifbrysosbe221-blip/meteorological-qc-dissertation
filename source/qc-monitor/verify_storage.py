"""Retain every full-suite attempt, output and exact source identities."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import platform
import sys
import unittest

from records import PROJECT, ROOT, code_identity, save_json, utc_now


def main():
    started=utc_now(); log=io.StringIO()
    with redirect_stdout(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.discover(str(PROJECT),'test_*.py'))
    path=ROOT/'evidence/storage-controller'/('tests-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    save_json(path,{'started_utc':started,'finished_utc':utc_now(),'code':code_identity(),'python':sys.version,
        'platform':platform.platform(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'success':result.wasSuccessful(),'output':log.getvalue(),
        'expectations':'Storage tests use byte equality, three tiny jobs, four passes of 936 and literal fault states; no research performance assertions'})
    print(log.getvalue()[-8000:]); print(path)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__': raise SystemExit(main())
