"""One final job: public replay first, saved evidence checks, then private scoring.

Continuous replay uses disk sinks without month resets. Evaluation is partitioned
only after monitoring; raw episodes are formed across all saved month partitions.
"""
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta
import csv
import gc
import json
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
from pathlib import Path
import sys
import time

from demo import process_peak_bytes
from evaluation_adapter import adapt, CONFIGS, unit_key
from evaluator import (point_scores, raw_nodes, raw_episodes, root_scores, normal_ids,
                       project_burden, excess_response, recovery_units)
from final_contract import (read, lines, STREAMS, LABELS, REVIEW, REVIEW_HASH,
                            verify_files, verify_freeze, roster, window)
from lossless_storage import inventory_files
from records import save_json, sha256, utc_now
from replay import _replay, Receipt, utc
from scenarios import _build, public_copy, planned_root
from scientific_replay import load_frozen, identity


def write_lines(path, rows):
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        for row in rows: stream.write(json.dumps(row,allow_nan=False)+'\n')


class DiskSink:
    def __init__(self, folder, name, stack):
        self.folder,self.name,self.stack = folder,name,stack
        self.files={}; self.count=0; self.last_progress=None
    def append(self, row):
        stamp = row.get('slot_utc') or row.get('received_at') or row['emitted_at']
        dt = utc(stamp)
        if self.name=='ledger' and row['check'] not in ('Q04','Q05','Q06'): dt-=timedelta(minutes=5)
        month=(dt-timedelta(hours=1)).strftime('%Y-%m')
        if month not in self.files:
            folder=self.folder/month;folder.mkdir(exist_ok=True)
            f=self.stack.enter_context((folder/(self.name+'.jsonl')).open('x',encoding='utf-8',newline='\n'))
            writer=None
            if self.name=='ledger':
                c=self.stack.enter_context((folder/'ledger.csv').open('x',encoding='utf-8',newline=''))
                writer=csv.writer(c);writer.writerow(row.keys())
            self.files[month]=(f,writer)
        f,writer=self.files[month]
        f.write(json.dumps(row,allow_nan=False)+'\n')
        if writer: writer.writerow(json.dumps(v,allow_nan=False) for v in row.values())
        self.count+=1
        if self.name=='states' and row['variable']=='U':
            day=stamp[:10]
            if day!=self.last_progress:
                print('REPLAY_DAY '+day,flush=True);self.last_progress=day


def monitor_save(folder, public, slots, settings, bounds, provenance, run_id):
    folder.mkdir()
    save_json(folder/'public-inputs.json',public);save_json(folder/'schedule.json',slots)
    save_json(folder/'provenance.json',provenance)
    with ExitStack() as stack:
        sinks={k:DiskSink(folder,k,stack) for k in STREAMS}
        run=_replay([Receipt.from_public(r) for r in public],slots,settings,run_id=run_id,
            protocol='submitted-v2-final-scientific-v1',purpose=provenance['purpose'],bounds=bounds,sinks=sinks)
        meta={k:v for k,v in run.items() if k not in STREAMS}
        meta['stream_counts']={k:s.count for k,s in sinks.items()}
    save_json(folder/'run.json',meta)
    save_json(folder/'completed.json',{'state':'monitor_complete','files':inventory_files(folder)})


def verify_monitor(folder):
    done=read(folder/'completed.json')
    if done.get('state')!='monitor_complete': raise ValueError('Monitor did not complete')
    verify_files(folder,done['files'])
    actual=inventory_files(folder);actual.pop('completed.json')
    if actual!=done['files']: raise ValueError('Unlisted monitor evidence')
    run=read(folder/'run.json');slots=read(folder/'schedule.json')
    if run['slots_closed']!=len(slots) or run['drain_until']!=(utc(slots[-1])+timedelta(hours=1)).isoformat():
        raise ValueError('Monitor truncated its schedule/drain')
    for name in STREAMS:
        count=sum(1 for p in folder.glob('*/'+name+'.jsonl') for _ in lines(p))
        if count!=run['stream_counts'][name]: raise ValueError('Missing saved stream rows')
    return run,read(folder/'public-inputs.json'),slots


def read_units(folder, case_id, month=None):
    meta=read(folder/'run.json'); public=read(folder/'public-inputs.json');slots=read(folder/'schedule.json')
    paths=sorted(p for p in folder.iterdir() if p.is_dir() and (month is None or p.name==month))
    run={**meta,**{name:[r for p in paths for r in lines(p/(name+'.jsonl'))] for name in STREAMS}}
    if month is not None:
        slots=[s for s in slots if (utc(s)-timedelta(hours=1)).strftime('%Y-%m')==month]
        public=[r for r in public if (utc(r['received_at'])-timedelta(hours=1)).strftime('%Y-%m')==month]
        run.update(slots_closed=len(slots),drain_until=(utc(slots[-1])+timedelta(hours=1)).isoformat())
    return adapt(run,public,slots,case_id=case_id),public,slots


