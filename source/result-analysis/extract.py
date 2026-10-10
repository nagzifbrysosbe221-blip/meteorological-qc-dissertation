"""Reduce saved evaluations to traceable contributions; never execute experiments."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from evidence import Bundle, require, LABELS, CONFIGS, MONTHS, identity
from metrics import compact_points, ratio


def source_month(stamp):
    return (datetime.fromisoformat(stamp)-timedelta(hours=1)).strftime('%Y-%m')


def recovery(rows, root, last):
    groups={}
    for r in rows:
        if not root['planned_end'] < r['slot_utc'] <= last: continue
        key=(r['configuration'],r['domain'],r['variable'])
        a=groups.setdefault(key,dict(configuration=key[0],task=key[1],variable=key[2],slots=0,complete=0,
            raw_positive=0,strict_positive=0,unevaluated=0,first_flag_utc=None,last_flag_utc=None,first_complete_negative_utc=None))
        a['slots']+=1; a['complete']+=r['complete']; a['unevaluated']+=not r['complete']
        a['raw_positive']+=r['prediction'] is True
        a['strict_positive']+=r['complete'] and r['prediction'] is True
        if r['prediction'] is True:
            a['first_flag_utc']=a['first_flag_utc'] or r['slot_utc']; a['last_flag_utc']=r['slot_utc']
        elif r['complete']:
            a['first_complete_negative_utc']=a['first_complete_negative_utc'] or r['slot_utc']
    return list(groups.values())


def ongoing(episodes, outcome, root):
    for cfg,c in outcome['configurations'].items():
        rid=c['result_id']; c['ongoing_at_first_effect']=False
        c['first_credit_route']=None
        if rid:
            check=rid.rsplit('/',1)[-1]
            c['first_credit_check']=check
            c['first_credit_route']='linked_absent_consequence' if check=='Q01' and root['family'] in ('off_grid_timestamp','invalid_timestamp') else 'direct'
            matches=[e for e in episodes if e['group'][1]==cfg and any(rid in n['contributors'] for n in e['nodes'])]
            require(len(matches)>=1,'First credited result absent from raw episodes')
            c['ongoing_at_first_effect']=any(e['nodes'][0]['time'] < root['first_effect'] for e in matches)
            c['matched_episode_ids']=[e['episode_id'] for e in matches]


def raw_months(episodes, first, last):
    result=defaultdict(lambda:dict(raw_positive_units_scored=0,raw_episode_starts_scored=0,raw_carry_in=0))
    for e in episodes:
        _,cfg,domain,var=e['group']
        nodes=[n for n in e['nodes'] if first<=n['time']<=last]
        if not nodes: continue
        for n in nodes: result[(cfg,domain,var,source_month(n['time']))]['raw_positive_units_scored']+=1
        # Broad episodes are truth-free; distinguish start before scored period.
        for m in {source_month(n['time']) for n in nodes}:
            key=(cfg,domain,var,m)
            if first<=e['nodes'][0]['time']<=last and source_month(e['nodes'][0]['time'])==m:
                result[key]['raw_episode_starts_scored']+=1
            else: result[key]['raw_carry_in']+=1
    return result


def extract_job(record):
    j=record['job']; pub,priv=Bundle(record,'public'),Bundle(record,'private')
    try:
        require(pub.json('job.json')==j,'Archived job differs')
        worker=pub.json('worker-completed.json')
        expected=dict(record['public']['files']);expected.pop('worker-completed.json')
        require(worker['public_files']==expected and worker['private_files']==record['private']['files'], 'Worker file roster differs')
        require(worker['state']=='complete' and worker['job_id']==j['job_id'] and worker['pass']==j['pass'] and
                worker['kind']==j['kind'] and worker['labels']==LABELS and worker['freeze_sha256']==record['freeze_sha256'] and
                worker['construction']==record['construction'] and worker['configurations']==list(CONFIGS),'Worker binding differs')
        win=pub.json('window.json'); provenance=pub.json('provenance.json')
        require(provenance['freeze_sha256']==record['freeze_sha256'] and provenance['labels']==LABELS and provenance['pass']==j['pass'],'Provenance differs')
        for side in (('background',) if j['kind']=='background' else ('injected','counterpart')):
            done=pub.json(side+'/completed.json'); run=pub.json(side+'/run.json')
            require(done['state']=='monitor_complete' and run['run_status']=='complete' and
                    run['slots_closed']==len(win['slots']) and run['drain_until']==win['drain_until'],'Incomplete saved run/drain')
            inputs=pub.json(side+'/public-inputs.json')
            require(identity(inputs)==provenance['counterpart_identity' if side=='counterpart' else 'input_identity'], 'Detector-visible input identity differs')
            del inputs
            for name,item in done['files'].items():
                require(record['public']['files'][side+'/'+name]==item,'Monitor inventory differs')
        result=dict(job=j,labels=LABELS,window={k:v for k,v in win.items() if k!='slots'},
            provenance=provenance,construction=record['construction'],points=[],burden=[],recovery=[])
        if j['kind']=='background':
            require(worker['primary_normal_exposure_hours']==8760 and win['scored_hours']==8760,'Background schedule differs')
            e=pub.json('background-evaluation.json'); require(e['labels']==LABELS,'Background labels differ')
            totals=defaultdict(Counter)
            for name in e['monthly_point_files']:
                month=name[:7];require(month in MONTHS,'Unknown background month')
                for r in compact_points(pub.json(name),name):
                    result['points'].append(dict(month=month,side='background',**r))
                    k='|'.join((r['task'],str(r['variable']),r['configuration']))
                    totals[k,r['cohort']].update({n:r[n] for n in ('TP','FP','FN','TN','n')})
            for key,cohorts in e['annual_counts'].items():
                for cohort,c in cohorts.items():
                    require(all(totals[key,cohort][k]==c[k] for k in ('TP','FP','FN','TN','n')),'Monthly/annual count mismatch')
            raw=raw_months(pub.json('raw-episodes.json'),win['first_scored'],win['last_scored'])
            for b in e['burden']:
                require(sum(x['exposure'] for x in b['monthly'].values())==b['exposure'] and
                        sum(x['segment_starts'] for x in b['monthly'].values())==b['segment_count'],'Exposure/segment mismatch')
                require(b['burden']==ratio(b['rate_factor']*b['segment_count'],b['exposure'],'no_eligible_negative_exposure'),'Burden arithmetic mismatch')
                _,cfg,domain,var=b['group']
                base=dict(configuration=cfg,task=domain,variable=var,cohort=b['cohort'],factor=b['rate_factor'])
                result['burden'].append(dict(**base,month='annual',exposure=b['exposure'],segment_starts=b['segment_count'],
                    carry_in=None,burden=b['burden'],raw_positive_units_scored=b['raw_positive_units_scored'],
                    raw_episodes_with_scored_positive=b['raw_episodes_with_scored_positive'],
                    raw_positive_units_including_context=b['raw_positive_units'],raw_episodes_including_context=b['raw_episodes']))
                for m in MONTHS:
                    v=b['monthly'].get(m,dict(exposure=0,segment_starts=0,carry_in=0,burden=ratio(0,0,'no_eligible_negative_exposure')))
                    require(v['burden']==ratio(b['rate_factor']*v['segment_starts'],v['exposure'],'no_eligible_negative_exposure'),'Monthly burden differs')
                    result['burden'].append(dict(**base,month=m,**v,**raw[(cfg,domain,var,m)]))
            result['root']=None
        else:
            require(worker['primary_normal_exposure_hours']==0,'Counterpart exposure leak')
            e=pub.json('evaluation.json'); require(e['labels']==LABELS and e['primary_normal_exposure_hours']==0,'Pair labels/exposure differs')
            g=priv.json('construction.json'); root=g['root'];del g
            require(all(root[k]==v for k,v in j['case'].items()),'Private root differs from planned case')
            require(root['N']==root['C']+root['Z']+root['U'] and root['construction']==record['construction'],'Construction mismatch')
            require(len(root['actual_change_mask'])==len(set(root['actual_change_mask']))==root['C'], 'Construction change-mask accounting differs')
            require(len(e['root_outcomes'])==1,'Expected one isolated root')
            outcome=e['root_outcomes'][0]
            require(outcome['case_id']==root['case_id'] and outcome['construction']==root['construction'] and
                    outcome['support']==root['support'],'Root outcome mismatch')
            ongoing(pub.json('raw-episodes.json'),outcome,root)
            for c in outcome['configurations'].values():
                require((c['category']=='hit') == (c['delay_seconds'] is not None),'Miss delay must remain null')
                c['required_complete_ids_sha256']=identity(c.pop('required_complete_unit_ids'))
                c['required_ids_sha256']=identity(c.pop('required_unit_ids'))
            result['root']={**{k:v for k,v in root.items() if k not in ('members','actual_change_mask','added_identities','deleted_identities','consequences')},
                            **outcome,'job_id':j['job_id'],'pass':j['pass'],'private_root_identity':identity(root)}
            result['diagnostics']={cfg:(d if d.get('status')=='inapplicable' else dict(
                comparable_opportunities=len(d['comparable_opportunities']),hit_slots=len(d['hit_slots']),
                diagnostic_hit=d['diagnostic_hit'],source_member='evaluation.json',source_path='excess_response.'+cfg))
                for cfg,d in e['excess_response'].items()}
            for side,key in [('injected','point_scores'),('counterpart','counterpart_point_scores')]:
                for r in compact_points(e[key],'evaluation.json#'+key,root):
                    result['points'].append(dict(month=j['case']['source_month'],side=side,**r))
            del e
            for side in ('injected','counterpart'):
                names=sorted(n for n in record['public']['files'] if n.startswith(side+'/') and n.endswith('/summaries.jsonl'))
                def rows():
                    for name in names: yield from pub.rows(name)
                result['recovery'].extend(dict(side=side,**r) for r in recovery(rows(),root,win['last_scored']))
        result['sources']=dict(public=pub.source(),private=priv.source())
        return result
    finally:
        pub.close();priv.close()


def export_timeline(record, folder):
    """Fixed-selection detail only. Full rows retain precision, UTC, reasons and IDs."""
    folder.mkdir(parents=True,exist_ok=False)
    pub,priv=Bundle(record,'public'),Bundle(record,'private')
    try:
        root=priv.json('construction.json')['root']
        # Full truth is a post-run overlay, kept separate from detector rows.
        from evidence import save, HERE
        import shutil
        save(folder/'post-run-root.json',root)
        outputs=[]
        for side in ('injected','counterpart'):
            for stream in ('ledger','states','summaries'):
                target=folder/(side+'-'+stream+'.jsonl')
                with target.open('xb') as f:
                    for name in sorted(pub.manifest['files']):
                        if name.startswith(side+'/') and name.endswith('/'+stream+'.jsonl'):
                            for data in pub.chunks(name):f.write(data)
                # File-based browser loading avoids a local web-service dependency.
                with target.with_suffix('.js').open('x',encoding='utf-8') as js, target.open(encoding='utf-8') as src:
                    js.write('window.loadEvidence('+__import__('json').dumps(side+'-'+stream)+',[')
                    for i,line in enumerate(src):
                        js.write((',' if i else '')+line.strip().replace('<','\\u003c'))
                    js.write(']);\n')
                outputs.append(target.name)
            for name in ('public-inputs.json','run.json'):
                pub.export(side+'/'+name,folder/(side+'-'+name));outputs.append(side+'-'+name)
        save(folder/'sources.json',dict(public=pub.source(),private=priv.source(),exports=outputs,
            interpretation='Truth overlay joined after saved monitoring. Emission times are simulated; onset estimates remain null.'))
        shutil.copyfile(HERE/'evidence-view.html',folder/'evidence.html')
    finally:pub.close();priv.close()
