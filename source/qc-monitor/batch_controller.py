"""Sequential synthetic batch/storage controller; research execution is disabled.

One immutable plan, fresh attempts, verified archives, atomic checkpoint snapshots.
Failed or interrupted attempts stay on disk; only verified successful scratch is removed.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback
import uuid

from lossless_storage import check_bundle, inventory_files, pack, remove_verified_scratch, within_work
from records import ROOT, PROJECT, code_identity, digest, save_json, sha256, utc_now
from scenarios import inventory

CONFIGS = ['B0', 'R', 'H']
PASSES = [
    {'id':'primary', 'alpha':0.01, 'T_bounds':[-40,50]},
    {'id':'alpha-0005', 'alpha':0.005, 'T_bounds':[-40,50], 'fixed':['model','lambda']},
    {'id':'alpha-002', 'alpha':0.02, 'T_bounds':[-40,50], 'fixed':['model','lambda']},
    {'id':'T-range', 'alpha':0.01, 'T_bounds':[-30,45], 'fixed':['EWMA']},
]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, allow_nan=False).encode())


def atomic_json(path, value):
    path = within_work(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def exclusive_lock(folder, name='controller.lock'):
    """OS releases the lock on a crash; an old lock file alone is not a stale lock."""
    path = folder/name
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def event(folder, state, **details):
    row = {'utc':utc_now(), 'state':state, **details}
    save_json(folder/'events'/(uuid.uuid4().hex+'.json'), row)
    atomic_json(folder/'progress.json', row)
    print(state.upper()+': '+json.dumps(details, ensure_ascii=True), flush=True)


def create_plan(folder, mode='fixture', full=False):
    folder = within_work(folder)
    folder.mkdir(parents=True, exist_ok=False)
    if mode == 'fixture':
        jobs = [{'job_id':f'fixture-{i}', 'kind':'fixture', 'configurations':CONFIGS} for i in range(3)]
    elif mode == 'synthetic':
        cases = inventory() if full else [inventory()[0], next(c for c in inventory() if c['family']=='gradual_bias' and c['source_month']=='2000-03')]
        jobs = [{'job_id':c['case_id'], 'kind':'synthetic', 'case':c, 'configurations':CONFIGS} for c in cases]
    else:
        raise ValueError('Research mode is unavailable: acceptance/models/calibration/freeze/pilot pending')
    plan = {'version':1, 'mode':mode,'scope':'full-synthetic-primary' if full else 'bounded-storage-demonstration',
            'jobs':jobs,'code':code_identity(),'created_utc':utc_now(), 'scientific_freeze':False,
            'primary_normal_exposure':'none; counterparts are diagnostic only',
            'reuse_policy':'none; each pair retained in full',
            'reserve_bytes':10*1024**3,'scratch_budget_bytes':512*1024**2,
            'per_job_budget_bytes':8*1024**2 if mode=='synthetic' else 1024**2,
            'budget_basis':'synthetic planning allowance only; per-case disk check repeats, no research capacity guarantee'}
    save_json(folder/'plan.json', plan)
    print(folder, flush=True)
    return plan


def validate_plan(plan):
    if plan['mode'] not in ('fixture','synthetic') or plan.get('scientific_freeze') is not False:
        raise ValueError('Research execution remains disabled')
    if plan['code'] != code_identity():
        raise ValueError('Code changed: preserve this batch and create a new plan; do not mix revisions')
    jobs = plan['jobs']
    ids = [j['job_id'] for j in jobs]
    if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in ids):
        raise ValueError('Missing/duplicate/unsafe job identities')
    for job in jobs:
        if job['configurations'] != CONFIGS or job['kind'] != plan['mode']:
            raise ValueError('All B0/R/H configurations required')
        if job['kind']=='synthetic' and job['case'] not in inventory():
            raise ValueError('Unknown fabricated case')
        if job['kind']=='synthetic' and job['job_id']!=job['case']['case_id']:
            raise ValueError('Job and case identities differ')
    if plan['scope']=='full-synthetic-primary' and (plan['mode']!='synthetic' or ids != [c['case_id'] for c in inventory()]):
        raise ValueError('Full synthetic plan must retain exactly all 936 cases in order')
    for key in ('reserve_bytes','scratch_budget_bytes','per_job_budget_bytes'):
        if type(plan[key]) is not int or plan[key]<=0:
            raise ValueError('Invalid disk budget')


def worker_process(job, public, private, attempt):
    job_path = attempt/'job.json'
    save_json(job_path, job)
    with (attempt/'worker.log').open('xb') as log:
        child = subprocess.Popen([sys.executable,'-X','utf8',str(PROJECT/'batch_worker.py'),str(job_path),str(public),str(private),str(attempt.parents[2])],
                                 cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        try:
            code = child.wait()
        except BaseException:
            child.kill(); child.wait()
            raise
    if code:
        raise RuntimeError(f'Worker exited {code}; inspect {attempt / "worker.log"}')


def check_worker(public, private, job):
    proof = read(public/'worker-completed.json')
    if proof.get('state')!='complete' or proof.get('job_id')!=job['job_id'] or proof.get('configurations')!=CONFIGS:
        raise ValueError('Worker incomplete or wrong job/configurations')
    if proof.get('primary_normal_exposure_hours') != 0:
        raise ValueError('Pair must not multiply primary normal exposure')
    actual = inventory_files(public)
    actual.pop('worker-completed.json')
    required = {'predictions.json','public-inputs.json'} if job['kind']=='fixture' else {
        'injected/ledger.jsonl','injected/states.jsonl','injected/summaries.jsonl','injected/public-inputs.json',
        'injected/ledger.csv','injected/settings.json','injected/schedule.json','injected/monitor-completed.json',
        'counterpart/ledger.jsonl','counterpart/states.jsonl','counterpart/summaries.jsonl','counterpart/public-inputs.json',
        'counterpart/ledger.csv','counterpart/settings.json','counterpart/schedule.json','counterpart/monitor-completed.json',
        'case.json','window.json','evaluation.json','point-scores.jsonl','point-scores.csv','raw-episodes.json'}
    required_private = {'truth.json'} if job['kind']=='fixture' else {'truth.json','counterpart-truth.json','construction.json'}
    if not required <= set(actual) or not required_private <= set(inventory_files(private)):
        raise ValueError('Required canonical output absent')
    if actual != proof['public_files'] or inventory_files(private) != proof['private_files']:
        raise ValueError('Worker manifest omits or misidentifies output')


def check_done(record, job, plan_hash, folder):
    if record.get('state')!='complete' or record.get('job_id')!=job['job_id'] or record.get('plan_hash')!=plan_hash:
        raise ValueError('Wrong completion identity')
    # Output archives must belong to this exact controller batch, including its private namespace.
    public = within_work(record['public_archive'])
    private = within_work(record['private_archive'])
    if not public.is_relative_to(folder/'attempts') or not private.is_relative_to(ROOT/'data/synthetic-private'/folder.name):
        raise ValueError('Completion archive outside this batch')
    check_bundle(public, record['public'])
    check_bundle(private, record['private'])
    if record.get('configurations')!=CONFIGS or record.get('primary_normal_exposure_hours')!=0:
        raise ValueError('Completion loses configuration/exposure contract')


def run_plan(folder, retry=False, stop_after=None, worker=worker_process):
    folder = within_work(folder)
    with exclusive_lock(folder):
        plan = read(folder/'plan.json'); validate_plan(plan)
        plan_hash = fingerprint(plan)
        binding = folder/'plan-binding.json'
        if binding.exists() and read(binding)['plan_hash'] != plan_hash:
            raise ValueError('Plan changed after start; create a new batch')
        if not binding.exists():
            save_json(binding, {'plan_hash':plan_hash})
        completed = []
        current = None
        try:
            # Hash every saved success on every resume; checkpoint alone is never proof.
            for job in plan['jobs']:
                marker = folder/'completed'/ (job['job_id']+'.json')
                if marker.exists():
                    check_done(read(marker), job, plan_hash, folder)
                    completed.append(job['job_id'])
            event(folder,'verified', completed=len(completed),total=len(plan['jobs']))
            for job in plan['jobs']:
                if job['job_id'] in completed:
                    continue
                current = job['job_id']
                prior = folder/'attempts'/current
                if prior.exists() and any(prior.iterdir()) and not retry:
                    raise ValueError('Incomplete/failed attempt retained; inspect then use --retry-incomplete')
                remaining = len(plan['jobs'])-len(completed)
                required = plan['reserve_bytes']+plan['scratch_budget_bytes']+remaining*plan['per_job_budget_bytes']
                free = shutil.disk_usage(folder).free
                event(folder,'preflight', job=current,free_bytes=free,required_bytes=required)
                if free < required:
                    raise OSError('Disk preflight failed; no job launched, no scope reduction')
                token = uuid.uuid4().hex
                attempt = prior/token; attempt.mkdir(parents=True)
                private_attempt = ROOT/'data/synthetic-private'/folder.name/current/token
                private_attempt.mkdir(parents=True)
                public, private = attempt/'public-scratch', private_attempt/'private-scratch'
                public.mkdir(); private.mkdir()
                save_json(attempt/'started.json', {'job_id':current,'plan_hash':plan_hash,'utc':utc_now(),'private_attempt':str(private_attempt)})
                event(folder,'running', job=current,completed=len(completed),total=len(plan['jobs']),attempt=str(attempt))
                worker(job,public,private,attempt)
                check_worker(public,private,job)
                pub = pack(public,attempt/'public.zip')
                priv = pack(private,private_attempt/'private.zip')
                done = {'state':'complete','job_id':current,'plan_hash':plan_hash,'configurations':CONFIGS,
                        'primary_normal_exposure_hours':0,'public_archive':str(attempt/'public.zip'),
                        'private_archive':str(private_attempt/'private.zip'),'public':pub,'private':priv,'utc':utc_now()}
                check_done(done,job,plan_hash,folder)
                # Marker is published only after BOTH archives have verified readback.
                marker = folder/'completed'/(current+'.json'); marker.parent.mkdir(exist_ok=True)
                atomic_json(marker,done)
                completed.append(current)
                remove_verified_scratch(public,attempt,pub)
                remove_verified_scratch(private,private_attempt,priv)
                event(folder,'checkpoint',job=current,completed=len(completed),total=len(plan['jobs']))
                if stop_after is not None and len(completed)>=stop_after and len(completed)<len(plan['jobs']):
                    event(folder,'incomplete',reason='intentional bounded stop',completed=len(completed),total=len(plan['jobs']))
                    return False
            event(folder,'complete',completed=len(completed),total=len(plan['jobs']),scientific_batch=False)
            return True
        except BaseException as exc:
            state = 'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed'
            event(folder,state,job=current,completed=len(completed),total=len(plan['jobs']),error=repr(exc),traceback=traceback.format_exc())
            raise


def inspect(folder):
    folder=within_work(folder); plan=read(folder/'plan.json'); validate_plan(plan)
    h=fingerprint(plan); checked=0
    if (folder/'plan-binding.json').exists() and read(folder/'plan-binding.json')['plan_hash']!=h:
        raise ValueError('Plan binding changed')
    for job in plan['jobs']:
        p=folder/'completed'/(job['job_id']+'.json')
        if p.exists():
            check_done(read(p),job,h,folder); checked+=1
    status={'state':'complete' if checked==len(plan['jobs']) else 'incomplete','verified':checked,'planned':len(plan['jobs']),
            'scientific_batch':False,'primary_normal_exposure_hours':0}
    print(json.dumps(status),flush=True)
    return status


def design_roster(path):
    """Planning only: no scientific job is executable before subsequent gates."""
    cases=inventory()
    save_json(within_work(path), {'purpose':'non-executable storage design roster; fabricated IDs identify submitted variants only',
        'research_enabled':False,'cases_per_pass':936,'configurations':CONFIGS,'passes':PASSES,
        'jobs':[{'pass':p['id'],'variant_case':c,'configurations':CONFIGS} for p in PASSES for c in cases],
        'continuous_normal_runs':[{'pass':p['id'],'configurations':CONFIGS,'primary_exposure_copies':1} for p in PASSES],
        'additional_work':'2021 fitting, 2022 calibration/ramp ranking, 2023 diagnostics, pilot, aggregation and reports remain separately budgeted',
        'manual_launch_preference':True})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init-fixture','init-synthetic-pilot','init-synthetic-full','run','status','design-roster'])
    parser.add_argument('folder',type=Path)
    parser.add_argument('--retry-incomplete',action='store_true')
    parser.add_argument('--stop-after',type=int)
    a=parser.parse_args()
    if a.stop_after is not None and a.stop_after<1: parser.error('--stop-after must be positive')
    if a.action.startswith('init-'):
        create_plan(a.folder,'fixture' if a.action=='init-fixture' else 'synthetic',a.action=='init-synthetic-full')
    elif a.action=='design-roster': design_roster(a.folder)
    elif a.action=='status': return 0 if inspect(a.folder)['state']=='complete' else 2
    else: return 0 if run_plan(a.folder,a.retry_incomplete,a.stop_after) else 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
