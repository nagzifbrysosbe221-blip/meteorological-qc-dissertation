"""Frozen scientific settings for the shared B0/R/H kernel.

Only fabricated year-2000 integration windows are executable in this increment.
Final acquisition, label acceptance and scientific freeze remain separate gates.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math

from records import ROOT, PROJECT, digest, sha256
from r1_calibration import approved_models
from r1_validation import A3, frozen_decisions
from scientific_models import calendar_features
from replay import Receipt, _replay, utc

BINDING = PROJECT / 'scientific-replay-protocol.json'
VALIDATION = ROOT / 'evidence/r1-validation-2026-10-08/manual-2023-20261007T211804852813Z'
REVIEW = ROOT / 'evidence/r1-validation-2026-10-08/completed-validation-review'
PASSES = {
    'primary': (.01, (-40., 50.)),
    'alpha-0005': (.005, (-40., 50.)),
    'alpha-002': (.02, (-40., 50.)),
    'T-range': (.01, (-30., 45.)),
}


def identity(value):
    return digest(json.dumps(value, sort_keys=True, allow_nan=False).encode())


@dataclass(frozen=True)
class ScientificSettings:
    variable: str
    kind: str
    coefficients: tuple
    centre: float
    scale: float
    weight: float
    cutoff: float
    alpha: float
    original_A2_eligible: bool
    exploratory: bool
    revision: str
    model_sha256: str
    decision_sha256: str
    permission_sha256: str
    validation_state: str
    validation_disposition: str

    @property
    def settings_id(self):
        return 'scientific-settings:' + identity(asdict(self))

    def validate(self):
        if self.variable not in ('T', 'U') or self.kind not in ('S', 'P'):
            raise ValueError('Invalid scientific model identity')
        if len(self.coefficients) != (14 if self.kind == 'P' else 13):
            raise ValueError('Wrong calendar coefficient count')
        values = (*self.coefficients, self.centre, self.scale, self.weight, self.cutoff, self.alpha)
        if not all(type(x) in (int, float) and math.isfinite(x) for x in values):
            raise ValueError('Nonfinite or invalid scientific settings')
        if self.scale <= 0 or self.cutoff <= 0 or self.weight not in (.02, .05, .10, .20) or self.alpha not in (.005, .01, .02):
            raise ValueError('Missing submitted scientific settings')
        if (self.original_A2_eligible is not (self.variable == 'T') or
                self.exploratory is not (self.variable == 'U') or self.revision != 'R1-approved-v1'):
            raise ValueError('Original eligibility/R1 labels cannot change')
        if self.validation_state != 'diagnosis_required' or self.validation_disposition != 'negative_U_retained_unchanged':
            raise ValueError('Validation history/disposition is required')
        for h in (self.model_sha256, self.decision_sha256, self.permission_sha256):
            if len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
                raise ValueError('Missing evidence identity')

    def prediction_at(self, timestamp, reference):
        if timestamp is None:
            raise ValueError('Scientific prediction requires the scheduled UTC timestamp')
        features = calendar_features(timestamp)
        if self.kind == 'P':
            features += (reference,)
        value = math.fsum(a*b for a, b in zip(features, self.coefficients, strict=True))
        if not math.isfinite(value):
            raise ArithmeticError('Nonfinite frozen prediction')
        return value


def verified_binding():
    protocol = json.loads(BINDING.read_text(encoding='utf-8'))
    if protocol['version'] != 'scientific-replay-integration-v1' or protocol['final_execution_enabled'] is not False:
        raise ValueError('Final execution is not enabled by the integration protocol')
    for name, expected in protocol['bound_evidence'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or sha256(path) != expected:
            raise ValueError('Bound scientific evidence changed: ' + name)
    return protocol


def load_frozen(pass_id='primary'):
    """Verify saved evidence only; never read observation values or fit a model."""
    if pass_id not in PASSES:
        raise ValueError('Unspecified sensitivity pass')
    protocol = verified_binding()
    models, permission = approved_models()
    decisions = frozen_decisions()
    completed = json.loads((VALIDATION/'completed.json').read_text())
    if completed['state'] != 'diagnosis_required' or (VALIDATION/'failed.json').exists():
        raise ValueError('Validation execution history conflicts')
    for name, item in completed['files'].items():
        path = (VALIDATION/name).resolve()
        if path.parent != VALIDATION.resolve() or sha256(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Saved validation output changed')
    audit = json.loads((REVIEW/'audit.json').read_text())
    handoff = json.loads((REVIEW/'handoff.json').read_text())
    if (audit['verified'] is not True or audit['settings_changed'] is not False or
            audit['completed_sha256'] != sha256(VALIDATION/'completed.json') or
            handoff['state'] != 'validation_diagnosed_negative_U_retained_unchanged' or
            handoff['report_sha256'] != sha256(ROOT/handoff['report'])):
        raise ValueError('Validation disposition is absent or inconsistent')
    saved = json.loads((VALIDATION/'settings.json').read_text())
    if saved != {'decisions': decisions, 'permission': permission}:
        raise ValueError('Validation did not use these exact settings')
    alpha, t_bounds = PASSES[pass_id]
    approval = json.loads((ROOT/'evidence/r1-calibration-2026-10-08/approval.json').read_text())
    result = {}
    for v in ('T', 'U'):
        m, d = models[v], decisions[v]
        cutoff = d['cutoff'] if alpha == .01 else d['alpha_sensitivity_cutoffs'][str(alpha)]
        result[v] = ScientificSettings(v, m.kind, m.coefficients, m.centre, m.scale,
            d['selected_lambda'], cutoff, alpha, d['original_A2_eligible'], d['exploratory'],
            d['revision'], sha256(ROOT/approval['original_run']/f'{v}-P-fit.json'),
            sha256(A3/f'{v}-decision.json'), permission['approval_sha256'],
            completed['state'], 'negative_U_retained_unchanged')
        result[v].validate()
    return result, {'T': t_bounds, 'U': (0., 100.)}, {
        'protocol_sha256': sha256(BINDING), 'bound_evidence': protocol['bound_evidence'],
        'pass_id': pass_id, 'settings_ids': {v:s.settings_id for v,s in result.items()},
        'U_original_A2_eligible': False, 'U_exploratory': True,
        'validation_original_state': 'diagnosis_required',
        'validation_disposition': 'negative_U_retained_unchanged',
        'scientific_freeze': False, 'final_execution_enabled': False,
    }


def replay_fixture(public, slots, settings, *, pass_id='primary', run_id='scientific-fixture'):
    """Bounded fabricated observations, scientific prediction/state/rule implementation.

    Models may be hand-constructed for independent tests or exact frozen models.
    A returned completion is a fixture completion, never a final month acceptance.
    """
    if pass_id not in PASSES or set(settings) != {'T', 'U'}:
        raise ValueError('Both variables and a prescribed pass are required')
    if not 1 <= len(slots) <= 360:
        raise ValueError('Only bounded integration fixtures (1..360 hours) are enabled')
    boundary = datetime(2001, 1, 1, tzinfo=timezone.utc)
    if any(utc(s).year != 2000 and utc(s) != boundary for s in slots):
        raise ValueError('Only fabricated source-year 2000 is enabled; final intake/freeze pending')
    alpha, t_bounds = PASSES[pass_id]
    for v, p in settings.items():
        if type(p) is not ScientificSettings or p.variable != v or p.alpha != alpha:
            raise ValueError('Teaching constants, wrong variables or crossed sensitivities forbidden')
        p.validate()
    run = _replay([Receipt.from_public(r) for r in public], slots, settings,
        run_id=run_id, protocol='submitted-v2-R1-scientific-kernel-1',
        purpose='fabricated integration with scientific model adapter; not final performance',
        bounds={'T':t_bounds, 'U':(0.,100.)})
    run['scientific_context'] = {
        'pass_id':pass_id, 'settings_ids':{v:p.settings_id for v,p in settings.items()},
        'original_A2_eligibility':{v:p.original_A2_eligible for v,p in settings.items()},
        'exploratory':{v:p.exploratory for v,p in settings.items()},
        'validation_original_state':'diagnosis_required',
        'validation_disposition':'negative_U_retained_unchanged',
        'scientific_freeze':False, 'final_performance':False}
    return run


def build_fixture(case, originals, settings):
    """Same seven recipes, using explicit scientific scales on fabricated originals."""
    from scenarios import _build, inventory, original_time
    if case not in inventory():
        raise ValueError('Only declared fabricated inventory cases are enabled')
    if set(settings) != {'T', 'U'}:
        raise ValueError('Both scientific settings required')
    for v, p in settings.items():
        if type(p) is not ScientificSettings or p.variable != v:
            raise ValueError('Teaching scale substitution forbidden')
        p.validate()
    if any(original_time(r) is not None and
           (original_time(r).year != 2000 and original_time(r) != datetime(2001,1,1,tzinfo=timezone.utc))
           for r in originals):
        raise ValueError('Final scenario construction remains gated')
    scales = {'kind':'frozen_scientific', **{v:p.scale for v,p in settings.items()},
              'model_sha256':{v:p.model_sha256 for v,p in settings.items()}}
    generated = _build(case, originals, scales, 'frozen_scientific')
    generated['purpose'] = 'fabricated scenario with scientific frozen scale; not final performance'
    return generated
