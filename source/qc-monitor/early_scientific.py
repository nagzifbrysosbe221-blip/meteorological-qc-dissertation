"""Acceptance-bound early inputs. No fitting, cleaning or detector imports."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path

from data import EARLY_ROLES, expected_schedule, source_date, utc_for
from records import ROOT, sha256

ACCEPTANCE = ROOT / 'evidence/early-acceptance-2026-10-08/acceptance.json'
ACCEPTANCE_SHA256 = '0aa1d2efe87a36b32e416f6ffb89410e2957a1abeb5bbab7cd0e36b1108307cd'


@dataclass(frozen=True)
class Cell:
    value: float | None
    reason: str
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hour:
    source_day: date
    source_hour: int
    utc: datetime
    target: Cell
    reference: Cell


@dataclass(frozen=True)
class Series:
    source_year: int
    role: str
    variable: str
    hours: tuple[Hour, ...]
    acceptance_sha256: str
    raw_sha256: str
    boundary_rows_excluded: int = 0


def require_role(series, role):
    year = {'fit': 2021, 'development': 2022, 'validation': 2023}[role]
    if series.source_year != year or series.role != role:
        raise ValueError(f'{role} requires original source year {year}')
    seen = set()
    for h in series.hours:
        key = (h.source_day, h.source_hour)
        if (h.source_day.year != year or not 1 <= h.source_hour <= 24 or
                h.utc != utc_for(*key) or key in seen):
            raise ValueError('Invalid, duplicate or wrong-role scheduled hour')
        seen.add(key)


def _within(root, relative):
    path = (root / relative.replace('\\', '/')).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Manifest path escapes study folder')
    return path


def _verify(path, expected):
    if sha256(path) != expected:
        raise ValueError(f'Hash mismatch: {path}')


def verified_manifest(root=ROOT, manifest=ACCEPTANCE, expected_sha256=ACCEPTANCE_SHA256):
    """The production default pins the decision itself, not only its payload files."""
    root, manifest = Path(root), Path(manifest)
    _verify(manifest, expected_sha256)
    m = json.loads(manifest.read_text(encoding='utf-8'))
    if (m['version'] != 'early-acceptance-v1' or not m['bounded_review_complete'] or
            m['decision'] != 'bounded_early_snapshot_acceptance_with_metadata_limitations' or
            m['numeric_truth_status'] != 'assumed_normal_not_certified' or
            m['independent_numeric_fault_mask'] != []):
        raise ValueError('Unsupported acceptance decision/mask: versioned review required')
    entries = m['accepted_snapshots']
    if sorted(e['source_year'] for e in entries) != [2021, 2022, 2023]:
        raise ValueError('Acceptance must name the three distinct early snapshots')
    for e in entries:
        year = e['source_year']
        if (e['source_role'] != EARLY_ROLES[year] or e['permitted_use'] != EARLY_ROLES[year]
                or e['accepted_for_fitting'] is not (year == 2021)
                or e['stations'] != [240, 260] or e['variables'] != ['T', 'U']
                or e['decision'] != 'accepted_for_declared_early_role_with_documented_assumptions'):
            raise ValueError('Acceptance roles/schema are inconsistent')
        raw, view = _within(root, e['snapshot']), _within(root, e['view'])
        _verify(raw / 'response.txt', e['raw_sha256'])
        _verify(raw / 'manifest.json', e['raw_manifest_sha256'])
        _verify(view / 'manifest.json', e['view_manifest_sha256'])
        if set(e['view_files']) != {'records.jsonl', 'values.csv', 'schedule.csv', 'issues.jsonl'}:
            raise ValueError('Incomplete accepted view roster')
        for name, identity in e['view_files'].items():
            path = view / name
            _verify(path, identity['sha256'])
            if path.stat().st_size != identity['bytes']:
                raise ValueError('View size mismatch')
    return m


def cell(rows, variable):
    """Preserve structural/missing reasons and all ambiguous parent identities."""
    ids = tuple(r['record_id'] for r in rows)
    if not rows:
        return Cell(None, 'absent_row')
    if len(rows) != 1:
        return Cell(None, 'ambiguous_rows', ids)
    r = rows[0]
    if r['schema_state'] != 'complete':
        return Cell(None, 'schema_mismatch', ids)
    c = r[variable]
    if c['state'] != 'finite':
        return Cell(None, c['state'], ids)
    if not isinstance(c['value'], (int, float)) or not math.isfinite(c['value']):
        raise ValueError('Finite cell contains invalid numeric payload')
    return Cell(float(c['value']), 'finite', ids)


def load_series(year, role, variable, *, root=ROOT, manifest=ACCEPTANCE,
                expected_sha256=ACCEPTANCE_SHA256):
    if year not in EARLY_ROLES or role != EARLY_ROLES[year] or variable not in ('T', 'U'):
        raise ValueError('Wrong source-year role or variable; no reserved intake')
    m = verified_manifest(root, manifest, expected_sha256)
    entry = next(e for e in m['accepted_snapshots'] if e['source_year'] == year)
    grouped = defaultdict(list)
    boundary = 0
    path = _within(Path(root), entry['view']) / 'records.jsonl'
    # Stream lines; do not retain the much larger full raw-token dictionaries.
    with path.open(encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            day = source_date(r['raw_tokens']['YYYYMMDD'] or '')
            if day is None or r['source_date'] != day.isoformat():
                raise ValueError('Unassignable/inconsistent original source date')
            if r['parent_sha256'] != entry['raw_sha256']:
                raise ValueError('Wrong raw parent')
            if day.year != year:
                if day != date(year + 1, 1, 1):
                    raise ValueError('Unexpected boundary or reserved source date')
                boundary += 1
                continue  # Before exposing measurements; never shift into another role.
            if r['source_role'] != role or r['station'] not in (240, 260):
                raise ValueError('Wrong row role/station')
            h = r['source_hour']
            if r['time_state'] != 'valid_hour' or h not in range(1, 25):
                raise ValueError('Unassignable time requires explicit derivation review')
            if r['timestamp_utc'] != utc_for(day, h).isoformat():
                raise ValueError('Converted UTC/source-hour disagreement')
            grouped[(day, h, r['station'])].append({
                k: r[k] for k in ('record_id', 'schema_state', variable)})
    _verify(path, entry['view_files']['records.jsonl']['sha256'])
    if boundary != entry['excluded_boundary_rows']:
        raise ValueError('Boundary count disagrees with accepted snapshot')
    hours = []
    for s in expected_schedule(date(year, 1, 1), date(year, 12, 31), (260,)):
        d, h = date.fromisoformat(s['source_date']), s['source_hour']
        hours.append(Hour(d, h, utc_for(d, h), cell(grouped[(d, h, 260)], variable),
                          cell(grouped[(d, h, 240)], variable)))
    series = Series(year, role, variable, tuple(hours), expected_sha256,
                    entry['raw_sha256'], boundary)
    require_role(series, role)
    return series
