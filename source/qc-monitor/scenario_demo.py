"""Persist all 936 generated fixtures, then measure ONE full-month B0/R/H pair.

No research fits, calibration, final observations or complete replay batch.
The inventory uses lossless compressed public copies and separate private truth.
"""

import argparse
import copy
import gc
import json
import platform
import shutil
import sys
import time
import traceback
import zipfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from demo import export_ledger, process_peak_bytes, write_jsonl
from evaluation_adapter import CONFIGS, adapt, run_health
from evaluator import excess_response, point_scores, raw_episodes, raw_nodes, recovery_units, root_scores
from evaluator_demo import export_points, read_completed
from fixtures import fixture_settings
from records import PROJECT, ROOT, code_identity, digest, save_json, sha256, utc_now
from replay import Receipt, replay
from scenarios import TEACHING_SCALES, build, fabricated_originals, inventory, month_window
from scenario_truth import private_truth, primary_task


def payload(value):
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))+"\n").encode()


def zip_save(archive, name, content):
    entry = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(entry, content)


def generate_inventory(folder, private_folder):
    begin = time.perf_counter()
    cases = inventory()
    save_json(folder/"planned-inventory.json", cases)
    public_path = ROOT/"data/synthetic-public"/(folder.name+"-936.zip")
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path = private_folder/"936-private-construction.zip"
    records = []
    with zipfile.ZipFile(public_path, "x") as pub, zipfile.ZipFile(private_path, "x") as priv:
        for month in range(1, 13):
            window = month_window(month)
            originals = fabricated_originals(window["slots"])
            original_bytes = payload(originals)
            zip_save(priv, f"originals-{month:02d}.json", original_bytes)
            zip_save(pub, f"window-{month:02d}.json", payload(window))
            for case in [c for c in cases if c["source_month"] == f"2000-{month:02d}"]:
                g = build(case, originals)
                if g["root"]["construction"] != "effective" or g["root"]["C"] != case["length_hours"]:
                    raise AssertionError("Complete fabricated baseline must support every planned unit")
                if g != build(case, originals): raise AssertionError("Generator repeat differed")
                if payload(originals) != original_bytes: raise AssertionError("Original was mutated")
                if case == next(c for c in cases if c["source_month"] == case["source_month"]):
                    zip_save(pub, f"counterpart-{month:02d}.json", payload(g["counterpart"]))
                public_bytes = payload(g["public"])
                # Full lineage is recoverable: unchanged receipts preserve original identity;
                # originals saved once per month, all changed/deleted/added parents listed.
                private = {k:g[k] for k in ("root","unit_accounting","scales","purpose")}
                private["lineage"] = [{k:v for k,v in x.items() if k != "original"}
                                       for x in g["lineage"] if x["disposition"] != "unchanged"]
                private["originals_member"] = f"originals-{month:02d}.json"
                private["originals_sha256"] = digest(original_bytes)
                private["unchanged_lineage_rule"] = "same opaque identity in the saved monthly originals"
                private_bytes = payload(private)
                zip_save(pub, case["case_id"]+".json", public_bytes)
                zip_save(priv, case["case_id"]+".json", private_bytes)
                records.append({**{k:g["root"][k] for k in ("case_id","family","source_month","variant","N","C","Z","U","support","construction")},
                                "public_sha256":digest(public_bytes), "private_sha256":digest(private_bytes),
                                "public_bytes_uncompressed":len(public_bytes),"private_bytes_uncompressed":len(private_bytes)})
            print(f"Constructed and repeated month {month}: 78 cases", flush=True)
    # Reopen every saved case: hash verification checks persistence, not just RAM.
    with zipfile.ZipFile(public_path) as pub, zipfile.ZipFile(private_path) as priv:
        if pub.testzip() or priv.testzip(): raise AssertionError("ZIP integrity failed")
        for row in records:
            name = row["case_id"]+".json"
            if digest(pub.read(name)) != row["public_sha256"] or digest(priv.read(name)) != row["private_sha256"]:
                raise AssertionError("Saved fixture differs from verified construction")
    save_json(folder/"constructed-inventory.json", records)
    summary = {"purpose":"936 constructed fabricated fixtures, no 936-case replay batch",
               "planned":len(cases),"constructed":len(records),"annual_families":dict(Counter(c["family"] for c in cases)),
               "monthly_counts":dict(Counter(c["source_month"] for c in cases)),
               "support":dict(Counter(c["support"] for c in records)),
               "outcomes":dict(Counter(c["construction"] for c in records)),
               "repeat_exact_all_cases":True,"all_saved_case_hashes_verified":True,
               "public_archive":str(public_path),"public_sha256":sha256(public_path),"public_archive_bytes":public_path.stat().st_size,
               "private_archive":str(private_path),"private_sha256":sha256(private_path),"private_archive_bytes":private_path.stat().st_size,
               "generation_repeat_export_verify_seconds":time.perf_counter()-begin,
               "teaching_scales":TEACHING_SCALES,"scientific_freeze":False}
    save_json(folder/"inventory-completed.json", summary)
    return summary


