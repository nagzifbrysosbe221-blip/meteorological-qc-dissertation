"""Manual R1 A3 stage; stops before exposed-2023 validation and final research replay."""
import argparse
from datetime import datetime, timezone
import gzip
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
from records import ROOT,PROJECT,save_json,sha256,code_identity,utc_now
from early_scientific import load_series,verified_manifest
from r1_calibration import (approved_models,prepare,calibrate,cases,development_pair,rank_candidates,
                            WEIGHTS,APPROVAL,APPROVAL_SHA)
from measure_storage import resources
from demo import process_peak_bytes
from lossless_storage import within_work

EVIDENCE=ROOT/'evidence/r1-calibration-2026-10-08'
PROTOCOL=PROJECT/'r1-a3-protocol.json'


def writer(stream):
    def emit(row): stream.write(json.dumps(row,allow_nan=False,separators=(',',':'))+'\n')
    return emit


def run(folder):
    folder=within_work(folder); folder.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter()
    save_json(folder/'started.json',{'utc':utc_now(),'code':code_identity(),'protocol_sha256':sha256(PROTOCOL),
      'approval_sha256':APPROVAL_SHA,'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),
      'command':sys.argv,'resources':resources(),'seed':None,'seed_reason':'deterministic',
      'scope':'R1 A3 only; no refit, exposed-2023 validation or final replay'})
    try:
        r=resources()
        if r['memory_available_bytes'] is None or r['memory_available_bytes']<256*1024**2:
            raise ValueError('Below 256 MiB available RAM allowance')
        if r['disk_free_bytes']<1024**3: raise ValueError('Below 1 GiB early-stage disk allowance')
        if np.__version__!='2.3.5': raise ValueError('Pinned NumPy required')
        models,permission=approved_models()
        save_json(folder/'permission.json',permission)
        with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(PROJECT.glob('*.py')): z.write(p,p.name)
            for p in (PROTOCOL,PROJECT/'requirements.txt',APPROVAL): z.write(p,p.name)
        decisions={}
        for variable in ('T','U'):
            print(f'LOADING {variable}; frozen 2021 model; 2021 context and 2022 development',flush=True)
            context=load_series(2021,'fit',variable); development=load_series(2022,'development',variable)
            inputs=prepare(context,development,models[variable]); del context,development
            save_json(folder/f'{variable}-roster.json',{'planned':cases(variable),'count':216})
            candidates=[]
            for weight in WEIGHTS:
                prefix=f'{variable}-{weight:.2f}'
                print(f'CALIBRATING {prefix}',flush=True)
                with gzip.open(folder/f'{prefix}-calibration.jsonl.gz','xt',encoding='utf-8') as f:
                    calibration=calibrate(inputs,weight,writer(f))
                records=[]
                if calibration['eligible']:
                    with gzip.open(folder/f'{prefix}-paired-traces.jsonl.gz','xt',encoding='utf-8') as f:
                        emit=writer(f)
                        for n,case in enumerate(cases(variable),1):
                            def trace(row): emit({'case_id':case['case_id'],**row})
                            records.append(development_pair(inputs,models[variable],weight,
                                                            calibration['cutoff'],case,trace))
                            if n%18==0: print(f'DEVELOPING {prefix}: {n}/216',flush=True)
                else: print(f'INELIGIBLE {prefix}: {calibration["failures"]}',flush=True)
                candidate={'lambda':weight,'calibration':calibration,'cases':records,
                  'planned_count':216,'executed_count':len(records),'blocked_count':216-len(records),
                  'variable':variable,'exploratory':variable=='U','revision':'R1-approved-v1'}
                save_json(folder/f'{prefix}-candidate.json',candidate); candidates.append(candidate)
            decision=rank_candidates(candidates)
            selected=next((c for c in candidates if c['lambda']==decision['selected_lambda']),None)
            decision.update({'variable':variable,'original_A2_eligible':variable=='T','exploratory':variable=='U',
              'revision':'R1-approved-v1','model':'P','cutoff':selected['calibration']['cutoff'] if selected else None,
              'alpha_sensitivity_cutoffs':selected['calibration']['ready_state_alphas'] if selected else None})
            save_json(folder/f'{variable}-decision.json',decision); decisions[variable]=decision
            print(f'SELECTION {variable}: {decision}',flush=True)
            del inputs
        state='awaiting_validation' if all(d['selected_lambda'] is not None for d in decisions.values()) else 'revision_required'
        save_json(folder/'decisions.json',{'state':state,'variables':decisions,'validation_performed':False,
          'scientific_freeze':False,'full_batch_run':False,'U_original_A2_eligible':False})
        before=json.loads((folder/'started.json').read_text())
        if before['code']!=code_identity() or before['protocol_sha256']!=sha256(PROTOCOL):
            raise ValueError('Code/protocol changed during stage')
        approved_models(); verified_manifest()
        files={p.name:{'sha256':sha256(p),'bytes':p.stat().st_size} for p in folder.iterdir() if p.is_file()}
        save_json(folder/'completed.json',{'state':state,'files':files,'elapsed_seconds':time.perf_counter()-start,
          'peak_process_bytes':process_peak_bytes(),'resources_after':resources(),'scientific_freeze':False,
          'scope':'R1 A3 statistical development, not final B0/R/H research batch'})
        print(f'{state.upper()}: {folder}',flush=True)
        return 0 if state=='awaiting_validation' else 3
    except BaseException as exc:
        save_json(folder/'failed.json',{'utc':utc_now(),'error':repr(exc),'traceback':traceback.format_exc(),
          'interrupted':isinstance(exc,KeyboardInterrupt),'recovery':'Preserve folder; correct cause and rerun to a fresh folder; no partial resume'})
        print(f'FAILED: {folder}: {exc}',flush=True)
        return 2 if isinstance(exc,KeyboardInterrupt) else 1


