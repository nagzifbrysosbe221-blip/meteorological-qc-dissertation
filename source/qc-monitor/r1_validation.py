"""Frozen transfer to previously exposed 2023; diagnosis only, never retuning."""
import argparse
from collections import Counter
from datetime import date,datetime,timedelta,timezone
import gzip
import json
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
from pathlib import Path
import sys
import time
import traceback
import zipfile
from early_scientific import load_series,require_role,verified_manifest
from scientific_models import standardised_residual
from r1_calibration import approved_models,State
from records import ROOT,PROJECT,sha256,save_json,code_identity,utc_now
from lossless_storage import within_work
from measure_storage import resources
from demo import process_peak_bytes

A3=ROOT/'evidence/r1-calibration-2026-10-08/manual-A3-20261007T210253930632Z'
BINDING=PROJECT/'r1-validation-protocol.json'
EVIDENCE=ROOT/'evidence/r1-validation-2026-10-08'


def frozen_decisions():
    protocol=json.loads(BINDING.read_text())
    if sha256(A3/'completed.json')!=protocol['A3_completed_sha256']:raise ValueError('A3 completion changed')
    completed=json.loads((A3/'completed.json').read_text())
    if completed['state']!='awaiting_validation':raise ValueError('A3 not eligible for validation')
    for name,identity in completed['files'].items():
        if sha256(A3/name)!=identity['sha256']:raise ValueError('A3 output changed: '+name)
    decisions={v:json.loads((A3/f'{v}-decision.json').read_text()) for v in ('T','U')}
    for v,d in decisions.items():
        if d['selected_lambda'] is None or d['cutoff'] is None or d['original_A2_eligible']!=(v=='T') or d['exploratory']!=(v=='U'):
            raise ValueError('Invalid frozen decision or lost exploratory label')
    return decisions


def rate(flags,ready,expected):
    return {'flags':flags,'eligible_assumed_normal_ready_hours':ready,'expected_hours':expected,
            'ready_coverage':ready/expected if expected else None,
            'statistical_stream_FPR':flags/ready if ready else None,
            'reason':None if ready else 'no_ready_assumed_normal_exposure'}


def diagnosis(annual,monthly):
    reasons=[]
    if annual['statistical_stream_FPR'] is None:reasons.append('annual_FPR_unavailable')
    elif annual['statistical_stream_FPR']>.01:reasons.append('annual_FPR_above_.01')
    for month,row in monthly.items():
        if row['statistical_stream_FPR'] is None:reasons.append(f'month_{month}_FPR_unavailable')
        elif row['statistical_stream_FPR']>.03:reasons.append(f'month_{month}_FPR_above_.03')
    return reasons


def validate(context,series,model,decision,emit=lambda row:None):
    require_role(context,'development');require_role(series,'validation')
    if context.variable!=series.variable or model.variable!=series.variable or decision['variable']!=series.variable:
        raise ValueError('Variable mismatch')
    if decision['model']!=model.kind:raise ValueError('Model mismatch')
    hours=[h for h in context.hours if h.source_day>=date(2022,12,18)]+list(series.hours)
    if len(hours)!=336+8760:raise ValueError('Need 14 earlier days and complete 2023 schedule')
    if hours[0].source_day!=date(2022,12,18) or hours[-1].source_day!=date(2023,12,31):
        raise ValueError('Wrong validation endpoints')
    state=State(decision['selected_lambda']);flags=Counter();ready=Counter();expected=Counter();unavailable=Counter()
    for i,h in enumerate(hours):
        if i and h.utc-hours[i-1].utc!=timedelta(hours=1):raise ValueError('Noncontiguous validation schedule')
        u,reason=standardised_residual(model,h);s=state.step(u,decision['cutoff']);iscontext=h.source_day.year==2022
        if not iscontext:
            m=h.source_day.month;expected[m]+=1
            ready[m]+=int(s['ready']);flags[m]+=int(s['flag'] is True)
            if not s['ready']:unavailable[reason or s['reason']]+=1
        emit({'utc':h.utc.isoformat(),'source_date':h.source_day.isoformat(),'source_hour':h.source_hour,
              'context':iscontext,'u':u,'input_reason':reason,**s})
    monthly={str(m):rate(flags[m],ready[m],expected[m]) for m in range(1,13)}
    annual=rate(sum(flags.values()),sum(ready.values()),sum(expected.values()))
    reasons=diagnosis(annual,monthly)
    return {'variable':series.variable,'source_year':2023,'previously_exposed':True,
      'annual':annual,'monthly':monthly,'unavailable_reasons':dict(unavailable),'diagnosis_reasons':reasons,
      'state':'diagnosis_required' if reasons else 'validation_checks_within_thresholds',
      'settings_transferred_unchanged':decision,'automatic_retuning':False,'scientific_freeze':False,
      'truth':'assumed normal, not certified','scope':'S01 statistical stream only; not combined-H FPR or final performance'}


