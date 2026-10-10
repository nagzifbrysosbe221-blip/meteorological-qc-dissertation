"""Short manual early stage. No A3 tuning, final intake or research batch launch."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
from pathlib import Path
import platform
import sys
import time
import traceback
import zipfile
import numpy as np

from early_scientific import load_series, verified_manifest, ACCEPTANCE, ACCEPTANCE_SHA256
from scientific_models import fit, assess_gates, diagnostics
from records import ROOT, PROJECT, code_identity, save_json, sha256, utc_now
from lossless_storage import within_work
from measure_storage import resources
from demo import process_peak_bytes

EVIDENCE=ROOT/'evidence/scientific-models-2026-10-08'
PROTOCOL=PROJECT/'early-stage-protocol.json'


def execute(action, folder, *, loader=load_series):
    if action not in ('verify-inputs','fit-gates'):
        raise ValueError('Unsupported stage')
    folder=within_work(folder)
    folder.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter()
    protocol=json.loads(PROTOCOL.read_text(encoding='utf-8'))
    before=resources()
    save_json(folder/'started.json',{'action':action,'started_utc':utc_now(),'code':code_identity(),
      'acceptance_sha256':ACCEPTANCE_SHA256,'protocol':protocol,'protocol_sha256':sha256(PROTOCOL),
      'numpy':np.__version__,'python':sys.version,'platform':platform.platform(),
      'resources':before,'seed':None,'seed_reason':'deterministic; not applicable',
      'command':sys.argv,'scope':'early inputs and A2 only; no calibration or scientific freeze'})
    with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PROJECT.glob('*.py')): z.write(p,p.name)
        for name in ('requirements.txt','early-stage-protocol.json'): z.write(PROJECT/name,name)
    try:
        if np.__version__ != '2.3.5': raise ValueError('Pinned NumPy 2.3.5 required')
        if before['disk_free_bytes'] < 1024**3:
            raise ValueError('Need 1 GiB free allowance for this early stage; not research batch capacity')
        if before['memory_available_bytes'] is None or before['memory_available_bytes'] < 256*1024**2:
            raise ValueError('Need at least 256 MiB currently available RAM for this bounded stage')
        print('VERIFYING acceptance and exact raw/view hashes',flush=True)
        if action == 'verify-inputs':
            counts=[]
            for year,role in ((2021,'fit'),(2022,'development'),(2023,'validation')):
                for variable in ('T','U'):
                    s=loader(year,role,variable)
                    counts.append({'year':year,'role':role,'variable':variable,'hours':len(s.hours),
                      'finite_target':sum(h.target.reason=='finite' for h in s.hours),
                      'finite_reference':sum(h.reference.reason=='finite' for h in s.hours),
                      'boundary_rows_excluded_per_snapshot':s.boundary_rows_excluded,
                      'last_source_date':s.hours[-1].source_day.isoformat(),
                      'last_utc':s.hours[-1].utc.isoformat()})
                    del s
                    print(f'VERIFIED {year} {variable}; no fitting',flush=True)
            save_json(folder/'inputs.json',{'counts':counts,'real_fitting':False,
              'note':'Boundary count is repeated per variable; do not sum to 288.'})
            state='inputs_verified'
        else:
            selections={}
            for variable in ('T','U'):
                training=loader(2021,'fit',variable)
                models={}
                for kind in ('S','P'):
                    print(f'FITTING {variable} {kind} on source year 2021',flush=True)
                    model,record=fit(training,kind); models[kind]=model
                    save_json(folder/f'{variable}-{kind}-fit.json',record|{'model':asdict(model)})
                    save_json(folder/f'{variable}-{kind}-2021-diagnostics.json',diagnostics(training,model))
                del training
                development=loader(2022,'development',variable)
                result=assess_gates(development,models['S'],models['P'])
                save_json(folder/f'{variable}-2022-gates.json',result)
                for kind,model in models.items():
                    save_json(folder/f'{variable}-{kind}-2022-diagnostics.json',diagnostics(development,model))
                selections[variable]=result['selected']
                print(f'GATES {variable}: {result["selected"] or "REVISION_REQUIRED"}; {result["reason"]}',flush=True)
                del development
            save_json(folder/'selection.json',{'models':selections,'calibration_complete':False,
              'lambda':None,'cutoffs':None,'development_ranking':None,'validation':None,
              'next':'Implement and verify A3 continuous calibration/context and 216-case paired ranking per variable; no defaults'})
            state='awaiting_calibration' if all(selections.values()) else 'revision_required'
        # Detect code/input/protocol changes during execution before publishing completion.
        started=json.loads((folder/'started.json').read_text())
        if started['code'] != code_identity() or started['protocol_sha256'] != sha256(PROTOCOL):
            raise ValueError('Code/protocol changed during stage')
        if loader is load_series and sha256(ACCEPTANCE) != ACCEPTANCE_SHA256:
            raise ValueError('Acceptance changed during stage')
        if loader is load_series: verified_manifest()
        files={p.name:{'sha256':sha256(p),'bytes':p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()}
        save_json(folder/'completed.json',{'state':state,'action':action,'files':files,
          'elapsed_seconds':time.perf_counter()-start,'process_peak_bytes':process_peak_bytes(),
          'resources_after':resources(),'calibration_complete':False,'scientific_freeze':False,
          'real_fitting':action=='fit-gates' and loader is load_series,'full_batch_run':False})
        print(f'{state.upper()}: {folder}',flush=True)
        return 3 if state=='revision_required' else 0
    except BaseException as exc:
        save_json(folder/'failed.json',{'error':repr(exc),'traceback':traceback.format_exc(),
          'state':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
          'elapsed_seconds':time.perf_counter()-start,'code':code_identity(),
          'scientific_freeze':False,'resume':'Keep this folder; correct the cause, then rerun to a fresh folder'})
        print(f'FAILED: {folder}: {exc}',flush=True)
        return 2 if isinstance(exc,KeyboardInterrupt) else 1


def inspect(folder):
    folder=within_work(folder)
    done=json.loads((folder/'completed.json').read_text(encoding='utf-8'))
    required={'started.json','source.zip'}
    if done['action']=='verify-inputs': required.add('inputs.json')
    elif done['action']=='fit-gates':
        required.add('selection.json')
        for v in ('T','U'):
            required.add(f'{v}-2022-gates.json')
            for k in ('S','P'):
                required.update((f'{v}-{k}-fit.json',f'{v}-{k}-2021-diagnostics.json',f'{v}-{k}-2022-diagnostics.json'))
    else: raise ValueError('Unknown completed action')
    if set(done['files']) != required:
        raise ValueError('Completed stage has missing/extra canonical outputs')
    if (folder/'failed.json').exists(): raise ValueError('Conflicting failure record')
    for name,identity in done['files'].items():
        p=folder/name
        if sha256(p)!=identity['sha256'] or p.stat().st_size!=identity['bytes']:
            raise ValueError(f'Completed output changed: {name}')
    print(json.dumps({'verified':True,'state':done['state'],'files':len(required),
                      'calibration_complete':False,'scientific_freeze':False}),flush=True)
    return done


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('verify-inputs','fit-gates','status'))
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.action=='status':
        if args.out is None: parser.error('status needs --out with the saved folder')
        inspect(args.out); return 0
    folder=args.out or EVIDENCE/(args.action+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    print(f'OUTPUT: {folder}',flush=True)
    return execute(args.action,folder)


if __name__=='__main__': raise SystemExit(main())
