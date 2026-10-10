"""Read back all saved synthetic copies and check preservation/lineage invariants.

This audit does not call the generator, monitor, evaluator or a data provider.
"""

import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from records import ROOT, digest, save_json, sha256, utc_now


def audit(folder):
    folder=Path(folder)
    summary=json.loads((folder/'inventory-completed.json').read_text())
    records=json.loads((folder/'constructed-inventory.json').read_text())
    expected=json.loads((ROOT/'working/qc-monitor/scenario_expected.json').read_text())
    assert len(records)==936
    assert Counter(r['family'] for r in records)==expected['annual_counts']
    checks=Counter()
    with zipfile.ZipFile(summary['public_archive']) as pub, zipfile.ZipFile(summary['private_archive']) as priv:
        for record in records:
            name=record['case_id']+'.json'
            assert digest(pub.read(name))==record['public_sha256']
            assert digest(priv.read(name))==record['private_sha256']
            rows=json.loads(pub.read(name)); private=json.loads(priv.read(name)); root=private['root']
            month=int(root['source_month'][-2:])
            control=json.loads(pub.read(f'counterpart-{month:02d}.json'))
            originals=json.loads(priv.read(private['originals_member']))
            assert digest(priv.read(private['originals_member']))==private['originals_sha256']
            # Independently reconstruct unchanged receipt fields from original observation time.
            original_public=[{k:r[k] for k in ('identity','station','timestamp','T','U')} | {'received_at':r['timestamp']} for r in originals]
            assert control==original_public
            left={r['identity']:r for r in rows};right={r['identity']:r for r in control}
            assert len(left)==len(rows)
            assert set(left)-set(right)==set(root['added_identities'])
            assert set(right)-set(left)==set(root['deleted_identities'])
            assert root['N']==root['C']+root['Z']+root['U']==root['length_hours']
            assert root['C']==len(root['actual_change_mask'])==len(set(root['actual_change_mask']))
            onset=datetime.fromisoformat(root['planned_onset'])
            intended=[(onset+timedelta(hours=i)).isoformat() for i in range(root['N'])]
            assert root['actual_change_mask']==intended # deliberately complete fabricated baseline
            assert root['planned_end']==intended[-1]
            assert root['first_effect']==intended[0] and root['last_effect']==intended[-1]
            assert all(u['status']=='changed' for u in private['unit_accounting'])
            for identity,r in right.items():
                affected=r['station']==260 and r['timestamp'] in intended
                if not affected:
                    assert left[identity]==r
                    checks['unchanged_reference_context_or_recovery_rows']+=1
                elif root['family']=='absent_row': assert identity not in left
                elif root['family']=='duplicate_receipt': assert left[identity]==r
                else:
                    altered={k for k in r if left[identity][k]!=r[k]}
                    assert altered=={root['variable'] if root['variable'] else 'timestamp'}
                    assert left[identity]['received_at']==r['received_at']
            for x in private['lineage']:
                assert x['parent_id'] in right
                if x['disposition']=='added':
                    assert {k:v for k,v in left[x['identity']].items() if k!='identity'}=={
                        k:v for k,v in right[x['parent_id']].items() if k!='identity'}
            if root['family']=='duplicate_receipt':
                assert len(root['added_identities'])==root['copies']*root['C']
                assert {s for c,s in root['members']}==set(root['added_identities'])
            if root['family']=='absent_row':assert len(root['deleted_identities'])==root['C']
            if root['family'] in {'absent_row','invalid_timestamp','off_grid_timestamp'}:
                assert len(root['consequences'])==root['C']
                assert all(x['root_id']==root['root_id'] for x in root['consequences'])
            checks['cases_verified']+=1
    answer={'recorded_utc':utc_now(),'checks':dict(checks),'public_sha256':sha256(summary['public_archive']),
            'private_sha256':sha256(summary['private_archive']),'method':'Independent saved-copy audit; no generator/replay/evaluator calls',
            'limitations':'Fully supported fabricated baseline. Partial and blocked prerequisites are covered by unit fixtures, not asserted here.'}
    save_json(ROOT/'evidence/scenarios'/('saved-inventory-audit-'+datetime.now().strftime('%Y%m%dT%H%M%S%f')+'.json'),answer)
    print(json.dumps(answer))


if __name__=='__main__':audit(sys.argv[1])
