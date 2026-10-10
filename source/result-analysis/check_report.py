"""Streaming table/export readback; no browser or detector calls."""
import csv
import hashlib
import json
from itertools import zip_longest
from pathlib import Path
from evidence import read,require,sha


def check(folder):
    folder=Path(folder);results=[]
    for item in read(folder/'table-inventory.json'):
        name=item['name'];total=0;combined=hashlib.sha256();combined.update(b'[');scripts={}
        # Large contribution cells retain complete exact source identities.
        old=csv.field_size_limit();csv.field_size_limit(20*1024*1024)
        try:
            with (folder/'tables'/item['jsonl']).open(encoding='utf-8') as source,(folder/'tables'/item['csv']).open(encoding='utf-8',newline='') as cf:
                reader=csv.DictReader(cf)
                for line,cr in zip_longest(source,reader):
                    require(line is not None and cr is not None,'Export row count differs')
                    row=json.loads(line);require(json.loads(cr['record_json'])==row,'CSV record lost data/types')
                    require(row['row_id']==name+':'+str(total+1),'Missing/duplicate/reordered table row')
                    for k in cr:
                        if k!='record_json':require(json.loads(cr[k])==row.get(k),'Promoted CSV value differs')
                    text=line.rstrip('\r\n');combined.update(((',' if total else '')+text).encode());total+=1
                    p=row.get('pass','all')
                    if p not in scripts:
                        h=hashlib.sha256();h.update(('window.loadTable('+json.dumps(name)+','+json.dumps(p)+',[').encode());scripts[p]=[h,0]
                    h,n=scripts[p];h.update(((',' if n else '')+text.replace('<','\\u003c')).encode());scripts[p][1]+=1
        finally:csv.field_size_limit(old)
        combined.update(b']\n');require(combined.hexdigest()==sha(folder/'tables'/item['json']),'Full JSON differs from JSONL')
        for p,(h,n) in scripts.items():
            h.update(b']);\n');require(h.hexdigest()==sha(folder/'tables'/(name+'--'+p+'.js')),'Browser data differs from canonical export')
        require(total==item['rows'],'Inventory count differs')
        results.append(dict(table=name,rows=total,json_jsonl_csv_browser_equal=True))
        print('EXPORT_READBACK '+name+' '+str(total),flush=True)
    figure_index=read(folder/'figures/index.json')
    require({f['group'] for f in figure_index}=={1,2,3,4,5,6},'Six figure groups required')
    for f in figure_index:
        for k in ('png','svg','data'):require((folder/'figures'/f[k]).is_file(),'Missing figure/data file')
    return dict(passed=True,scope='Streaming table/export equality and figure-file inventory; not live browser QA',tables=results,
                figures=len(figure_index),display_groups=6)


if __name__=='__main__':
    import sys
    from evidence import save
    result=check(Path(sys.argv[1]));save(Path(sys.argv[2]),result)
