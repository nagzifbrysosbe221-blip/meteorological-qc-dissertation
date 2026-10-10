"""Bounded manufactured checks with literal arithmetic and calendar expectations."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import final_contract as contract
import final_controller as controller
import final_worker as worker
from final_data import originals_from_inventory, load_originals
from final_labels import load_labels, reviewed_truth
from evaluation_adapter import unit_key
from evaluator import point_scores, normal_ids, raw_episodes, project_burden
from records import ROOT, save_json, sha256, code_identity
from replay import _replay, Receipt
from scenarios import fabricated_originals, inventory, _build, FAMILIES
from scientific_replay import PASSES
from test_scientific_replay import hand_settings, public_fixture


def labels_for(originals):
    return {(r['station'],r['timestamp'],v):{'station':r['station'],'timestamp_utc':r['timestamp'],
        'source_date':r['source_date'],'source_hour':r['source_hour'],'variable':v,
        'scope':'final','target_label':r['station']==260,'scored_period':True,
        'record_ids':[r['identity']],'numeric_truth':False,'numeric_truth_state':'assumed_normal_not_certified',
        'review_sha256':'a'*64} for r in originals for v in ('T','U')}


class FinalControllerTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'evidence/final-controller-2026-10-08/test-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def test_full_roster_and_exact_pilot_are_prescribed(self):
        full=controller.jobs('full');pilot=controller.jobs('pilot')
        self.assertEqual(len(full),3748);self.assertEqual(len(pilot),2)
        self.assertEqual(pilot[0]['job_id'],'primary-background')
        c=pilot[1]['case'];self.assertEqual((c['source_month'],c['variable'],c['sign'],c['scale_multiple'],c['ramp_hours']),('2024-03','T',1,2,24))
        for p in PASSES:
            cases=[j['case'] for j in full if j['pass']==p and j['kind']=='pair']
            self.assertEqual(len(cases),936);self.assertEqual({c['family'] for c in cases},set(FAMILIES))
            self.assertEqual(len({c['variant'] for c in cases}),78)

    def test_source_calendar_context_and_hh24_literal_answers(self):
        jan,feb,dec=[contract.window(m) for m in (1,2,12)]
        self.assertEqual((jan['context_hours'],jan['scored_hours']),(24,720))
        self.assertEqual((feb['context_hours'],feb['scored_hours']),(336,696))
        self.assertEqual(jan['first_scored'],'2024-01-02T01:00:00+00:00')
        self.assertEqual(jan['last_scored'],'2024-02-01T00:00:00+00:00')
        self.assertEqual(dec['last_scored'],'2025-01-01T00:00:00+00:00')
        self.assertEqual((len(contract.window()['slots']),contract.window()['scored_hours']),(8784,8760))

    def test_streamed_kernel_equals_memory_kernel_and_csv_types(self):
        public,slots=public_fixture(12);settings=hand_settings();out=self.root/'monitor'
        worker.monitor_save(out,public,slots,settings,{'T':(-40,50),'U':(0,100)}, {'purpose':'fabricated'},'fixture')
        meta,_,_=worker.verify_monitor(out)
        expected=_replay([Receipt.from_public(r) for r in public],slots,settings,run_id='fixture',
            protocol='submitted-v2-final-scientific-v1',purpose='fabricated',bounds={'T':(-40,50),'U':(0,100)})
        for name in contract.STREAMS:
            actual=[r for p in sorted(out.glob('*/'+name+'.jsonl')) for r in contract.lines(p)]
            self.assertEqual(actual,expected[name])
        import csv
        with next(out.glob('*/ledger.csv')).open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f);rows=[{k:json.loads(v) for k,v in r.items()} for r in reader]
        self.assertEqual(rows,expected['ledger'])

    def test_continuous_month_boundary_has_no_state_reset(self):
        public,slots=public_fixture(230,datetime(2000,1,23,11,tzinfo=timezone.utc))
        out=self.root/'monitor';settings=hand_settings()
        worker.monitor_save(out,public,slots,settings,{'T':(-40,50),'U':(0,100)},{'purpose':'fabricated'},'continuous')
        worker.verify_monitor(out)
        states=[r for p in sorted(out.glob('*/states.jsonl')) for r in contract.lines(p) if r['variable']=='T']
        for i,s in enumerate(states,1):
            self.assertEqual(s['valid_updates'],i);self.assertAlmostEqual(s['z'],2*(1-.8**i),places=13)
        self.assertIsNone(states[226]['prediction']);self.assertTrue(states[227]['prediction'])
        feb,_public,_slots=worker.read_units(out,'background','2000-02')
        h=[u for u in feb if u['configuration']=='H' and u['task']=='value' and u['variable']=='T']
        self.assertEqual(h[0]['checks'][1]['execution'],'unevaluated')
        self.assertTrue(h[-1]['complete'])

    def test_saved_monitor_corruption_is_execution_failure(self):
        public,slots=public_fixture(2);out=self.root/'monitor'
        worker.monitor_save(out,public,slots,hand_settings(),{'T':(-40,50),'U':(0,100)},{'purpose':'fabricated'},'x')
        p=next(out.glob('*/states.jsonl'));p.write_text('[]\n')
        with self.assertRaises(ValueError):worker.verify_monitor(out)

    def generated(self,family='gradual_bias'):
        c=next(c for c in inventory() if c['source_month']=='2000-03' and c['family']==family)
        start=datetime.fromisoformat(c['planned_onset'])-timedelta(hours=228)
        slots=[(start+timedelta(hours=i)).isoformat() for i in range(228+c['length_hours']+2)]
        originals=fabricated_originals(slots)
        g=_build(c,originals,{'kind':'frozen_scientific','T':2.,'U':2.},'frozen_scientific')
        win={'slots':slots,'first_scored':c['planned_onset'],'last_scored':slots[-1]}
        return g,win,labels_for(originals)

    def test_unknown_review_labels_never_become_negative(self):
        g,win,labels=self.generated();s=win['slots'][-1]
        labels[260,s,'T'].update(numeric_truth=None,numeric_truth_state='unknown_suspect')
        truth=reviewed_truth(g,win,labels)
        row=truth['units'][unit_key(g['root']['case_id'],'value',s,'T')]
        self.assertIsNone(row['truth']);self.assertEqual(row['origin'],'unknown')
        del labels[260,s,'T']
        with self.assertRaises(ValueError):reviewed_truth(g,win,labels)

    def test_changed_label_identity_fails_closed(self):
        g,win,labels=self.generated();labels[260,win['slots'][0],'T']['record_ids']=['wrong']
        with self.assertRaises(ValueError):reviewed_truth(g,win,labels)

    def test_sparse_changes_and_counterpart_labels_all_seven_families(self):
        for family in FAMILIES:
            g,win,labels=self.generated(family)
            truth=reviewed_truth(g,win,labels);control=reviewed_truth(g,win,labels,counterpart=True)
            self.assertEqual((g['root']['N'],g['root']['C'],g['root']['Z'],g['root']['U']),
                             (g['root']['length_hours'],g['root']['length_hours'],0,0))
            self.assertFalse(any(x['truth'] is True for x in control['units'].values()))
            self.assertTrue(any(x['truth'] is True for x in truth['units'].values()))
            for label in truth['units'].values():
                if label['scope']=='context':self.assertIsNot(label['truth'],True)

    def test_label_loader_duplicate_review_and_hh24_checks(self):
        r=list(labels_for(fabricated_originals(['2000-04-01T00:00:00+00:00'])).values())[0]
        p=self.root/'labels.jsonl';worker.write_lines(p,[r])
        self.assertEqual(len(load_labels(p,'a'*64)),1)
        for change in ({'source_hour':0},{'review_sha256':'b'*64},{'numeric_truth_state':'healthy'}):
            q=self.root/(uuid.uuid4().hex+'.jsonl');worker.write_lines(q,[r|change])
            with self.assertRaises(ValueError):load_labels(q,'a'*64)
        q=self.root/'duplicate.jsonl';worker.write_lines(q,[r,r])
        with self.assertRaises(ValueError):load_labels(q,'a'*64)

    def test_value_reader_conversion_boundary_roles_and_identity(self):
        p=self.root/'raw.txt';p.write_text('# STN,YYYYMMDD,HH,T,U\n260,20241231,24,153,87\n260,20250101,1,999,999\n')
        h=sha256(p);row={'selected':True,'source_role':'final','parent_sha256':h,'line_number':2,
            'record_id':h+':2','timestamp_utc':'2025-01-01T00:00:00+00:00','source_date':'2024-12-31',
            'source_hour':24,'station':260,'cells':{'T':'finite','U':'finite'}}
        out=originals_from_inventory(p,[row,{'selected':False}])
        self.assertEqual((out[0]['T'],out[0]['U'],out[0]['source_date']),('15.3','87.0','2024-12-31'))
        with self.assertRaises(ValueError):originals_from_inventory(p,[row|{'source_role':'fit'}])
        with self.assertRaises(ValueError):originals_from_inventory(p,[row|{'record_id':'wrong'}])

    def test_missing_freeze_blocks_before_values_are_opened(self):
        with patch('final_data.originals_from_inventory',side_effect=AssertionError('values opened')):
            with self.assertRaises(FileNotFoundError):load_originals(self.root/'absent',contract.window())

    def test_resource_gate_no_scope_reduction(self):
        p={'resources':{'minimum_available_memory_bytes':800,'reserve_bytes':10,'scratch_budget_bytes':20,'per_job_budget_bytes':5}}
        self.assertEqual(controller.preflight(p,4,{'memory_available_bytes':800,'disk_free_bytes':50}),50)
        for snap in ({'memory_available_bytes':799,'disk_free_bytes':50},{'memory_available_bytes':800,'disk_free_bytes':49}):
            with self.assertRaises(ValueError):controller.preflight(p,4,snap)

    def test_freeze_requires_current_clean_full_suite(self):
        p=self.root/'tests.json';save_json(p,{'success':True,'scope':'test_*.py','code':{},'tests_run':999,'failures':0,'errors':0,'skipped':0})
        out=self.root/'freeze'
        with self.assertRaises(ValueError):contract.create_freeze(out,p)
        self.assertTrue((out/'failed.json').exists());self.assertFalse((out/'freeze.json').exists())
        with self.assertRaises(FileExistsError):contract.create_freeze(out,p)

    def test_freeze_create_only_and_tampering(self):
        p=self.root/'tests.json';save_json(p,{'success':True,'scope':'test_*.py','code':code_identity(),'tests_run':300,'failures':0,'errors':0,'skipped':0})
        out=self.root/'freeze'
        with patch.object(contract,'accepted_evidence',return_value=({},{})),patch.object(contract,'environment',return_value={'fixture':True}):
            contract.create_freeze(out,p);self.assertTrue(contract.verify_freeze(out)['actual_scientific_freeze'])
            with self.assertRaises(FileExistsError):contract.create_freeze(out,p)
            (out/'design.json').write_text('{}')
            with self.assertRaises(ValueError):contract.verify_freeze(out)

    def test_full_plan_requires_measured_pilot(self):
        with patch.object(controller,'verify_freeze',return_value={}):
            with self.assertRaises(ValueError):controller.create_plan(self.root/'plan',self.root,'full')

    def test_bounded_pair_saved_scoring_all_passes_no_truth_leak(self):
        g,win,labels=self.generated('out_of_range')
        label_path=self.root/'review';label_path.mkdir();worker.write_lines(label_path/'numeric-labels.jsonl',labels.values())
        # One changed T value -41: Q03 hit for B0/R/H, one-hour horizon inclusive +5min.
        for pass_id,(alpha,bounds) in PASSES.items():
            public_dir=self.root/pass_id;private_dir=self.root/(pass_id+'-private');public_dir.mkdir();private_dir.mkdir()
            settings={v:replace(s,alpha=alpha) for v,s in hand_settings().items()}
            for side,key in (('injected','public'),('counterpart','counterpart')):
                worker.monitor_save(public_dir/side,g[key],win['slots'],settings,{'T':bounds,'U':(0,100)},
                    {'purpose':'bounded fabricated fixture'},'test-'+side)
            before=sha256(public_dir/'injected/completed.json')
            with patch.object(worker,'REVIEW',label_path),patch.object(worker,'REVIEW_HASH','a'*64):
                worker.score_pair(public_dir,private_dir,g,win)
            self.assertEqual(before,sha256(public_dir/'injected/completed.json'))
            result=contract.read(public_dir/'evaluation.json')
            self.assertEqual(result['primary_normal_exposure_hours'],0)
            value=[p for p in result['point_scores'] if p['task']=='value' and p['variable']=='T']
            for point in value:self.assertEqual(point['own']['TP'],1)
            self.assertEqual(len(result['root_outcomes']),1)
            outcomes=result['root_outcomes'][0]['configurations']
            self.assertEqual(set(outcomes),{'B0','R','H'})
            for outcome in outcomes.values():
                self.assertEqual((outcome['category'],outcome['delay_seconds']),('hit',300))

    def test_bounded_background_scoring_context_exposure_and_cross_month(self):
        public,slots=public_fixture(230,datetime(2000,1,22,12,tzinfo=timezone.utc))
        originals=[]
        for r in public:
            t=datetime.fromisoformat(r['timestamp'])
            originals.append({k:v for k,v in r.items() if k!='received_at'}|{
                'source_date':(t-timedelta(hours=1)).date().isoformat(),'source_hour':t.hour or 24,'nominal_delivery':r['received_at']})
        g=worker.background_generation(originals,'fabricated-background')
        win={'slots':slots,'first_scored':slots[24],'last_scored':slots[-1],'scored_hours':206}
        public_dir=self.root/'public';private_dir=self.root/'private';public_dir.mkdir();private_dir.mkdir()
        label_path=self.root/'review';label_path.mkdir();worker.write_lines(label_path/'numeric-labels.jsonl',labels_for(originals).values())
        worker.monitor_save(public_dir/'background',g['public'],slots,hand_settings(),{'T':(-40,50),'U':(0,100)},
            {'purpose':'bounded fabricated background'},'background')
        with patch.object(worker,'REVIEW',label_path),patch.object(worker,'REVIEW_HASH','a'*64):
            worker.score_background(public_dir,private_dir,g,win)
        result=contract.read(public_dir/'background-evaluation.json')
        counts=result['annual_counts']['value|T|H']
        self.assertEqual((counts['own']['n'],counts['own']['FP'],counts['common']['n']),(3,3,3))
        self.assertEqual(result['annual_counts']['value|T|B0']['own']['TN'],206)
        h=next(b for b in result['burden'] if b['group'][1:] == ['H','value','T'] and b['cohort']=='common')
        self.assertEqual((h['exposure'],h['segment_count'],h['burden']['value']),(3,1,240))
        self.assertEqual((h['monthly']['2000-01']['exposure'],h['monthly']['2000-01']['segment_starts']),(2,1))
        self.assertEqual((h['monthly']['2000-02']['exposure'],h['monthly']['2000-02']['segment_starts'],h['monthly']['2000-02']['carry_in']),(1,0,1))

    def mocked_plan(self):
        freeze=self.root/'freeze';freeze.mkdir();save_json(freeze/'freeze.json',{'fixture':True})
        with patch.object(controller,'verify_freeze',return_value={}):
            controller.create_plan(self.root/'plan',freeze,'pilot')
        return self.root/'plan',freeze

    def fake_worker(self,job,public,private,attempt,folder,freeze):
        from lossless_storage import inventory_files
        for name in ('window.json','job.json','provenance.json'):
            save_json(public/name,{'fixture':True})
        if job['kind']=='background':names=['background/completed.json','background-evaluation.json','raw-episodes.json']
        else:names=['injected/completed.json','counterpart/completed.json','evaluation.json','raw-episodes.json','injected-recovery.jsonl','counterpart-recovery.jsonl']
        for name in names:save_json(public/name,{'fixture':True})
        save_json(private/'truth.json',{'fixture':True,'truth':None})
        save_json(public/'worker-completed.json',{'state':'complete','job_id':job['job_id'],'kind':job['kind'],
            'pass':job['pass'],'labels':contract.LABELS,'freeze_sha256':sha256(freeze/'freeze.json'),
            'configurations':['B0','R','H'],'construction':'no_effect' if job['kind']=='background' else 'effective',
            'public_files':inventory_files(public),'private_files':inventory_files(private),
            'primary_normal_exposure_hours':8760 if job['kind']=='background' else 0,
            'worker_seconds':.1,'peak_process_bytes':1024})

    def test_controller_checkpoint_resume_hashes_and_no_duplicate_success(self):
        folder,freeze=self.mocked_plan()
        with patch.object(controller,'verify_freeze',return_value={}),patch.object(controller,'preflight',return_value=0),patch.object(controller,'worker',side_effect=self.fake_worker):
            self.assertFalse(controller.run_plan(folder,stop_after=1))
            marker=folder/'completed/primary-background.json';h=sha256(marker)
            self.assertTrue(controller.run_plan(folder));self.assertEqual(sha256(marker),h)
            status,done=controller.inspect(folder);self.assertEqual(status['verified_jobs'],2)
            Path(done[0]['public_archive']).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):controller.inspect(folder)

    def test_controller_interruption_retained_requires_explicit_retry(self):
        folder,freeze=self.mocked_plan()
        with patch.object(controller,'verify_freeze',return_value={}),patch.object(controller,'preflight',return_value=0),patch.object(controller,'worker',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):controller.run_plan(folder)
            self.assertEqual(contract.read(folder/'progress.json')['state'],'interrupted')
        with patch.object(controller,'verify_freeze',return_value={}),patch.object(controller,'preflight',return_value=0),patch.object(controller,'worker',side_effect=self.fake_worker):
            with self.assertRaises(ValueError):controller.run_plan(folder)
            self.assertTrue(controller.run_plan(folder,retry=True))
        self.assertEqual(len(list((folder/'attempts/primary-background').iterdir())),2)

    def test_private_archive_failure_prevents_completion_marker(self):
        folder,freeze=self.mocked_plan();real_pack=controller.pack
        def fail_private(source,destination):
            if destination.name=='private.zip':raise OSError('deliberate fixture archive failure')
            return real_pack(source,destination)
        with patch.object(controller,'verify_freeze',return_value={}),patch.object(controller,'preflight',return_value=0),patch.object(controller,'worker',side_effect=self.fake_worker),patch.object(controller,'pack',side_effect=fail_private):
            with self.assertRaises(OSError):controller.run_plan(folder)
        self.assertFalse((folder/'completed/primary-background.json').exists())
        self.assertTrue(list(folder.glob('attempts/*/*/public-scratch')))

    def test_worker_pipeline_manufactured_final_year_without_real_values(self):
        from dataclasses import asdict
        g,win,labels=self.generated('out_of_range')
        originals=[r['original'] for r in g['lineage'] if 'original' in r]
        case=next(c for c in inventory() if c['case_id']==g['root']['case_id'])
        # Exercise the actual job driver with bounded fabricated observations;
        # the test supplies its own freeze/models and never opens accepted values.
        job={'job_id':'fabricated-driver','kind':'pair','pass':'primary','case':case}
        freeze=self.root/'freeze';freeze.mkdir();save_json(freeze/'freeze.json',{'fabricated':True})
        settings=hand_settings();save_json(freeze/'settings.json',{'primary':{'settings':{v:asdict(s) for v,s in settings.items()}}})
        public=self.root/'public';private=self.root/'private';public.mkdir();private.mkdir()
        label_path=self.root/'review';label_path.mkdir();worker.write_lines(label_path/'numeric-labels.jsonl',labels.values())
        with patch.object(worker,'verify_freeze',return_value={}),patch.object(worker,'load_frozen',return_value=(settings,{'T':(-40,50),'U':(0,100)},{})),patch.object(worker,'window',return_value=win),patch.object(worker,'roster',return_value=[case]),patch('final_data.load_originals',return_value=originals),patch.object(worker,'REVIEW',label_path),patch.object(worker,'REVIEW_HASH','a'*64):
            worker.execute(job,public,private,freeze)
        done=controller.check_worker(public,private,job,sha256(freeze/'freeze.json'))
        self.assertEqual(done['construction'],'effective');self.assertEqual(done['primary_normal_exposure_hours'],0)

    def test_plan_cannot_change_before_first_job(self):
        folder,freeze=self.mocked_plan()
        plan=contract.read(folder/'plan.json');plan['resources']['minimum_available_memory_bytes']=1
        (folder/'plan.json').write_text(json.dumps(plan))
        with patch.object(controller,'verify_freeze',return_value={}):
            with self.assertRaises(ValueError):controller.inspect(folder)


if __name__=='__main__':unittest.main()
