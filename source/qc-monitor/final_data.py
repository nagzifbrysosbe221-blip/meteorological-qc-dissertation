"""Post-freeze value reader; never used during intake or freeze preparation."""
import csv
from datetime import datetime, timedelta
from data import numeric_cell
from records import sha256


def originals_from_inventory(raw_path, structural_rows):
    """Read only original selected lines. Roles are fixed before HH24 conversion."""
    parent = sha256(raw_path)
    with raw_path.open(encoding='utf-8-sig') as stream:
        raw_lines = stream.read().splitlines()
    header = None
    for line in raw_lines:
        if line.lstrip().startswith('#'):
            cols = [c.strip() for c in next(csv.reader([line.lstrip()[1:].strip()]))]
            if cols and cols[0]=='STN' and 'YYYYMMDD' in cols: header=cols
    if header is None or set(header) != {'STN','YYYYMMDD','HH','T','U'}:
        raise ValueError('Unknown bound source schema')
    result = []
    for row in structural_rows:
        if not row['selected']: continue
        if row['source_role'] not in ('final','context_only'):
            raise ValueError('Early/boundary rows cannot enter final replay')
        if row['parent_sha256'] != parent or row['record_id'] != f"{parent}:{row['line_number']}":
            raise ValueError('Source identity mismatch')
        tokens = next(csv.reader([raw_lines[row['line_number']-1]]))
        fields = dict(zip(header,tokens,strict=True))
        day = datetime.strptime(fields['YYYYMMDD'].strip(),'%Y%m%d').date()
        expected_role=('context_only' if day.isoformat()=='2024-01-01' else
                       'final' if '2024-01-02'<=day.isoformat()<='2024-12-31' else 'excluded')
        if row['source_role']!=expected_role:
            raise ValueError('Original source date conflicts with final/context role')
        hour, station = int(fields['HH']), int(fields['STN'])
        stamp = datetime.fromisoformat(row['timestamp_utc'])
        if (day.isoformat()!=row['source_date'] or hour!=row['source_hour'] or station!=row['station'] or
                not 1<=hour<=24 or stamp.replace(tzinfo=None)!=datetime.combine(day,datetime.min.time())+timedelta(hours=hour)):
            raise ValueError('Original nominal delivery/source mapping unresolved')
        values = {}
        for v, divisor in (('T',10),('U',1)):
            cell = numeric_cell(fields[v],divisor)
            if cell['state'] != row['cells'][v]: raise ValueError('Saved structural state mismatch')
            values[v] = repr(cell['value']) if cell['state']=='finite' else fields[v]
        result.append({'identity':row['record_id'],'station':station,'source_date':day.isoformat(),
            'source_hour':hour,'timestamp':row['timestamp_utc'],'nominal_delivery':row['timestamp_utc'],**values})
    return result


def load_originals(freeze_folder, window):
    from final_contract import verify_freeze, INTAKE, CONTEXT, lines
    verify_freeze(freeze_folder)  # Must complete before raw values can be opened.
    rows = originals_from_inventory(INTAKE/'raw/response.txt', lines(INTAKE/'structure/records-structure.jsonl'))
    rows += originals_from_inventory(CONTEXT, lines(INTAKE/'structure/context-structure.jsonl'))
    slots = set(window['slots'])
    result = [r for r in rows if r['timestamp'] in slots]
    if len({r['identity'] for r in result}) != len(result): raise ValueError('Overlapping original identities')
    return sorted(result,key=lambda r:(r['nominal_delivery'],r['station'],r['identity']))
