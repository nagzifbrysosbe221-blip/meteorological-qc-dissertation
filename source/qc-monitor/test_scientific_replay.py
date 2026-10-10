"""Independent arithmetic and integration contracts on manufactured observations."""
import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import math
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ewma import EWMA
from evaluation_adapter import adapt, run_health
from evaluation_fixtures import label
from evaluator import point_scores
from fixtures import fixture_settings
from replay import Receipt, replay
from scenarios import FAMILIES, fabricated_originals, inventory, public_copy
from scientific_replay import (PASSES, ScientificSettings, build_fixture, load_frozen,
                               replay_fixture)


def hand_settings():
    # All 13 calendar terms zero except intercept 3; reference coefficient 2.
    # reference=5 -> prediction 13; target=18, centre=1, scale=2 -> u=2.
    return {v:ScientificSettings(v, 'P', (3.,)+(0.,)*12+(2.,), 1., 2., .2, 1., .01,
             v=='T', v=='U', 'R1-approved-v1', 'a'*64, 'b'*64, 'c'*64,
             'diagnosis_required', 'negative_U_retained_unchanged') for v in ('T','U')}


def public_fixture(n=230, start=None):
    start = start or datetime(2000, 3, 1, 1, tzinfo=timezone.utc)
    slots = [(start+timedelta(hours=i)).isoformat() for i in range(n)]
    rows = []
    for i, s in enumerate(slots):
        for stn, val in ((240,'5'), (260,'18')):
            rows.append(dict(identity=f'{stn}-{i}',station=stn,received_at=s,timestamp=s,T=val,U=val))
    return rows, slots


