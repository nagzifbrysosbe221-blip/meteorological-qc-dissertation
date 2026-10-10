"""Submitted A2: frozen calendar OLS and conditional same-variable reference OLS."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import numpy as np

from early_scientific import require_role

FEATURES = ('intercept', 'annual_sin1', 'annual_cos1', 'annual_sin2', 'annual_cos2',
            'daily_sin1', 'daily_cos1', 'daily_sin2', 'daily_cos2',
            'annual_sin1_daily_sin1', 'annual_sin1_daily_cos1',
            'annual_cos1_daily_sin1', 'annual_cos1_daily_cos1')


def calendar_features(stamp):
    if stamp.tzinfo is None:
        raise ValueError('UTC-aware timestamp required')
    t = stamp.astimezone(timezone.utc)
    start = datetime(t.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(t.year + 1, 1, 1, tzinfo=timezone.utc)
    annual = 2 * math.pi * (t - start).total_seconds() / (end - start).total_seconds()
    daily = 2 * math.pi * (t.hour + t.minute / 60 + (t.second + t.microsecond / 1e6) / 3600) / 24
    a, b, c, d = math.sin(annual), math.cos(annual), math.sin(daily), math.cos(daily)
    return (1., a, b, math.sin(2*annual), math.cos(2*annual), c, d,
            math.sin(2*daily), math.cos(2*daily), a*c, a*d, b*c, b*d)


def design(hour, kind):
    if kind not in ('S', 'P'):
        raise ValueError('Only submitted S/P candidates are supported')
    x = calendar_features(hour.utc)
    if kind == 'P':
        if hour.reference.reason != 'finite' or hour.reference.value is None:
            return None, 'reference_' + hour.reference.reason
        if not math.isfinite(hour.reference.value):
            return None, 'reference_nonfinite'
        x += (hour.reference.value,)
    return x, None


def stable_ols(x, y):
    """Scale columns then solve by SVD; never form X'X or accept deficient rank."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if x.ndim != 2 or y.shape != (len(x),) or len(x) < 2:
        raise ValueError('Invalid/insufficient fit dimensions')
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Nonfinite fit input')
    norms = np.linalg.norm(x, axis=0)
    if not np.isfinite(norms).all() or np.any(norms == 0):
        raise ValueError('Rank deficient or nonfinite column norm')
    rcond = np.finfo(np.float64).eps * max(x.shape)
    scaled_beta, _, rank, singular = np.linalg.lstsq(x / norms, y, rcond=rcond)
    if rank != x.shape[1]:
        raise ValueError(f'Full column rank required: {rank}/{x.shape[1]}')
    beta = scaled_beta / norms
    residual = y - x @ beta
    centre = float(np.mean(residual))
    scale = float(np.std(residual, ddof=1))  # A2 says n-1, NOT n-p.
    if not np.isfinite(beta).all() or not math.isfinite(centre) or not math.isfinite(scale):
        raise ValueError('Nonfinite fit output')
    return tuple(float(b) for b in beta), centre, scale, {
        'rank': int(rank), 'columns': x.shape[1], 'rcond': float(rcond),
        'singular_values_scaled': singular.tolist(), 'column_norms': norms.tolist(),
        'scaled_condition_number': float(singular[0] / singular[-1]),
        'residual_scale_denominator': len(x)-1, 'solver': 'numpy.linalg.lstsq column-scaled SVD'}


@dataclass(frozen=True)
class Model:
    kind: str
    variable: str
    coefficients: tuple[float, ...]
    centre: float | None
    scale: float | None
    status: str
    reason: str | None


