"""One isolated fabricated job. No scientific model, fit, or final-data route."""
import gc
import json
from pathlib import Path
import sys
import time

from demo import process_peak_bytes
from evaluation_adapter import CONFIGS, adapt
from evaluator import excess_response, point_scores, raw_episodes, raw_nodes, recovery_units, root_scores
from evaluator_demo import export_points, read_completed
from lossless_storage import inventory_files, within_work
from records import save_json, sha256
from scenario_demo import monitor_save, payload
from scenario_truth import private_truth, primary_task
from scenarios import build, fabricated_originals, inventory, month_window


def fixture(job, public, private):
    """Tiny storage-only records with deliberately nontrivial types and precision."""
    rows=[{'job':job['job_id'],'configuration':c,'value':10+1/24,'missing':None,
           'zero':0,'flag':False,'lineage':['original-1'],'text':'T/U °C','raw':'10.041666666666666'} for c in CONFIGS]
    save_json(public/'predictions.json',rows)
    save_json(public/'public-inputs.json',{'identity':'original-1','value':10+1/24})
    save_json(private/'truth.json',{'root':'r1','parent':'original-1','truth':None,'raw_token':''})


def synthetic(job, public, private):
    case=job['case']
    if case not in inventory(): raise ValueError('Only known fabricated cases enabled')
    window=month_window(int(case['source_month'][-2:]))
    original=fabricated_originals(window['slots'])
    g=build(case,original)
    if g['root']['construction']!='effective': raise ValueError('Construction blocked or ineffective; retain explicit failure')
    save_json(private/'construction.json',g | {'public':None,'counterpart':None})
    save_json(public/'case.json',case); save_json(public/'window.json',window)
    monitor_save(public/'injected',g['public'],window,case['case_id']+'-injected')
    monitor_save(public/'counterpart',g['counterpart'],window,case['case_id']+'-counterpart')
    del original,g
    gc.collect()
    injected,receipts,slots=read_completed(public/'injected')
    counterpart,controls_public,_=read_completed(public/'counterpart')
    units=adapt(injected,receipts,slots,case_id=case['case_id'])
    controls=adapt(counterpart,controls_public,slots,case_id=case['case_id']+'-control')
    episodes=raw_episodes(raw_nodes(units))  # still before truth loading
    prediction_hashes={x:sha256(public/x/'ledger.jsonl') for x in ('injected','counterpart')}
    saved=json.loads((private/'construction.json').read_text())
    saved.update(public=receipts,counterpart=controls_public)
    truth=private_truth(saved,window)
    save_json(private/'truth.json',truth)
    save_json(private/'counterpart-truth.json',private_truth(saved,window,counterpart=True))
    health={(case['case_id'],c):{'valid':True,'reason':None} for c in CONFIGS}
    def evaluate():
        points=point_scores(units,truth['units'])
        diagnostic={}
        for c in CONFIGS:
            select=lambda items:[u for u in items if u['task']=='value' and u['configuration']==c]
            diagnostic[c]=excess_response(select(units),select(controls),
                {(s,case['variable']) for s in truth['roots'][0]['actual_change_mask']},
                sha256(public/'injected/settings.json'),sha256(public/'counterpart/settings.json'))
        return {'point_scores':points,'root_outcomes':root_scores(truth['roots'],units,health),
                'counterpart_excess_response':diagnostic,
                'primary_point_groups':[[x['task'],x['variable'],x['configuration']] for x in points if primary_task(case,x['task'],x['variable'])],
                'recovery_units_injected':len(recovery_units(units,case['planned_onset'],case['length_hours'],window['last_scored'])),
                'recovery_units_counterpart':len(recovery_units(controls,case['planned_onset'],case['length_hours'],window['last_scored'])),
                'primary_normal_exposure_hours':0,'purpose':'fabricated storage pilot, no research results'}
    answers=evaluate()
    if payload(answers)!=payload(evaluate()): raise ValueError('Evaluation repeat differs')
    save_json(public/'evaluation.json',answers); export_points(public,answers['point_scores'])
    save_json(public/'raw-episodes.json',[{'episode_id':e['episode_id'],'group':e['group'],
        'nodes':[{'key':n['key'],'time':n['time'],'contributors':n['contributors']} for n in e['nodes']]} for e in episodes])
    if prediction_hashes!={x:sha256(public/x/'ledger.jsonl') for x in prediction_hashes}:
        raise ValueError('Evaluation changed predictions')


def execute(job, public, private):
    start=time.perf_counter()
    if job.get('configurations')!=list(CONFIGS): raise ValueError('All configurations required')
    if job['kind']=='fixture': fixture(job,public,private)
    elif job['kind']=='synthetic': synthetic(job,public,private)
    else: raise ValueError('Research worker disabled')
    save_json(public/'worker-completed.json',{'state':'complete','job_id':job['job_id'],
        'configurations':list(CONFIGS),'primary_normal_exposure_hours':0,
        'public_files':inventory_files(public),'private_files':inventory_files(private),
        'worker_seconds':time.perf_counter()-start,'peak_process_bytes':process_peak_bytes(),
        'kind':job['kind'],'scientific_results':False})


if __name__=='__main__':
    from batch_controller import exclusive_lock
    # A worker can briefly outlive a forcibly killed controller. This separate OS
    # lock prevents a resumed controller's child from running at the same time.
    with exclusive_lock(within_work(sys.argv[4]),'worker.lock'):
        execute(json.loads(Path(sys.argv[1]).read_text()),within_work(sys.argv[2]),within_work(sys.argv[3]))
