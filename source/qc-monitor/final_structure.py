"""Restricted final intake: source structure only, never numeric magnitudes.

Raw response bytes remain the value store. Derived records contain identities,
source roles, cell states and independent structural labels only.
"""
import calendar
from collections import Counter, defaultdict
import csv
from datetime import date
import math
import re

from data import source_date, integer, utc_for, expected_schedule
from records import digest

START, END = date(2024,1,2), date(2024,12,31)
CONTEXT = date(2024,1,1)
COLUMNS = {'STN','YYYYMMDD','HH','T','U'}


class IntakeBlocked(ValueError):
    """A preserved source/mapping problem, not permission to alter observations."""


def cell_state(token):
    if token is None:return 'absent_field'
    if not token.strip():return 'missing'
    try:finite=math.isfinite(float(token))
    except (ValueError,OverflowError):return 'invalid'
    return 'finite' if finite else 'nonfinite'


def metadata_check(comments):
    """Check known delivered mapping; retain original header in raw bytes."""
    text=' '.join(comments).casefold()
    definitions={}
    for line in comments:
        m=re.match(r'#\s*(T|U|HH|YYYYMMDD)\s*[:=](.*)',line,re.I)
        if m:
            key=m.group(1).upper()
            if key in definitions:raise IntakeBlocked('Repeated field definition')
            definitions[key]=m.group(2).casefold()
    t,u,h=definitions.get('T',''),definitions.get('U',''),definitions.get('HH','')
    if ('0.1' not in t or not any(x in t for x in ('celsius',)) or '1.50' not in t or
        not any(x in t for x in ('temperature','temperatuur'))):
        raise IntakeBlocked('Temperature definition requires reviewed mapping')
    if ('1.50' not in u or not any(x in u for x in ('humidity','vochtigheid')) or
        not any(x in u for x in ('percent','procent'))):
        raise IntakeBlocked('Humidity definition requires reviewed mapping')
    if 'ut' not in h or 'YYYYMMDD' not in definitions:
        raise IntakeBlocked('Source date/hour definition requires reviewed mapping')
    if 'royal netherlands meteorological institute' not in text or 'inhomogeneous' not in text:
        raise IntakeBlocked('Source/archive header warning requires review')
    for station,name in ((240,'schiphol'),(260,'de bilt')):
        if not any(re.match(r'#\s*'+str(station)+r'\s',line) and name in line.casefold() for line in comments):
            raise IntakeBlocked('Station metadata requires review')
    # All source comments are preserved; unfamiliar licensing or quality fields require review.
    if any(word in text for word in ('copyright','licence','license','cc by','cc-by','all rights reserved')):
        raise IntakeBlocked('Delivered response contains a terms notice requiring review')
    return {'T_source_units':'tenths_degC','T_later_conversion_divisor':10,
            'U_source_units':'percent','U_later_conversion_divisor':1,
            'hours':'source 1..24 UTC; role assigned before conversion',
            'per_observation_quality_column':False,'header_sha256':digest('\n'.join(comments).encode())}