def run(folder):
    folder=within_work(folder);folder.mkdir(parents=True,exist_ok=False);start=time.perf_counter()
    save_json(folder/'started.json',{'utc':utc_now(),'code':code_identity(),'protocol_sha256':sha256(BINDING),
      'resources':resources(),'command':sys.argv,'scope':'previously exposed 2023, frozen transfer, no retuning'})
    try:
        r=resources()
        if r['memory_available_bytes'] is None or r['memory_available_bytes']<256*1024**2 or r['disk_free_bytes']<1024**3:
            raise ValueError('Insufficient early-stage resource allowance (256 MiB RAM / 1 GiB disk)')
        decisions=frozen_decisions();models,permission=approved_models()
        save_json(folder/'settings.json',{'decisions':decisions,'permission':permission})
        with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(PROJECT.glob('*.py')):z.write(p,p.name)
            z.write(BINDING,BINDING.name)
        results={}
        for v in ('T','U'):
            print(f'VALIDATING {v}: exposed 2023; lambda/cutoff unchanged',flush=True)
            context=load_series(2022,'development',v);series=load_series(2023,'validation',v)
            with gzip.open(folder/f'{v}-states.jsonl.gz','xt',encoding='utf-8') as stream:
                def emit(row):stream.write(json.dumps(row,allow_nan=False)+'\n')
                result=validate(context,series,models[v],decisions[v],emit)
            save_json(folder/f'{v}-validation.json',result);results[v]=result
            print(f'RESULT {v}: {result["state"]}; annual FPR={result["annual"]["statistical_stream_FPR"]}; {result["diagnosis_reasons"]}',flush=True)
            del context,series
        state='diagnosis_required' if any(r['diagnosis_reasons'] for r in results.values()) else 'validation_checks_within_thresholds'
        save_json(folder/'summary.json',{'state':state,'automatic_retuning':False,'scientific_freeze':False,
           'U_original_A2_eligible':False,'U_exploratory':True,'next':'Review diagnostics before final protocol/intake; high rates may be retained as disclosed negative findings, never automatic retuning'})
        before=json.loads((folder/'started.json').read_text())
        if before['code']!=code_identity() or before['protocol_sha256']!=sha256(BINDING):raise ValueError('Code/protocol changed')
        frozen_decisions();approved_models();verified_manifest()
        files={p.name:{'sha256':sha256(p),'bytes':p.stat().st_size} for p in folder.iterdir() if p.is_file()}
        save_json(folder/'completed.json',{'state':state,'files':files,'elapsed_seconds':time.perf_counter()-start,
          'peak_process_bytes':process_peak_bytes(),'resources_after':resources(),'scientific_freeze':False})
        print(f'{state.upper()}: {folder}',flush=True);return 3 if state=='diagnosis_required' else 0
    except BaseException as exc:
        save_json(folder/'failed.json',{'error':repr(exc),'traceback':traceback.format_exc(),'interrupted':isinstance(exc,KeyboardInterrupt)})
        print(f'FAILED: {folder}: {exc}',flush=True);return 2 if isinstance(exc,KeyboardInterrupt) else 1


def inspect(folder):
    folder=within_work(folder);done=json.loads((folder/'completed.json').read_text())
    required={'started.json','source.zip','settings.json','summary.json','T-states.jsonl.gz','U-states.jsonl.gz','T-validation.json','U-validation.json'}
    if set(done['files'])!=required or (folder/'failed.json').exists():raise ValueError('Incomplete/conflicting output')
    for name,identity in done['files'].items():
        if sha256(folder/name)!=identity['sha256'] or (folder/name).stat().st_size!=identity['bytes']:raise ValueError('Output changed: '+name)
    for v in ('T','U'):
        counts=Counter();expected=Counter();flags=Counter();context=0
        with gzip.open(folder/f'{v}-states.jsonl.gz','rt') as stream:
            for line in stream:
                row=json.loads(line)
                if row['context']:context+=1;continue
                m=int(row['source_date'][5:7]);expected[m]+=1;counts[m]+=int(row['ready']);flags[m]+=int(row['flag'] is True)
        r=json.loads((folder/f'{v}-validation.json').read_text())
        if context!=336 or sum(expected.values())!=8760:raise ValueError('Incomplete validation ledger')
        if r['annual']!=rate(sum(flags.values()),sum(counts.values()),8760):raise ValueError('Annual readback mismatch')
        if r['monthly']!={str(m):rate(flags[m],counts[m],expected[m]) for m in range(1,13)}:raise ValueError('Monthly readback mismatch')
        if r['diagnosis_reasons']!=diagnosis(r['annual'],r['monthly']):raise ValueError('Diagnosis changed')
    print(json.dumps({'verified':True,'state':done['state'],'files':8,'scientific_freeze':False}));return done


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('run','status'));p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.action=='status':
        if a.out is None:p.error('status requires --out')
        inspect(a.out);return 0
    folder=a.out or EVIDENCE/('manual-2023-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    print('OUTPUT: '+str(folder),flush=True);return run(folder)


if __name__=='__main__':raise SystemExit(main())
