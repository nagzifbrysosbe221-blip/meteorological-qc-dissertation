"""Independent literal answers for analysis; no frozen production imports."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import unittest
import zipfile
from evidence import ROOT,HERE,Bundle,sha,token,read,save,identity
from metrics import counts,ratio,quantile,describe_months,root_summary,contrasts,compact_points,pool_points,select_timelines
from extract import recovery,source_month,ongoing,raw_months
from report import Table


def root(i,categories,delays=None,month='2024-01'):
    delays=delays or [300 if c=='hit' else None for c in categories]
    return dict(job_id='job'+str(i),case_id='case'+str(i),root_id='root'+str(i),**{'pass':'primary'},
        family='gradual_bias',variable='T',support='full',variant='same',construction='effective',paired=True,
        source_month=month,configurations={cfg:dict(category=c,delay_seconds=d,statistical_opportunities=1,
            full_required_coverage=dict(numerator=1,denominator=1,value=1,reason=None),
            first_credit_route='direct' if c=='hit' else None) for cfg,c,d in zip(('B0','R','H'),categories,delays)})


class AnalysisChecks(unittest.TestCase):
    def test_literal_confusion_and_nulls(self):
        c=counts(dict(TP=2,FP=1,FN=3,TN=4))
        self.assertEqual((c['n'],c['precision']['value'],c['recall']['value'],c['fpr']['value']),(10,2/3,2/5,1/5))
        z=counts(dict(TP=0,FP=0,FN=0,TN=4));self.assertIsNone(z['precision']['value']);self.assertIsNone(z['recall']['value']);self.assertEqual(z['fpr']['value'],0)

    def test_type7_hand_interpolation(self):
        self.assertEqual(quantile([1,2,8,9],.25),1.75)
        self.assertEqual(quantile([1,2,8,9],.5),5)
        self.assertEqual(quantile([1,2,8,9],.75),8.25)

    def test_monthly_ratio_and_lomo_not_mean_of_rates(self):
        d=describe_months([dict(month='2024-01',numerator=1,denominator=2),dict(month='2024-02',numerator=9,denominator=10)])
        self.assertEqual((d['defined'],d['undefined'],d['median'],d['q1'],d['q3']),(2,10,.7,.6,.8))
        self.assertEqual(d['leave_one_month_out'][0]['value'],.9)
        self.assertEqual(d['leave_one_month_out'][1]['value'],.5)
        self.assertEqual(d['leave_one_month_out'][2]['value'],10/12)
        self.assertEqual(d['leave_out_range'],[.5,.9])

    def test_zero_months_and_duplicate_rejected(self):
        d=describe_months([]);self.assertEqual(d['undefined'],12);self.assertIsNone(d['median']);self.assertIsNone(d['leave_out_range'])
        with self.assertRaises(ValueError):describe_months([dict(month='2024-01',numerator=0,denominator=0)]*2)

    def test_event_miss_partition_and_opportunity(self):
        rows=[root(1,['hit','hit','hit']),root(2,['evaluated_miss','evaluated_miss','evaluated_miss']),
              root(3,['unavailable_miss']*3),root(4,['absent_capability_miss']*3)]
        s=root_summary(rows)[2]
        self.assertEqual((s['paired'],s['hit'],s['evaluated_miss'],s['unavailable_miss'],s['absent_capability_miss']),(4,1,1,1,1))
        self.assertEqual((s['detection']['value'],s['opportunity_coverage']['value'],s['conditional_detection']['value']),(.25,.5,.5))

    def test_common_hit_only_and_signed_contrast(self):
        rows=[root(1,['hit','hit','hit'],[600,600,300]),root(2,['evaluated_miss','evaluated_miss','hit']),
              root(3,['hit','hit','evaluated_miss']),root(4,['evaluated_miss']*3)]
        c=contrasts(rows)[0]
        self.assertEqual(c['detection_difference']['value'],0)
        self.assertEqual([r['difference_seconds'] for r in c['common_hits']],[-300])
        self.assertEqual(c['first_only'],['job2']);self.assertEqual(c['second_only'],['job3']);self.assertEqual(c['neither'],['job4'])

    def test_failure_not_miss_and_construction_not_detection(self):
        a=root(1,['evaluated_miss']*3);b=root(2,['hit']*3);b['paired']=False
        s=root_summary([a,b])[0]
        self.assertEqual((s['effective'],s['paired'],s['missing_or_failed_pairs'],s['hit']),(2,1,1,0))

    def test_no_cross_family_pooling(self):
        a=root(1,['hit']*3);b=root(2,['hit']*3);b['family']='missing_cell'
        with self.assertRaises(ValueError):root_summary([a,b])
        with self.assertRaises(ValueError):contrasts([a,b])

    def test_fixed_selection_same_miss_deduplicated(self):
        rows=[root(3,['hit']*3),root(2,['evaluated_miss']*3),root(1,['hit']*3)]
        self.assertEqual([r['case_id'] for r in select_timelines(rows)],['case1','case2'])
        rows[2]['configurations']['H']['category']='evaluated_miss'
        self.assertEqual([r['case_id'] for r in select_timelines(rows)],['case1'])

    def test_hh24_source_month(self):
        self.assertEqual(source_month('2024-02-01T00:00:00+00:00'),'2024-01')
        self.assertEqual(source_month('2024-02-01T01:00:00+00:00'),'2024-02')

    def test_recovery_end_exclusive_no_event_rescue(self):
        rows=[dict(configuration='H',domain='value',variable='T',slot_utc=f'2024-01-01T{h:02}:00:00+00:00',
                   prediction=p,complete=c) for h,p,c in [(1,True,True),(2,True,False),(3,None,False),(4,False,True)]]
        r=recovery(rows,dict(planned_end='2024-01-01T01:00:00+00:00'),'2024-01-01T04:00:00+00:00')[0]
        self.assertEqual((r['slots'],r['complete'],r['raw_positive'],r['strict_positive'],r['unevaluated']),(3,1,1,0,2))

    def test_saved_fabricated_audit_literal_answers(self):
        audit=read(ROOT/'evidence/final-controller-2026-10-08/saved-fixture-audit-v1/audit.json')
        p=next(Path(k) for k in audit['inputs'] if k.endswith('primary\\evaluation.json'))
        self.assertEqual(sha(p),audit['inputs'][str(p)])
        points=compact_points(read(p)['point_scores'],'evaluation.json')
        t=[r for r in points if r['task']=='value' and r['variable']=='T' and r['cohort']=='common']
        self.assertEqual([(r['TP'],r['FP'],r['FN'],r['TN']) for r in t],[(1,0,0,2),(1,0,0,2),(1,2,0,0)])
        self.assertEqual(t[2]['precision']['value'],1/3)

    def test_saved_count_corruption_rejected(self):
        p=ROOT/'evidence/final-controller-2026-10-08/test-fixtures/60bf8ab6600b4c0d812886e5b571513c/primary/evaluation.json'
        points=read(p)['point_scores'];points[0]['own']['n']+=1
        with self.assertRaises(ValueError):compact_points(points,'bad')

    def test_archive_hash_rejects_same_length_damage(self):
        folder=ROOT/'evidence/result-analysis-2026-10-09/test-material'/token();folder.mkdir(parents=True)
        p=folder/'fixture.zip';data=b'{"a":1}'
        with zipfile.ZipFile(p,'x') as z:z.writestr('x.json',data)
        record=dict(public_archive=str(p),public=dict(bytes=p.stat().st_size,sha256=sha(p),files={'x.json':dict(bytes=len(data),sha256=hashlib.sha256(b'{"a":2}').hexdigest())}))
        b=Bundle(record,'public')
        try:
            with self.assertRaises(ValueError):b.json('x.json')
        finally:b.close()

    def test_export_roundtrip_types_precision_and_html_safety(self):
        folder=ROOT/'evidence/result-analysis-2026-10-09/test-material'/token();folder.mkdir(parents=True)
        t=Table(folder,'fixture');original=dict(**{'pass':'primary'},variable='T',value=2.4816976238219928,zero=0,
               missing=None,flag=False,reason='</script><script>bad</script>',ids=['a','a'])
        expected=t.add(original);t.close()
        self.assertEqual(read(folder/'fixture.json'),[expected])
        with (folder/'fixture.csv').open(newline='') as f:r=next(csv.DictReader(f))
        self.assertEqual(json.loads(r['record_json']),expected)
        self.assertNotIn('</script>',(folder/'fixture--primary.js').read_text())

    def test_raw_cross_month_carry_not_second_start(self):
        episodes=[dict(group=['x','H','value','T'],nodes=[dict(time='2024-02-01T00:00:00+00:00'),dict(time='2024-02-01T01:00:00+00:00')])]
        r=raw_months(episodes,'2024-01-01T01:00:00+00:00','2024-12-31T23:00:00+00:00')
        self.assertEqual(r['H','value','T','2024-01']['raw_episode_starts_scored'],1)
        self.assertEqual(r['H','value','T','2024-02']['raw_episode_starts_scored'],0)
        self.assertEqual(r['H','value','T','2024-02']['raw_carry_in'],1)

    def test_point_pool_exact_variant_count_weighting(self):
        def point(i,variant,tp,fn):
            return dict(**{'pass':'primary'},family='gradual_bias',scenario_variable='T',support='full',variant=variant,side='injected',
                task='value',variable='T',configuration='H',cohort='common',status='applicable',primary_task=True,
                TP=tp,FP=0,FN=fn,TN=0,scored_subjects=tp+fn,row_id=str(i),month=f'2024-0{i}')
        result=pool_points([point(1,'a',1,1),point(2,'a',9,1),point(3,'b',0,2)])
        self.assertEqual(len(result),2);self.assertEqual(result[0]['recall']['value'],10/12);self.assertEqual(result[1]['recall']['value'],0)


if __name__=='__main__':unittest.main()
