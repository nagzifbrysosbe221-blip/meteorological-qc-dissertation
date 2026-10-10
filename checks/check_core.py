"""Nine copied C6 fixtures only; no acquisition, fitting or scientific replay batch."""
import sys, pathlib, unittest
root=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'source/qc-monitor'))
from test_evaluator import EvaluatorChecks
names=[n for n in unittest.defaultTestLoader.getTestCaseNames(EvaluatorChecks) if n.startswith('test_C6_')]
assert len(names)==9
result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(EvaluatorChecks(n) for n in names))
raise SystemExit(0 if result.wasSuccessful() else 1)
