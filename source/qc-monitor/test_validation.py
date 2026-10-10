"""Fabricated frozen-transfer and reporting fixtures; no actual 2023 analysis."""
from dataclasses import replace
from datetime import timedelta,date
import copy
import unittest
from early_scientific import Cell
from r1_validation import validate,rate,diagnosis,frozen_decisions
from test_r1 import fixture


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _,cls.context,cls.model=fixture()
        cls.series=replace(cls.context,source_year=2023,role='validation',hours=tuple(
          replace(h,source_day=h.source_day+timedelta(days=365),utc=h.utc+timedelta(days=365)) for h in cls.context.hours))
        cls.decision={'variable':'T','model':'S','selected_lambda':.2,'cutoff':2.,'exploratory':False}
    def test_frozen_settings_readback(self):
        d=frozen_decisions();self.assertEqual(d['T']['selected_lambda'],.2);self.assertTrue(d['U']['exploratory'])
        self.assertFalse(d['U']['original_A2_eligible'])
    def test_hand_rate_and_coverage(self):
        r=rate(2,100,120);self.assertEqual(r['statistical_stream_FPR'],.02)
        self.assertEqual(r['ready_coverage'],100/120)
    def test_no_exposure_is_null_not_zero(self):
        r=rate(0,0,744);self.assertIsNone(r['statistical_stream_FPR']);self.assertIsNotNone(r['reason'])
    def test_strict_diagnosis_thresholds(self):
        self.assertEqual(diagnosis(rate(1,100,100),{'1':rate(3,100,100)}),[])
        self.assertEqual(diagnosis(rate(2,100,100),{'1':rate(4,100,100)}),['annual_FPR_above_.01','month_1_FPR_above_.03'])
    def test_unavailable_rate_requests_diagnosis(self):
        self.assertEqual(diagnosis(rate(0,0,100),{'1':rate(0,0,100)}),['annual_FPR_unavailable','month_1_FPR_unavailable'])
    def test_unchanged_settings_and_full_ready_context(self):
        before=copy.deepcopy(self.decision)
        r=validate(self.context,self.series,self.model,self.decision)
        self.assertEqual(r['annual']['eligible_assumed_normal_ready_hours'],8760)
        self.assertEqual(r['annual']['statistical_stream_FPR'],0)
        self.assertEqual(r['state'],'validation_checks_within_thresholds')
        self.assertEqual(before,self.decision);self.assertFalse(r['automatic_retuning'])
    def test_high_FPR_is_diagnosis_not_reselection(self):
        def high(s):return replace(s,hours=tuple(replace(h,target=Cell(10.,'finite')) for h in s.hours))
        r=validate(high(self.context),high(self.series),self.model,self.decision)
        self.assertEqual(r['annual']['statistical_stream_FPR'],1.)
        self.assertEqual(len(r['diagnosis_reasons']),13);self.assertEqual(r['state'],'diagnosis_required')
        self.assertEqual(r['settings_transferred_unchanged']['cutoff'],2.)
    def test_gaps_exclude_unavailable_not_tn(self):
        hs=list(self.series.hours)
        for i in range(10):hs[i]=replace(hs[i],target=Cell(None,'missing'))
        r=validate(self.context,replace(self.series,hours=tuple(hs)),self.model,self.decision)
        self.assertEqual(r['annual']['eligible_assumed_normal_ready_hours'],8760-10-227)
        self.assertEqual(r['monthly']['1']['eligible_assumed_normal_ready_hours'],744-10-227)
        self.assertEqual(r['unavailable_reasons'],{'target_missing':10,'warm_up':227})
    def test_HH24_stays_scored_source_year(self):
        rows=[];validate(self.context,self.series,self.model,self.decision,rows.append)
        self.assertEqual(sum(r['context'] for r in rows),336)
        self.assertEqual(rows[-1]['source_date'],'2023-12-31');self.assertTrue(rows[-1]['utc'].startswith('2024-01-01'))
        self.assertFalse(rows[-1]['context'])
    def test_wrong_roles_rejected(self):
        with self.assertRaises(ValueError):validate(self.context,replace(self.series,role='development'),self.model,self.decision)
        with self.assertRaises(ValueError):validate(replace(self.context,role='fit'),self.series,self.model,self.decision)
    def test_partial_context_rejected(self):
        with self.assertRaises(ValueError):validate(replace(self.context,hours=self.context.hours[-24:]),self.series,self.model,self.decision)
    def test_wrong_variable_model_rejected(self):
        with self.assertRaises(ValueError):validate(self.context,self.series,replace(self.model,variable='U'),self.decision)
    def test_repeat_payload_equal(self):
        self.assertEqual(validate(self.context,self.series,self.model,self.decision),validate(self.context,self.series,self.model,self.decision))


if __name__=='__main__':unittest.main()