def background_generation(originals, case_id):
    root=planned_root({'case_id':case_id,'family':'missing_cell','variable':None,
        'planned_onset':originals[0]['timestamp'],'length_hours':1,'variant':'continuous_background'})
    root.update(construction='no_effect',C=0,Z=1,U=0,accounting_assessed=True,unassessed_units=0)
    public=[public_copy(r) for r in originals]
    return {'public':public,'counterpart':public,'root':root,
            'lineage':[{'identity':r['identity'],'parent_id':r['identity'],'original':r} for r in originals]}


def compact_nodes(units):
    return [{k:v for k,v in n.items() if k!='checks'} for n in raw_nodes(units)]


def source_months(nodes, truth):
    result={}
    for n in nodes:
        key=n['key'] if n['domain']!='timestamp' else unit_key(n['case_id'],'Q04',n['subject'])
        result[n['key']]=truth[key]['source_month']
    return result


def score_background(public_dir, private_dir, generated, win):
    case_id=generated['root']['case_id']; folder=public_dir/'background'
    verify_monitor(folder)
    months=sorted(p.name for p in folder.iterdir() if p.is_dir())
    all_nodes=[]
    # First pass is truth-free. No labels are opened until full raw episodes exist.
    for month in months:
        units,_,_=read_units(folder,case_id,month)
        all_nodes.extend(compact_nodes(units));del units;gc.collect()
    episodes=raw_episodes(all_nodes)
    save_json(public_dir/'raw-episodes.json',episodes)
    from final_labels import load_labels, reviewed_truth
    labels=load_labels(REVIEW/'numeric-labels.jsonl',REVIEW_HASH)
    allowed=defaultdict(set); ownership={}; totals={}; scored_keys=set()
    for month in months:
        print('SCORE_MONTH '+month,flush=True)
        units,receipts,slots=read_units(folder,case_id,month)
        part={**generated,'public':receipts,'counterpart':receipts}
        truth=reviewed_truth(part,{**win,'slots':slots},labels,background=True)
        write_lines(private_dir/(month+'-truth.jsonl'),({'key':k,**v} for k,v in truth['units'].items()))
        points=point_scores(units,truth['units']);save_json(public_dir/(month+'-points.json'),points)
        for p in points:
            key='|'.join((p['task'],str(p['variable']),p['configuration']))
            t=totals.setdefault(key,{cohort:{k:0 for k in ('TP','FP','FN','TN','n')} for cohort in ('own','common')})
            for cohort in t:
                for k in t[cohort]: t[cohort][k]+=p[cohort][k]
        nodes=compact_nodes(units);ownership.update(source_months(nodes,truth['units']))
        for n in nodes:
            k=n['key'] if n['domain']!='timestamp' else unit_key(n['case_id'],'Q04',n['subject'])
            if truth['units'][k]['scope']=='scored':scored_keys.add(n['key'])
        for cfg in CONFIGS:
            for cohort in ('own','common'):
                allowed[cfg,cohort].update(normal_ids(units,truth['units'],cfg,cohort))
        del units,truth,nodes,points;gc.collect()
    groups=defaultdict(list)
    for n in all_nodes:groups[(n['case_id'],n['configuration'],n['domain'],n['variable'])].append(n)
    burdens=[]
    for group,nodes in sorted(groups.items(),key=str):
        keys={n['key'] for n in nodes};eps=[e for e in episodes if tuple(e['group'])==group]
        for cohort in ('own','common'):
            burdens.append({'group':list(group),'cohort':cohort,
                **project_burden(nodes,eps,allowed[group[1],cohort]&keys,ownership),
                'raw_replay_counts_include_unscored_context':True,
                'raw_positive_units_scored':sum(n['prediction'] is True and n['key'] in scored_keys for n in nodes),
                'raw_episodes_with_scored_positive':sum(any(n['key'] in scored_keys for n in e['nodes']) for e in eps)})
    for t in totals.values():
        for counts in t.values():
            d=counts['FP']+counts['TN'];counts['fpr']={'value':counts['FP']/d if d else None,
                'denominator':d,'reason':None if d else 'no_truth_negatives'}
    save_json(public_dir/'background-evaluation.json',{'annual_counts':totals,'burden':burdens,
        'labels':LABELS,'monthly_point_files':[m+'-points.json' for m in months],
        'primary_exposure_source':'this continuous run only; no monthly resets',
        'scored_schedule_hours':win['scored_hours']})


