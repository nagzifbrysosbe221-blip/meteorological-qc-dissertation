"""Transparent count aggregation only. No detector, label changes or significance tests."""
from collections import Counter, defaultdict
from evidence import require, identity, MONTHS, CONFIGS


def ratio(n, d, reason='zero_denominator'):
    require(d >= 0, 'Negative denominator')
    return dict(numerator=n, denominator=d, value=n/d if d else None, reason=None if d else reason)


def counts(c):
    require(all(type(c[k]) is int and c[k] >= 0 for k in ('TP','FP','FN','TN')), 'Invalid counts')
    tp, fp, fn, tn = (c[k] for k in ('TP','FP','FN','TN'))
    return dict(TP=tp, FP=fp, FN=fn, TN=tn, n=tp+fp+fn+tn,
                precision=ratio(tp,tp+fp,'no_predicted_positives'), recall=ratio(tp,tp+fn,'no_truth_positives'),
                fpr=ratio(fp,fp+tn,'no_truth_negatives'))


def quantile(values, p):
    a = sorted(v for v in values if v is not None)
    if not a: return None
    h = (len(a)-1)*p; lo = int(h)
    return a[lo]+(h-lo)*(a[min(lo+1,len(a)-1)]-a[lo])


def distribution(values):
    a = [v for v in values if v is not None]
    q1, q3 = quantile(a,.25), quantile(a,.75)
    return dict(defined=len(a), undefined=len(values)-len(a), median=quantile(a,.5), q1=q1, q3=q3,
                iqr=q3-q1 if a else None, minimum=min(a) if a else None, maximum=max(a) if a else None)


def describe_months(rows):
    require(len({r['month'] for r in rows}) == len(rows), 'Duplicate monthly contribution')
    require(set(r['month'] for r in rows) <= set(MONTHS), 'Unknown source month')
    index = {r['month']: r for r in rows}
    complete = [dict(month=m, numerator=index.get(m,{}).get('numerator',0),
                     denominator=index.get(m,{}).get('denominator',0)) for m in MONTHS]
    values = [ratio(r['numerator'],r['denominator'])['value'] for r in complete]
    leave = [dict(omitted_month=m, **ratio(sum(r['numerator'] for r in complete if r['month']!=m),
                        sum(r['denominator'] for r in complete if r['month']!=m),'no_remaining_denominator')) for m in MONTHS]
    defined = [r['value'] for r in leave if r['value'] is not None]
    return dict(monthly_contributions=complete, **distribution(values), leave_one_month_out=leave,
                leave_out_range=[min(defined),max(defined)] if defined else None)


def compact_points(points, source, root=None):
    output=[]
    for i,p in enumerate(points):
        require(len(p['own_ids']) == len(set(p['own_ids'])) and len(p['common_ids']) == len(set(p['common_ids'])), 'Duplicate cohort ID')
        require(set(p['common_ids']) <= set(p['own_ids']), 'Common outside own cohort')
        for cohort in ('own','common'):
            c = counts(p[cohort]); require(c == p[cohort], 'Saved ratio/count mismatch')
            require(c['n'] == len(p[cohort+'_ids']), 'Cohort length mismatch')
            cov = p[cohort+'_coverage']; require(cov['numerator'] == c['n'], 'Coverage differs')
            require(cov == ratio(cov['numerator'],cov['denominator'],'no_scored_subjects'), 'Coverage arithmetic mismatch')
            excluded = Counter(); outside=Counter()
            for reasons in p['exclusions'].values():
                (outside if 'outside_point_scope' in reasons else excluded).update(reasons)
            primary = True if root is None else (
                p['task']=='value' and root['family'] in ('out_of_range','gradual_bias') and p['variable']==root['variable'] or
                p['task']=='availability' and root['family'] in ('missing_cell','absent_row') and root.get('variable') in (None,p['variable']) or
                p['task']=={'duplicate_receipt':'Q06','off_grid_timestamp':'Q05','invalid_timestamp':'Q04'}.get(root['family']))
            output.append(dict(task=p['task'], variable=p['variable'], configuration=p['configuration'],
                cohort=cohort,status=p['status'],primary_task=primary, **c, coverage=cov,
                scored_subjects=cov['denominator'], excluded_scored=cov['denominator']-c['n'],
                own_exclusion_reasons=dict(excluded), context_exclusion_reasons=dict(outside),
                natural_structural_count=len(p['natural_structural']), cohort_ids_sha256=identity(p[cohort+'_ids']),
                source_member=source, source_row=i))
    for task,var in {(p['task'],p['variable']) for p in points}:
        items=[p for p in points if (p['task'],p['variable'])==(task,var) and p['status']=='applicable']
        require(len({identity(p['common_ids']) for p in items})==1, 'Unmatched common cohort')
        require(set(items[0]['common_ids'])==set.intersection(*(set(p['own_ids']) for p in items)), 'Common cohort is not exact own intersection')
    return output


