"""Fabricated final-source responses; never download or analyse reserved observations."""
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from records import ROOT, PROJECT, digest, save_json, sha256
import final_intake as driver
from final_structure import (IntakeBlocked, cell_state, compare_context, parse_structure,
                             review_template, structural_inventory)

HEADER="""# SOURCE: ROYAL NETHERLANDS METEOROLOGICAL INSTITUTE (KNMI)
# Comment: These time series are inhomogeneous because of station changes.
# 240 4.790 52.318 -3.30 Schiphol Airport
# 260 5.180 52.100 1.90 De Bilt
# YYYYMMDD : Date (YYYY=year MM=month DD=day)
# HH : Time in UT, source hours 1 to 24
# T : Temperature (in 0.1 degrees Celsius) at 1.50 m
# U : Relative atmospheric humidity (in percents) at 1.50 m
# STN,YYYYMMDD,HH,T,U
"""
FORM=b"""<form>0.1 graden Celsius at 1.50 m, relatieve vochtigheid in procenten Schiphol De Bilt
<input name="vars[T]" value="1"><input name="vars[U]" value="1">
<input name="stns[240]" value="1"><input name="stns[260]" value="1"></form>"""


def raw(*rows):
    return (HEADER+'\n'.join(rows)+'\n').encode()


def context_bytes():
    return raw(*(f'{s},20240101,{h},123,60' for s in (240,260) for h in range(1,25)))


def full_year():
    lines=[];day=date(2024,1,2)
    while day<=date(2024,12,31):
        for station in (240,260):
            for hour in range(1,25):lines.append(f'{station},{day:%Y%m%d},{hour},123,60')
        day+=timedelta(days=1)
    return raw(*lines)


