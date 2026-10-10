"""Manual saved-output analysis. Contains no experiment-launch path."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
import traceback
from evidence import (ROOT,HERE,STUDY,FREEZE,read,save,sha,token,now,require,metadata_audit,
                      resources,code_identity,identity)
from extract import extract_job,export_timeline
from metrics import select_timelines


def fingerprint(record):
    return {s:dict(path=record[s+'_archive'],bytes=Path(record[s+'_archive']).stat().st_size,
                   mtime_ns=Path(record[s+'_archive']).stat().st_mtime_ns)
            for s in ('public','private')}


def run(out,audit_path,limit=None):
    require(out.resolve().is_relative_to((ROOT/'evidence/result-analysis-2026-10-09').resolve()),'Use dedicated analysis evidence folder')
    require(limit is None or 1<=limit<=3,'Bounded demonstration limit is 1..3')
    audit=read(audit_path)
    require(audit['state']=='metadata_verified' and audit['plan_sha256']==sha(STUDY/'plan.json') and
            audit['freeze_sha256']==sha(FREEZE/'freeze.json'),'Audit binding differs')
    freeze=read(FREEZE/'freeze.json');project=ROOT/'working/qc-monitor'
    actual={p.name:sha(p) for p in project.iterdir() if p.suffix in ('.py','.json') or p.name=='requirements.txt'}
    require(actual==freeze['code'],'Frozen scientific code changed')
    out.mkdir(parents=True,exist_ok=True)
    binding=dict(version='saved-result-analysis-v1',code=code_identity(),audit_path=str(audit_path.resolve()),
        audit_sha256=sha(audit_path),plan_sha256=audit['plan_sha256'],freeze_sha256=audit['freeze_sha256'],
        scope='bounded_saved_readback' if limit else 'full',limit=limit)
    if (out/'binding.json').exists():require(read(out/'binding.json')==binding,'Analysis version changed; create a new output directory')
    else: save(out/'binding.json',binding)
    if (out/'completed.json').exists():
        print('ALREADY_COMPLETE '+str(out/'completed.json'),flush=True);return
    lock=(out/'analysis.lock').open('a+b')
    import msvcrt
    lock.seek(0);lock.write(b'1');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    session=out/'sessions'/token();session.mkdir(parents=True)
    save(session/'started.json',dict(utc=now(),command=sys.argv,resources=resources(),binding_sha256=sha(out/'binding.json')))
    started=time.monotonic()
    try:
        entries=audit['jobs'][:limit] if limit else audit['jobs']
        checkpoints=out/'checkpoints'; checkpoints.mkdir(exist_ok=True)
        for i,entry in enumerate(entries,1):
            job_id=entry['job_id'];marker=STUDY/'completed'/(job_id+'.json');record=read(marker)
            require(sha(marker)==entry['marker_sha256'],'Completion marker changed '+job_id)
            cached=checkpoints/(job_id+'.json')
            if cached.exists():
                c=read(cached);p=out/c['path']
                require(c['marker_sha256']==entry['marker_sha256'] and c['archives']==fingerprint(record) and
                        c['sha256']==sha(p),'Checkpoint/source changed; preserve evidence and diagnose')
                print(f'REUSE {i}/{len(entries)} {job_id}',flush=True);continue
            r=resources(); threshold=768*1024**2
            if limit is None:
                require(r['memory_available_bytes'] is not None and r['memory_available_bytes']>=threshold,
                        'Analysis RAM below 768 MiB. Close other applications, then resume same command. No detector is run.')
                require(r['disk_free_bytes']>=10*1024**3,'Analysis disk below 10 GiB reserve')
            print(f'READ {i}/{len(entries)} {job_id}; elapsed {time.monotonic()-started:.1f}s',flush=True)
            attempt=out/'attempts'/job_id/token();attempt.mkdir(parents=True)
            save(attempt/'started.json',dict(utc=now(),resources=r,marker_sha256=entry['marker_sha256']))
            value=extract_job(record);value['marker_sha256']=entry['marker_sha256']
            save(attempt/'contribution.json',value)
            save(cached,dict(path=(attempt/'contribution.json').relative_to(out).as_posix(),
                 sha256=sha(attempt/'contribution.json'),marker_sha256=entry['marker_sha256'],archives=fingerprint(record)))
            print(f'CHECKPOINT {i}/{len(entries)} {job_id}',flush=True)
        if limit:
            save(session/'bounded-completed.json',dict(utc=now(),jobs=len(entries),full_study_analysis=False,
                seconds=time.monotonic()-started,resources=resources()))
            print('BOUNDED_READBACK_COMPLETE; no full-study report claimed',flush=True);return
        roots=[]
        for entry in entries:
            c=read(checkpoints/(entry['job_id']+'.json')); v=read(out/c['path'])
            if v['root']:roots.append(v['root'])
        require(len(roots)==3744,'Incomplete root roster')
        selection=select_timelines(roots)
        # Fresh publication attempt; interruption never presents a partial report as complete.
        publication=out/'publications'/token();publication.mkdir(parents=True)
        save(publication/'timeline-selection.json',selection)
        for i,s in enumerate(selection,1):
            if s['status']!='selected':continue
            print(f'TIMELINE {i}/{len(selection)} {s["job_id"]}',flush=True)
            export_timeline(read(STUDY/'completed'/(s['job_id']+'.json')),publication/'timelines'/s['job_id'])
        from report import publish
        publish(out,publication,audit,selection)
        from check_report import check
        save(publication/'export-readback.json',check(publication))
        files={};paths=sorted(p for p in publication.rglob('*') if p.is_file())
        for i,p in enumerate(paths,1):
            if i%10==1:print(f'OUTPUT_IDENTITIES {i}/{len(paths)} {p.name}',flush=True)
            files[p.relative_to(publication).as_posix()]=dict(bytes=p.stat().st_size,sha256=sha(p))
        save(publication/'manifest.json',dict(files=files,binding_sha256=sha(out/'binding.json'),
            audit_sha256=sha(audit_path),labels=audit['labels'],created_utc=now(),scope='full'))
        save(out/'completed.json',dict(state='complete',jobs=3748,publication=str(publication),
            manifest_sha256=sha(publication/'manifest.json'),utc=now(),seconds=time.monotonic()-started,
            no_detector_rerun=True))
        print('ANALYSIS_COMPLETE '+str(publication/'report.html'),flush=True)
    except BaseException as exc:
        save(session/'failed.json',dict(state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
            error=repr(exc),traceback=traceback.format_exc(),utc=now()))
        raise
    finally:
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1);lock.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('audit');a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('run');a.add_argument('--out',type=Path,required=True);a.add_argument('--audit',type=Path,required=True)
    a.add_argument('--bounded-jobs',type=int)
    a=s.add_parser('status');a.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.action=='audit':save(args.out,metadata_audit());print('AUDIT_SAVED '+str(args.out))
    elif args.action=='run':run(args.out,args.audit,args.bounded_jobs)
    else:
        if (args.out/'completed.json').exists():print(json.dumps(read(args.out/'completed.json'),indent=2))
        else:print(json.dumps(dict(state='analysis_pending_or_incomplete',checkpoints=len(list((args.out/'checkpoints').glob('*.json'))),
                                   experiments_rerun=False)))


if __name__=='__main__':main()
