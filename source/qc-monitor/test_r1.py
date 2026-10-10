"""Hand expectations for R1 permission/A3; all observations are fabricated."""
from dataclasses import replace
from datetime import date,timedelta
import json
import math
import unittest
import uuid
from early_scientific import Cell,Hour,Series
from data import expected_schedule,utc_for
from scientific_models import Model
from records import ROOT,sha256
from r1_calibration import (approved_models,APPROVAL,State,order_cutoff,prepare,calibrate,cases,
                            development_pair,rank_candidates)


def fixture():
    def series(year):
        hours=[]
        for r in expected_schedule(date(year,1,1),date(year,12,31),(260,)):
            d=date.fromisoformat(r['source_date']);h=r['source_hour']
            hours.append(Hour(d,h,utc_for(d,h),Cell(1. if h%2 else -1.,'finite'),Cell(0.,'finite')))
        return Series(year,'fit' if year==2021 else 'development','T',tuple(hours),'fabricated','fabricated')
    model=Model('S','T',(0.,)*13,0.,1.,'fitted',None)
    return series(2021),series(2022),model


class R1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fit,cls.dev,cls.model=fixture();cls.inputs=prepare(cls.fit,cls.dev,cls.model)
    def test_approval_retains_failed_humidity(self):
        models,p=approved_models();self.assertFalse(p['U_original_A2_eligible'])
        self.assertIsNone(p['U_original_selected_model']);self.assertEqual(models['U'].kind,'P')
    def bad_approval(self,change):
        folder=ROOT/'evidence/r1-calibration-2026-10-08/fixtures'/uuid.uuid4().hex
        folder.mkdir(parents=True);p=folder/'approval.json'
        a=json.loads(APPROVAL.read_text());change(a);p.write_text(json.dumps(a))
        with self.assertRaises(ValueError):approved_models(p,sha256(p))
    def test_unapproved_rejected(self):self.bad_approval(lambda a:a.update(researcher_approved=False))
    def test_cannot_relabel_failed_eligibility(self):self.bad_approval(lambda a:a.update(original_U_A2_eligible=True))
    def test_wrong_variable_permission(self):self.bad_approval(lambda a:a['exploratory_permission'].update(variable='T'))
    def test_changed_bound_fit(self):self.bad_approval(lambda a:a['bound_inputs'].update({'U-P-fit.json':'0'*64}))
    def test_approval_digest_pinned(self):
        with self.assertRaises(ValueError):approved_models(APPROVAL,'0'*64)
    def test_hand_recurrence(self):
        s=State(.1);self.assertAlmostEqual(s.step(2)['z'],.2);self.assertAlmostEqual(s.step(-1)['z'],.08)
    def test_warmup_exactly_228(self):
        s=State(.1)
        for _ in range(227):self.assertFalse(s.step(1,1)['ready'])
        self.assertTrue(s.step(1,1)['ready'])
    def test_gaps_hold_reset_rewarm(self):
        s=State(.1);s.step(2)
        for _ in range(6):self.assertEqual(s.step(None)['z'],.2)
        self.assertEqual(s.step(None)['count'],0);self.assertEqual(s.step(2)['count'],1)
    def test_strict_ties(self):
        s=State(.1)
        for _ in range(227):s.step(0)
        self.assertFalse(s.step(10,1)['flag'])
    def test_nonfinite_and_unknown_lambda(self):
        with self.assertRaises(ValueError):State(.3)
        with self.assertRaises(ArithmeticError):State(.1).step(float('nan'))
    def test_cutoff_hand_order_statistic(self):
        self.assertEqual(order_cutoff(list(range(1,101))), (99,99))
        self.assertEqual(order_cutoff([1,2,3,4]),(4,4))
        self.assertEqual(order_cutoff(list(range(1,201)),.005),(199,199))
    def test_invalid_cutoffs_fail(self):
        for values in ([],[0]*5,[float('inf')]):
            with self.assertRaises(ValueError):order_cutoff(values)
    def test_context_336_unscored_source_ownership(self):
        self.assertEqual(len(self.inputs),9096)
        self.assertEqual(self.inputs[335].hour.utc.year,2022)
        self.assertEqual(self.inputs[335].hour.source_day.year,2021)
        self.assertEqual(self.inputs[336].hour.source_day,date(2022,1,1))
    def test_wrong_year_or_variable_before_calibration(self):
        with self.assertRaises(ValueError):prepare(self.fit,replace(self.dev,role='validation'),self.model)
        with self.assertRaises(ValueError):prepare(self.fit,self.dev,replace(self.model,variable='U'))
    def test_context_required(self):
        with self.assertRaises(ValueError):prepare(replace(self.fit,hours=self.fit.hours[-24:]),self.dev,self.model)
    def test_calibration_context_excluded_and_months_continuous(self):
        c=calibrate(self.inputs,.1)
        self.assertTrue(c['eligible']);self.assertEqual(c['ready_states'],8760)
        self.assertTrue(all(r['coverage']==1 for r in c['monthly'].values()))
    def test_cold_january_rejected(self):
        c=calibrate(self.inputs[336:],.1)
        self.assertFalse(c['eligible']);self.assertEqual(c['monthly']['1']['ready'],517)
    def test_zero_states_no_default_cutoff(self):
        c=calibrate(tuple(replace(i,u=0.) for i in self.inputs),.1)
        self.assertFalse(c['eligible']);self.assertIsNone(c['cutoff'])
    def test_full_rosters(self):
        for v in ('T','U'):
            r=cases(v);self.assertEqual(len(r),216);self.assertEqual(len({c['case_id'] for c in r}),216)
            self.assertEqual(sum(c['month']==1 for c in r),18)
    def test_ramp_hand_and_hole_does_not_shift(self):
        case=cases('T')[0];trace=[]
        rows=list(self.inputs)
        index=next(n for n,i in enumerate(rows) if i.hour.source_day==date(2022,1,8) and i.hour.source_hour==2)
        rows[index]=replace(rows[index],hour=replace(rows[index].hour,target=Cell(None,'missing')),u=None)
        result=development_pair(rows,self.model,.1,10,case,trace.append)
        self.assertAlmostEqual(trace[0]['injected_u']-trace[0]['original_u'],-.5/24)
        self.assertIsNone(trace[1]['injected_u'])
        self.assertAlmostEqual(trace[2]['injected_u']-trace[2]['original_u'],-.5*3/24)
        self.assertAlmostEqual(trace[23]['injected_u']-trace[23]['original_u'],-.5)
        self.assertEqual((result['N'],result['C'],result['Z'],result['U']),(48,47,0,1))
    def test_paired_hit_requires_counterpart_unflagged(self):
        result=development_pair(self.inputs,self.model,.1,.00001,cases('T')[0])
        self.assertFalse(result['hit']);self.assertEqual(result['normalised_delay'],1)
    def test_no_effect_window_not_counted_as_hit(self):
        rows=tuple(replace(i,u=None,hour=replace(i.hour,target=Cell(None,'missing'))) for i in self.inputs)
        result=development_pair(rows,self.model,.1,1,cases('T')[0])
        self.assertFalse(result['hit']);self.assertEqual(result['U'],48)
    def test_repeated_pair_equal(self):
        args=(self.inputs,self.model,.1,1,cases('T')[0])
        self.assertEqual(development_pair(*args),development_pair(*args))
    def candidates(self,hits=1):
        return [{'lambda':w,'calibration':{'eligible':True},'cases':[
          {'case_id':str(i),'hit':i<hits,'delay_numerator':1,'delay_denominator':2 if i<hits else 1}
          for i in range(216)]} for w in (.02,.05,.1,.2)]
    def test_exact_tie_larger_lambda(self):self.assertEqual(rank_candidates(self.candidates())['selected_lambda'],.2)
    def test_zero_detection_fails(self):self.assertIsNone(rank_candidates(self.candidates(0))['selected_lambda'])
    def test_missing_cases_fail(self):
        c=self.candidates();c[0]['cases'].pop()
        with self.assertRaises(ValueError):rank_candidates(c)
    def test_hits_before_delay(self):
        c=self.candidates();c[0]['cases'][1].update(hit=True)
        self.assertEqual(rank_candidates(c)['selected_lambda'],.02)
    def test_delay_before_lambda(self):
        c=self.candidates();c[0]['cases'][0]['delay_denominator']=3
        self.assertEqual(rank_candidates(c)['selected_lambda'],.02)
    def test_failed_calibration_not_selected(self):
        c=self.candidates();c[-1]['calibration']['eligible']=False
        self.assertEqual(rank_candidates(c)['selected_lambda'],.1)
    def test_missing_lambda_rejected(self):
        with self.assertRaises(ValueError):rank_candidates(self.candidates()[:-1])
    def test_direct_validation_calibration_rejected(self):
        h=self.inputs[-1].hour
        wrong=replace(self.inputs[-1],hour=replace(h,source_day=date(2023,1,1)))
        with self.assertRaises(ValueError):calibrate((wrong,),.1)
    def test_hand_first_hit_and_delay(self):
        rows=tuple(replace(i,u=0.,hour=replace(i.hour,target=Cell(0.,'finite'))) for i in self.inputs)
        r=development_pair(rows,self.model,.1,.001,cases('T')[0])
        self.assertTrue(r['hit']);self.assertEqual(r['first_hit_j'],1)
        self.assertEqual((r['delay_numerator'],r['delay_denominator']),(1,49))
    def test_reference_gap_still_mutated_but_unavailable(self):
        rows=tuple(replace(i,u=None,reason='reference_missing') for i in self.inputs)
        r=development_pair(rows,self.model,.1,.001,cases('T')[0])
        self.assertFalse(r['hit']);self.assertEqual(r['C'],48)
    def test_state_matches_existing_EWMA_with_gaps(self):
        from ewma import EWMA,FixtureSettings
        for weight in (.02,.05,.1,.2):
            a=State(weight);b=EWMA(FixtureSettings('S',0,0,0,1,weight,1))
            for i,u in enumerate([1.]*230+[None]*6+[-2.]+[None]*7+[.5]*230):
                x=a.step(u,1);y=b.step(str(i),u)
                self.assertEqual((x['z'],x['count'],x['gap'],x['flag']),
                                 (y['z'],y['valid_updates'],y['unavailable_hours'],y['prediction']))


if __name__=='__main__':unittest.main()
