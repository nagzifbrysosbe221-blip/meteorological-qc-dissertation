"""Six D3 table groups and a file-based read-only dashboard, built from contributions."""
from collections import defaultdict, Counter
import csv
import json
from pathlib import Path
import shutil
from evidence import HERE,STUDY,PASSES,MONTHS,CONFIGS,read,save,require,identity,LABELS
from metrics import counts,ratio,root_summary,contrasts,describe_months,distribution,quantile

FILTERS=('pass','family','variable','configuration','month','task','cohort','side','support')


class Table:
    """Streaming JSON/JSONL and typed CSV. One pass at a time loads in the dashboard."""
    def __init__(self,folder,name):
        self.name=name;self.count=0;self.folder=folder
        self.jl=(folder/(name+'.jsonl')).open('x',encoding='utf-8',newline='\n')
        self.js=(folder/(name+'.json')).open('x',encoding='utf-8',newline='\n');self.js.write('[')
        self.cf=(folder/(name+'.csv')).open('x',encoding='utf-8',newline='')
        self.csv=csv.writer(self.cf);self.csv.writerow(('row_id',*FILTERS,'record_json'))
        self.browser={}

    def add(self,row):
        row=dict(row_id=f'{self.name}:{self.count+1}',scientific_labels=LABELS,**row);self.count+=1
        text=json.dumps(row,allow_nan=False,ensure_ascii=True,separators=(',',':'))
        self.jl.write(text+'\n');self.js.write((',' if self.count>1 else '')+text)
        # JSON in every cell distinguishes null/zero/false and prevents formula interpretation.
        self.csv.writerow([json.dumps(row.get(k),allow_nan=False,ensure_ascii=True) for k in ('row_id',*FILTERS)]+[text])
        pass_id=row.get('pass','all')
        if pass_id not in self.browser:
            f=(self.folder/(self.name+'--'+pass_id+'.js')).open('x',encoding='utf-8',newline='\n')
            f.write('window.loadTable('+json.dumps(self.name)+','+json.dumps(pass_id)+',[')
            self.browser[pass_id]=[f,0]
        f,n=self.browser[pass_id];f.write((',' if n else '')+text.replace('<','\\u003c'));self.browser[pass_id][1]+=1
        return row

    def close(self):
        self.jl.close();self.js.write(']\n');self.js.close();self.cf.close()
        for f,n in self.browser.values():f.write(']);\n');f.close()
        return dict(name=self.name,rows=self.count,passes=list(self.browser),json=self.name+'.json',
                    jsonl=self.name+'.jsonl',csv=self.name+'.csv')


