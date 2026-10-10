"""Manual reserved-data acquisition followed ONLY by structural intake.

No detector/model/evaluator imports. Raw values are preserved but never reported.
Each attempt is create-only; no automatic retry, resume or final scientific freeze.
"""
import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import re
import shutil
import sys
import traceback
import urllib.parse
import urllib.request
import zipfile

from final_structure import (IntakeBlocked, compare_context, parse_structure,
                             review_template, structural_inventory)
from lossless_storage import within_work, inventory_files
from records import ROOT, PROJECT, code_identity, digest, save_json, sha256, utc_now

URL='https://www.daggegevens.knmi.nl/klimatologie/uurgegevens'
PARAMETERS={'stns':'240:260','vars':'T:U','start':'2024010201','end':'2024123124'}
BINDING=PROJECT/'final-intake-binding.json'
EVIDENCE=ROOT/'evidence/final-intake-2026-10-08'
MAX_RESPONSE=32*1024**2 # Operational response cap; never truncate and accept.


def resources():
    # Kept local so restricted intake never imports demo/replay via a resource helper.
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[
            (x,ctypes.c_ulonglong) for x in ('total','available','page_total','page_available',
                                           'virtual_total','virtual_available','extended')]
    m=Memory();m.length=ctypes.sizeof(m)
    ok=ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {'memory_total_bytes':m.total if ok else None,'memory_available_bytes':m.available if ok else None,
            'disk_free_bytes':shutil.disk_usage(ROOT).free,'recorded_utc':utc_now()}


def get_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify_prerequisites(now=None):
    binding=get_json(BINDING)
    if binding['version']!='restricted-final-intake-v1' or binding['performance_execution_enabled'] is not False:
        raise IntakeBlocked('Wrong intake protocol')
    for name,h in binding['bound_records'].items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or sha256(path)!=h:
            raise IntakeBlocked('Prospective record or context binding changed')
    review=get_json(ROOT/binding['source_review'])
    current=now or datetime.now(timezone.utc)
    reviewed=datetime.fromisoformat(review['reviewed_utc'])
    if not timedelta(0)<=current-reviewed<=timedelta(hours=24):
        raise IntakeBlocked('Source documentation review is older than 24 hours or future-dated; refresh its record before retrieval')
    if (review['state']!='reviewed_for_restricted_intake' or review['route']!=URL or
            review['reserved_observations_retrieved'] is not False):
        raise IntakeBlocked('Source mapping review not complete')
    return binding,review