def monitor_save(folder, public, window, run_id):
    folder.mkdir()
    settings = fixture_settings()
    save_json(folder/"public-inputs.json", public)
    save_json(folder/"schedule.json", window["slots"])
    save_json(folder/"settings.json", {v:asdict(p) for v,p in settings.items()})
    begin = time.perf_counter()
    run = replay([Receipt.from_public(r) for r in public], window["slots"], settings, run_id=run_id)
    seconds = time.perf_counter()-begin
    peak = process_peak_bytes()
    repeat = replay([Receipt.from_public(r) for r in public], window["slots"], settings, run_id=run_id)
    if run != repeat: raise AssertionError("Full-month replay repeat differed")
    del repeat
    begin = time.perf_counter()
    export_ledger(folder, run["ledger"])
    for table in ("states", "summaries", "receipt_audit"):
        write_jsonl(folder/(table+".jsonl"), run[table])
    export_seconds = time.perf_counter()-begin
    files = {p.name:{"sha256":sha256(p),"bytes":p.stat().st_size} for p in folder.iterdir() if p.is_file()}
    save_json(folder/"monitor-completed.json", {"run_status":"complete","purpose":"synthetic full-month correctness",
              "slots_closed":run["slots_closed"],"drain_until":run["drain_until"],"files":files,
              "replay_seconds":seconds,"export_and_roundtrip_seconds":export_seconds,"repeat_exact":True,
              "process_peak_after_replay":peak,"ledger_rows":len(run["ledger"]),"public_receipts":len(public)})
    return {"replay_seconds":seconds,"export_and_roundtrip_seconds":export_seconds,
            "ledger_rows":len(run["ledger"]),"process_peak_after_replay":peak}