class StructureTests(unittest.TestCase):
    def test_final_source_year_HH24_and_boundary_exclusions(self):
        p=parse_structure(raw('260,20231231,24,9,8','260,20240101,24,9,8',
                              '260,20240102,1,9,8','260,20241231,24,9,8','260,20250101,1,9,8'))
        r=p['records']
        self.assertEqual([x['source_role'] for x in r],
                         ['excluded_boundary','context_only','final','final','excluded_boundary'])
        self.assertEqual(r[3]['timestamp_utc'],'2025-01-01T00:00:00+00:00')
        self.assertTrue(r[3]['selected']);self.assertFalse(r[4]['selected'])
        self.assertEqual(r[4]['cells']['T'],'not_examined_outside_scope')

    def test_zero_extreme_and_finite_values_are_only_states(self):
        p=parse_structure(raw('260,20240102,1,0,-99999','260,20240102,2,153.75,99999'))
        self.assertTrue(all(x['cells']=={'T':'finite','U':'finite'} for x in p['records']))
        text=json.dumps(p)
        for forbidden in ('153.75','99999','raw_tokens','minimum','maximum','model_prediction'):
            self.assertNotIn(forbidden,text)

    def test_cell_states_missing_invalid_nonfinite_absent(self):
        self.assertEqual([cell_state(x) for x in ('','abc','NaN','inf',None,'0')],
                         ['missing','invalid','nonfinite','nonfinite','absent_field','finite'])

    def test_empty_review_is_explicitly_pending(self):
        r=review_template({'raw_sha256':'a'*64})
        self.assertEqual(r['state'],'pending');self.assertIsNone(r['reviewer'])
        self.assertTrue(all(x['state']=='pending' for x in r['coverage'].values()))
        self.assertIn('not evidence',r['meaning_of_empty_lists'])

    def test_independent_schedule_complete_year_8760_not_8784(self):
        summary,slots=structural_inventory(parse_structure(full_year()))
        self.assertEqual(summary['state'],'structural_inventory_complete_review_pending')
        self.assertEqual(len(slots),17520)
        for station in (240,260):
            months=[m for m in summary['monthly'].values() if m['station']==station]
            self.assertEqual(sum(m['expected_slots'] for m in months),8760)
            self.assertEqual(summary['monthly'][f'{station}/2024-01']['expected_slots'],720)
            self.assertEqual(summary['monthly'][f'{station}/2024-02']['expected_slots'],696)
            self.assertEqual(sum(m['variables']['T']['finite_unique_slots'] for m in months),8760)
        self.assertTrue(all(s['variables']['T']['numeric_truth'] is None for s in slots))

    def test_absent_row_missing_cell_and_duplicate_are_distinct(self):
        p=parse_structure(raw('260,20240102,1,,60','260,20240102,2,100,60',
                              '260,20240102,2,100,60','240,20240102,1,100,60'))
        self.assertEqual([r['structural_labels']['Q06'] for r in p['records']],[False,False,True,False])
        self.assertTrue(p['records'][0]['structural_labels']['Q02']['T'])
        summary,slots=structural_inventory(p)
        picked={s['source_hour']:s for s in slots if s['station']==260 and s['source_date']=='2024-01-02'}
        self.assertFalse(picked[1]['Q01_absent'])
        self.assertTrue(picked[1]['variables']['T']['availability_truth'])
        self.assertFalse(picked[2]['variables']['T']['structurally_usable'])
        self.assertTrue(picked[3]['Q01_absent'])
        self.assertEqual(summary['monthly']['260/2024-01']['additional_duplicate_rows'],1)
        self.assertEqual(summary['monthly']['260/2024-01']['absent_slots'],718)

    def test_original_invalid_time_blocks_without_repair(self):
        for hour in ('0','25','1.5',''):
            p=parse_structure(raw(f'260,20240102,{hour},100,60','240,20240102,1,100,60'))
            summary,_=structural_inventory(p)
            self.assertEqual(summary['state'],'blocked_structural_intake')
            self.assertIn('invalid_original_time_without_nominal_delivery',summary['blocked_reasons'])
            self.assertTrue(p['records'][0]['structural_labels']['Q04'])
            self.assertIsNone(p['records'][0]['timestamp_utc'])
            self.assertIsNone(p['records'][0]['structural_labels']['Q05'])

    def test_unassigned_date_is_preserved_and_blocks(self):
        p=parse_structure(raw('260,INVALID_DATE,1,98765,60'))
        summary,_=structural_inventory(p)
        self.assertEqual(len(p['records']),1)
        self.assertIn('unassigned_source_date_blocks_delivery',summary['blocked_reasons'])
        self.assertNotIn('98765',json.dumps(summary))

    def test_short_row_preserved_and_absent_field_is_unknown(self):
        p=parse_structure(raw('260,20240102,1,123','240,20240102,1,123,60'))
        summary,slots=structural_inventory(p)
        self.assertIn('row_schema_mismatch',summary['blocked_reasons'])
        row=next(s for s in slots if s['station']==260)
        self.assertIsNone(row['variables']['U']['availability_truth'])

    def test_unknown_schema_and_wrong_units_fail_closed(self):
        for payload in (raw('260,20240102,1,12,60').replace(b'HH,T,U',b'HH,T,RH'),
                        raw('260,20240102,1,12,60').replace(b'HH,T,U',b'HH,T,U,FLAG'),
                        raw('260,20240102,1,12,60').replace(b'0.1 degrees',b'1 degrees'),
                        raw('260,20240102,1,12,60').replace(b'percents',b'fractions')):
            with self.assertRaises(IntakeBlocked):parse_structure(payload)

    def test_html_error_and_repeated_schema_rejected_without_value_echo(self):
        for payload in (b'<html>9876543 server error</html>',raw('260,20240102,1,12,60')+b'# STN,YYYYMMDD,HH,T,U\n'):
            with self.assertRaises(IntakeBlocked) as caught:parse_structure(payload)
            self.assertNotIn('9876543',str(caught.exception))

    def test_response_terms_notice_requires_review(self):
        with self.assertRaises(IntakeBlocked):
            parse_structure(('# copyright new terms\n').encode()+raw('260,20240102,1,12,60'))

    def test_context_is_48_rows_and_24_hours_each_station(self):
        p=parse_structure(context_bytes(),scope='context')
        summary,slots=structural_inventory(p)
        self.assertEqual(len(slots),48);self.assertEqual(summary['selected_rows'],48)
        self.assertEqual(summary['expected_slots_per_station'],24)
        self.assertTrue(all(s['source_role']=='context_only' for s in slots))

    def test_context_overlap_never_compares_or_replaces_numbers(self):
        old=parse_structure(context_bytes(),scope='context')['records']
        new=parse_structure(raw('240,20240101,1,99999,99'))['records']
        report=compare_context(old,new)
        self.assertTrue(report['structural_match'])
        self.assertFalse(report['value_comparison_performed'])
        self.assertFalse(report['context_replacement_permitted'])
        altered=parse_structure(raw('240,20240101,1,,99'))['records']
        self.assertFalse(compare_context(old,altered)['structural_match'])

    def test_current_selection_form_actual_names(self):
        driver.selection_check(FORM)
        for old,new in ((b'vars[U]',b'vars[RH]'),(b'0.1 graden',b'1 graden')):
            with self.assertRaises(IntakeBlocked):driver.selection_check(FORM.replace(old,new))

    def test_request_is_exact_source_interval_no_earlier_context(self):
        self.assertEqual(driver.PARAMETERS,{'stns':'240:260','vars':'T:U','start':'2024010201','end':'2024123124'})

    def test_old_early_reader_remains_locked(self):
        from data import parse_knmi
        with self.assertRaises(ValueError):parse_knmi(raw('260,20240102,1,123,60'))

    def test_intake_imports_no_detector_or_evaluator(self):
        code="import sys; import final_intake; assert not ({'replay','ewma','scientific_models','scientific_replay','evaluator','scenario_truth','scenarios','demo'} & set(sys.modules)); print('isolated')"
        r=subprocess.run([sys.executable,'-c',code],cwd=PROJECT,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)