class ScientificReplayTests(unittest.TestCase):
    def test_hand_prediction_centre_scale_and_closed_form_state(self):
        rows, slots = public_fixture()
        run = replay_fixture(rows, slots, hand_settings())
        states = [s for s in run['states'] if s['variable']=='T']
        for i, s in enumerate(states, 1):
            self.assertEqual(s['model_prediction'],13)
            self.assertEqual(s['standardised_residual'],2)
            self.assertAlmostEqual(s['z'],2*(1-.8**i),places=13)
        self.assertIsNone(states[226]['prediction'])
        self.assertTrue(states[227]['prediction'])
        self.assertAlmostEqual(states[1]['z'],.72)
        self.assertEqual(len(adapt(run,rows,slots,case_id='hand')),len(slots)*21)

    def test_calendar_prediction_uses_all_terms_in_document_order(self):
        p = replace(hand_settings()['T'],coefficients=tuple(float(i) for i in range(1,15)))
        t = datetime(2000,1,1,6,tzinfo=timezone.utc)
        a = 2*math.pi*(6/(366*24)); d=math.pi/2
        x = (1,math.sin(a),math.cos(a),math.sin(2*a),math.cos(2*a),
             math.sin(d),math.cos(d),math.sin(2*d),math.cos(2*d),
             math.sin(a)*math.sin(d),math.sin(a)*math.cos(d),
             math.cos(a)*math.sin(d),math.cos(a)*math.cos(d),5)
        self.assertEqual(p.prediction_at(t,5),math.fsum((i+1)*v for i,v in enumerate(x)))

    def test_strict_tie_and_unclipped_range_input(self):
        rows, slots = public_fixture(228)
        for r in rows:
            if r['station']==260:r['T']='14' # zero standardised residual
        rows[-1]['T']='24' # residual-centre =10; u=5; z=1 exactly
        run = replay_fixture(rows,slots,hand_settings())
        t = [s for s in run['states'] if s['variable']=='T'][-1]
        self.assertEqual((t['z'],t['prediction']),(1.,False))
        rows[-1]['T']='51'
        run = replay_fixture(rows,slots,hand_settings())
        self.assertAlmostEqual(run['states'][-2]['z'],3.7)
        self.assertTrue(run['states'][-2]['prediction'])

    def test_missing_reference_hold_six_reset_seventh_no_fallback(self):
        rows, slots = public_fixture(236)
        rows=[r for r in rows if not (r['station']==240 and 228<=int(r['identity'].split('-')[1])<=234)]
        run=replay_fixture(rows,slots,hand_settings())
        states=[s for s in run['states'] if s['variable']=='T']
        self.assertEqual(states[233]['action'],'hold')
        self.assertEqual(states[233]['z'],states[227]['z'])
        self.assertEqual(states[234]['action'],'reset')
        self.assertEqual(states[234]['reason'],'reference_absent')
        self.assertEqual((states[235]['valid_updates'],states[235]['z']),(1,.4))

    def test_reference_duplicates_and_missing_cells_explicit(self):
        for change, reason in (('duplicate','reference_ambiguous'),('blank','reference_missing')):
            rows, slots=public_fixture(1)
            if change=='duplicate':rows.append({**rows[0],'identity':'extra'})
            else:rows[0]['T']=''
            r=replay_fixture(rows,slots,hand_settings())
            self.assertEqual(r['states'][0]['reason'],reason)
            self.assertIsNone(r['states'][0]['prediction'])

    def test_current_timestamp_controls_alignment_not_delivery_repair(self):
        rows, slots=public_fixture(2)
        rows[1]['timestamp']='INVALID_DATE'
        r=replay_fixture(rows,slots,hand_settings())
        self.assertEqual(r['states'][0]['reason'],'target_absent')
        self.assertTrue(next(x for x in r['ledger'] if x['check']=='Q04' and x['configuration']=='R')['prediction'])
        self.assertTrue(run_health(r,rows,slots,case_id='timing')['valid'])

    def test_late_reference_cannot_revise_closed_state(self):
        rows,slots=public_fixture(2)
        rows[0]['received_at']=(datetime.fromisoformat(slots[0])+timedelta(minutes=6)).isoformat()
        r=replay_fixture(rows,slots,hand_settings())
        self.assertEqual(r['states'][0]['reason'],'reference_absent')
        self.assertEqual(r['states'][2]['valid_updates'],1)

    def test_month_boundary_never_resets(self):
        rows,slots=public_fixture(230,datetime(2000,2,28,1,tzinfo=timezone.utc))
        r=replay_fixture(rows,slots,hand_settings())
        self.assertEqual(r['states'][-2]['valid_updates'],230)

    def test_source_year_HH24_fabricated_boundary_accepted(self):
        rows,slots=public_fixture(2,datetime(2000,12,31,23,tzinfo=timezone.utc))
        r=replay_fixture(rows,slots,hand_settings())
        self.assertEqual(r['slots_closed'],2)
        # Source ownership is established outside the detector; it receives no repair metadata.
        self.assertEqual((datetime.fromisoformat(slots[-1])-timedelta(hours=1)).year,2000)

    def test_own_common_cohort_counts_from_hand_arithmetic(self):
        rows,slots=public_fixture(230)
        r=replay_fixture(rows,slots,hand_settings())
        units=adapt(r,rows,slots,case_id='cohort')
        truth={u['key']:label(False,month='2000-03') for u in units}
        scores=point_scores(units,truth)
        h=next(s for s in scores if s['configuration']=='H' and s['task']=='value' and s['variable']=='T')
        b=next(s for s in scores if s['configuration']=='B0' and s['task']=='value' and s['variable']=='T')
        self.assertEqual(h['own']['n'],3)
        self.assertEqual(h['own']['FP'],3)
        self.assertEqual(b['own']['n'],230)
        self.assertEqual(b['common']['n'],3)
        self.assertEqual(b['common']['TN'],3)

    def test_partial_H_range_flag_does_not_make_complete_value(self):
        rows,slots=public_fixture(1);rows[-1]['T']='51'
        r=replay_fixture(rows,slots,hand_settings())
        s=next(s for s in r['summaries'] if s['configuration']=='H' and s['variable']=='T' and s['domain']=='value')
        self.assertTrue(s['prediction']);self.assertFalse(s['complete'])

    def test_temperature_range_sensitivity_changes_all_Q03_only(self):
        rows,slots=public_fixture(2);rows[-1]['T']='47'
        a=replay_fixture(rows,slots,hand_settings())
        b=replay_fixture(rows,slots,hand_settings(),pass_id='T-range')
        self.assertEqual(a['states'],b['states'])
        for cfg in ('B0','R','H'):
            pick=lambda r:next(x for x in reversed(r['ledger']) if x['check']=='Q03' and x['variable']=='T' and x['configuration']==cfg)
            self.assertFalse(pick(a)['prediction']);self.assertTrue(pick(b)['prediction'])

    def test_alpha_changes_only_cutoff_not_lambda_model_or_state(self):
        rows,slots=public_fixture()
        a=hand_settings()
        b={v:replace(p,alpha=.005,cutoff=3.) for v,p in a.items()}
        ra=replay_fixture(rows,slots,a)
        rb=replay_fixture(rows,slots,b,pass_id='alpha-0005')
        self.assertEqual([s['z'] for s in ra['states']],[s['z'] for s in rb['states']])
        self.assertTrue(ra['states'][-1]['prediction'])
        self.assertFalse(rb['states'][-1]['prediction'])

    def test_teaching_constants_and_real_calendar_blocked(self):
        rows,slots=public_fixture(1)
        with self.assertRaises(ValueError):replay_fixture(rows,slots,fixture_settings())
        rows,slots=public_fixture(1,datetime(2024,1,2,1,tzinfo=timezone.utc))
        with self.assertRaises(ValueError):replay_fixture(rows,slots,hand_settings())
        with self.assertRaises(ValueError):replay([Receipt.from_public(r) for r in rows],slots,hand_settings())

    def test_private_fields_rejected(self):
        rows,slots=public_fixture(1);rows[0]['truth']=False
        with self.assertRaises(ValueError):replay_fixture(rows,slots,hand_settings())

    def test_lost_R1_labels_or_invalid_parameters_fail(self):
        for p in (replace(hand_settings()['U'],original_A2_eligible=True),
                  replace(hand_settings()['U'],exploratory=False),
                  replace(hand_settings()['U'],scale=0),
                  replace(hand_settings()['U'],coefficients=(1.,)),
                  replace(hand_settings()['U'],cutoff=float('nan'))):
            with self.assertRaises(ValueError):p.validate()

    def test_missing_statistical_result_is_execution_failure(self):
        rows,slots=public_fixture(1);r=replay_fixture(rows,slots,hand_settings())
        r['ledger']=[x for x in r['ledger'] if not(x['check']=='S01' and x['variable']=='T')]
        self.assertFalse(run_health(r,rows,slots,case_id='broken')['valid'])

    def test_all_seven_families_use_shared_scientific_kernel(self):
        for family in FAMILIES:
            case=next(c for c in inventory() if c['family']==family and c['source_month']=='2000-03')
            start=datetime.fromisoformat(case['planned_onset'])
            slots=[(start+timedelta(hours=i)).isoformat() for i in range(case['length_hours'])]
            original=fabricated_originals(slots)
            before=copy.deepcopy(original)
            g=build_fixture(case,original,hand_settings())
            self.assertEqual(g['root']['construction'],'effective')
            r=replay_fixture(g['public'],slots,hand_settings())
            self.assertTrue(run_health(r,g['public'],slots,case_id=case['case_id'])['valid'])
            self.assertEqual(original,before)

    def test_gradual_bias_uses_frozen_scale_and_unchanged_reference(self):
        case=next(c for c in inventory() if c['family']=='gradual_bias' and c['source_month']=='2000-03' and c['variable']=='T')
        start=datetime.fromisoformat(case['planned_onset'])
        slots=[(start+timedelta(hours=i)).isoformat() for i in range(48)]
        original=fabricated_originals(slots);settings=hand_settings()
        settings['T']=replace(settings['T'],scale=6.)
        g=build_fixture(case,original,settings)
        self.assertEqual((g['root']['N'],g['root']['C']),(48,48))
        old=next(r for r in g['counterpart'] if r['station']==260)
        new=next(r for r in g['public'] if r['station']==260)
        self.assertAlmostEqual(float(new['T'])-float(old['T']),-.125) # -.5*6/24
        self.assertEqual([r for r in g['public'] if r['station']==240],
                         [r for r in g['counterpart'] if r['station']==240])

    def test_actual_frozen_identity_and_all_sensitivities(self):
        for pass_id in PASSES:
            settings,bounds,proof=load_frozen(pass_id)
            self.assertEqual([s.weight for s in settings.values()],[.2,.2])
            self.assertFalse(settings['U'].original_A2_eligible)
            self.assertTrue(settings['U'].exploratory)
            self.assertFalse(proof['scientific_freeze'])
            self.assertEqual(bounds['T'],PASSES[pass_id][1])
        p,_,_=load_frozen()
        self.assertEqual(p['T'].cutoff,2.4816976238219928)
        self.assertEqual(p['U'].cutoff,2.057779719375828)

    def test_changed_evidence_binding_fails_closed(self):
        with patch('scientific_replay.sha256',return_value='0'*64):
            with self.assertRaises(ValueError):load_frozen()

    def test_prescribed_inventory_not_reduced(self):
        from collections import Counter
        cases=inventory()
        self.assertEqual(len(cases),936)
        self.assertEqual(set(Counter(c['source_month'] for c in cases).values()),{78})
        self.assertEqual(len(PASSES),4)

    def test_exact_repeat_keeps_settings_and_predictions(self):
        rows,slots=public_fixture(2);settings=hand_settings();before=copy.deepcopy(settings)
        self.assertEqual(replay_fixture(rows,slots,settings),replay_fixture(rows,slots,settings))
        self.assertEqual(settings,before)

    def test_saved_scientific_readback_and_corruption_rejection(self):
        from records import ROOT
        from scientific_replay import identity
        from scientific_replay_demo import monitor_save, read_completed
        rows,slots=public_fixture(2);settings=hand_settings()
        proof={'pass_id':'primary','settings_ids':{v:p.settings_id for v,p in settings.items()}}
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temporary:
            folder=Path(temporary)/'saved'
            monitor_save(folder,rows,slots,settings,proof,'saved')
            loaded,public,schedule=read_completed(folder)
            self.assertEqual(public,rows);self.assertEqual(schedule,slots)
            self.assertEqual(loaded['settings']['T']['scale'],2.)
            with (folder/'run.json').open('a') as stream:stream.write(' ')
            with self.assertRaises(ValueError):read_completed(folder)

    def test_driver_failure_and_interruption_preserve_explicit_state(self):
        from records import ROOT
        import scientific_replay_demo as driver
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temporary:
            for i, error in enumerate((ValueError('deliberate binding failure'), KeyboardInterrupt())):
                folder=Path(temporary)/str(i)
                with patch.object(driver,'load_frozen',side_effect=error), patch.object(driver,'resources',
                     return_value={'memory_available_bytes':1024**3,'disk_free_bytes':2*1024**3}):
                    self.assertEqual(driver.run(folder),1 if i==0 else 2)
                state=json.loads((folder/'failed.json').read_text())
                self.assertEqual(state['state'],'failed' if i==0 else 'interrupted')
                self.assertFalse((folder/'completed.json').exists())
                with self.assertRaises(ValueError):driver.inspect(folder)

    def test_driver_low_resource_stops_before_model_loading(self):
        from records import ROOT
        import scientific_replay_demo as driver
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temporary:
            folder=Path(temporary)/'low'
            with patch.object(driver,'resources',return_value={'memory_available_bytes':1,'disk_free_bytes':2*1024**3}), patch.object(driver,'load_frozen') as loader:
                self.assertEqual(driver.run(folder),1)
                loader.assert_not_called()
            self.assertTrue((folder/'resource-preflight.json').exists())
            self.assertTrue((folder/'failed.json').exists())

    def test_U_gradual_bias_scale_is_separate_from_T(self):
        case=next(c for c in inventory() if c['family']=='gradual_bias' and c['source_month']=='2000-03' and c['variable']=='U')
        start=datetime.fromisoformat(case['planned_onset'])
        slots=[(start+timedelta(hours=i)).isoformat() for i in range(48)]
        settings=hand_settings();settings['U']=replace(settings['U'],scale=12.)
        g=build_fixture(case,fabricated_originals(slots),settings)
        old=next(r for r in g['counterpart'] if r['station']==260)
        new=next(r for r in g['public'] if r['station']==260)
        self.assertAlmostEqual(float(new['U'])-float(old['U']),-.25) # -.5*12/24
        self.assertEqual(new['T'],old['T'])