def pool_points(rows):
    keys=('pass','family','scenario_variable','support','variant','side','task','variable','configuration','cohort','status','primary_task')
    groups=defaultdict(list)
    for r in rows: groups[tuple(r[k] for k in keys)].append(r)
    result=[]
    for key,items in sorted(groups.items(),key=str):
        c=counts({k:sum(r[k] for r in items) for k in ('TP','FP','FN','TN')})
        n=sum(r['scored_subjects'] for r in items)
        result.append(dict(zip(keys,key)) | dict(period='annual', **c, coverage=ratio(c['n'],n,'no_scored_subjects'),
            scored_subjects=n, excluded_scored=n-c['n'], contributions=[r['row_id'] for r in items],
            source_months=sorted({r['month'] for r in items})))
    return result


def root_summary(items):
    require(len({(r['pass'],r['family'],r['variable'],r['support']) for r in items})<=1,'Cross-family/variable/support pooling')
    paired=[r for r in items if r['paired']]; n=len(paired); effective=sum(r['construction']=='effective' for r in items)
    result=[]
    for cfg in CONFIGS:
        cc=Counter(r['configurations'][cfg]['category'] for r in paired)
        hit=cc['hit']; opportunity=hit+cc['evaluated_miss']
        cfgs=[r['configurations'][cfg] for r in paired]
        result.append(dict(configuration=cfg,planned=len(items),effective=effective,paired=n,
            construction_counts={s:sum(r['construction']==s for r in items) for s in ('pending','blocked','generation_error','no_effect','effective')}, missing_or_failed_pairs=effective-n,
            construction_coverage=ratio(effective,len(items)),completion=ratio(n,effective),
            **{k:cc[k] for k in ('hit','evaluated_miss','unavailable_miss','absent_capability_miss')},
            detection=ratio(hit,n,'no_valid_pairs'),opportunity_coverage=ratio(opportunity,n,'no_valid_pairs'),
            conditional_detection=ratio(hit,opportunity,'no_evaluated_opportunities'),workflow_yield=ratio(hit,len(items)),
            full_required_coverage=ratio(sum(c['full_required_coverage']['numerator'] for c in cfgs),
                                        sum(c['full_required_coverage']['denominator'] for c in cfgs)),
            statistical_opportunities=sum(c['statistical_opportunities'] for c in cfgs),
            first_credit_routes=dict(Counter(c['first_credit_route'] for c in cfgs if c['category']=='hit')),
            ongoing_hits=sum(c.get('ongoing_at_first_effect',False) for c in cfgs),
            contributions=[r['job_id'] for r in items]))
    return result


def contrasts(items):
    require(len({(r['pass'],r['family'],r['variable'],r['support']) for r in items})<=1,'Unmatched contrast stratum')
    paired=[r for r in items if r['paired']]; result=[]
    for a,b in [('H','B0'),('R','B0'),('H','R')]:
        both=[]; first=[]; second=[]; neither=[]
        for r in paired:
            x,y=r['configurations'][a],r['configurations'][b]
            ah,bh=x['category']=='hit',y['category']=='hit'
            if ah and bh:
                require(x['delay_seconds'] is not None and y['delay_seconds'] is not None,'Missing hit delay')
                both.append(dict(job_id=r['job_id'],case_id=r['case_id'], first_delay_seconds=x['delay_seconds'],
                    second_delay_seconds=y['delay_seconds'], difference_seconds=x['delay_seconds']-y['delay_seconds']))
            else: (first if ah else second if bh else neither).append(r['job_id'])
        result.append(dict(comparison=a+'-'+b, primary=a+'-'+b=='H-B0', paired=len(paired),
            detection_difference=ratio(len(first)-len(second),len(paired),'no_valid_pairs'),common_hits=both,
            common_hit_delay=distribution([r['difference_seconds'] for r in both]),first_only=first,second_only=second,neither=neither,
            contributions=[r['job_id'] for r in items]))
    return result


def select_timelines(roots):
    selected=[]
    for pass_id,family in sorted({(r['pass'],r['family']) for r in roots}):
        items=sorted((r for r in roots if r['pass']==pass_id and r['family']==family and r['construction']=='effective'),key=lambda r:r['case_id'])
        if not items:
            selected.append(dict(**{'pass':pass_id},family=family,status='no_effective_case'));continue
        missed=next((r for r in items if r['paired'] and any(c['category']!='hit' for c in r['configurations'].values())),None)
        for r in items[:1]+([missed] if missed and missed!=items[0] else []):
            selected.append(dict(**{'pass':pass_id},family=family,status='selected',job_id=r['job_id'],case_id=r['case_id'],
                reason='first_effective_and_first_miss' if r==items[0] and r==missed else
                       'first_effective' if r==items[0] else 'first_missed_by_any_configuration'))
    return selected