class IntakeDriverTests(unittest.TestCase):
    def setup_fixture(self,folder):
        context=folder/'context.txt';context.write_bytes(context_bytes())
        binding={'context_raw':str(context.relative_to(ROOT)),
                 'bound_records':{str(context.relative_to(ROOT)):sha256(context)},
                 'source_review':'evidence/final-intake-2026-10-08/source-review.json'}
        return binding,{'capture_limitation':'fabricated test'}

    def fake_fetch(self,payload):
        def fetch(url,params,folder,name):
            folder.mkdir();p=folder/name;p.write_bytes(payload if params else FORM)
            save_json(folder/'manifest.json',{'sha256':sha256(p),'bytes':p.stat().st_size,
                                              'fabricated':True,'parameters':params})
            return p
        return fetch

    def invoke(self,folder,payload,error=None,low=False):
        inputs=self.setup_fixture(folder)
        resources={'memory_available_bytes':1 if low else 1024**3,'disk_free_bytes':2*1024**3}
        download=self.fake_fetch(payload)
        if error:
            def download(*args):raise error
        log=io.StringIO()
        with patch.object(driver,'verify_prerequisites',return_value=inputs),patch.object(driver,'resources',return_value=resources),redirect_stdout(log):
            result=driver.run(folder/'attempt',downloader=download)
        return result,log.getvalue(),folder/'attempt'

    def test_full_fabricated_driver_readback_and_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            code,log,out=self.invoke(Path(temp),full_year())
            self.assertEqual(code,0,log);self.assertTrue(driver.inspect(out))
            self.assertNotIn('123',log)
            self.assertEqual(json.loads((out/'review-template.json').read_text())['state'],'pending')
            with (out/'structure/slots-structure.jsonl').open('a') as stream:stream.write(' ')
            with self.assertRaises(IntakeBlocked):driver.inspect(out)

    def test_blocked_structural_outcome_is_not_accepted(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            code,log,out=self.invoke(Path(temp),raw('260,20240102,0,999999,60','240,20240102,1,123,60'))
            self.assertEqual(code,3)
            done=json.loads((out/'completed.json').read_text())
            self.assertFalse(done['data_accepted_for_performance'])
            self.assertEqual(done['state'],'blocked_structural_intake')
            self.assertNotIn('999999',log)

    def test_unknown_schema_preserves_download_but_no_completion(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            code,log,out=self.invoke(Path(temp),b'UNKNOWN 987654\n')
            self.assertEqual(code,3);self.assertTrue((out/'raw/response.txt').exists())
            self.assertFalse((out/'completed.json').exists());self.assertNotIn('987654',log)

    def test_transport_error_and_interruption_are_retained(self):
        for error,expected in ((OSError('sensitive payload 987654'),1),(KeyboardInterrupt(),2)):
            with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
                code,log,out=self.invoke(Path(temp),b'',error)
                self.assertEqual(code,expected);self.assertTrue((out/'failed.json').exists())
                self.assertNotIn('987654',log+(out/'failed.json').read_text())
                self.assertFalse((out/'completed.json').exists())

    def test_low_resource_stops_before_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            code,log,out=self.invoke(Path(temp),b'',low=True)
            self.assertEqual(code,3);self.assertFalse((out/'selection').exists())

    def test_stale_source_review_blocks_before_request(self):
        with self.assertRaises(IntakeBlocked):
            driver.verify_prerequisites(now=datetime.now(timezone.utc)+timedelta(days=2))

    def test_changed_binding_fails_closed(self):
        with patch.object(driver,'sha256',return_value='0'*64):
            with self.assertRaises(IntakeBlocked):driver.verify_prerequisites()

    def test_redirect_is_blocked_without_following(self):
        with self.assertRaises(IntakeBlocked):
            driver.SameRoute().redirect_request(None,None,302,'',{},'https://example.org/')

    def test_transfer_partial_bytes_preserved_on_interruption(self):
        class Response:
            status=200;url=driver.URL;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):
                if getattr(self,'sent',False):raise KeyboardInterrupt()
                self.sent=True;return b'fabricated-partial-bytes'
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            with patch('urllib.request.build_opener',return_value=Opener()):
                with self.assertRaises(KeyboardInterrupt):driver.fetch(driver.URL,driver.PARAMETERS,Path(temp)/'download','response.txt')
            path=Path(temp)/'download'
            self.assertEqual((path/'response.txt.partial').read_bytes(),b'fabricated-partial-bytes')
            m=json.loads((path/'manifest.json').read_text())
            self.assertEqual(m['state'],'interrupted')
            self.assertEqual(m['partial_sha256'],sha256(path/'response.txt.partial'))

    def test_short_HTTP_body_never_becomes_completed_response(self):
        class Response:
            status=200;url=driver.URL;headers={'Content-Length':'99'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):
                if getattr(self,'sent',False):return b''
                self.sent=True;return b'short'
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with tempfile.TemporaryDirectory(dir=ROOT/'evidence') as temp:
            with patch('urllib.request.build_opener',return_value=Opener()):
                with self.assertRaises(IntakeBlocked):driver.fetch(driver.URL,driver.PARAMETERS,Path(temp)/'raw','response.txt')
            self.assertTrue((Path(temp)/'raw/response.txt.partial').exists())
            self.assertFalse((Path(temp)/'raw/response.txt').exists())
