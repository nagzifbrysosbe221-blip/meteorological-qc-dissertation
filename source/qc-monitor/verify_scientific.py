"""Run fabricated checks, saving every attempt and its source, even on failure."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import platform
import sys
import unittest
import zipfile
import numpy as np
from records import ROOT, PROJECT, code_identity, save_json, utc_now


def main():
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder=ROOT/'evidence/scientific-models-2026-10-08'/('tests-'+tag)
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PROJECT.glob('*.py')): z.write(p,p.name)
        z.write(PROJECT/'requirements.txt','requirements.txt')
    started=utc_now(); log=io.StringIO()
    pattern='test_*.py' if '--full' in sys.argv else 'test_scientific.py'
    with redirect_stdout(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.discover(str(PROJECT),pattern))
    save_json(folder/'result.json',{'started_utc':started,'finished_utc':utc_now(),
      'code':code_identity(),'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),
      'scope':pattern,'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
      'skipped':len(result.skipped),'success':result.wasSuccessful(),'output':log.getvalue(),
      'real_fitting':False,'expectations':'Hand OLS .7 + 1.2*x, sample scale sqrt(.6); independent S/P coefficient equations; literal source roles and gate ratios'})
    print(log.getvalue()[-12000:]); print(folder)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__': raise SystemExit(main())
