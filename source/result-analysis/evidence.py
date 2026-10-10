"""Read-only provenance and selected-member verification. No scientific imports."""
from collections import Counter
from datetime import datetime, timezone
import ctypes
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STUDY = ROOT/'evidence/final-controller-2026-10-08/manual-full-v1'
FREEZE = ROOT/'evidence/final-controller-2026-10-08/freeze-v2'
PASSES = ('primary', 'alpha-0005', 'alpha-002', 'T-range')
CONFIGS = ('B0', 'R', 'H')
MONTHS = tuple(f'2024-{m:02d}' for m in range(1, 13))
LABELS = dict(T_original_A2_eligible=True, U_original_A2_eligible=False,
    U_exploratory=True, revision='R1-approved-v1', validation_original_state='diagnosis_required',
    validation_disposition='negative_U_retained_unchanged', normal_truth='assumed_normal_not_certified',
    independent_human_review=False, newly_untouched_holdout=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def token():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    require(path.resolve().is_relative_to(ROOT.resolve()), 'Output outside completion work')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=True, allow_nan=False, indent=2)
        f.write('\n')


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def code_identity():
    return {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.suffix in ('.py', '.html', '.js', '.md', '.txt')}


def resources():
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total', 'page_available',
                                                  'virtual_total', 'virtual_available', 'extended')]
    m = Memory(); m.length = ctypes.sizeof(m)
    available = total = None
    if hasattr(ctypes, 'windll') and ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        available, total = m.available, m.total
    return dict(utc=now(), memory_available_bytes=available, memory_total_bytes=total,
                disk_free_bytes=shutil.disk_usage(ROOT).free)


def check_files(base, files):
    for name, item in files.items():
        p = (base/name).resolve()
        require(p.is_relative_to(base.resolve()) and not p.is_symlink(), 'Unsafe evidence path')
        require(p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], 'Changed evidence: '+str(p))


def verify_settings(settings):
    primary = settings['primary']
    for v, cutoff in [('T', 2.4816976238219928), ('U', 2.057779719375828)]:
        s = primary['settings'][v]
        require(s['weight'] == .20 and s['cutoff'] == cutoff, 'Primary settings differ')
    for pass_id in PASSES:
        s = settings[pass_id]
        for v in ('T', 'U'):
            a, b = primary['settings'][v], s['settings'][v]
            changes = {k for k in a if a[k] != b[k]}
            allowed = {'alpha', 'cutoff'} if pass_id.startswith('alpha-') else set()
            require(changes <= allowed, 'Unprescribed scientific sensitivity')
            alpha = {'primary': .01, 'alpha-0005': .005, 'alpha-002': .02, 'T-range': .01}[pass_id]
            require(b['alpha'] == alpha, 'Wrong sensitivity alpha')
        require(s['bounds'] == {'T': [-30., 45.] if pass_id == 'T-range' else [-40., 50.],
                                'U': [0., 100.]}, 'Wrong bounds')


