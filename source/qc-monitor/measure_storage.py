"""Measure existing full-month evidence using new archives and extracted copies."""
import ctypes
import json
import shutil
import time
import traceback
from datetime import datetime, timezone

from demo import process_peak_bytes
from lossless_storage import inventory_files, pack, verify_zip
from records import ROOT, code_identity, save_json, utc_now


def resources():
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (x, ctypes.c_ulonglong) for x in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    m = Memory()
    m.length = ctypes.sizeof(m)
    ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {'memory_total_bytes':m.total if ok else None, 'memory_available_bytes':m.available if ok else None,
            'disk_free_bytes':shutil.disk_usage(ROOT).free, 'recorded_utc':utc_now()}


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT/'evidence/storage-controller'/('measurement-'+stamp)
    private_out = ROOT/'data/synthetic-private'/out.name
    out.mkdir(); private_out.mkdir()
    month = 'month-20261007T190811301473Z'
    sources = {'public':ROOT/'evidence/scenarios'/month, 'private':ROOT/'data/synthetic-private'/month}
    save_json(out/'started.json', {'code':code_identity(),'resources':resources(),'sources':{k:str(v) for k,v in sources.items()}})
    try:
        result = {}
        for label, source in sources.items():
            destination = out if label == 'public' else private_out
            before = inventory_files(source)
            start = time.perf_counter()
            bundle = pack(source, destination/(label+'.zip'))
            compress_seconds = time.perf_counter()-start
            start = time.perf_counter()
            verify_zip(destination/(label+'.zip'), before, destination/'extracted')
            extract_seconds = time.perf_counter()-start
            assert inventory_files(source) == before
            result[label] = {**bundle, 'source':str(source), 'archive':str(destination/(label+'.zip')),
                             'compress_and_readback_seconds':compress_seconds, 'extract_and_hash_seconds':extract_seconds}
            print(f'{label}: {bundle["uncompressed_bytes"]:,} -> {bundle["bytes"]:,} bytes; extracted hashes match', flush=True)
        plain = sum(x['uncompressed_bytes'] for x in result.values())
        compressed = sum(x['bytes'] for x in result.values())
        save_json(out/'completed.json', {'run_status':'complete','recorded_utc':utc_now(),'bundles':result,
            'uncompressed_bytes':plain,'compressed_bytes':compressed,'compressed_fraction':compressed/plain,
            'process_peak_bytes':process_peak_bytes(),'resources_after':resources(),
            'scope':'Existing selected month including final manifests; DEFLATE level 6, stream verification and actual extracted-file hashes; originals retained',
            'estimate_only':{'one_936_case_pass_bytes':compressed*936,'four_passes_bytes':compressed*936*4,
                'four_passes_with_2x_planning_allowance_bytes':compressed*936*4*2},
            'scientific_batch_run':False,'fitting_performed':False})
        print(out, flush=True)
    except BaseException as exc:
        save_json(out/'failed.json', {'run_status':'failed','error':repr(exc),'traceback':traceback.format_exc()})
        raise


if __name__ == '__main__':
    main()