def parse_structure(payload, *, scope='final'):
    if scope not in ('final','context'):raise ValueError('Unknown structural scope')
    parent=digest(payload);header=None;comments=[];records=[]
    try:lines=payload.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError:raise IntakeBlocked('Unknown response encoding') from None
    seen=set()
    for number,line in enumerate(lines,1):
        if not line.strip():continue
        if line.lstrip().startswith('#'):
            comments.append(line)
            cols=[c.strip() for c in next(csv.reader([line.lstrip()[1:].strip()]))]
            if cols and cols[0]=='STN' and 'YYYYMMDD' in cols:
                if header is not None or len(cols)!=5 or set(cols)!=COLUMNS:
                    raise IntakeBlocked('Changed, repeated or unsupported schema; raw response preserved')
                header=cols
            continue
        if header is None:raise IntakeBlocked('Data before known schema; response preserved')
        try:tokens=next(csv.reader([line],strict=True))
        except csv.Error:raise IntakeBlocked('Malformed CSV row; response preserved') from None
        raw={k:tokens[i] if i<len(tokens) else None for i,k in enumerate(header)}
        day=source_date(raw['YYYYMMDD'] or '');hour=integer(raw['HH'] or '')
        station=integer(raw['STN'] or '')
        role=('unassigned' if day is None else 'final' if START<=day<=END else
              'context_only' if day==CONTEXT else 'excluded_boundary')
        selected=(role=='final') if scope=='final' else (role=='context_only')
        valid=day is not None and hour is not None and 1<=hour<=24
        stamp=utc_for(day,hour).isoformat() if valid else None
        key=(station,stamp)
        duplicate=key in seen if valid else None
        if valid:seen.add(key)
        states={v:cell_state(raw[v]) if selected or (scope=='final' and role=='context_only')
                else 'not_examined_outside_scope' for v in ('T','U')}
        records.append({'record_id':f'{parent}:{number}','parent_sha256':parent,'line_number':number,
            'station':station,'source_date':day.isoformat() if day else None,'source_hour':hour,
            'source_role':role,'selected':selected,'timestamp_utc':stamp,
            'time_state':'valid_hour' if valid else 'invalid_or_missing',
            'schema_state':'complete' if len(tokens)==5 else 'field_count_mismatch',
            'extra_token_count':max(0,len(tokens)-5),'cells':states,
            'structural_labels':{'Q04':not valid,'Q05':False if valid else None,'Q06':duplicate,
                'Q02':{v:None if states[v] in ('absent_field','not_examined_outside_scope') else
                       states[v]=='missing' for v in ('T','U')}}})
    if header is None:raise IntakeBlocked('No known KNMI schema')
    mapping=metadata_check(comments)
    return {'raw_sha256':parent,'mapping':mapping,'scope':scope,'records':records}


