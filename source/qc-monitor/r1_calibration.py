"""R1 permission plus A3 statistical calibration/development; not final B0/R/H replay."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
import calendar
import json
import math

from early_scientific import require_role
from scientific_models import Model, predict
from records import ROOT, sha256

APPROVAL=ROOT/'evidence/r1-calibration-2026-10-08/approval.json'
APPROVAL_SHA='4ae391e34d463b3719a7397e1daf51cd9b177625083b16ce32ff24d182e6f8e4'
WEIGHTS=(.02,.05,.10,.20)


def approved_models(path=APPROVAL, expected=APPROVAL_SHA, root=ROOT):
    if sha256(path)!=expected: raise ValueError('R1 approval missing or changed')
    a=json.loads(path.read_text())
    proposal=root/'evidence/humidity-revision-proposal-2026-10-08/proposal.json'
    if (a['version']!='R1-approved-v1' or a['researcher_approved'] is not True or
        a['proposal_sha256']!=sha256(proposal) or a['original_U_A2_eligible'] is not False or
        a['original_U_selected_model'] is not None or a['exploratory_permission']['variable']!='U' or
        a['exploratory_permission']['model']!='P'):
        raise ValueError('Wrong R1 permission; cannot relabel original eligibility')
    run=root/a['original_run']
    if not run.resolve().is_relative_to(root.resolve()): raise ValueError('Run outside study')
    for name,h in a['bound_inputs'].items():
        if sha256(run/name)!=h: raise ValueError('Bound original fit/gate changed')
    selection=json.loads((run/'selection.json').read_text())
    if selection['models']!={'T':'P','U':None}: raise ValueError('Original selection changed')
    models={}
    for variable in ('T','U'):
        record=json.loads((run/f'{variable}-P-fit.json').read_text())
        m=record['model']; m['coefficients']=tuple(m['coefficients']); model=Model(**m)
        if (model.variable!=variable or model.kind!='P' or model.status!='fitted' or
            len(model.coefficients)!=14 or not all(math.isfinite(b) for b in model.coefficients) or
            not math.isfinite(model.centre) or not math.isfinite(model.scale) or model.scale<=0 or
            record['source_year']!=2021): raise ValueError('Invalid frozen model')
        models[variable]=model
    return models, {'approval_sha256':expected,'T_original_A2_eligible':True,
                   'U_original_A2_eligible':False,'U_original_selected_model':None,
                   'U_exploratory_model':'P','revision':'R1-approved-v1'}


@dataclass(frozen=True)
class Input:
    hour: object
    prediction: float | None
    u: float | None
    reason: str | None


def prepare(fit_context, development, model):
    require_role(fit_context,'fit'); require_role(development,'development')
    if fit_context.variable!=model.variable or development.variable!=model.variable:
        raise ValueError('Variable mismatch')
    expected=Counter({m:calendar.monthrange(2022,m)[1]*24 for m in range(1,13)})
    if Counter(h.source_day.month for h in development.hours)!=expected:
        raise ValueError('Complete independent 2022 schedule required')
    hours=[h for h in fit_context.hours if h.source_day>=date(2021,12,18)]+list(development.hours)
    if len(hours)!=336+8760: raise ValueError('Need exactly 14 earlier source days of context')
    prepared=[]
    for i,h in enumerate(hours):
        if i and h.utc-hours[i-1].utc!=timedelta(hours=1): raise ValueError('Schedule must be contiguous')
        value,reason=predict(model,h)
        if h.target.reason!='finite' or h.target.value is None: reason='target_'+h.target.reason
        u=None if reason else (h.target.value-value-model.centre)/model.scale
        if u is not None and not math.isfinite(u): raise ArithmeticError('Nonfinite residual')
        prepared.append(Input(h,value,u,reason))
    return tuple(prepared)


class State:
    def __init__(self,weight):
        if weight not in WEIGHTS: raise ValueError('Unspecified lambda')
        self.weight=weight; self.z=0.; self.count=0; self.gap=0
    def step(self,u,cutoff=None):
        if cutoff is not None and (not math.isfinite(cutoff) or cutoff<=0): raise ValueError('Invalid cutoff')
        if u is None:
            self.gap+=1
            if self.gap>=7: self.z=0.; self.count=0
            return {'z':self.z,'count':self.count,'gap':self.gap,'ready':False,'flag':None,'reason':'unavailable'}
        if not math.isfinite(u): raise ArithmeticError('Nonfinite state input')
        self.z=self.weight*u+(1-self.weight)*self.z
        if not math.isfinite(self.z): raise ArithmeticError('Nonfinite EWMA')
        self.count+=1; self.gap=0
        ready=self.count>=228
        return {'z':self.z,'count':self.count,'gap':0,'ready':ready,
                'flag':abs(self.z)>cutoff if ready and cutoff is not None else None,
                'reason':'ready' if ready else 'warm_up'}


def order_cutoff(values,alpha=.01):
    if alpha not in (.005,.01,.02): raise ValueError('Unspecified alpha')
    if not values or any(not math.isfinite(v) for v in values): raise ValueError('No finite ready states')
    ordered=sorted(abs(v) for v in values)
    # Exact decimal alpha avoids a rounding-induced order-statistic index change.
    k=math.ceil((1-Fraction(str(alpha)))*len(ordered))
    c=ordered[k-1]
    if c<=0: raise ValueError('Cutoff must be strictly positive')
    return c,k


def calibrate(inputs,weight,emit=lambda row:None):
    state=State(weight); expected=Counter(); ready=Counter(); values=[]
    for index,item in enumerate(inputs):
        if item.hour.source_day.year not in (2021,2022): raise ValueError('Calibration is 2022 only with earlier 2021 context')
        if item.hour.source_day.year==2021 and item.hour.source_day<date(2021,12,18): raise ValueError('Context exceeds 14 source days')
        if index and item.hour.utc-inputs[index-1].hour.utc!=timedelta(hours=1): raise ValueError('Noncontiguous calibration schedule')
        result=state.step(item.u)
        h=item.hour
        context=h.source_day.year==2021
        if not context:
            expected[h.source_day.month]+=1
            if result['ready']: ready[h.source_day.month]+=1; values.append(result['z'])
        emit({'utc':h.utc.isoformat(),'source_date':h.source_day.isoformat(),'source_hour':h.source_hour,
              'context':context,'u':item.u,'input_reason':item.reason,**result})
    monthly={str(m):{'ready':ready[m],'expected':expected[m],
                    'coverage':ready[m]/expected[m] if expected[m] else None} for m in range(1,13)}
    failures=[]
    if any(expected[m]!=calendar.monthrange(2022,m)[1]*24 for m in range(1,13)):
        failures.append('incomplete_2022_schedule')
    if any(r['coverage'] is None or r['coverage']<.90 for r in monthly.values()):
        failures.append('monthly_ready_coverage_below_.90')
    try: cutoff,k=order_cutoff(values)
    except ValueError as exc: cutoff=k=None; failures.append(str(exc))
    return {'lambda':weight,'alpha':.01,'ready_states':len(values),'monthly':monthly,
            'cutoff':cutoff,'order_index':k,'eligible':not failures,'failures':failures,
            'ready_state_alphas':{str(a):order_cutoff(values,a)[0] for a in (.005,.02)} if cutoff else {}}


def cases(variable):
    return tuple({'case_id':f'dev-2022-{month:02d}-{variable}-{sign}-{amplitude}-{d}',
      'variable':variable,'month':month,'sign':sign,'amplitude_scales':amplitude,'ramp_hours':d}
      for month in range(1,13) for sign in (-1,1) for amplitude in (.5,1.,2.) for d in (24,72,168))


def development_pair(inputs,model,weight,cutoff,case,emit=lambda row:None):
    if case not in cases(model.variable): raise ValueError('Case outside exact development roster')
    month=case['month']; d=case['ramp_hours']
    first=date(2022,month,2 if month==1 else 1)
    begin=first-timedelta(days=14)
    stop=date(2022,month,calendar.monthrange(2022,month)[1])
    window=[item for item in inputs if begin<=item.hour.source_day<=stop]
    expected=((stop-begin).days+1)*24
    if len(window)!=expected: raise ValueError('Incomplete pair schedule/context')
    original=State(weight); injected=State(weight)
    first_hit=None; changed=unsupported=unchanged=0
    onset=next(item.hour.utc for item in window if item.hour.source_day==date(2022,month,8) and item.hour.source_hour==1)
    for item in window:
        j=int((item.hour.utc-onset).total_seconds()/3600)+1
        active=1<=j<=2*d
        modified_u=item.u; changed_here=False; y=item.hour.target.value
        if active:
            if item.hour.target.reason=='finite' and y is not None and math.isfinite(y):
                altered=y+case['sign']*case['amplitude_scales']*model.scale*min(j/d,1)
                if not math.isfinite(altered): raise ArithmeticError('Nonfinite injected value')
                changed_here=altered!=y
                changed+=int(changed_here); unchanged+=int(not changed_here)
                # Reference unavailability stays unavailable; no fallback or filled cell.
                if item.u is not None: modified_u=(altered-item.prediction-model.centre)/model.scale
            else: unsupported+=1
        a=original.step(item.u,cutoff); b=injected.step(modified_u,cutoff)
        hit=active and changed_here and b['flag'] is True and a['flag'] is False
        if hit and first_hit is None: first_hit=j
        if active:
            emit({'j':j,'utc':item.hour.utc.isoformat(),'changed':changed_here,
                  'input_reason':item.reason,
                  'original_u':item.u,'injected_u':modified_u,'original':a,'injected':b,'paired_hit':hit})
    assert changed+unsupported+unchanged==2*d
    delay=Fraction(first_hit,2*d+1) if first_hit is not None else Fraction(1)
    return {**case,'hit':first_hit is not None,'first_hit_j':first_hit,
            'delay_numerator':delay.numerator,'delay_denominator':delay.denominator,
            'normalised_delay':float(delay),'N':2*d,'C':changed,'Z':unchanged,'U':unsupported,
            'context_hours':336,'window_hours':len(window),'full_month_completed':True,
            'scope':'A3 statistical paired diagnostic, not final C4 event score; zero primary normal exposure'}


def rank_candidates(candidates):
    if len(candidates)!=4 or {c['lambda'] for c in candidates}!=set(WEIGHTS):
        raise ValueError('All four prescribed lambda candidates required')
    eligible=[]
    for c in candidates:
        if not c['calibration']['eligible']: continue
        rows=c['cases']
        if len(rows)!=216 or len({r['case_id'] for r in rows})!=216: raise ValueError('Need every development case')
        hits=sum(r['hit'] for r in rows)
        delay=sum((Fraction(r['delay_numerator'],r['delay_denominator']) for r in rows),Fraction())/216
        eligible.append((hits,delay,c['lambda']))
    if not eligible: return {'selected_lambda':None,'reason':'no_calibration_candidate_revision_required'}
    hits,delay,weight=min(eligible,key=lambda x:(-x[0],x[1],-x[2]))
    if hits==0: return {'selected_lambda':None,'reason':'all_zero_detection_revision_required'}
    return {'selected_lambda':weight,'hits':hits,'denominator':216,'detected_fraction':hits/216,
            'mean_normalised_delay':float(delay),'delay_exact':str(delay),'reason':'A3_paired_ranking'}
