"""Manual sequential final controller with immutable plans and verified archives.

freeze -> init-pilot -> run -> status -> init-full (measured pilot required).
No acquisition, fitting, retuning, automatic retries, or detector-led exclusions.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

from batch_controller import exclusive_lock, event, atomic_json
from final_contract import (EVIDENCE, read, design, LABELS, PASSES, verify_freeze,
                            create_freeze, verify_files)
from final_intake import resources
from lossless_storage import within_work, inventory_files, pack, check_bundle, remove_verified_scratch
from records import ROOT, PROJECT, save_json, sha256, utc_now

MIB=1024**2
GIB=1024**3


def jobs(scope):
    d=design();result=[]
    for pass_id in PASSES:
        result.append({'job_id':pass_id+'-background','kind':'background','pass':pass_id})
        result.extend({'job_id':pass_id+'-'+c['case_id'],'kind':'pair','pass':pass_id,'case':c} for c in d['cases'])
    if scope=='pilot':
        return [result[0],next(j for j in result if j.get('case',{}).get('case_id')==d['pilot_case'])]
    if scope!='full': raise ValueError('Unknown scientific scope')
    return result


def check_done(record, job, folder, plan):
    if (record.get('state')!='complete' or record.get('job')!=job or
            record.get('plan_sha256')!=sha256(folder/'plan.json') or
            record.get('freeze_sha256')!=plan['freeze_sha256'] or record.get('labels')!=LABELS):
        raise ValueError('Wrong job, plan, freeze or eligibility labels')
    public=within_work(record['public_archive']);private=within_work(record['private_archive'])
    if (not public.is_relative_to(folder/'attempts') or
            not private.is_relative_to(ROOT/'data/final-private'/folder.name)):
        raise ValueError('Archives outside this final batch')
    check_bundle(public,record['public']);check_bundle(private,record['private'])


def inspect(folder, verify_inputs=True):
    folder=within_work(folder);plan=read(folder/'plan.json')
    if read(folder/'plan-binding.json')['plan_sha256']!=sha256(folder/'plan.json'):
        raise ValueError('Immutable plan changed; preserve it and create a new plan')
    if plan.get('version')!='final-controller-plan-v1' or plan['jobs']!=jobs(plan['scope']):
        raise ValueError('Plan does not retain prescribed roster')
    if sha256(Path(plan['freeze'])/'freeze.json')!=plan['freeze_sha256']: raise ValueError('Freeze changed')
    if verify_inputs: verify_freeze(Path(plan['freeze']))
    completed=[];construction=Counter()
    for job in plan['jobs']:
        marker=folder/'completed'/(job['job_id']+'.json')
        if marker.exists():
            record=read(marker);check_done(record,job,folder,plan)
            completed.append(record);construction[record['construction']]+=1
    state='complete' if len(completed)==len(plan['jobs']) else 'incomplete'
    return {'state':state,'scope':plan['scope'],'verified_jobs':len(completed),'planned_jobs':len(plan['jobs']),
            'pending_jobs':len(plan['jobs'])-len(completed),'construction':dict(construction),
            'actual_scientific_freeze':True,'labels':LABELS},completed


def create_plan(folder, freeze, scope, pilot=None):
    folder=within_work(folder);freeze=within_work(freeze);verify_freeze(freeze)
    allowance={'minimum_available_memory_bytes':768*MIB,'reserve_bytes':10*GIB,
        'scratch_budget_bytes':2*GIB,'per_job_budget_bytes':32*MIB,
        'basis':'conservative pilot allowance; empirical final capacity not yet measured'}
    pilot_binding=None
    if scope=='full':
        if pilot is None: raise ValueError('A completed measured pilot is required')
        status,done=inspect(pilot)
        pp=read(Path(pilot)/'plan.json')
        if (status['state']!='complete' or status['scope']!='pilot' or
                pp['freeze_sha256']!=sha256(freeze/'freeze.json') or
                any(d['construction'] in ('blocked','generation_error') for d in done)):
            raise ValueError('Pilot incomplete, blocked, failed or from another freeze')
        peak=max(d['peak_process_bytes'] for d in done)
        size=max(d['public']['bytes']+d['private']['bytes'] for d in done if d['job']['kind']=='pair')
        scratch=max(d['public']['uncompressed_bytes']+d['private']['uncompressed_bytes'] for d in done)
        allowance.update(minimum_available_memory_bytes=max(768*MIB,int(peak*1.5)),
            scratch_budget_bytes=max(2*GIB,scratch*2),per_job_budget_bytes=max(8*MIB,size*2),
            basis='2x measured pair archive and scratch; 1.5x peak process memory, minimum 768MiB; estimates only')
        pilot_binding={'folder':str(Path(pilot).resolve()),'plan_sha256':sha256(Path(pilot)/'plan.json'),
                       'completion_identities':{d['job']['job_id']:sha256(Path(pilot)/'completed'/(d['job']['job_id']+'.json')) for d in done},
                       'interpretation':'pilot retained as exposure/reproduction evidence; full plan alone supplies canonical performance'}
    folder.mkdir(parents=True,exist_ok=False)
    plan={'version':'final-controller-plan-v1','scope':scope,'freeze':str(freeze),
        'freeze_sha256':sha256(freeze/'freeze.json'),'jobs':jobs(scope),'created_utc':utc_now(),
        'labels':LABELS,'resources':allowance,'resource_snapshot':resources(),'pilot':pilot_binding,
        'primary_exposure':'one background job per pass in this plan only; never add controls or pilot copies',
        'manual_launch':True}
    save_json(folder/'plan.json',plan)
    save_json(folder/'plan-binding.json',{'plan_sha256':sha256(folder/'plan.json')})
    return plan


def preflight(plan, remaining, snapshot):
    budget=plan['resources']
    required=budget['reserve_bytes']+budget['scratch_budget_bytes']+remaining*budget['per_job_budget_bytes']
    if snapshot['memory_available_bytes'] is None or snapshot['memory_available_bytes']<budget['minimum_available_memory_bytes']:
        raise ValueError('Insufficient available RAM; no job launched; close other applications and retry unchanged')
    if snapshot['disk_free_bytes']<required:
        raise ValueError('Insufficient disk allowance; no job launched; do not reduce scientific roster')
    return required


def worker(job, public, private, attempt, folder, freeze):
    save_json(attempt/'job.json',job)
    with (attempt/'worker.log').open('xb') as log:
        child=subprocess.Popen([sys.executable,'-X','utf8',str(PROJECT/'final_worker.py'),
            str(attempt/'job.json'),str(public),str(private),str(freeze),str(folder)],
            cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        try:
            while True:
                try:
                    code=child.wait(timeout=30);break
                except subprocess.TimeoutExpired:
                    print('RUNNING '+job['job_id']+'; progress: '+str(attempt/'worker.log'),flush=True)
        except BaseException:
            child.kill();child.wait();raise
    if code: raise RuntimeError(f'Worker exited {code}; inspect {attempt / "worker.log"}')


def check_worker(public, private, job, freeze_hash):
    done=read(public/'worker-completed.json')
    if (done.get('state')!='complete' or done.get('job_id')!=job['job_id'] or
            done.get('pass')!=job['pass'] or done.get('kind')!=job['kind'] or
            done.get('labels')!=LABELS or done.get('freeze_sha256')!=freeze_hash or
            done.get('configurations')!=['B0','R','H']): raise ValueError('Incomplete or misbound worker')
    actual=inventory_files(public);actual.pop('worker-completed.json')
    if actual!=done['public_files'] or inventory_files(private)!=done['private_files']:
        raise ValueError('Worker output roster differs')
    if done['construction'] not in ('blocked','generation_error','no_effect','effective'):
        raise ValueError('Missing explicit construction disposition')
    required={'window.json','job.json','provenance.json'}
    if done['construction'] in ('blocked','generation_error'):required.add('construction-disposition.json')
    elif job['kind']=='background':required|={'background/completed.json','background-evaluation.json','raw-episodes.json'}
    else:required|={'injected/completed.json','counterpart/completed.json','evaluation.json','raw-episodes.json','injected-recovery.jsonl','counterpart-recovery.jsonl'}
    if not required<=set(actual):raise ValueError('Canonical scientific outputs missing')
    if done['primary_normal_exposure_hours']!=(8760 if job['kind']=='background' else 0):
        raise ValueError('Incorrect primary schedule exposure')
    return done


def run_plan(folder, retry=False, stop_after=None):
    folder=within_work(folder)
    with exclusive_lock(folder):
        plan=read(folder/'plan.json');current=None
        try:
            status,done=inspect(folder);completed={d['job']['job_id'] for d in done}
            event(folder,'verified',**{k:v for k,v in status.items() if k!='state'})
            for job in plan['jobs']:
                if job['job_id'] in completed:continue
                current=job['job_id'];prior=folder/'attempts'/current
                if prior.exists() and any(prior.iterdir()) and not retry:
                    raise ValueError('Prior incomplete attempt retained; inspect then explicitly --retry-incomplete')
                snapshot=resources();required=preflight(plan,len(plan['jobs'])-len(completed),snapshot)
                event(folder,'preflight',job=current,required_disk_bytes=required,resources=snapshot)
                token=uuid.uuid4().hex;attempt=prior/token;attempt.mkdir(parents=True)
                priv_attempt=ROOT/'data/final-private'/folder.name/current/token;priv_attempt.mkdir(parents=True)
                public,private=attempt/'public-scratch',priv_attempt/'private-scratch';public.mkdir();private.mkdir()
                save_json(attempt/'started.json',{'utc':utc_now(),'plan_sha256':sha256(folder/'plan.json'),
                    'resources':snapshot,'command':sys.argv,'final_period_exposure_begins_with_worker':True})
                event(folder,'running',job=current,attempt=str(attempt),completed=len(completed),total=len(plan['jobs']))
                worker(job,public,private,attempt,folder,Path(plan['freeze']))
                proof=check_worker(public,private,job,plan['freeze_sha256'])
                pub=pack(public,attempt/'public.zip');priv=pack(private,priv_attempt/'private.zip')
                record={'state':'complete','job':job,'plan_sha256':sha256(folder/'plan.json'),
                    'freeze_sha256':plan['freeze_sha256'],'labels':LABELS,'construction':proof['construction'],
                    'public_archive':str(attempt/'public.zip'),'private_archive':str(priv_attempt/'private.zip'),
                    'public':pub,'private':priv,'worker_seconds':proof['worker_seconds'],
                    'peak_process_bytes':proof['peak_process_bytes'],'utc':utc_now()}
                check_done(record,job,folder,plan)
                save_json(folder/'completed'/(current+'.json'),record)
                completed.add(current)
                remove_verified_scratch(public,attempt,pub);remove_verified_scratch(private,priv_attempt,priv)
                event(folder,'checkpoint',job=current,completed=len(completed),total=len(plan['jobs']))
                if stop_after and len(completed)>=stop_after and len(completed)<len(plan['jobs']):
                    event(folder,'incomplete',reason='requested bounded stop');return False
            status,_=inspect(folder)
            event(folder,'complete',**{k:v for k,v in status.items() if k!='state'})
            return True
        except BaseException as exc:
            state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'blocked' if isinstance(exc,ValueError) else 'failed'
            event(folder,state,job=current,error=repr(exc),traceback=traceback.format_exc());raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['freeze','freeze-status','init-pilot','init-full','run','status'])
    p.add_argument('--out',type=Path,required=True);p.add_argument('--freeze',type=Path)
    p.add_argument('--tests',type=Path);p.add_argument('--pilot',type=Path)
    p.add_argument('--retry-incomplete',action='store_true');p.add_argument('--stop-after',type=int)
    a=p.parse_args()
    if a.stop_after is not None and a.stop_after<1:p.error('--stop-after must be positive')
    if a.action=='freeze':
        if a.tests is None:p.error('--tests is required')
        create_freeze(a.out,a.tests);print('FROZEN '+str(a.out.resolve()))
    elif a.action=='freeze-status':
        f=verify_freeze(a.out);print(json.dumps({'state':f['state'],'actual_scientific_freeze':True,'verified':True,'labels':LABELS}))
    elif a.action.startswith('init-'):
        if a.freeze is None:p.error('--freeze is required')
        plan=create_plan(a.out,a.freeze,a.action[5:],a.pilot)
        print('PLAN_READY '+str(a.out.resolve())+' jobs='+str(len(plan['jobs'])))
    elif a.action=='status':
        status,_=inspect(a.out);print(json.dumps(status));return 0 if status['state']=='complete' else 2
    else:return 0 if run_plan(a.out,a.retry_incomplete,a.stop_after) else 2
    return 0


if __name__=='__main__':raise SystemExit(main())