def metadata_audit(progress=print):
    """Small-file audit; never opens all archive payloads or calls controller status."""
    plan = read(STUDY/'plan.json'); ph = sha(STUDY/'plan.json')
    freeze = read(FREEZE/'freeze.json'); fh = sha(FREEZE/'freeze.json')
    require(read(STUDY/'plan-binding.json')['plan_sha256'] == ph, 'Plan binding changed')
    require(plan['scope'] == 'full' and plan['freeze_sha256'] == fh and
            Path(plan['freeze']).resolve() == FREEZE.resolve(), 'Wrong canonical study/freeze')
    require(plan['labels'] == freeze['labels'] == LABELS and freeze['state'] == 'frozen', 'Labels/freeze differ')
    progress('AUDIT frozen source and accepted evidence (no archive scan)', flush=True)
    project = ROOT/'working/qc-monitor'
    actual = {p.name: sha(p) for p in project.iterdir() if p.suffix in ('.py', '.json') or p.name == 'requirements.txt'}
    require(actual == freeze['code'], 'Frozen implementation changed')
    check_files(FREEZE, freeze['local_files']); check_files(ROOT, freeze['accepted_files'])
    design = read(FREEZE/'design.json'); settings = read(FREEZE/'settings.json'); verify_settings(settings)
    cases = design['cases']; expected = []
    require(len(cases) == len({c['case_id'] for c in cases}) == 936, 'Case roster differs')
    require(Counter(c['source_month'] for c in cases) == dict.fromkeys(MONTHS, 78), 'Monthly roster differs')
    require(Counter(c['family'] for c in cases) == dict(missing_cell=96, absent_row=48,
        duplicate_receipt=72, off_grid_timestamp=72, invalid_timestamp=72, out_of_range=144, gradual_bias=432), 'Family roster differs')
    for pass_id in PASSES:
        expected.append(dict(job_id=pass_id+'-background', kind='background', **{'pass': pass_id}))
        expected.extend(dict(job_id=pass_id+'-'+c['case_id'], kind='pair', **{'pass': pass_id}, case=c) for c in cases)
    require(plan['jobs'] == expected, 'Full plan not identical to frozen roster')
    require({p.stem for p in (STUDY/'completed').glob('*.json')} == {j['job_id'] for j in expected}, 'Missing/extra completion markers')
    entries = []; construction = Counter(); total_bytes = 0
    for i, j in enumerate(expected, 1):
        marker = STUDY/'completed'/(j['job_id']+'.json'); d = read(marker)
        require(d['state'] == 'complete' and d['job'] == j and d['plan_sha256'] == ph and
                d['freeze_sha256'] == fh and d['labels'] == LABELS, 'Misbound completion '+j['job_id'])
        construction[d['construction']] += 1
        for side in ('public', 'private'):
            archive = Path(d[side+'_archive']).resolve()
            parent = STUDY/'attempts'/j['job_id'] if side == 'public' else ROOT/'data/final-private'/STUDY.name/j['job_id']
            require(archive.is_relative_to(parent.resolve()) and archive.stat().st_size == d[side]['bytes'], 'Archive missing/size changed')
            require(d[side]['verified_readback'] is True, 'Original verification absent')
            total_bytes += d[side]['bytes']
        started_path = Path(d['public_archive']).parent/'started.json'
        started = read(started_path)
        require(started['plan_sha256'] == ph and freeze['recorded_utc'] < started['utc'] < d['utc'], 'Freeze/run ordering differs')
        entries.append(dict(job_id=j['job_id'], marker_sha256=sha(marker), construction=d['construction'],
            public_archive=d['public_archive'], private_archive=d['private_archive'],
            public_sha256=d['public']['sha256'], private_sha256=d['private']['sha256'], utc=d['utc']))
        if i % 250 == 0: progress(f'AUDIT markers {i}/3748', flush=True)
    saved = read(STUDY/'progress.json')
    require(saved['state'] == 'complete' and saved['verified_jobs'] == saved['planned_jobs'] == 3748 and
            saved['pending_jobs'] == 0 and saved['construction'] == dict(construction) and saved['labels'] == LABELS,
            'Saved controller completion disagrees')
    events = []; event_counts = Counter()
    for i,p in enumerate((STUDY/'events').glob('*.json'),1):
        e = read(p); event_counts[e['state']] += 1
        if e['state'] in ('blocked', 'failed', 'interrupted', 'incomplete', 'complete', 'verified'):
            events.append(dict(path=str(p.relative_to(ROOT)), sha256=sha(p), record=e))
        if i%1000==0:progress(f'AUDIT execution-history records {i}',flush=True)
    completed_attempts = {Path(e['public_archive']).parent.resolve() for e in entries}
    incomplete = [dict(path=str(p.relative_to(ROOT)), started_sha256=sha(p/'started.json'),
                       files=sorted(x.name for x in p.iterdir()))
                  for p in (STUDY/'attempts').glob('*/*') if p.is_dir() and p.resolve() not in completed_attempts]
    return dict(version='analysis-metadata-audit-v1', utc=now(), state='metadata_verified',
        controller_saved_completion=saved, plan_sha256=ph, freeze_sha256=fh, labels=LABELS,
        frozen_implementation_unchanged=True, accepted_files_verified=len(freeze['accepted_files']),
        archive_bytes=total_bytes, fresh_archive_payload_scan=False,
        verification_scope='Current marker/roster/freeze identities and archive sizes; original controller verified all bundles at completion. Analysis verifies consumed member bytes separately.',
        jobs=entries, events=sorted(events, key=lambda x: x['record']['utc']),
        event_counts=dict(event_counts), incomplete_attempts=incomplete, resources=resources())


class Bundle:
    """Only publish computed output after generator exhausted and member hash matches."""
    def __init__(self, record, side):
        self.path = Path(record[side+'_archive']); self.manifest = record[side]
        require(self.path.stat().st_size == self.manifest['bytes'], 'Archive size changed')
        self.z = zipfile.ZipFile(self.path)
        names = self.z.namelist()
        require(len(names) == len(set(names)) and set(names) == set(self.manifest['files']), 'ZIP roster differs')
        self.used = {}

    def close(self):
        self.z.close()

    def chunks(self, name, lines=False):
        rel = PurePosixPath(name)
        require(not rel.is_absolute() and '..' not in rel.parts and ':' not in name and '\\' not in name, 'Unsafe member')
        expected = self.manifest['files'][name]; h = hashlib.sha256(); count = 0
        with self.z.open(name) as f:
            source = f if lines else iter(lambda: f.read(1024*1024), b'')
            for data in source:
                h.update(data); count += len(data); yield data
        require(count == expected['bytes'] and h.hexdigest() == expected['sha256'], 'Member changed: '+name)
        self.used[name] = expected

    def json(self, name):
        return json.loads(b''.join(self.chunks(name)))

    def rows(self, name):
        for line in self.chunks(name, lines=True):
            yield json.loads(line)

    def export(self, name, target):
        with Path(target).open('xb') as f:
            for data in self.chunks(name): f.write(data)

    def source(self):
        return dict(archive=str(self.path), archive_sha256_as_recorded=self.manifest['sha256'],
                    verified_members=self.used, verification='SHA256 of each consumed uncompressed member')
