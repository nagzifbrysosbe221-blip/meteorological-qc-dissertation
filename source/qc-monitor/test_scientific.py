"""Fabricated mathematical/role fixtures. Never fit actual observations in tests."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import unittest
import uuid

from data import expected_schedule, utc_for
from early_scientific import (Cell, Hour, Series, cell, load_series, verified_manifest, require_role)
from records import ROOT, sha256
from scientific_models import (Model, calendar_features, stable_ols, fit, predict,
                               standardised_residual, assess_gates, diagnostics)


def fabricated(year=2021, variable='T'):
    hours = []
    for month in range(1,13):
        for hour in (1,3,5,7,9,11,14,17,20,24):
            day = date(year, month, 10)
            utc = utc_for(day, hour)
            # Independently written equation: intercept 2, annual sine 4, daily cosine 5.
            fraction = (utc-datetime(year,1,1,tzinfo=timezone.utc)).total_seconds()/(365*86400)
            r = ((month*hour) % 17)-8.
            y = 2 + 4*math.sin(2*math.pi*fraction) + 5*math.cos(2*math.pi*(hour%24)/24)
            hours.append(Hour(day,hour,utc,Cell(y,'finite',(f't-{month}-{hour}',)),
                              Cell(r,'finite',(f'r-{month}-{hour}',))))
    return Series(year,{2021:'fit',2022:'development',2023:'validation'}[year], variable,
                  tuple(hours),'fabricated','fabricated')


def gate_series():
    hours=[]
    for s in expected_schedule(date(2022,1,1),date(2022,12,31),(260,)):
        d=date.fromisoformat(s['source_date']); h=s['source_hour']
        y=1. if h%2 else -1.
        hours.append(Hour(d,h,utc_for(d,h),Cell(y,'finite'),Cell(.5*y,'finite')))
    return Series(2022,'development','T',tuple(hours),'fabricated','fabricated')


def gate_models():
    return (Model('S','T',(0.,)*13,0.,2.,'fitted',None),
            Model('P','T',(0.,)*13+(1.,),0.,2.,'fitted',None))


class MathTests(unittest.TestCase):
    def test_exact_feature_origin(self):
        self.assertEqual(calendar_features(datetime(2022,1,1,tzinfo=timezone.utc)),
                         (1,0,1,0,1,0,1,0,1,0,0,0,1))

    def test_daily_quarter_and_products(self):
        f=calendar_features(datetime(2021,1,1,6,tzinfo=timezone.utc))
        a=2*math.pi*.25/365
        self.assertAlmostEqual(f[5],1); self.assertAlmostEqual(f[6],0)
        self.assertAlmostEqual(f[9],math.sin(a)); self.assertAlmostEqual(f[11],math.cos(a))
        self.assertAlmostEqual(f[8],-1)

    def test_leap_year_phase_and_naive_rejection(self):
        f=calendar_features(datetime(2020,7,2,tzinfo=timezone.utc))
        self.assertAlmostEqual(f[1],0); self.assertAlmostEqual(f[2],-1)
        with self.assertRaises(ValueError): calendar_features(datetime(2021,1,1))

    def test_hand_ols_centre_and_n_minus_one(self):
        b,c,s,d=stable_ols([[1,0],[1,1],[1,2],[1,3]],[1,2,2,5])
        self.assertAlmostEqual(b[0],.7); self.assertAlmostEqual(b[1],1.2)
        self.assertAlmostEqual(c,0); self.assertAlmostEqual(s,math.sqrt(.6))
        self.assertEqual(d['residual_scale_denominator'],3)

    def test_rescaled_predictor_same_solution(self):
        b,_,s,_=stable_ols([[1,0],[1,1e10],[1,2e10],[1,3e10]],[1,2,2,5])
        self.assertAlmostEqual(b[0],.7); self.assertAlmostEqual(b[1]*1e10,1.2)
        self.assertAlmostEqual(s,math.sqrt(.6))

    def test_rank_and_nonfinite_failures(self):
        for x,y in [([[1,1],[1,1]],[1,2]), ([[1,0],[1,1]],[1,float('nan')]),
                    ([[1,0]],[1]), ([[1,0],[1,0]],[1,2])]:
            with self.assertRaises(ValueError): stable_ols(x,y)

    def test_seasonal_fit_independent_coefficients(self):
        m,r=fit(fabricated(),'S')
        self.assertEqual(r['diagnostics']['rank'],13)
        for i,b in enumerate(m.coefficients):
            self.assertAlmostEqual(b,{0:2,1:4,6:5}.get(i,0),places=10)

    def test_reference_fit_independent_coefficients(self):
        data=fabricated(variable='U')
        data=replace(data,hours=tuple(replace(h,target=replace(h.target,value=h.target.value+3*h.reference.value))
                                     for h in data.hours))
        m,r=fit(data,'P')
        self.assertEqual(r['diagnostics']['rank'],14)
        for i,b in enumerate(m.coefficients):
            self.assertAlmostEqual(b,{0:2,1:4,6:5,13:3}.get(i,0),places=10)

    def test_zero_scale_explicit(self):
        data=fabricated(); data=replace(data,hours=tuple(replace(h,target=Cell(0.,'finite')) for h in data.hours))
        m,_=fit(data,'S')
        self.assertEqual(m.status,'ineligible_scale'); self.assertEqual(m.scale,0)
        self.assertEqual(standardised_residual(m,data.hours[0]),(None,'model_ineligible'))

    def test_fit_blocks_other_years_and_mixed_source_ownership(self):
        for year in (2022,2023):
            with self.assertRaises(ValueError): fit(fabricated(year),'S')
        data=fabricated(); h=data.hours[0]
        with self.assertRaises(ValueError):
            fit(replace(data,hours=(replace(h,source_day=date(2022,1,10)),)+data.hours[1:]),'S')

    def test_eligibility_missing_ambiguous_and_no_clipping(self):
        data=fabricated(); hs=list(data.hours)
        hs[0]=replace(hs[0],target=Cell(None,'missing'))
        hs[1]=replace(hs[1],reference=Cell(None,'ambiguous_rows',('a','b')))
        hs[2]=replace(hs[2],target=Cell(1e5,'finite',('extreme',)))
        s,sr=fit(replace(data,hours=tuple(hs)),'S'); p,pr=fit(replace(data,hours=tuple(hs)),'P')
        self.assertEqual(sr['n'],119); self.assertEqual(pr['n'],118)
        self.assertEqual(pr['exclusions'],{'target_missing':1,'reference_ambiguous_rows':1})
        self.assertIn(('extreme',),[r['target'] for r in pr['fit_rows']])

    def test_reference_gap_never_falls_back(self):
        s,p=gate_models(); h=gate_series().hours[0]
        h=replace(h,reference=Cell(None,'missing'))
        self.assertEqual(predict(p,h),(None,'reference_missing'))
        self.assertEqual(predict(s,h),(0.,None))

    def test_frozen_centre_scale_and_unclipped_prediction(self):
        _,p=gate_models(); p=replace(p,coefficients=(200.,)+(0.,)*12+(1.,),centre=2.,scale=4.)
        h=fabricated().hours[0]; h=replace(h,target=Cell(220.,'finite'),reference=Cell(10.,'finite'))
        self.assertEqual(predict(p,h),(210.,None)); self.assertEqual(standardised_residual(p,h),(2.,None))
        self.assertEqual((p.centre,p.scale),(2.,4.))

    def test_repeat_fit_payload_exact(self):
        self.assertEqual(fit(fabricated(),'S'),fit(fabricated(),'S'))

    def test_actual_hour_lags_do_not_compress_gaps(self):
        d=date(2021,1,1)
        hours=tuple(Hour(d,h,utc_for(d,h),Cell(float(h),'finite'),Cell(None,'absent_row')) for h in (1,2,8))
        s=replace(fabricated(),hours=hours)
        m=gate_models()[0]
        r=diagnostics(s,m)['actual_hour_lag_dependence']
        self.assertEqual(r['1']['pairs'],1); self.assertEqual(r['6']['pairs'],1)
        self.assertEqual(r['24']['pairs'],0)

    def test_diagnostic_coverage_retains_unavailable_denominator(self):
        data=fabricated(); hours=list(data.hours)
        hours[0]=replace(hours[0],target=Cell(None,'missing'))
        r=diagnostics(replace(data,hours=tuple(hours)),gate_models()[0])
        self.assertEqual(r['monthly_source_date']['1']['expected'],10)
        self.assertEqual(r['monthly_source_date']['1']['coverage'],.9)


class GateTests(unittest.TestCase):
    def setUp(self): self.data=gate_series(); self.s,self.p=gate_models()
    def test_P_all_gates_literal_half_rmse(self):
        r=assess_gates(self.data,self.s,self.p)
        self.assertEqual(r['selected'],'P'); self.assertEqual(r['common_sample']['annual']['ratio'],.5)
    def test_P_annual_exact_threshold(self):
        p=replace(self.p,coefficients=(0.,)*13+(.2,))
        self.assertEqual(assess_gates(self.data,self.s,p)['selected'],'P')
    def test_P_monthly_gate_can_fail_with_annual_pass(self):
        hours=tuple(replace(h,reference=Cell(-.2*h.target.value,'finite')) if h.source_day.month==1 else h
                    for h in self.data.hours)
        r=assess_gates(replace(self.data,hours=hours),self.s,self.p)
        self.assertLess(r['common_sample']['annual']['ratio'],.9); self.assertEqual(r['selected'],'S')
    def test_reference_coverage_uses_all_expected_hours(self):
        hours=tuple(replace(h,reference=Cell(None,'missing')) if i<75 else h for i,h in enumerate(self.data.hours))
        r=assess_gates(replace(self.data,hours=hours),self.s,self.p)
        self.assertEqual(r['selected'],'S')
        self.assertEqual(r['candidates']['P']['monthly']['1']['coverage'],669/744)
    def test_monthly_bias_inclusive_half_scale(self):
        s=replace(self.s,coefficients=(1.,)+(0.,)*12)
        p=replace(self.p,status='fit_failed',coefficients=())
        self.assertEqual(assess_gates(self.data,s,p)['selected'],'S')
        s=replace(s,coefficients=(1.0001,)+(0.,)*12)
        self.assertIsNone(assess_gates(self.data,s,p)['selected'])
    def test_failed_S_comparator_blocks_P(self):
        r=assess_gates(self.data,replace(self.s,coefficients=(),status='fit_failed'),self.p)
        self.assertIsNone(r['selected']); self.assertIn('comparator',r['reason'])
    def test_zero_common_S_rmse_retains_eligible_S(self):
        hours=tuple(replace(h,target=Cell(0.,'finite')) for h in self.data.hours)
        r=assess_gates(replace(self.data,hours=hours),self.s,self.p)
        self.assertIsNone(r['common_sample']['annual']['ratio']); self.assertEqual(r['selected'],'S')
    def test_validation_and_partial_year_cannot_select(self):
        with self.assertRaises(ValueError): assess_gates(replace(self.data,role='validation'),self.s,self.p)
        with self.assertRaises(ValueError): assess_gates(replace(self.data,hours=self.data.hours[:-1]),self.s,self.p)

    def test_annual_coverage_gate_fails_even_when_every_month_passes(self):
        hours=tuple(replace(h,reference=Cell(None,'missing')) if h.source_day.day in (1,2) else h
                    for h in self.data.hours)
        r=assess_gates(replace(self.data,hours=hours),self.s,self.p)
        self.assertTrue(all(m['coverage']>=.9 for m in r['candidates']['P']['monthly'].values()))
        self.assertLess(r['candidates']['P']['annual']['coverage'],.95)
        self.assertEqual(r['selected'],'S')

    def test_monthly_zero_common_rmse_blocks_ratio(self):
        hours=tuple(replace(h,target=Cell(0.,'finite')) if h.source_day.month==1 else h for h in self.data.hours)
        r=assess_gates(replace(self.data,hours=hours),self.s,self.p)
        self.assertIsNone(r['common_sample']['monthly']['1']['ratio'])
        self.assertEqual(r['selected'],'S')


class LoaderTests(unittest.TestCase):
    def setUp(self):
        # Persistent new fixtures, never mutations to accepted observations.
        self.root=ROOT/'evidence/scientific-models-2026-10-08/fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.m={'version':'early-acceptance-v1','bounded_review_complete':True,
                'decision':'bounded_early_snapshot_acceptance_with_metadata_limitations',
                'numeric_truth_status':'assumed_normal_not_certified','independent_numeric_fault_mask':[],
                'accepted_snapshots':[]}
        for year,role in ((2021,'fit'),(2022,'development'),(2023,'validation')):
            raw=self.root/f'raw-{year}'; view=self.root/f'view-{year}'
            raw.mkdir(); view.mkdir(); (raw/'response.txt').write_text('fabricated')
            (raw/'manifest.json').write_text('{}'); (view/'manifest.json').write_text('{}')
            parent=sha256(raw/'response.txt'); rows=[]
            for i,(day,hour) in enumerate(((date(year,12,31),24),(date(year+1,1,1),1))):
                rows.append({'raw_tokens':{'YYYYMMDD':day.strftime('%Y%m%d')},'source_date':day.isoformat(),
                  'source_hour':hour,'source_role':role,'station':260,'time_state':'valid_hour',
                  'timestamp_utc':utc_for(day,hour).isoformat(),'parent_sha256':parent,'record_id':str(i),
                  'schema_state':'complete','T':{'value':15.3,'state':'finite'},'U':{'value':0.,'state':'finite'}})
            (view/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            for name in ('values.csv','schedule.csv','issues.jsonl'): (view/name).write_text('fixture')
            self.m['accepted_snapshots'].append({'source_year':year,'source_role':role,'permitted_use':role,
              'accepted_for_fitting':year==2021,'stations':[240,260],'variables':['T','U'],
              'decision':'accepted_for_declared_early_role_with_documented_assumptions','snapshot':raw.name,
              'view':view.name,'raw_sha256':parent,'raw_manifest_sha256':sha256(raw/'manifest.json'),
              'view_manifest_sha256':sha256(view/'manifest.json'),'excluded_boundary_rows':1,
              'view_files':{p.name:{'sha256':sha256(p),'bytes':p.stat().st_size}
                            for p in view.iterdir() if p.name!='manifest.json'}})
        self.manifest=self.root/'acceptance.json'; self.save()
    def save(self):
        self.manifest.write_text(json.dumps(self.m)); self.digest=sha256(self.manifest)
    def load(self,year=2021,role='fit',variable='T'):
        return load_series(year,role,variable,root=self.root,manifest=self.manifest,expected_sha256=self.digest)
    def test_hour24_keeps_original_year_boundary_excluded(self):
        s=self.load(); self.assertEqual(len(s.hours),8760); self.assertEqual(s.boundary_rows_excluded,1)
        self.assertEqual(s.hours[-1].utc.year,2022); self.assertEqual(s.hours[-1].source_day.year,2021)
        self.assertEqual(s.hours[-1].target.value,15.3); self.assertEqual(s.hours[0].target.reason,'absent_row')
        self.assertEqual(self.load(variable='U').hours[-1].target.value,0.)
    def test_acceptance_all_years_does_not_enable_fitting_all_years(self):
        for y in (2022,2023,2024):
            with self.assertRaises(ValueError): self.load(y,'fit')
    def test_raw_tampering(self):
        (self.root/'raw-2021/response.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'Hash mismatch'): self.load()
    def test_view_tampering_even_unused_year(self):
        (self.root/'view-2023/values.csv').write_text('changed')
        with self.assertRaisesRegex(ValueError,'Hash mismatch'): self.load()
    def test_manifest_tampering(self):
        self.manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Hash mismatch'): self.load()
    def test_rehashed_wrong_role_still_rejected(self):
        self.m['accepted_snapshots'][1]['accepted_for_fitting']=True; self.save()
        with self.assertRaisesRegex(ValueError,'roles'): self.load()
    def test_new_fault_mask_needs_new_supported_decision(self):
        self.m['independent_numeric_fault_mask']=[{'fault':'new'}]; self.save()
        with self.assertRaisesRegex(ValueError,'review required'): self.load()
    def test_missing_ambiguous_invalid_cells_remain_explicit(self):
        row={'record_id':'a','schema_state':'complete','T':{'state':'missing','value':None}}
        self.assertEqual(cell([row],'T'),Cell(None,'missing',('a',)))
        self.assertEqual(cell([row,row],'T'),Cell(None,'ambiguous_rows',('a','a')))
        self.assertEqual(cell([],'T'),Cell(None,'absent_row'))

    def test_changed_record_role_rejected_even_with_fresh_fixture_hashes(self):
        path=self.root/'view-2021/records.jsonl'
        rows=[json.loads(l) for l in path.read_text().splitlines()]
        rows[0]['source_role']='development'; path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.m['accepted_snapshots'][0]['view_files']['records.jsonl']={'sha256':sha256(path),'bytes':path.stat().st_size}
        self.save()
        with self.assertRaisesRegex(ValueError,'row role'): self.load()


class StageTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'evidence/scientific-models-2026-10-08/stage-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def test_saved_success_readback_and_corruption_rejection(self):
        from scientific_stage import execute, inspect
        from unittest.mock import patch
        def loader(year,role,var):
            return replace(fabricated() if year==2021 else gate_series(),variable=var)
        def fake_fit(s,kind):
            model=gate_models()[0 if kind=='S' else 1]
            return replace(model,variable=s.variable),{'fixture':'manufactured driver outcome; not an OLS test'}
        with patch('scientific_stage.fit',side_effect=fake_fit):
            self.assertEqual(execute('fit-gates',self.root/'pass',loader=loader),0)
        done=inspect(self.root/'pass')
        self.assertEqual(done['state'],'awaiting_calibration'); self.assertFalse(done['real_fitting'])
        (self.root/'pass/selection.json').write_text('{}')
        with self.assertRaises(ValueError): inspect(self.root/'pass')

    def test_revision_required_has_no_settings_defaults(self):
        from scientific_stage import execute, inspect
        from unittest.mock import patch
        def loader(year,role,var):
            return replace(fabricated() if year==2021 else gate_series(),variable=var)
        def failed_fit(s,kind): return Model(kind,s.variable,(),None,None,'fit_failed','fixture'),{}
        with patch('scientific_stage.fit',side_effect=failed_fit):
            self.assertEqual(execute('fit-gates',self.root/'blocked',loader=loader),3)
        self.assertEqual(inspect(self.root/'blocked')['state'],'revision_required')
        selected=json.loads((self.root/'blocked/selection.json').read_text())
        self.assertEqual(selected['models'],{'T':None,'U':None}); self.assertIsNone(selected['cutoffs'])

    def test_failure_and_interruption_preserve_attempts(self):
        from scientific_stage import execute
        for label,exc,code in [('failure',ValueError('deliberate fixture'),1),('interrupt',KeyboardInterrupt(),2)]:
            def loader(*args): raise exc
            self.assertEqual(execute('verify-inputs',self.root/label,loader=loader),code)
            self.assertTrue((self.root/label/'failed.json').exists())
            self.assertFalse((self.root/label/'completed.json').exists())

    def test_missing_canonical_output_rejected(self):
        from scientific_stage import execute, inspect
        def loader(year,role,var): return fabricated(year,var)
        self.assertEqual(execute('verify-inputs',self.root/'inputs',loader=loader),0)
        path=self.root/'inputs/completed.json'; done=json.loads(path.read_text())
        del done['files']['inputs.json']; path.write_text(json.dumps(done))
        with self.assertRaisesRegex(ValueError,'canonical'): inspect(self.root/'inputs')


if __name__=='__main__': unittest.main()