def score_pair(public_dir, private_dir, generated, win):
    case_id=generated['root']['case_id']
    for side in ('injected','counterpart'): verify_monitor(public_dir/side)
    units,receipts,slots=read_units(public_dir/'injected',case_id)
    controls,control_public,_=read_units(public_dir/'counterpart',case_id+'-control')
    episodes=raw_episodes(compact_nodes(units));save_json(public_dir/'raw-episodes.json',episodes)
    save_json(public_dir/'counterpart-raw-episodes.json',raw_episodes(compact_nodes(controls)))
    from final_labels import load_labels, reviewed_truth
    labels=load_labels(REVIEW/'numeric-labels.jsonl',REVIEW_HASH)
    truth=reviewed_truth(generated,win,labels)
    control_truth=reviewed_truth(generated,win,labels,counterpart=True)
    save_json(private_dir/'truth.json',truth);save_json(private_dir/'counterpart-truth.json',control_truth)
    health={(case_id,c):{'valid':True,'reason':None} for c in CONFIGS}
    diagnostics={}
    settings_id=identity(read(public_dir/'injected/run.json')['settings'])
    control_settings_id=identity(read(public_dir/'counterpart/run.json')['settings'])
    for cfg in CONFIGS:
        select=lambda rows:[u for u in rows if u['task']=='value' and u['configuration']==cfg]
        changed={(s,generated['root']['variable']) for s in generated['root']['actual_change_mask']}
        diagnostics[cfg]=(excess_response(select(units),select(controls),changed,settings_id,control_settings_id)
            if generated['root']['family'] in ('gradual_bias','out_of_range') else {'status':'inapplicable'})
    save_json(public_dir/'evaluation.json',{'point_scores':point_scores(units,truth['units']),
        'counterpart_point_scores':point_scores(controls,control_truth['units']),
        'root_outcomes':root_scores(truth['roots'],units,health),'excess_response':diagnostics,
        'labels':LABELS,'primary_normal_exposure_hours':0})
    for side,items in (('injected',units),('counterpart',controls)):
        write_lines(public_dir/(side+'-recovery.jsonl'),recovery_units(items,generated['root']['planned_onset'],
            generated['root']['length_hours'],win['last_scored']))


def execute(job, public_dir, private_dir, freeze_folder):
    start=time.perf_counter();freeze=verify_freeze(freeze_folder)
    pass_id=job['pass'];settings,bounds,proof=load_frozen(pass_id)
    frozen=read(freeze_folder/'settings.json')[pass_id]
    if {v:asdict(s) for v,s in settings.items()} != {v:{**s,'coefficients':tuple(s['coefficients'])} for v,s in frozen['settings'].items()}:
        raise ValueError('Settings changed after freeze')
    case=job.get('case');win=window(None if case is None else int(case['source_month'][-2:]))
    if case is not None and case not in roster(): raise ValueError('Unspecified final case')
    from final_data import load_originals
    originals=load_originals(freeze_folder,win)
    scales={'kind':'frozen_scientific',**{v:s.scale for v,s in settings.items()},
            'model_sha256':{v:s.model_sha256 for v,s in settings.items()}}
    g=background_generation(originals,job['job_id']) if case is None else _build(case,originals,scales,'frozen_scientific')
    save_json(private_dir/'construction.json',{**g,'public':None,'counterpart':None})
    save_json(public_dir/'window.json',win);save_json(public_dir/'job.json',job)
    provenance={'purpose':'final performance; assumed-normal background; exploratory humidity R1',
        'freeze_sha256':sha256(freeze_folder/'freeze.json'),'labels':LABELS,'scientific_adapter':proof,
        'actual_scientific_freeze':True,'pass':pass_id,'input_identity':identity(g['public']),
        'counterpart_identity':identity(g['counterpart'])}
    save_json(public_dir/'provenance.json',provenance)
    status=g['root']['construction']
    if g['public'] is not None:
        for side,key in ([('background','public')] if case is None else [('injected','public'),('counterpart','counterpart')]):
            print('MONITOR '+side+' '+job['job_id'],flush=True)
            monitor_save(public_dir/side,g[key],win['slots'],settings,bounds,provenance,job['job_id']+'-'+side)
        print('MONITORS_SAVED; VERIFY_AND_SCORE '+job['job_id'],flush=True)
        if case is None:score_background(public_dir,private_dir,g,win)
        else:score_pair(public_dir,private_dir,g,win)
    else:
        save_json(public_dir/'construction-disposition.json',g['root'])
    verify_freeze(freeze_folder)
    save_json(public_dir/'worker-completed.json',{'state':'complete','job_id':job['job_id'],'pass':pass_id,
        'kind':job['kind'],'construction':status,'labels':LABELS,
        'configurations':list(CONFIGS),'freeze_sha256':sha256(freeze_folder/'freeze.json'),
        'public_files':inventory_files(public_dir),'private_files':inventory_files(private_dir),
        'primary_normal_exposure_hours':win['scored_hours'] if case is None else 0,
        'exposure_count_meaning':'scheduled background hours, not eligible performance denominator',
        'worker_seconds':time.perf_counter()-start,'peak_process_bytes':process_peak_bytes(),'finished_utc':utc_now()})


if __name__=='__main__':
    from batch_controller import exclusive_lock
    from lossless_storage import within_work
    with exclusive_lock(within_work(sys.argv[5]),'worker.lock'):
        execute(read(Path(sys.argv[1])),within_work(sys.argv[2]),within_work(sys.argv[3]),within_work(sys.argv[4]))