class SameRoute(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise IntakeBlocked('Source redirected; explicit migration review required')


def fetch(url,params,folder,basename):
    """Stream untouched bytes to disk; partial transfer remains on failure."""
    folder.mkdir(parents=True,exist_ok=False)
    body=urllib.parse.urlencode(params).encode('ascii') if params else None
    request=urllib.request.Request(url,data=body,headers={
        'User-Agent':'Dissertation-QC-restricted-intake/1.0','Accept-Encoding':'identity'})
    record={'started_utc':utc_now(),'url':url,'parameters':params,'method':request.get_method(),
            'TLS_certificate_verification':True}
    partial=folder/(basename+'.partial')
    try:
        opener=urllib.request.build_opener(SameRoute())
        with opener.open(request,timeout=45) as response, partial.open('xb') as stream:
            record.update(status=response.status,resolved_url=response.url,
                          headers={k:v for k,v in response.headers.items()})
            if response.status!=200 or response.url!=url:
                raise IntakeBlocked('Unexpected source route or HTTP status')
            if response.headers.get('Content-Encoding','identity').lower() not in ('','identity'):
                raise IntakeBlocked('Unexpected transfer encoding')
            count=0
            while chunk:=response.read(65536):
                stream.write(chunk);count+=len(chunk)
                if count>MAX_RESPONSE:raise IntakeBlocked('Response exceeds prepared operational size cap')
            declared=response.headers.get('Content-Length')
            if declared is not None and (not declared.isdigit() or int(declared)!=count):
                raise IntakeBlocked('Response length differs from HTTP declaration; partial bytes retained')
        destination=folder/basename;partial.rename(destination)
        record.update(finished_utc=utc_now(),state='downloaded_not_accepted',
                      file=basename,bytes=destination.stat().st_size,sha256=sha256(destination))
        save_json(folder/'manifest.json',record)
        return destination
    except BaseException as error:
        record.update(finished_utc=utc_now(),state='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
                      error_type=type(error).__name__,reason='Source transfer failed; partial bytes preserved')
        if partial.exists():record.update(partial_file=partial.name,partial_bytes=partial.stat().st_size,partial_sha256=sha256(partial))
        save_json(folder/'manifest.json',record)
        raise


def selection_check(payload):
    text=payload.decode('utf-8',errors='replace').casefold()
    # Verify the currently served selection form, not its dynamic whole-page hash.
    plain=re.sub('<[^>]+>',' ',text)
    plain=' '.join(plain.split())
    expected=('0.1 graden celsius','1.50 m','relatieve vochtigheid','procenten','schiphol','de bilt')
    if any(x not in plain for x in expected):
        raise IntakeBlocked('Current selection definitions differ from the reviewed mapping')
    if not all(re.search(r'name\s*=\s*["\x27]'+re.escape(x)+r'["\x27]',text)
               for x in ('vars[t]','vars[u]','stns[240]','stns[260]')):
        raise IntakeBlocked('Current selection variable/station identifiers changed')


def write_jsonl(path,rows):
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')


def process_response(raw_path,context_path,folder):
    """Read raw bytes without exposing values; context is pinned separately."""
    parsed=parse_structure(raw_path.read_bytes())
    context=parse_structure(context_path.read_bytes(),scope='context')
    summary,slots=structural_inventory(parsed)
    context_summary,context_slots=structural_inventory(context)
    overlap=compare_context(context['records'],parsed['records'])
    if context_summary['blocked_reasons'] or context_summary['expected_slots_per_station']!=24:
        summary['blocked_reasons'].append('pinned_context_structural_problem')
    if any(m['absent_slots'] or m['duplicate_keys'] for m in context_summary['monthly'].values()):
        summary['blocked_reasons'].append('pinned_context_not_unique_complete_schedule')
    if not overlap['structural_match']:summary['blocked_reasons'].append('new_context_overlap_structure_differs')
    if summary['blocked_reasons']:summary['state']='blocked_structural_intake'
    folder.mkdir()
    write_jsonl(folder/'records-structure.jsonl',parsed['records'])
    write_jsonl(folder/'slots-structure.jsonl',slots)
    write_jsonl(folder/'context-structure.jsonl',[r for r in context['records'] if r['selected']])
    write_jsonl(folder/'context-slots.jsonl',context_slots)
    save_json(folder/'structural-summary.json',summary)
    save_json(folder/'context-summary.json',context_summary)
    save_json(folder/'context-overlap.json',overlap)
    return summary


def run(folder,downloader=fetch):
    folder=within_work(folder);folder.mkdir(parents=True,exist_ok=False)
    save_json(folder/'started.json',{'utc':utc_now(),'command':sys.argv,'code':code_identity(),
        'python':sys.version,'platform':platform.platform(),'scope':'restricted structural intake only',
        'input_origin':'manual_network' if downloader is fetch else 'fabricated_injected_downloader',
        'performance_execution':False,'actual_scientific_freeze':False})
    try:
        r=resources();save_json(folder/'resource-preflight.json',r)
        if r['memory_available_bytes'] is None or r['memory_available_bytes']<256*1024**2 or r['disk_free_bytes']<1024**3:
            raise IntakeBlocked('Requires 256 MiB available RAM and 1 GiB disk; no request sent')
        binding,review=verify_prerequisites()
        save_json(folder/'bindings.json',{'protocol_sha256':sha256(BINDING),'protocol':binding,'source_review':review})
        with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(PROJECT.glob('*.py')):archive.write(p,p.name)
            archive.write(BINDING,BINDING.name)
            archive.write(PROJECT/'requirements.txt','requirements.txt')
        print('SOURCE_CHECK: current selection route; no observations requested yet',flush=True)
        selection=downloader(URL,None,folder/'selection','response.html')
        selection_check(selection.read_bytes())
        save_json(folder/'selection-reviewed.json',{'state':'mapping_matches','sha256':sha256(selection),
            'source_review_sha256':sha256(ROOT/binding['source_review']),
            'documentation_capture_limitation':review['capture_limitation']})
        # Only this manual action sends the reserved-source request.
        print('ACQUIRING: fixed stations 240/260, T/U, source 2024-01-02 through 2024-12-31; raw bytes only',flush=True)
        raw=downloader(URL,dict(PARAMETERS),folder/'raw','response.txt')
        print('STRUCTURE: source roles, schedule, cells and duplicates; no value summaries',flush=True)
        context=ROOT/binding['context_raw']
        if sha256(context)!=binding['bound_records'][binding['context_raw']]:
            raise IntakeBlocked('Context changed')
        summary=process_response(raw,context,folder/'structure')
        identity={'raw_sha256':sha256(raw),'structural_summary_sha256':sha256(folder/'structure/structural-summary.json'),
                  'context_sha256':sha256(context),'protocol_sha256':sha256(BINDING)}
        save_json(folder/'review-template.json',review_template(identity))
        before=get_json(folder/'started.json')
        verify_prerequisites()
        if before['code']!=code_identity() or sha256(BINDING)!=get_json(folder/'bindings.json')['protocol_sha256']:
            raise IntakeBlocked('Code or protocol changed during intake')
        save_json(folder/'completed.json',{'state':summary['state'],'files':inventory_files(folder),
            'independent_label_review':'pending','data_accepted_for_performance':False,
            'actual_scientific_freeze':False,'blocked_reasons':summary['blocked_reasons'],
            'resources_after':resources()})
        inspect(folder)
        print(summary['state'].upper()+': '+str(folder),flush=True)
        return 3 if summary['blocked_reasons'] else 0
    except BaseException as error:
        state='interrupted' if isinstance(error,KeyboardInterrupt) else 'blocked' if isinstance(error,IntakeBlocked) else 'failed'
        # Never log untrusted payload fragments or exception source lines containing observations.
        safe_reason=str(error) if isinstance(error,IntakeBlocked) else 'Execution/transport error; inspect preserved metadata'
        save_json(folder/'failed.json',{'state':state,'error_type':type(error).__name__,'reason':safe_reason,
            'traceback_locations':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                                   for f in traceback.extract_tb(error.__traceback__)],
            'actual_scientific_freeze':False,'data_accepted_for_performance':False})
        print(state.upper()+': '+safe_reason+'; '+str(folder),flush=True)
        return 2 if state=='interrupted' else 3 if state=='blocked' else 1


