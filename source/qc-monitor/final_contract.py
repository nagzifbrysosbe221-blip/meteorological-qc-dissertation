"""Create-only D2 freeze: identities and schedules, never observation magnitudes.

The old intake/replay records remain historical. This separate gate grants only
the fixed final study; it cannot make U eligible or erase previous exposure.
"""
import calendar
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import sys
import zipfile

from records import ROOT, PROJECT, save_json, sha256, code_identity, utc_now
from lossless_storage import within_work, inventory_files
from scenarios import inventory
from scientific_replay import load_frozen, PASSES, identity, verified_binding

INTAKE = ROOT/'evidence/final-intake-2026-10-08/manual-intake-20261008T073302044512Z'
REVIEW = ROOT/'evidence/final-label-review-2026-10-08/v1'
CONTEXT = ROOT/'data/raw/knmi-2023-20261007T172339608278Z/response.txt'
REVIEW_HASH = '4af95ad59c372420a83e70e43ca8393e15662a9d7f1f6879a44ebad430d93c30'
EVIDENCE = ROOT/'evidence/final-controller-2026-10-08'
STREAMS = ('ledger', 'states', 'summaries', 'receipt_audit')
LABELS = {'T_original_A2_eligible': True, 'U_original_A2_eligible': False,
          'U_exploratory': True, 'revision': 'R1-approved-v1',
          'validation_original_state': 'diagnosis_required',
          'validation_disposition': 'negative_U_retained_unchanged',
          'normal_truth': 'assumed_normal_not_certified',
          'independent_human_review': False, 'newly_untouched_holdout': False}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def lines(path):
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def verify_files(folder, files):
    for name, item in files.items():
        p = (folder/name).resolve()
        if not p.is_relative_to(folder.resolve()) or p.is_symlink():
            raise ValueError('Unsafe evidence path')
        if sha256(p) != item['sha256'] or p.stat().st_size != item['bytes']:
            raise ValueError('Evidence changed: '+str(p))


def roster():
    # Both 2000 and 2024 are leap years; replace the declared source year only.
    return [{**c, 'case_id': c['case_id'].replace('synthetic-2000', 'final-2024'),
             'source_month': c['source_month'].replace('2000', '2024'),
             'planned_onset': c['planned_onset'].replace('2000', '2024')}
            for c in inventory()]


def window(month=None, year=2024):
    first = datetime(year, month or 1, 2 if month in (None, 1) else 1, 1, tzinfo=timezone.utc)
    end_month = month or 12
    last = datetime(year, end_month, calendar.monthrange(year, end_month)[1], tzinfo=timezone.utc)+timedelta(days=1)
    start = max(first-timedelta(days=14), datetime(year, 1, 1, 1, tzinfo=timezone.utc))
    slots = [(start+timedelta(hours=i)).isoformat() for i in range(int((last-start).total_seconds()/3600)+1)]
    return {'slots': slots, 'first_scored': first.isoformat(), 'last_scored': last.isoformat(),
            'drain_until': (last+timedelta(hours=1)).isoformat(),
            'context_hours': int((first-start).total_seconds()/3600),
            'scored_hours': int((last-first).total_seconds()/3600)+1,
            'kind': 'continuous' if month is None else 'source_month'}


def design():
    cases = roster()
    if len(cases) != 936 or Counter(c['source_month'] for c in cases) != {f'2024-{m:02d}':78 for m in range(1,13)}:
        raise ValueError('Incomplete case roster')
    return {'cases': cases, 'passes': {p: {'alpha': a, 'T_bounds': list(b)} for p,(a,b) in PASSES.items()},
            'configurations': ['B0','R','H'], 'variables': ['T','U'],
            'windows': {str(m):window(m) for m in range(1,13)}, 'continuous': window(),
            'seed': None, 'seed_reason': 'not applicable; deterministic',
            'pilot_case': next(c['case_id'] for c in cases if c['source_month']=='2024-03'
                and c['family']=='gradual_bias' and c['variable']=='T' and c['sign']==1
                and c['scale_multiple']==2 and c['ramp_hours']==24),
            'pilot_jobs': 'primary continuous background, then prescribed March T +2-scale 24h ramp/24h hold pair',
            'scoring': 'submitted A4/C1-C6; saved complete predictions; raw episodes before private labels',
            'exposure': 'one continuous background per pass; counterparts contribute zero primary exposure',
            'timing': 'equal-time arrivals before closure at +5min; 1h drain; no extra scored slots',
            'failure_states': ['pending','blocked','generation_error','no_effect','effective','failed','interrupted','complete']}