def inspect(folder):
    folder=within_work(folder); done=json.loads((folder/'completed.json').read_text())
    required={'started.json','permission.json','source.zip','decisions.json'}
    for v in ('T','U'):
        required.update((f'{v}-roster.json',f'{v}-decision.json'))
        roster=json.loads((folder/f'{v}-roster.json').read_text())
        if roster!={'planned':list(cases(v)),'count':216}: raise ValueError('Roster changed')
        candidates=[]
        for w in WEIGHTS:
            prefix=f'{v}-{w:.2f}'
            required.update((f'{prefix}-calibration.jsonl.gz',f'{prefix}-candidate.json'))
            c=json.loads((folder/f'{prefix}-candidate.json').read_text()); candidates.append(c)
            if c['calibration']['eligible']:
                required.add(f'{prefix}-paired-traces.jsonl.gz')
                if {row['case_id'] for row in c['cases']}!={row['case_id'] for row in cases(v)}:
                    raise ValueError('Missing development cases')
            elif c['cases']: raise ValueError('Ineligible candidate ran cases')
        ranked=rank_candidates(candidates); decision=json.loads((folder/f'{v}-decision.json').read_text())
        if any(decision[k]!=value for k,value in ranked.items()): raise ValueError('Ranking readback mismatch')
    if set(done['files'])!=required or (folder/'failed.json').exists(): raise ValueError('Incomplete/conflicting outputs')
    for name,identity in done['files'].items():
        p=folder/name
        if sha256(p)!=identity['sha256'] or p.stat().st_size!=identity['bytes']: raise ValueError('Corrupt output '+name)
        if name.endswith('.gz'):
            with gzip.open(p,'rt',encoding='utf-8') as f:
                for line in f: json.loads(line)  # Full compressed readback/CRC, not just archive hash.
    print(json.dumps({'verified':True,'state':done['state'],'files':len(required),'scientific_freeze':False}))
    return done


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('run','status'));p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.action=='status':
        if a.out is None:p.error('status requires --out')
        inspect(a.out);return 0
    out=a.out or EVIDENCE/('manual-A3-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    print('OUTPUT: '+str(out),flush=True);return run(out)


if __name__=='__main__': raise SystemExit(main())