def inspect(folder):
    folder=within_work(folder)
    if (folder/'failed.json').exists():
        failed=get_json(folder/'failed.json')
        print(json.dumps({'verified_completion':False,'state':failed['state'],'reason':failed['reason']}),flush=True)
        return False
    done=get_json(folder/'completed.json')
    if done['state'] not in ('structural_inventory_complete_review_pending','blocked_structural_intake'):
        raise IntakeBlocked('Unknown completion state')
    required={'started.json','bindings.json','resource-preflight.json','source.zip','selection/manifest.json',
        'selection/response.html','selection-reviewed.json','raw/manifest.json','raw/response.txt',
        'structure/records-structure.jsonl','structure/slots-structure.jsonl','structure/structural-summary.json',
        'structure/context-structure.jsonl','structure/context-slots.jsonl','structure/context-summary.json',
        'structure/context-overlap.json','review-template.json'}
    if set(done['files'])!=required:raise IntakeBlocked('Incomplete output roster')
    for name,item in done['files'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder) or sha256(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:
            raise IntakeBlocked('Saved intake output changed')
    summary=get_json(folder/'structure/structural-summary.json')
    if done['state']!=summary['state'] or summary['expected_slots_per_station']!=8760:
        raise IntakeBlocked('Summary state/schedule mismatch')
    if done['data_accepted_for_performance'] is not False or done['actual_scientific_freeze'] is not False:
        raise IntakeBlocked('Intake cannot confer scientific freeze')
    raw_manifest=get_json(folder/'raw/manifest.json')
    if raw_manifest['sha256']!=sha256(folder/'raw/response.txt') or raw_manifest['bytes']!=(folder/'raw/response.txt').stat().st_size:
        raise IntakeBlocked('Raw response identity disagrees with acquisition')
    template=get_json(folder/'review-template.json')
    if template['state']!='pending' or template['intake_identity']['raw_sha256']!=raw_manifest['sha256']:
        raise IntakeBlocked('Original pending review template changed or mismatched')
    print(json.dumps({'verified_completion':True,'state':done['state'],
        'input_origin':get_json(folder/'started.json')['input_origin'],
        'expected_slots_per_station':8760,'selected_rows':summary['selected_rows'],
        'blocked_reasons':summary['blocked_reasons'],'independent_label_review':'pending',
        'actual_scientific_freeze':False}),flush=True)
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('acquire','status'));p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.action=='status':
        if a.out is None:p.error('status requires --out')
        return 0 if inspect(a.out) else 1
    folder=a.out or EVIDENCE/('manual-intake-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    print('OUTPUT: '+str(folder),flush=True)
    return run(folder)


if __name__=='__main__':raise SystemExit(main())
