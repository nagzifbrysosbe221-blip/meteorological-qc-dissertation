"""Bounded fabricated pair through frozen replay, saved readback and evaluator.

This is not the final controller, scientific pilot or performance batch.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import gc
import json
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
from pathlib import Path
import sys
import time
import traceback
import zipfile

from demo import process_peak_bytes
from evaluation_adapter import adapt
from evaluator import excess_response, point_scores, raw_episodes, raw_nodes, root_scores
from lossless_storage import check_bundle, inventory_files, pack, within_work
from measure_storage import resources
from records import ROOT, PROJECT, code_identity, save_json, sha256, utc_now
from scenario_truth import private_truth
from scenarios import fabricated_originals, inventory
from scientific_replay import BINDING, PASSES, build_fixture, identity, load_frozen, replay_fixture

EVIDENCE = ROOT/'evidence/scientific-replay-2026-10-08'


def monitor_save(folder, public, slots, settings, proof, run_id):
    folder.mkdir()
    run = replay_fixture(public,slots,settings,pass_id=proof['pass_id'],run_id=run_id)
    if run != replay_fixture(public,slots,settings,pass_id=proof['pass_id'],run_id=run_id):
        raise ValueError('Repeated fabricated replay differs')
    for name, value in {'run.json':run, 'public-inputs.json':public,
                        'schedule.json':slots, 'provenance.json':proof}.items():
        save_json(folder/name,value)
    save_json(folder/'completed.json',{'state':'fixture_complete','files':inventory_files(folder),
                                      'scientific_freeze':False,'final_performance':False})


def read_completed(folder):
    done=json.loads((folder/'completed.json').read_text())
    if done.get('state')!='fixture_complete' or done.get('final_performance') is not False:
        raise ValueError('Not a completed scientific integration fixture')
    if set(done['files'])!={'run.json','public-inputs.json','schedule.json','provenance.json'}:
        raise ValueError('Incomplete scientific fixture files')
    for name, item in done['files'].items():
        if sha256(folder/name)!=item['sha256'] or (folder/name).stat().st_size!=item['bytes']:
            raise ValueError('Changed scientific fixture output: '+name)
    run,public,slots,proof=[json.loads((folder/name).read_text()) for name in
                          ('run.json','public-inputs.json','schedule.json','provenance.json')]
    ids={v:'scientific-settings:'+identity(p) for v,p in run['settings'].items()}
    if ids!=proof['settings_ids'] or ids!=run['scientific_context']['settings_ids']:
        raise ValueError('Lost scientific settings provenance')
    if (run['scientific_context']['original_A2_eligibility']!={'T':True,'U':False} or
            run['scientific_context']['exploratory']!={'T':False,'U':True}):
        raise ValueError('Lost eligibility/R1 labels')
    adapt(run,public,slots,case_id='readback') # Completeness before any private labels.
    return run,public,slots


def inspect(folder):
    folder=within_work(folder)
    if (folder/'failed.json').exists():
        raise ValueError('Failed attempt cannot be complete')
    done=json.loads((folder/'completed.json').read_text())
    if done['state']!='fixture_complete' or done['passes']!=list(PASSES) or done['final_performance'] is not False:
        raise ValueError('Wrong scope or incomplete prescribed passes')
    for name,item in done['files'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder) or sha256(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:
            raise ValueError('Evidence changed: '+name)
    for pass_id in PASSES:
        for side in ('injected','counterpart'):
            read_completed(folder/'public'/pass_id/side)
    for side in ('public','private'):
        check_bundle(folder/(side+'.zip'),done['archives'][side])
    print(json.dumps({'verified':True,'state':done['state'],'passes':4,'paired_fixtures':4,
                      'final_performance':False,'scientific_freeze':False}),flush=True)
    return done


def run(folder):
    folder=within_work(folder);folder.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter()
    save_json(folder/'started.json',{'utc':utc_now(),'code':code_identity(),'resources':resources(),
              'command':sys.argv,'protocol_sha256':sha256(BINDING),
              'scope':'manufactured 276-hour integration window; not a full source month or real scientific pilot'})
    try:
        allowance=resources();save_json(folder/'resource-preflight.json',allowance)
        if (allowance['memory_available_bytes'] is None or allowance['memory_available_bytes']<256*1024**2 or
                allowance['disk_free_bytes']<1024**3):
            raise ValueError('Bounded fixture needs 256 MiB available RAM and 1 GiB free disk')
        with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(PROJECT.glob('*.py')):archive.write(p,p.name)
            archive.write(BINDING,BINDING.name)
            archive.write(PROJECT/'requirements.txt','requirements.txt')
        case=next(c for c in inventory() if c['source_month']=='2000-03' and c['family']=='gradual_bias'
                  and c['variable']=='T' and c['sign']==1 and c['scale_multiple']==2 and c['ramp_hours']==24)
        onset=datetime.fromisoformat(case['planned_onset'])
        slots=[(onset+timedelta(hours=i)).isoformat() for i in range(-228,48)]
        window={'slots':slots,'first_scored':case['planned_onset'],'last_scored':slots[-1],
                'scope':'48 scored fabricated hours, 228 context hours; NOT a complete scientific source month'}
        originals=fabricated_originals(slots)
        (folder/'public').mkdir();(folder/'private').mkdir()
        primary,_,_=load_frozen()
        generated=build_fixture(case,originals,primary)
        if generated['root']['construction']!='effective' or generated['root']['C']!=48:
            raise ValueError('Fabricated construction did not realise its 48 supported changes')
        save_json(folder/'private/construction.json',generated)
        save_json(folder/'public/window.json',window)
        input_ids={s:identity(generated[s]) for s in ('public','counterpart')}
        for pass_id in PASSES:
            print('REPLAY '+pass_id+': 276 fabricated hours, B0/R/H and unchanged counterpart',flush=True)
            settings,_,proof=load_frozen(pass_id)
            if any(settings[v].scale!=primary[v].scale for v in ('T','U')):
                raise ValueError('Sensitivity changed injection scale')
            part=folder/'public'/pass_id;part.mkdir()
            for side,key in (('injected','public'),('counterpart','counterpart')):
                monitor_save(part/side,generated[key],slots,settings,proof,pass_id+'-'+side)
            injected,receipts,_=read_completed(part/'injected')
            control,controls_public,_=read_completed(part/'counterpart')
            units=adapt(injected,receipts,slots,case_id=case['case_id'])
            controls=adapt(control,controls_public,slots,case_id=case['case_id']+'-control')
            episodes=raw_episodes(raw_nodes(units)) # Built before loading private truth.
            before={s:sha256(part/s/'run.json') for s in ('injected','counterpart')}
            private=json.loads((folder/'private/construction.json').read_text())
            truth=private_truth(private,window)
            health={(case['case_id'],cfg):{'valid':True,'reason':None} for cfg in ('B0','R','H')}
            # Validity here refers only to the declared tiny fixture, never a full-month pair.
            diagnostics={}
            for cfg in ('B0','R','H'):
                pick=lambda rows:[u for u in rows if u['task']=='value' and u['configuration']==cfg]
                diagnostics[cfg]=excess_response(pick(units),pick(controls),
                    {(s,'T') for s in truth['roots'][0]['actual_change_mask']},
                    identity(injected['settings']),identity(control['settings']))
            save_json(part/'evaluation.json',{'scope':window['scope'],'point_scores':point_scores(units,truth['units']),
                'roots':root_scores(truth['roots'],units,health),'excess_response':diagnostics,
                'raw_episode_count':len(episodes),'primary_normal_exposure_hours':0,
                'input_identities':input_ids,'R1_U_exploratory':True,'final_performance':False})
            if before!={s:sha256(part/s/'run.json') for s in before}:
                raise ValueError('Evaluation changed saved predictions')
            print('VERIFIED '+pass_id+': readback, evaluator, unchanged inputs/settings',flush=True)
            del injected,control,units,controls,episodes,private,truth
            gc.collect()
        save_json(folder/'private/truth.json',private_truth(generated,window))
        save_json(folder/'private/counterpart-truth.json',private_truth(generated,window,counterpart=True))
        archives={side:pack(folder/side,folder/(side+'.zip')) for side in ('public','private')}
        old=json.loads((folder/'started.json').read_text())
        if old['code']!=code_identity() or old['protocol_sha256']!=sha256(BINDING):
            raise ValueError('Code/protocol changed during run')
        load_frozen()
        save_json(folder/'completed.json',{'state':'fixture_complete','passes':list(PASSES),
            'files':inventory_files(folder),'archives':archives,'elapsed_seconds':time.perf_counter()-start,
            'peak_process_bytes':process_peak_bytes(),'resources_after':resources(),
            'scientific_freeze':False,'final_performance':False,'primary_normal_exposure_hours':0})
        inspect(folder)
        print('COMPLETE: '+str(folder),flush=True)
        return 0
    except BaseException as exc:
        save_json(folder/'failed.json',{'state':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
            'error':repr(exc),'traceback':traceback.format_exc(),'final_performance':False})
        print('FAILED: '+str(folder)+': '+repr(exc),flush=True)
        return 2 if isinstance(exc,KeyboardInterrupt) else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('demo','status'));p.add_argument('--out',type=Path)
    args=p.parse_args()
    if args.action=='status':
        if args.out is None:p.error('status requires --out')
        inspect(args.out);return 0
    folder=args.out or EVIDENCE/('fixture-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    print('OUTPUT: '+str(folder),flush=True)
    return run(folder)


if __name__=='__main__':raise SystemExit(main())