def measure_month(folder, private_folder):
    begin = time.perf_counter()
    # March is a 31-day month with full 14-day context; longest declared bias.
    case = next(c for c in inventory() if c["source_month"]=="2000-03" and c["family"]=="gradual_bias" and
                c["variable"]=="T" and c["sign"]==1 and c["scale_multiple"]==2 and c["ramp_hours"]==168)
    window = month_window(3)
    g = build(case, fabricated_originals(window["slots"]))
    if g != build(case, fabricated_originals(window["slots"])): raise AssertionError("Representative generator repeat differed")
    if g["root"]["construction"] != "effective": raise AssertionError("Representative construction failed")
    construction_path = private_folder/"representative-construction.json"
    save_json(construction_path, g | {"public":None,"counterpart":None})
    save_json(folder/"window.json", window)
    gc.collect()
    injected = monitor_save(folder/"injected", g["public"], window, "full-month-injected")
    control = monitor_save(folder/"counterpart", g["counterpart"], window, "full-month-counterpart")
    # Both saved runs complete BEFORE private labels are loaded or joined.
    injected_run, public, slots = read_completed(folder/"injected")
    counterpart_run, public_control, _ = read_completed(folder/"counterpart")
    units = adapt(injected_run, public, slots, case_id=case["case_id"])
    controls = adapt(counterpart_run, public_control, slots, case_id=case["case_id"]+"-control")
    nodes = raw_nodes(units)
    episodes = raw_episodes(nodes)  # no truth consulted
    hashes_before = [sha256(folder/k/"ledger.jsonl") for k in ("injected","counterpart")]
    monitor_payload_before = (digest(payload(injected_run)), digest(payload(counterpart_run)))
    # Reload the private construction evidence only after both completion manifests.
    saved_private = json.loads(construction_path.read_text())
    saved_private.update(public=public,counterpart=public_control)
    private = private_truth(saved_private,window)
    control_private = private_truth(saved_private,window,counterpart=True)
    save_json(private_folder/"representative-truth.json",private)
    save_json(private_folder/"counterpart-truth.json",control_private)
    eval_begin = time.perf_counter()
    health = {(case["case_id"],cfg):{"valid":True,"reason":None} for cfg in CONFIGS} # adapt above certified complete window/drain
    def evaluate():
        points = point_scores(units,private["units"])
        outcomes = root_scores(private["roots"],units,health)
        diagnostic = {}
        for cfg in CONFIGS:
            select=lambda items:[u for u in items if u["task"]=="value" and u["configuration"]==cfg]
            diagnostic[cfg]=excess_response(select(units),select(controls),
                {(s,case["variable"]) for s in private["roots"][0]["actual_change_mask"]},
                sha256(folder/"injected/settings.json"), sha256(folder/"counterpart/settings.json"))
        return {"point_scores":points,"root_outcomes":outcomes,"counterpart_excess_response":diagnostic,
                "primary_point_groups":[[p["task"],p["variable"],p["configuration"]] for p in points if primary_task(case,p["task"],p["variable"])],
                "other_point_groups":"supplementary domain checks, not pooled into primary affected task",
                "recovery_units_injected":len(recovery_units(units,case["planned_onset"],case["length_hours"],window["last_scored"])),
                "recovery_units_counterpart":len(recovery_units(controls,case["planned_onset"],case["length_hours"],window["last_scored"])),
                "normal_exposure_policy":"This pair is NOT the primary continuous uninjected final-period normal pool"}
    answers=evaluate()
    first_eval_seconds=time.perf_counter()-eval_begin
    if payload(answers)!=payload(evaluate()):raise AssertionError("Repeated evaluation payload differs")
    save_json(folder/"evaluation.json",answers)
    export_points(folder,answers["point_scores"])
    # Compact episode evidence preserves exact members/check contributors without copying whole ledgers.
    save_json(folder/"raw-episodes.json",[{"episode_id":e["episode_id"],"group":e["group"],
              "nodes":[{"key":n["key"],"time":n["time"],"contributors":n["contributors"]} for n in e["nodes"]]} for e in episodes])
    if hashes_before != [sha256(folder/k/"ledger.jsonl") for k in ("injected","counterpart")]:raise AssertionError("Prediction files changed")
    if monitor_payload_before != (digest(payload(injected_run)),digest(payload(counterpart_run))):raise AssertionError("In-memory predictions changed")
    output_bytes=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    metrics={"purpose":"one fabricated full-month B0/R/H pair, teaching constants only", "case":case,
             "window":{k:v for k,v in window.items() if k!="slots"},"slots_per_replay":len(slots),
             "configurations":list(CONFIGS),"injected":injected,"counterpart":control,
             "first_evaluation_seconds":first_eval_seconds,"end_to_end_seconds":time.perf_counter()-begin,
             "process_peak_working_set_bytes":process_peak_bytes(),"output_bytes_before_metrics":output_bytes,
             "private_representative_bytes":sum(p.stat().st_size for p in private_folder.glob('*.json')),
             "scope":"Fresh worker process: construction, two replays each repeated, full JSONL/typed CSV export/readback, saved-run audit, adaptation, raw episodes, private truth, two evaluations. Peak is process high-water including Python and temporary verification allocations; not isolated detector memory.",
             "repeat_generator":True,"repeat_monitor_exact":True,"repeat_evaluation_exact":True,"predictions_unchanged":True,
             "python":sys.version,"platform":platform.platform(),"code":code_identity(),
             "free_disk_bytes_at_completion":shutil.disk_usage(folder).free,
             "scientific_freeze":False,"research_batch_run":False}
    save_json(folder/"capacity.json",metrics)
    return metrics


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("inventory","month"))
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder=ROOT/"evidence/scenarios"/(args.mode+"-"+stamp)
    private_folder=ROOT/"data/synthetic-private"/folder.name
    folder.mkdir();private_folder.mkdir()
    save_json(folder/"started.json",{"started_utc":utc_now(),"code":code_identity(),"purpose":"fabricated correctness/resources only"})
    try:
        result=generate_inventory(folder,private_folder) if args.mode=="inventory" else measure_month(folder,private_folder)
        save_json(folder/"completed.json",{"finished_utc":utc_now(),"run_status":"complete","purpose":"synthetic construction/resource demonstration",
                  "files":{str(p.relative_to(folder)):{"sha256":sha256(p),"bytes":p.stat().st_size} for p in folder.rglob('*') if p.is_file()},
                  "private_folder":str(private_folder),"scientific_freeze":False})
        print(folder,flush=True)
        if args.mode=="month":print(json.dumps({k:result[k] for k in ("end_to_end_seconds","process_peak_working_set_bytes","output_bytes_before_metrics")}),flush=True)
    except Exception as exc:
        save_json(folder/"failed.json",{"finished_utc":utc_now(),"run_status":"failed","error":str(exc),"traceback":traceback.format_exc(),"scientific_use_valid":False})
        raise


if __name__=="__main__":main()