def fit(series, kind):
    require_role(series, 'fit')  # Before looking at numeric inputs.
    if kind not in ('S', 'P'):
        raise ValueError('Unknown model')
    x, y, ids, excluded = [], [], [], Counter()
    for hour in series.hours:
        if hour.target.reason != 'finite' or hour.target.value is None:
            excluded['target_' + hour.target.reason] += 1
            continue
        if not math.isfinite(hour.target.value):
            excluded['target_nonfinite'] += 1
            continue
        row, reason = design(hour, kind)
        if reason:
            excluded[reason] += 1
            continue
        x.append(row); y.append(hour.target.value)
        ids.append({'source_date': hour.source_day.isoformat(), 'source_hour': hour.source_hour,
                    'target': hour.target.record_ids,
                    'reference': hour.reference.record_ids if kind == 'P' else ()})
    record = {'kind': kind, 'variable': series.variable, 'source_year': 2021,
              'acceptance_sha256': series.acceptance_sha256, 'raw_sha256': series.raw_sha256,
              'features': FEATURES + (('schiphol_same_variable',) if kind == 'P' else ()),
              'fit_rows': ids, 'n': len(y), 'exclusions': dict(excluded)}
    try:
        beta, centre, scale, diagnostics = stable_ols(x, y)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        return Model(kind, series.variable, (), None, None, 'fit_failed', str(exc)), record
    record['diagnostics'] = diagnostics
    status = 'fitted' if scale > 0 else 'ineligible_scale'
    return Model(kind, series.variable, beta, centre, scale, status,
                 None if scale > 0 else 'Residual sample standard deviation must be positive'), record


def predict(model, hour):
    if not model.coefficients:
        return None, 'model_fit_failed'
    x, reason = design(hour, model.kind)
    if reason:
        return None, reason
    value = math.fsum(a*b for a, b in zip(x, model.coefficients, strict=True))
    return (value, None) if math.isfinite(value) else (None, 'nonfinite_prediction')


def standardised_residual(model, hour):
    if model.status != 'fitted' or model.scale is None or model.scale <= 0:
        return None, 'model_ineligible'
    if hour.target.reason != 'finite' or hour.target.value is None:
        return None, 'target_' + hour.target.reason
    value, reason = predict(model, hour)
    if reason:
        return None, reason
    u = (hour.target.value - value - model.centre) / model.scale
    return (u, None) if math.isfinite(u) else (None, 'nonfinite_residual')


def _summary(errors, expected, scale):
    n = len(errors)
    mean = math.fsum(errors) / n if n else None
    return {'usable': n, 'expected': expected, 'coverage': n/expected if expected else None,
            'mean_error': mean, 'rmse': math.sqrt(math.fsum(e*e for e in errors)/n) if n else None,
            'absolute_mean_over_frozen_scale': abs(mean)/scale if n and scale and scale > 0 else None}