def structural_inventory(parsed):
    scope=parsed['scope'];records=parsed['records']
    start,end=(START,END) if scope=='final' else (CONTEXT,CONTEXT)
    groups=defaultdict(list)
    reasons=set()
    for r in records:
        if r['source_role']=='unassigned':reasons.add('unassigned_source_date_blocks_delivery')
        if r['selected']:
            if r['station'] not in (240,260):reasons.add('unexpected_or_invalid_station')
            if r['time_state']!='valid_hour':reasons.add('invalid_original_time_without_nominal_delivery')
            if r['schema_state']!='complete':reasons.add('row_schema_mismatch')
            if r['station'] in (240,260) and r['time_state']=='valid_hour':
                groups[r['station'],r['source_date'],r['source_hour']].append(r)
    slots=[];monthly={}
    for s in expected_schedule(start,end):
        station,day,hour=s['station'],s['source_date'],s['source_hour']
        rows=groups[station,day,hour]
        month=day[:7];key=f'{station}/{month}'
        if key not in monthly:
            monthly[key]={'station':station,'source_month':month,'expected_slots':0,
                'received_rows':0,'absent_slots':0,'duplicate_keys':0,'additional_duplicate_rows':0,
                'variables':{v:{'finite_unique_slots':0,'cell_states':{}} for v in ('T','U')}}
        m=monthly[key];m['expected_slots']+=1;m['received_rows']+=len(rows)
        m['absent_slots']+=int(not rows);m['duplicate_keys']+=int(len(rows)>1)
        m['additional_duplicate_rows']+=max(0,len(rows)-1)
        cells={}
        for v in ('T','U'):
            states=[r['cells'][v] for r in rows]
            for state in states:
                counts=m['variables'][v]['cell_states'];counts[state]=counts.get(state,0)+1
            usable=len(rows)==1 and rows[0]['schema_state']=='complete' and states[0]=='finite'
            m['variables'][v]['finite_unique_slots']+=int(usable)
            availability=True if not rows or 'missing' in states else None if 'absent_field' in states else False
            cells[v]={'structurally_usable':usable,'availability_truth':availability,
                      'numeric_truth':None,'numeric_truth_state':'pending_independent_review' if usable else 'structurally_unusable'}
        slots.append({'station':station,'source_date':day,'source_hour':hour,'timestamp_utc':s['timestamp_utc'],
            'scope':scope,'source_role':'final' if scope=='final' else 'context_only',
            'record_ids':[r['record_id'] for r in rows],'Q01_absent':not rows,'variables':cells})
    selected=[r for r in records if r['selected']]
    if not selected:reasons.add('no_selected_source_rows')
    for station in (240,260):
        if not any(r['station']==station for r in selected):reasons.add(f'no_selected_rows_station_{station}')
    # Unknown nominal delivery cannot be repaired; explicit rows remain in the record inventory.
    row_summary={}
    for r in selected:
        key=f"{r['station']}/{r['source_date'][:7] if r['source_date'] else 'unassigned'}"
        if key not in row_summary:row_summary[key]={'rows':0,'invalid_time':0,'schema_mismatch':0,'Q06_positive':0,
                                                    'cell_states':{'T':{},'U':{}}}
        q=row_summary[key];q['rows']+=1;q['invalid_time']+=int(r['time_state']!='valid_hour')
        q['schema_mismatch']+=int(r['schema_state']!='complete')
        q['Q06_positive']+=int(r['structural_labels']['Q06'] is True)
        for v in ('T','U'):
            state=r['cells'][v];q['cell_states'][v][state]=q['cell_states'][v].get(state,0)+1
    summary={'scope':scope,'source_start':start.isoformat(),'source_end':end.isoformat(),
        'expected_slots_per_station':(end-start).days*24+24,'selected_rows':len(selected),
        'preserved_rows':len(records),'roles':dict(Counter(r['source_role'] for r in records)),
        'source_mapping':parsed['mapping'],'monthly':monthly,'record_structure':row_summary,
        'blocked_reasons':sorted(reasons),'state':'blocked_structural_intake' if reasons else 'structural_inventory_complete_review_pending',
        'numeric_magnitudes_reported':False,'numeric_labels_accepted':False,'actual_scientific_freeze':False}
    return summary,slots


def compare_context(existing,new):
    """Structural overlap only. No numeric equality or changed-value display."""
    def signature(records):
        result=defaultdict(list)
        for r in records:
            if r['source_role']=='context_only':
                result[(r['station'],r['source_date'],r['source_hour'])].append(
                    (r['time_state'],r['schema_state'],r['cells']['T'],r['cells']['U']))
        return dict(result)
    old,delivered=signature(existing),signature(new)
    return {'existing_context_keys':len(old),'new_overlap_keys':len(delivered),
        'structural_match':all(k in old and v==old[k] for k,v in delivered.items()),
        'value_comparison_performed':False,'context_replacement_permitted':False,
        'policy':'Use pinned earlier context; new overlapping rows remain excluded raw evidence, not replacements'}


def review_template(intake_identity):
    return {'version':'independent-final-label-review-v1','intake_identity':intake_identity,
        'state':'pending','reviewer':None,'assistance':None,'reviewed_utc':None,
        'independent_of_detector_outputs':None,'independent_human_review':False,
        'searches':[],'evidence':[],'numeric_exclusions':[],
        'coverage':{f'{s}/{v}':{'state':'pending','scope':None,'gaps':None,'disposition':None}
                    for s in (240,260) for v in ('T','U')},
        'meaning_of_empty_lists':'Review not performed; not evidence of no faults',
        'required_search_fields':['review_id','reviewer','source_title','url_or_path','capture_utc','sha256',
            'station','variable','source_interval','supported_fact','proposed_label','rationale','uncertainty',
            'affected_subject_ids_or_mapping','disposition'],
        'forbidden_label_basis':['detector flags','residuals','numeric ranges','visual anomalies','neighbour disagreements'],
        'actual_scientific_freeze':False}