def publish(out,publication,audit,selection,fixture=False):
    tables_dir=publication/'tables';tables_dir.mkdir()
    names=['construction','background_points','background_burden','point_monthly','point_annual',
           'roots','root_summary','contrasts','diagnostics','recovery','monthly_description','sensitivity']
    tables={n:Table(tables_dir,n) for n in names}
    roots=[];background=[];burdens=[];annual=defaultdict(lambda:dict(counts=Counter(),scored=0,contributions=[],months=set()))
    monthly_point=defaultdict(lambda:defaultdict(lambda:[0,0]));case_versions=defaultdict(dict)
    root_keys=('pass','family','variable','support')
    point_keys=('pass','family','scenario_variable','support','variant','side','task','variable','configuration','cohort','status','primary_task')
    try:
        entries=audit['jobs']
        for i,entry in enumerate(entries,1):
            c=read(out/'checkpoints'/(entry['job_id']+'.json'));v=read(out/c['path']);j=v['job'];r=v['root']
            print(f'AGGREGATE {i}/{len(entries)} {j["job_id"]}',flush=True) if i%100==0 or i==1 else None
            provenance=dict(job_id=j['job_id'],completion_marker=None if fixture else str(STUDY/'completed'/(j['job_id']+'.json')),
                contribution=c['path'],contribution_sha256=c['sha256'],marker_sha256=v['marker_sha256'])
            base={'pass':j['pass'],'job_id':j['job_id'],'evidence':provenance}
            if r:
                roots.append(r)
                rc={k:r.get(k) for k in ('family','variable','support','variant','case_id','source_month','planned_onset','planned_end',
                    'first_effect','last_effect','N','C','Z','U','range_crossing','construction','paired','pair_exclusions','unassessed_units')}
                tables['construction'].add(dict(**base,month=r['source_month'],**rc))
                stable={k:r[k] for k in ('case_id','family','variable','support','variant','N','C','Z','U','construction','planned_onset','first_effect')}
                case_versions[r['case_id']][j['pass']]=dict(root=r,input=v['provenance']['input_identity'],
                    counterpart=v['provenance']['counterpart_identity'],construction=r.get('private_root_identity',identity(stable)))
                for cfg,cfgdata in r['configurations'].items():
                    tables['roots'].add(dict(**base,family=r['family'],variable=r['variable'],support=r['support'],variant=r['variant'],
                        month=r['source_month'],case_id=r['case_id'],configuration=cfg,first_effect=r.get('first_effect'),horizon=r.get('horizon'),**cfgdata))
                for cfg,d in v['diagnostics'].items():
                    tables['diagnostics'].add(dict(**base,family=r['family'],variable=r['variable'],month=r['source_month'],configuration=cfg,**d))
                for recovery in v['recovery']:
                    paired_recovery=next(x for x in v['recovery'] if x['side']!=recovery['side'] and
                        (x['configuration'],x['task'],x['variable'])==(recovery['configuration'],recovery['task'],recovery['variable']))
                    require(paired_recovery['slots']==recovery['slots'],'Recovery schedule mismatch')
                    tables['recovery'].add(dict(**base,family=r['family'],scenario_variable=r['variable'],month=r['source_month'],
                        planned_end=r['planned_end'],recovery_end=v['window']['last_scored'],**recovery,
                        minus_other_side_raw_positive=recovery['raw_positive']-paired_recovery['raw_positive'],
                        minus_other_side_complete_slots=recovery['complete']-paired_recovery['complete'],
                        limitation='Raw post-end load; not primary normal exposure, event rescue, or causal recovery estimate'))
            else:
                for b in v['burden']:
                    bb=dict(**base,**b);burdens.append(bb);tables['background_burden'].add(bb)
            for p in v['points']:
                row=dict(**base,**p)
                if not r:
                    background.append(row);tables['background_points'].add(row);continue
                row.update(family=r['family'],scenario_variable=r['variable'],support=r['support'],variant=r['variant'])
                row=tables['point_monthly'].add(row)
                key=tuple(row[k] for k in point_keys);a=annual[key]
                a['counts'].update({k:row[k] for k in ('TP','FP','FN','TN')});a['scored']+=row['scored_subjects']
                a['contributions'].append(row['row_id']);a['months'].add(row['month'])
                if row['primary_task'] and row['side']=='injected':
                    for metric,n,d in [('precision',row['TP'],row['TP']+row['FP']),('recall',row['TP'],row['TP']+row['FN']),
                                       ('fpr',row['FP'],row['FP']+row['TN']),('coverage',row['n'],row['scored_subjects'])]:
                        x=monthly_point[key,metric][row['month']];x[0]+=n;x[1]+=d
        print('AGGREGATE exact-variant annual counts and descriptions',flush=True)
        for key,a in sorted(annual.items(),key=str):
            c=counts(a['counts']);tables['point_annual'].add(dict(zip(point_keys,key)) | dict(month='annual',**c,
                scored_subjects=a['scored'],coverage=ratio(c['n'],a['scored'],'no_scored_subjects'),
                excluded_scored=a['scored']-c['n'],contributions=a['contributions'],source_months=sorted(a['months'])))
        for (key,metric),months in sorted(monthly_point.items(),key=str):
            tables['monthly_description'].add(dict(zip(point_keys,key)) | dict(group='exact_variant_points',metric=metric,
                **describe_months([dict(month=m,numerator=v[0],denominator=v[1]) for m,v in months.items()])))
        # Background annual counts are recomputed from their monthly contributions, never from labels/schedule counts.
        bg_groups=defaultdict(list)
        for r in background:bg_groups[tuple(r[k] for k in ('pass','task','variable','configuration','cohort','status'))].append(r)
        for key,items in sorted(bg_groups.items(),key=str):
            common=dict(zip(('pass','task','variable','configuration','cohort','status'),key))
            cc=counts({k:sum(r[k] for r in items) for k in ('TP','FP','FN','TN')});n=sum(r['scored_subjects'] for r in items)
            tables['background_points'].add(dict(**common,month='annual',**cc,scored_subjects=n,
                coverage=ratio(cc['n'],n),excluded_scored=n-cc['n'],contributions=[r['job_id']+':'+r['month']+':'+str(r['source_row']) for r in items]))
            for metric in ('fpr','coverage'):
                tables['monthly_description'].add(dict(**common,group='continuous_background',metric=metric,
                    **describe_months([dict(month=r['month'],numerator=r[metric]['numerator'],denominator=r[metric]['denominator']) for r in items])))
        b_groups=defaultdict(list)
        for r in burdens:
            if r['month']!='annual':b_groups[tuple(r[k] for k in ('pass','task','variable','configuration','cohort'))].append(r)
        for key,items in sorted(b_groups.items(),key=str):
            tables['monthly_description'].add(dict(zip(('pass','task','variable','configuration','cohort'),key)) | dict(
                group='continuous_background',metric='burden',**describe_months([dict(month=r['month'],
                    numerator=r['factor']*r['segment_starts'],denominator=r['exposure']) for r in items])))
        root_groups=defaultdict(list);summary_rows=[];contrast_rows=[]
        for r in roots:root_groups[tuple(r[k] for k in root_keys)].append(r)
        for key,items in sorted(root_groups.items(),key=str):
            common=dict(zip(root_keys,key));monthly_s=defaultdict(list);monthly_c=defaultdict(list)
            for month in ('annual',*MONTHS):
                group=items if month=='annual' else [r for r in items if r['source_month']==month]
                for row in root_summary(group):
                    row=dict(**common,month=month,**row);summary_rows.append(row);tables['root_summary'].add(row)
                    if month!='annual':monthly_s[row['configuration']].append(row)
                for row in contrasts(group):
                    row=dict(**common,month=month,**row);contrast_rows.append(row);tables['contrasts'].add(row)
                    if month!='annual':monthly_c[row['comparison']].append(row)
            for cfg,rows in monthly_s.items():
                for metric in ('detection','opportunity_coverage','conditional_detection','workflow_yield'):
                    tables['monthly_description'].add(dict(**common,configuration=cfg,group='root_family',metric=metric,
                        **describe_months([dict(month=r['month'],numerator=r[metric]['numerator'],denominator=r[metric]['denominator']) for r in rows])))
            for comparison,rows in monthly_c.items():
                tables['monthly_description'].add(dict(**common,comparison=comparison,group='paired_contrast',metric='detection_difference',
                    **describe_months([dict(month=r['month'],numerator=r['detection_difference']['numerator'],denominator=r['paired']) for r in rows])))
                month_medians=[r['common_hit_delay']['median'] for r in rows]
                leave=[dict(omitted_month=m,common_hits=sum(len(r['common_hits']) for r in rows if r['month']!=m),
                    delay_difference_median_seconds=quantile([h['difference_seconds'] for r in rows if r['month']!=m for h in r['common_hits']],.5)) for m in MONTHS]
                defined=[r['delay_difference_median_seconds'] for r in leave if r['delay_difference_median_seconds'] is not None]
                tables['monthly_description'].add(dict(**common,comparison=comparison,group='common_hit_delay',metric='monthly_median_delay_difference_seconds',
                    **distribution(month_medians),leave_one_month_out=leave,leave_out_range=[min(defined),max(defined)] if defined else None,
                    definition='Type-7 distribution of monthly common-hit median differences; LOMO recomputes median over retained common-hit roots'))
        for case_id,versions in sorted(case_versions.items()):
            require(set(versions)==set(PASSES) or fixture,'Missing sensitivity case')
            primary=versions['primary']
            for pass_id,v in versions.items():
                require(all(v[k]==primary[k] for k in ('input','counterpart','construction')),'Sensitivity input/construction changed')
                if pass_id=='primary':continue
                r=v['root']
                for cfg in CONFIGS:
                    a,b=primary['root']['configurations'][cfg],r['configurations'][cfg]
                    both=a['category']==b['category']=='hit'
                    tables['sensitivity'].add(dict(**{'pass':pass_id},family=r['family'],variable=r['variable'],support=r['support'],
                        month=r['source_month'],case_id=case_id,configuration=cfg,primary_category=a['category'],sensitivity_category=b['category'],
                        detection_change=int(b['category']=='hit')-int(a['category']=='hit'),
                        common_hit_delay_difference_seconds=b['delay_seconds']-a['delay_seconds'] if both else None,
                        primary_job=primary['root']['job_id'],sensitivity_job=r['job_id'],identical_inputs_verified=True))
    finally:
        inventory=[t.close() for t in tables.values()]
    save(publication/'table-inventory.json',inventory)
    from figures import draw
    draw(publication,roots,background,burdens,summary_rows,contrast_rows,selection,fixture=fixture)
    config=dict(tables=inventory,labels=LABELS,fixture=fixture,scope='FABRICATED CHECK — NOT RESEARCH FINDINGS' if fixture else 'Complete saved full study',
        jobs=len(audit['jobs']),controller=audit.get('controller_saved_completion'),timelines=selection,
        verification='Consumed archive members checked by SHA-256. Original full controller completion retained. No detector rerun.',
        figure_index=read(publication/'figures/index.json'))
    (publication/'report-data.js').write_text('window.REPORT='+json.dumps(config,ensure_ascii=True).replace('<','\\u003c')+';\n',encoding='utf-8')
    shutil.copyfile(HERE/'dashboard.html',publication/'report.html');shutil.copyfile(HERE/'dashboard.js',publication/'dashboard.js')
    save(publication/'audit-summary.json',{k:v for k,v in audit.items() if k!='jobs'})
    save(publication/'source-index.json',audit['jobs'])
