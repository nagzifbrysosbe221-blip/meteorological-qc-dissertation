"""Bounded fabricated report exercise. All displayed study contributions are fabricated."""
import copy
import json
import shutil
from evidence import ROOT,HERE,PASSES,CONFIGS,LABELS,read,save,sha,token,identity
from metrics import counts,ratio,select_timelines
from report import publish


def build():
    out=ROOT/'evidence/result-analysis-2026-10-09'/('fabricated-report-'+token());out.mkdir(parents=True)
    jobs=[];roots=[]
    def checkpoint(v):
        jid=v['job']['job_id'];path=out/'attempts'/jid/'contribution.json';save(path,v)
        save(out/'checkpoints'/(jid+'.json'),dict(path=path.relative_to(out).as_posix(),sha256=sha(path)))
        jobs.append(dict(job_id=jid))
    for pass_id in PASSES:
        points=[];burdens=[]
        for month in ('2024-01','2024-02'):
            for cfg in CONFIGS:
                for var in ('T','U'):
                    for cohort in ('own','common'):
                        c=counts(dict(TP=0,FP=int(cfg=='H'),FN=0,TN=3-int(cfg=='H')))
                        points.append(dict(month=month,side='background',task='value',variable=var,configuration=cfg,cohort=cohort,
                            status='applicable',primary_task=True,**c,coverage=ratio(3,4),scored_subjects=4,excluded_scored=1,
                            own_exclusion_reasons={'required_checks_incomplete':1},context_exclusion_reasons={},natural_structural_count=0,
                            cohort_ids_sha256='fabricated',source_member='fabricated',source_row=0))
                        n=int(cfg=='H')
                        burdens.append(dict(month=month,task='value',variable=var,configuration=cfg,cohort=cohort,factor=720,
                            exposure=3,segment_starts=n,carry_in=0,burden=ratio(720*n,3),raw_positive_units_scored=n,raw_episode_starts_scored=n))
        for cfg in CONFIGS:
            for var in ('T','U'):
                for cohort in ('own','common'):
                    n=2*int(cfg=='H');burdens.append(dict(month='annual',task='value',variable=var,configuration=cfg,cohort=cohort,factor=720,
                        exposure=6,segment_starts=n,carry_in=None,burden=ratio(720*n,6),raw_positive_units_scored=n))
        checkpoint(dict(job={'job_id':pass_id+'-fabricated-background','kind':'background','pass':pass_id},root=None,
            points=points,burden=burdens,recovery=[],marker_sha256='fabricated',provenance={},window={}))
        for i,var in enumerate(('T','U')):
            for m in (1,2):
                case=f'fabricated-{var}-{m}';jid=pass_id+'-'+case
                cfgs={}
                for cfg in CONFIGS:
                    hit=(cfg=='H' and m==2);category='hit' if hit else 'evaluated_miss' if cfg!='H' or m==2 else 'unavailable_miss'
                    cfgs[cfg]=dict(category=category,delay_seconds=300.0 if hit else None,result_id='fabricated-result' if hit else None,
                        statistical_opportunities=int(cfg=='H' and m==2),compatible_evaluated_results=int(category!='unavailable_miss'),
                        full_required_coverage=ratio(int(category!='unavailable_miss'),1),first_credit_route='direct' if hit else None,
                        ongoing_at_first_effect=False)
                r=dict(job_id=jid,case_id=case,root_id='root:'+case,**{'pass':pass_id},family='gradual_bias',variable=var,
                    support='full',variant=json.dumps(dict(variable=var,sign=1,scale_multiple=1,ramp_hours=24),sort_keys=True),
                    sign=1,scale_multiple=1,ramp_hours=24,length_hours=48,construction='effective',paired=True,pair_exclusions={},
                    source_month=f'2024-0{m}',planned_onset=f'2024-0{m}-08T01:00:00+00:00',planned_end=f'2024-0{m}-10T00:00:00+00:00',
                    first_effect=f'2024-0{m}-08T01:00:00+00:00',last_effect=f'2024-0{m}-10T00:00:00+00:00',
                    horizon=f'2024-0{m}-10T00:05:00+00:00',N=48,C=48,Z=0,U=0,range_crossing=False,unassessed_units=0,configurations=cfgs)
                roots.append(r);ps=[]
                for side in ('injected','counterpart'):
                    for cfg in CONFIGS:
                        for cohort in ('own','common'):
                            c=counts(dict(TP=int(cfg=='H' and m==2 and side=='injected'),FP=int(cfg=='H' and side=='counterpart'),
                                FN=1-int(cfg=='H' and m==2) if side=='injected' else 0,TN=2-int(cfg=='H' and side=='counterpart')))
                            ps.append(dict(month=f'2024-0{m}',side=side,task='value',variable=var,configuration=cfg,cohort=cohort,status='applicable',
                                primary_task=True,**c,coverage=ratio(c['n'],4),scored_subjects=4,excluded_scored=4-c['n'],
                                source_member='fabricated',source_row=0))
                checkpoint(dict(job=dict(job_id=jid,kind='pair',**{'pass':pass_id}),root=r,points=ps,burden=[],
                    recovery=[dict(side=s,configuration='H',task='value',variable=var,slots=2,complete=1,raw_positive=1,
                        strict_positive=0,unevaluated=1,first_flag_utc=None,last_flag_utc=None,first_complete_negative_utc=None) for s in ('injected','counterpart')],
                    diagnostics={cfg:dict(comparable_opportunities=1,hit_slots=int(cfg=='H' and m==2),diagnostic_hit=cfg=='H' and m==2) for cfg in CONFIGS},
                    marker_sha256='fabricated',provenance=dict(input_identity=case,counterpart_identity=case+'-control'),window=dict(last_scored=f'2024-0{m}-28T23:00:00+00:00')))
    publication=out/'publications'/'demo';publication.mkdir(parents=True)
    selection=select_timelines(roots);save(publication/'timeline-selection.json',selection)
    # Tiny, explicitly fabricated saved rows exercise the evidence viewer and all F6 streams.
    for s in selection:
        r=next(r for r in roots if r['job_id']==s['job_id']);folder=publication/'timelines'/r['job_id'];folder.mkdir(parents=True)
        save(folder/'post-run-root.json',r);save(folder/'sources.json',dict(scope='fabricated rows; not scientific evidence'))
        for side in ('injected','counterpart'):
            states=[];summaries=[];ledger=[]
            for hour in range(1,7):
                stamp=r['planned_onset'][:11]+f'{hour:02}:00:00+00:00'
                for var in ('T','U'):
                    state_id=f'{side}-state-{hour}-{var}'
                    states.append(dict(state_id=state_id,previous_valid_state_id=f'{side}-state-{hour-1}-{var}' if hour>1 else None,
                        slot_utc=stamp,variable=var,z=hour/10,execution='unevaluated' if hour==1 else 'evaluated',prediction=None if hour==1 else hour>3,
                        reason='warm_up' if hour==1 else 'fabricated',model_prediction=10.,reference_value=9.,settings_id='fabricated-settings'))
                    for cfg in CONFIGS:
                        for domain in ('value','availability'):
                            summaries.append(dict(configuration=cfg,variable=var,domain=domain,slot_utc=stamp,emitted_at=stamp,
                                execution='unevaluated' if cfg=='H' and hour==1 else 'evaluated',prediction=None if cfg=='H' and hour==1 else cfg=='H' and hour>3,
                                complete=not(cfg=='H' and hour==1)))
                        ledger.append(dict(result_id=f'{side}-{hour}-{var}-{cfg}',configuration=cfg,variable=var,units='degC' if var=='T' else '%',
                            check='S01' if cfg=='H' else 'Q03',emitted_at=stamp,prediction=None if cfg=='H' and hour==1 else cfg=='H' and hour>3,
                            execution='unevaluated' if cfg=='H' and hour==1 else 'evaluated',reason='warm_up' if cfg=='H' and hour==1 else 'fabricated',
                            evidence={'state_id':state_id} if cfg=='H' else {'value':0,'limits':[-40,50]},estimated_onset=None))
            for name,rows in [('states',states),('summaries',summaries),('ledger',ledger)]:
                (folder/(side+'-'+name+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
                (folder/(side+'-'+name+'.js')).write_text('window.loadEvidence('+json.dumps(side+'-'+name)+','+json.dumps(rows)+');',encoding='utf-8')
            save(folder/(side+'-public-inputs.json'),[]);save(folder/(side+'-run.json'),dict(scope='fabricated',settings='fabricated'))
        shutil.copyfile(HERE/'evidence-view.html',folder/'evidence.html')
    audit=dict(jobs=jobs,state='fabricated',labels=LABELS,controller_saved_completion=None)
    publish(out,publication,audit,selection,fixture=True)
    save(publication/'manifest.json',dict(scope='bounded fabricated report verification only',files={p.relative_to(publication).as_posix():sha(p) for p in publication.rglob('*') if p.is_file()}))
    print('FABRICATED_REPORT '+str(publication/'report.html'));return publication


if __name__=='__main__':build()