def assess_gates(series, s, p):
    """2022 only; full independent schedule denominators, source-month grouping."""
    require_role(series, 'development')
    if s.kind != 'S' or p.kind != 'P' or s.variable != series.variable or p.variable != series.variable:
        raise ValueError('Wrong candidate or variable')
    expected = Counter(h.source_day.month for h in series.hours)
    # Partial fixtures are not mistaken for a scientifically complete 2022 gate year.
    import calendar
    if expected != Counter({m: calendar.monthrange(2022, m)[1]*24 for m in range(1,13)}):
        raise ValueError('A2 gates require all expected 2022 source hours')
    errors = {'S': {}, 'P': {}}
    for i, h in enumerate(series.hours):
        if h.target.reason != 'finite' or h.target.value is None or not math.isfinite(h.target.value):
            continue
        for model in (s, p):
            value, reason = predict(model, h)
            if reason is None:
                errors[model.kind][i] = h.target.value - value
    candidates = {}
    for model in (s, p):
        e = errors[model.kind]
        annual = _summary(list(e.values()), len(series.hours), model.scale)
        monthly = {str(m): _summary([v for i,v in e.items() if series.hours[i].source_day.month == m],
                                     expected[m], model.scale) for m in range(1,13)}
        passed = (model.status == 'fitted' and annual['coverage'] >= .95 and
                  all(v['coverage'] >= .90 and v['absolute_mean_over_frozen_scale'] is not None and
                      v['absolute_mean_over_frozen_scale'] <= .5 for v in monthly.values()))
        candidates[model.kind] = {'annual': annual, 'monthly': monthly, 'own_gates_pass': passed,
                                  'fit_status': model.status}
    common = sorted(errors['S'].keys() & errors['P'].keys())
    def comparison(indices):
        a = _summary([errors['S'][i] for i in indices], len(indices), None)['rmse']
        b = _summary([errors['P'][i] for i in indices], len(indices), None)['rmse']
        return {'n': len(indices), 'S_rmse': a, 'P_rmse': b,
                'ratio': b/a if a is not None and a > 0 else None}
    annual = comparison(common)
    monthly = {str(m): comparison([i for i in common if series.hours[i].source_day.month == m])
               for m in range(1,13)}
    improvement = (annual['ratio'] is not None and annual['ratio'] <= .90 and
                   all(v['ratio'] is not None and v['ratio'] <= 1.10 for v in monthly.values()))
    # P cannot rescue failure to fit the full-rank S comparator.
    if not s.coefficients:
        selected, reason = None, 'full_rank_S_comparator_failed_revision_required'
    elif candidates['P']['own_gates_pass'] and improvement:
        selected, reason = 'P', 'all_P_gates_pass'
    elif candidates['S']['own_gates_pass']:
        selected, reason = 'S', 'P_ineligible_or_improvement_unestablished'
    else:
        selected, reason = None, 'no_eligible_candidate_revision_required'
    return {'year': 2022, 'variable': series.variable, 'candidates': candidates,
            'common_sample': {'annual': annual, 'monthly': monthly},
            'P_comparative_gates_pass': improvement, 'selected': selected, 'reason': reason,
            'calibration_complete': False, 'scientific_freeze': False}


def diagnostics(series, model):
    """Descriptive A2 checks only: no diagnostic can remove a fitting row."""
    residuals, target, predicted = {}, [], []
    hourly = {str(h): [] for h in range(24)}
    monthly = {str(m): [] for m in range(1,13)}
    expected_hourly = Counter(str(h.utc.hour) for h in series.hours)
    expected_monthly = Counter(str(h.source_day.month) for h in series.hours)
    unavailable = Counter()
    for hour in series.hours:
        if hour.target.reason != 'finite' or hour.target.value is None:
            unavailable['target_' + hour.target.reason] += 1
            continue
        value, reason = predict(model, hour)
        if reason:
            unavailable[reason] += 1
            continue
        error = hour.target.value-value
        residuals[hour.utc] = error
        hourly[str(hour.utc.hour)].append(error)
        monthly[str(hour.source_day.month)].append(error)
        target.append(hour.target.value); predicted.append(value)
    def extrema(values):
        return {'min': min(values) if values else None, 'max': max(values) if values else None}
    dependence = {}
    for lag in (1,6,24,48,168):
        pairs = [(residuals[t-timedelta(hours=lag)],v) for t,v in residuals.items()
                 if t-timedelta(hours=lag) in residuals]
        corr, reason = None, 'insufficient_pairs'
        if len(pairs) >= 2:
            a,b = np.asarray(pairs,dtype=float).T
            a,b = a-a.mean(), b-b.mean()
            denominator = float(np.linalg.norm(a)*np.linalg.norm(b))
            reason = 'zero_variance' if denominator == 0 else None
            if denominator > 0: corr = float(np.dot(a,b)/denominator)
        dependence[str(lag)] = {'pairs':len(pairs),'correlation':corr,'reason':reason}
    return {'source_year':series.source_year,'variable':series.variable,'model':model.kind,
            'unavailable':dict(unavailable),'target_extrema':extrema(target),
            'prediction_extrema':extrema(predicted),'residual_extrema':extrema(list(residuals.values())),
            'hourly_UTC':{k:_summary(v,expected_hourly[k],model.scale) for k,v in hourly.items()},
            'monthly_source_date':{k:_summary(v,expected_monthly[k],model.scale) for k,v in monthly.items()},
            'actual_hour_lag_dependence':dependence,'cleaning_applied':False,
            'limits':'Descriptive dependence/representativeness diagnostics; no health or independence certification'}
