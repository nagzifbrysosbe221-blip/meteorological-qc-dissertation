"""Save every focused analysis test attempt, independently of the scientific suite."""
import io
import sys
import unittest
import zipfile
from evidence import ROOT,HERE,token,save,code_identity,sha,now
import test_analysis

folder=ROOT/'evidence/result-analysis-2026-10-09'/('tests-'+token());folder.mkdir(parents=True)
stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_analysis))
(folder/'output.txt').write_text(stream.getvalue(),encoding='utf-8');print(stream.getvalue())
with zipfile.ZipFile(folder/'analysis-source.zip','x',zipfile.ZIP_DEFLATED) as z:
    for name in code_identity():z.write(HERE/name,name)
save(folder/'result.json',dict(utc=now(),scope='separate analysis fixture suite; no experimental batch',tests_run=result.testsRun,
    failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),success=result.wasSuccessful(),
    code=code_identity(),source_sha256=sha(folder/'analysis-source.zip'),scientific_suite_rerun=False,independent_human_review=False))
print('SAVED '+str(folder/'result.json'));sys.exit(0 if result.wasSuccessful() else 1)
