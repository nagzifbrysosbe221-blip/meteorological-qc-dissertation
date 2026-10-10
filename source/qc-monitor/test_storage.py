"""Independent fault-injection checks on small fabricated storage fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
import zipfile

from batch_controller import (CONFIGS, check_worker, create_plan, design_roster, exclusive_lock,
                              inspect, read, run_plan, validate_plan)
from batch_worker import execute
from lossless_storage import check_bundle, inventory_files, pack, verify_zip
from records import ROOT, PROJECT, save_json, sha256


def local_worker(job, public, private, attempt):
    execute(job,public,private)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'evidence/storage-controller/test-fixtures'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.batch=self.root/'batch'

    def plan(self):
        return create_plan(self.batch)

    def test_lossless_extraction_null_precision_unicode(self):
        source=self.root/'source'; source.mkdir()
        original=b'{"n":null,"z":0,"v":10.041666666666666,"raw":"","flag":false}\n'
        (source/'data.json').write_bytes(original)
        manifest=pack(source,self.root/'copy.zip')
        verify_zip(self.root/'copy.zip',manifest['files'],self.root/'extracted')
        self.assertEqual((self.root/'extracted/data.json').read_bytes(),original)
        self.assertEqual((source/'data.json').read_bytes(),original)

    def test_archive_corruption_rejected(self):
        source=self.root/'source'; source.mkdir(); (source/'a').write_text('abc')
        manifest=pack(source,self.root/'copy.zip')
        with (self.root/'copy.zip').open('r+b') as f: f.truncate(20)
        with self.assertRaises(ValueError): check_bundle(self.root/'copy.zip',manifest)

    def test_wrong_decompressed_hash_rejected(self):
        source=self.root/'source'; source.mkdir(); (source/'a').write_text('abc')
        manifest=pack(source,self.root/'copy.zip'); manifest['files']['a']['sha256']='0'*64
        with self.assertRaises(ValueError): verify_zip(self.root/'copy.zip',manifest['files'])

    def test_incomplete_archive_member_roster_rejected(self):
        source=self.root/'source'; source.mkdir(); (source/'a').write_text('abc')
        expected=inventory_files(source)
        with zipfile.ZipFile(self.root/'empty.zip','x'): pass
        with self.assertRaises(ValueError): verify_zip(self.root/'empty.zip',expected)

    def test_pause_resume_skips_only_verified_completed_job(self):
        self.plan()
        self.assertFalse(run_plan(self.batch,stop_after=1,worker=local_worker))
        marker=next((self.batch/'completed').glob('*.json')); before=sha256(marker)
        self.assertEqual(inspect(self.batch)['verified'],1)
        self.assertTrue(run_plan(self.batch,worker=local_worker))
        self.assertEqual(sha256(marker),before)
        self.assertEqual(inspect(self.batch)['verified'],3)

    def test_keyboard_interruption_and_explicit_resume(self):
        self.plan()
        def interrupt(job,public,private,attempt):
            (public/'partial').write_text('incomplete')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): run_plan(self.batch,worker=interrupt)
        self.assertEqual(read(self.batch/'progress.json')['state'],'interrupted')
        with self.assertRaises(ValueError): run_plan(self.batch,worker=local_worker)
        self.assertTrue(run_plan(self.batch,retry=True,worker=local_worker))
        self.assertEqual(len(list((self.batch/'attempts').rglob('partial'))),1)

    def test_abrupt_process_exit_releases_lock_and_requires_retry(self):
        self.plan()
        script='from pathlib import Path; import os,sys; from batch_controller import run_plan; run_plan(Path(sys.argv[1]),worker=lambda *a: os._exit(75))'
        process=subprocess.run([sys.executable,'-c',script,str(self.batch)],cwd=PROJECT,capture_output=True)
        self.assertEqual(process.returncode,75)
        self.assertEqual(inspect(self.batch)['state'],'incomplete')
        with self.assertRaises(ValueError): run_plan(self.batch,worker=local_worker)
        self.assertTrue(run_plan(self.batch,retry=True,worker=local_worker))

    def test_worker_error_stops_without_silent_skip(self):
        self.plan()
        def fail(*args): raise RuntimeError('injected fixture failure')
        with self.assertRaises(RuntimeError): run_plan(self.batch,worker=fail)
        self.assertEqual(read(self.batch/'progress.json')['state'],'failed')
        self.assertFalse((self.batch/'completed').exists())
        self.assertEqual(len(list((self.batch/'attempts').glob('*'))),1)

    def test_missing_worker_completion_rejected(self):
        self.plan()
        with self.assertRaises(FileNotFoundError): run_plan(self.batch,worker=lambda *args:None)
        self.assertFalse((self.batch/'completed').exists())

    def test_incomplete_worker_configuration_rejected(self):
        self.plan()
        def bad(job,public,private,attempt):
            save_json(public/'worker-completed.json',{'state':'complete','job_id':job['job_id'],'configurations':['B0']})
        with self.assertRaises(ValueError): run_plan(self.batch,worker=bad)

    def test_corrupt_completed_job_blocks_resume_and_inspection(self):
        self.plan(); run_plan(self.batch,stop_after=1,worker=local_worker)
        completed=read(next((self.batch/'completed').glob('*.json')))
        Path(completed['private_archive']).write_bytes(b'corrupt fixture')
        with self.assertRaises(ValueError): run_plan(self.batch,retry=True,worker=local_worker)
        with self.assertRaises(ValueError): inspect(self.batch)
        self.assertEqual(len(list((self.batch/'completed').glob('*.json'))),1)

    def test_low_disk_launches_no_worker(self):
        self.plan()
        with patch('batch_controller.shutil.disk_usage',return_value=type('Disk',(),{'free':1})()):
            with self.assertRaises(OSError): run_plan(self.batch,worker=local_worker)
        self.assertFalse((self.batch/'attempts').exists())

    def test_private_archive_failure_never_publishes_success(self):
        self.plan()
        real_pack=pack
        def fail_private(source,destination):
            if destination.name=='private.zip': raise OSError('injected mid-archive disk failure')
            return real_pack(source,destination)
        with patch('batch_controller.pack',side_effect=fail_private):
            with self.assertRaises(OSError): run_plan(self.batch,worker=local_worker)
        self.assertFalse((self.batch/'completed').exists())
        self.assertTrue(list((self.batch/'attempts').rglob('public.zip')))
        self.assertTrue(run_plan(self.batch,retry=True,worker=local_worker))

    def test_manifest_cannot_omit_canonical_predictions(self):
        self.plan()
        def omit(job,public,private,attempt):
            save_json(public/'arbitrary.json',{})
            save_json(private/'truth.json',{})
            save_json(public/'worker-completed.json',{'state':'complete','job_id':job['job_id'],'configurations':CONFIGS,
                'primary_normal_exposure_hours':0,'public_files':inventory_files(public),'private_files':inventory_files(private)})
        with self.assertRaises(ValueError): run_plan(self.batch,worker=omit)

    def test_live_orphan_worker_lock_blocks_another_worker(self):
        self.plan()
        # Simulate a still-running child after its parent controller has exited.
        with exclusive_lock(self.batch,'worker.lock'):
            with self.assertRaises(RuntimeError): run_plan(self.batch)
        self.assertFalse((self.batch/'completed').exists())
        self.assertTrue(run_plan(self.batch,retry=True,worker=local_worker))

    def test_mutated_plan_cannot_resume(self):
        self.plan(); run_plan(self.batch,stop_after=1,worker=local_worker)
        plan=read(self.batch/'plan.json'); plan['reserve_bytes']+=1
        (self.batch/'plan.json').write_text(json.dumps(plan))
        with self.assertRaises(ValueError): run_plan(self.batch,worker=local_worker)

    def test_code_change_is_rejected(self):
        plan=self.plan(); plan['code']['replay.py']='wrong'
        with self.assertRaises(ValueError): validate_plan(plan)

    def test_no_parallel_writer(self):
        self.plan()
        with exclusive_lock(self.batch):
            with self.assertRaises(OSError):
                with exclusive_lock(self.batch): pass

    def test_research_mode_rejected(self):
        plan=self.plan(); plan['mode']='research'
        with self.assertRaises(ValueError): validate_plan(plan)

    def test_full_roster_936_and_all_three_configurations(self):
        plan=create_plan(self.batch,'synthetic',full=True)
        validate_plan(plan)
        self.assertEqual(len(plan['jobs']),936)
        self.assertTrue(all(x['configurations']==['B0','R','H'] for x in plan['jobs']))
        plan['jobs'].pop()
        with self.assertRaises(ValueError): validate_plan(plan)

    def test_four_pass_design_3744_and_unique_normal_exposure(self):
        path=self.root/'design.json'; design_roster(path); design=read(path)
        self.assertEqual(len(design['jobs']),3744)
        self.assertEqual([p['alpha'] for p in design['passes']],[.01,.005,.02,.01])
        self.assertEqual(design['passes'][3]['T_bounds'],[-30,45])
        self.assertEqual(sum(x['primary_exposure_copies'] for x in design['continuous_normal_runs']),4)
        self.assertFalse(design['research_enabled'])

    def test_success_retains_archives_and_zero_primary_pair_exposure(self):
        self.plan(); run_plan(self.batch,worker=local_worker)
        for p in (self.batch/'completed').glob('*.json'):
            done=read(p)
            self.assertEqual(done['primary_normal_exposure_hours'],0)
            self.assertFalse((Path(done['public_archive']).parent/'public-scratch').exists())
            self.assertFalse((Path(done['private_archive']).parent/'private-scratch').exists())
            check_bundle(done['public_archive'],done['public']); check_bundle(done['private_archive'],done['private'])


if __name__=='__main__': unittest.main()