def accepted_evidence():
    """Verify immutable review/intake and scientific chain without reading values."""
    if sha256(REVIEW/'review.json') != REVIEW_HASH:
        raise ValueError('Accepted review identity changed')
    review, audit, handoff = [read(REVIEW/n) for n in ('review.json','audit.json','handoff.json')]
    verify_files(REVIEW, handoff['files'])
    if (review['state'] != 'bounded_review_complete_with_limitations' or
            review['independent_of_detector_outputs'] is not True or audit['passed'] is not True or
            audit['labels_sha256'] != sha256(REVIEW/'numeric-labels.jsonl') or
            audit['review_sha256'] != REVIEW_HASH or len(review['coverage']) != 4 or
            any(x['state'] != 'reviewed_with_documentation_gaps' for x in review['coverage'].values())):
        raise ValueError('Review incomplete or inconsistent')
    done = read(INTAKE/'completed.json')
    if (sha256(INTAKE/'completed.json') != review['original_intake_completion_sha256'] or
            done['state'] != 'structural_inventory_complete_review_pending' or done['blocked_reasons']):
        raise ValueError('Original intake identity/state changed')
    verify_files(INTAKE, done['files'])
    if (sha256(CONTEXT) != review['intake_identity']['context_sha256'] or
            sha256(INTAKE/'raw/response.txt') != review['intake_identity']['raw_sha256']):
        raise ValueError('Accepted raw/context changed')
    paths = set()
    def add(path): paths.add(Path(path).resolve())
    for p in (REVIEW/'handoff.json', INTAKE/'completed.json', CONTEXT): add(p)
    for name in handoff['files']: add(REVIEW/name)
    for name in done['files']: add(INTAKE/name)
    for name,h in verified_binding()['bound_evidence'].items():
        add(ROOT/name)
    binding = read(PROJECT/'final-intake-binding.json')
    for name,h in binding['bound_records'].items():
        if sha256(ROOT/name) != h: raise ValueError('Prospective input changed')
        add(ROOT/name)
    early = read(ROOT/'evidence/early-acceptance-2026-10-08/acceptance.json')
    for snapshot in early['accepted_snapshots']:
        for name, expected in [('response.txt',snapshot['raw_sha256']),('manifest.json',snapshot['raw_manifest_sha256'])]:
            path=ROOT/snapshot['snapshot']/name
            if sha256(path)!=expected: raise ValueError('Accepted early snapshot changed')
            add(path)
        view=ROOT/snapshot['view'];verify_files(view,snapshot['view_files'])
        if sha256(view/'manifest.json')!=snapshot['view_manifest_sha256']:raise ValueError('Early derivation changed')
        add(view/'manifest.json')
        for name in snapshot['view_files']:add(view/name)
    original = ROOT.parent/'Chapters-1-to-3-Supervisor-Review/v2/Chapters-1-to-3-Supervisor-Review-v2.pdf'
    if sha256(original)!=sha256(ROOT/'reference/Chapters-1-to-3-Supervisor-Review-v2.pdf'):
        raise ValueError('Submitted original differs from preserved authority')
    audit_folder=EVIDENCE/'saved-fixture-audit-v1'
    fixture_audit=read(audit_folder/'audit.json')
    if (fixture_audit.get('passed') is not True or fixture_audit.get('production_imports') is not False or
            fixture_audit.get('threshold_decisions_require_exact_equality') is not True or
            fixture_audit['source_sha256']!=sha256(audit_folder/'audit_saved.py')):
        raise ValueError('Independent fabricated readback audit absent or changed')
    add(audit_folder/'audit.json');add(audit_folder/'audit_saved.py')
    for name,h in fixture_audit['inputs'].items():
        p=within_work(name)
        if sha256(p)!=h:raise ValueError('Audited fabricated payload changed')
        add(p)
    # Include complete saved fits/gates, calibration, validation, and permission.
    approval = read(ROOT/'evidence/r1-calibration-2026-10-08/approval.json')
    add(ROOT/'evidence/humidity-revision-proposal-2026-10-08/proposal.json')
    folders = [ROOT/approval['original_run'],
        ROOT/'evidence/r1-calibration-2026-10-08/manual-A3-20261007T210253930632Z',
        ROOT/'evidence/r1-validation-2026-10-08/manual-2023-20261007T211804852813Z',
        ROOT/'evidence/r1-validation-2026-10-08/completed-validation-review']
    for folder in folders:
        for p in folder.rglob('*'):
            if p.is_file(): add(p)
    settings = {}
    for pass_id in PASSES:
        chosen, bounds, proof = load_frozen(pass_id)
        settings[pass_id] = {'settings': {v:asdict(s) for v,s in chosen.items()}, 'proof':proof,
                             'bounds':{v:list(b) for v,b in bounds.items()}}
    return {p.relative_to(ROOT).as_posix():{'sha256':sha256(p),'bytes':p.stat().st_size} for p in sorted(paths)}, settings


