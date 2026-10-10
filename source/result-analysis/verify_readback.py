"""Bounded read-only check: four saved backgrounds and first planned case per family."""
from collections import Counter
import time
from evidence import ROOT,STUDY,read,save,sha,token,resources,code_identity,require
from extract import extract_job

out=ROOT/'evidence/result-analysis-2026-10-09'/('bounded-family-readback-'+token());out.mkdir(parents=True)
plan=read(STUDY/'plan.json');chosen=[j for j in plan['jobs'] if j['kind']=='background'];seen=set()
for j in plan['jobs']:
    if j['kind']=='pair' and j['pass']=='primary' and j['case']['family'] not in seen:
        chosen.append(j);seen.add(j['case']['family'])
save(out/'started.json',dict(selection='four full-study backgrounds plus first planned primary case per family; fixed before opening outcomes',
    jobs=chosen,resources=resources(),code=code_identity(),experimental_rerun=False))
start=time.perf_counter();results=[]
try:
    for i,j in enumerate(chosen,1):
        print(f'BOUNDED_SAVED_READ {i}/{len(chosen)} {j["job_id"]}',flush=True)
        marker=STUDY/'completed'/(j['job_id']+'.json');r=extract_job(read(marker));r['marker_sha256']=sha(marker)
        save(out/(j['job_id']+'.json'),r)
        results.append(dict(job_id=j['job_id'],contribution_sha256=sha(out/(j['job_id']+'.json')),
            consumed_public_members=len(r['sources']['public']['verified_members']),consumed_private_members=len(r['sources']['private']['verified_members'])))
    require(len(results)==11,'Bounded roster differs')
    save(out/'audit.json',dict(passed=True,scope='11 bounded saved-output readbacks; not complete aggregation or experimental reruns',
        seconds=time.perf_counter()-start,results=results,resources=resources(),code=code_identity()))
    print('BOUNDED_SAVED_AUDIT '+str(out/'audit.json'))
except BaseException as exc:
    save(out/'failed.json',dict(error=repr(exc),seconds=time.perf_counter()-start));raise