def environment():
    import numpy
    package = Path(numpy.__file__).parent
    binaries = {str(p.relative_to(package.parent)):sha256(p) for folder in (package, package.parent/'numpy.libs')
                for p in sorted(folder.rglob('*')) if p.suffix.lower() in ('.pyd','.dll')}
    base = Path(sys.base_prefix)
    return {'python':sys.version,'executable':str(Path(sys.executable).resolve()),
            'executable_sha256':sha256(sys.executable),'numpy':numpy.__version__,
            'numerical_binaries':binaries, 'python_dlls':{p.name:sha256(p) for p in sorted(base.glob('python*.dll'))},
            'platform':platform.platform(),'requirements_sha256':sha256(PROJECT/'requirements.txt'),
            'threads':{'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}}


def implementation():
    return code_identity() | {p.name:sha256(p) for p in sorted(PROJECT.glob('*.json'))} | {
        'requirements.txt':sha256(PROJECT/'requirements.txt')}


def create_freeze(folder, test_result):
    """Metadata-only actual freeze. A failed attempt never publishes freeze.json."""
    folder = within_work(folder); folder.mkdir(parents=True, exist_ok=False)
    save_json(folder/'started.json', {'utc':utc_now(),'action':'actual scientific freeze; no performance execution'})
    try:
        tests = read(test_result)
        if (tests.get('success') is not True or tests.get('scope') != 'test_*.py' or
                tests.get('code') != code_identity() or tests.get('tests_run',0) < 257 or
                any(tests.get(k) != 0 for k in ('failures','errors','skipped'))):
            raise ValueError('A clean current full suite is required')
        files, settings = accepted_evidence()
        test_result = within_work(test_result)
        files[test_result.relative_to(ROOT).as_posix()] = {'sha256':sha256(test_result),'bytes':test_result.stat().st_size}
        save_json(folder/'design.json', design())
        save_json(folder/'settings.json', settings)
        with zipfile.ZipFile(folder/'source.zip','x',zipfile.ZIP_DEFLATED) as archive:
            for name in implementation(): archive.write(PROJECT/name,name)
        freeze = {'version':'final-scientific-freeze-v1','state':'frozen','actual_scientific_freeze':True,
                  'recorded_utc':utc_now(),'code':implementation(),'environment':environment(),
                  'accepted_files':files,'labels':LABELS,'local_files':inventory_files(folder),
                  'final_observation_performance_opened':False,
                  'limitations':['incomplete station histories','assumed normal, not certified healthy',
                    'prior public-summary exposure'],
                  'manual_processing_only':True}
        save_json(folder/'freeze.json', freeze)
        verify_freeze(folder)
        return freeze
    except BaseException as exc:
        save_json(folder/'failed.json', {'state':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
                  'error':repr(exc),'utc':utc_now()})
        raise


def verify_freeze(folder):
    folder = within_work(folder)
    f = read(folder/'freeze.json')
    if ((folder/'failed.json').exists() or f.get('version') != 'final-scientific-freeze-v1' or
            f.get('state') != 'frozen' or f.get('actual_scientific_freeze') is not True or f.get('labels') != LABELS):
        raise ValueError('No valid actual scientific freeze')
    if f['code'] != implementation() or f['environment'] != environment():
        raise ValueError('Implementation/environment changed; preserve freeze and account for affected results')
    verify_files(folder, f['local_files']); verify_files(ROOT, f['accepted_files'])
    if read(folder/'design.json') != design(): raise ValueError('Frozen design differs')
    return f
