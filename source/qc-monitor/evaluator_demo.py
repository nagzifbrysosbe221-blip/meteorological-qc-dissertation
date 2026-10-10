"""Persist a small post-monitoring evaluation and its independently worked answers.

Only fabricated year-2000 demonstrations are enabled. No final-data route exists.
"""

import copy
import csv
import json
import platform
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from demo import export_ledger, write_jsonl
from evaluation_adapter import CONFIGS, IncompleteRun, adapt, run_health
from evaluation_fixtures import cohort_fixture, healthy, hourly_nodes, tiny_private, tiny_public
from evaluator import (VERSION, case_accounting, monthly_description, normal_ids, paired_differences,
                       point_scores, project_burden, raw_episodes, raw_nodes, root_scores, root_summary)
from records import PROJECT, ROOT, code_identity, save_json, sha256, utc_now
from replay import Receipt, replay
from ewma import FixtureSettings


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_completed(folder):
    """Verify saved hashes before loading the complete monitoring ledger."""
    folder = Path(folder).resolve()
    name = "monitor-completed.json" if (folder/"monitor-completed.json").exists() else "completed.json"
    manifest = read_json(folder/name)
    if manifest.get("run_status") != "complete" or "synthetic" not in manifest.get("purpose", "").lower():
        raise IncompleteRun("Only completed synthetic evidence may use this fixture loader")
    required = {"ledger.jsonl", "states.jsonl", "summaries.jsonl", "public-inputs.json", "schedule.json", "settings.json"}
    if not required <= set(manifest["files"]):
        raise IncompleteRun("Required export omitted from completion manifest")
    for filename, entry in manifest["files"].items():
        p = (folder/filename).resolve()
        if p.parent != folder or not p.is_file() or sha256(p) != entry["sha256"]:
            raise IncompleteRun("Corrupt/missing required artefact: "+filename)
    run = {k: manifest[k] for k in ("run_status", "slots_closed", "drain_until")}
    for table in ("ledger", "states", "summaries"):
        run[table] = [json.loads(line) for line in (folder/(table+".jsonl")).read_text(encoding="utf-8").splitlines()]
    return run, read_json(folder/"public-inputs.json"), read_json(folder/"schedule.json")


def analyse(units, private):
    # Episodes are explicitly built before labels are consulted.
    nodes = raw_nodes(units)
    episodes = raw_episodes(nodes)
    points = point_scores(units, private["units"])
    roots = root_scores(private["roots"], units, healthy(["tiny"]))
    burdens = []
    for cfg in CONFIGS:
        for cohort in ("own", "common"):
            allowed = normal_ids(units, private["units"], cfg, cohort)
            groups = sorted({(n["case_id"], n["configuration"], n["domain"], n["variable"])
                             for n in nodes if n["configuration"] == cfg}, key=str)
            for group in groups:
                group_nodes = [n for n in nodes if (n["case_id"], n["configuration"], n["domain"], n["variable"]) == group]
                group_episodes = [e for e in episodes if tuple(e["group"]) == group]
                ids = allowed & {n["key"] for n in group_nodes}
                months = {}
                for n in group_nodes:
                    # Timestamp union uses the same original row's Q04 month.
                    key = n["key"].replace("|timestamp|", "|Q04|") if n["domain"] == "timestamp" else n["key"]
                    months[n["key"]] = private["units"][key]["source_month"]
                burdens.append({"group": list(group), "cohort": cohort, "exposure_scope": "tiny_injected_fixture_only_not_primary_normal_pool",
                                **project_burden(group_nodes, group_episodes, ids, months)})
    manual_units, manual_truth = cohort_fixture()
    episode_nodes = hourly_nodes()
    episode_worked = project_burden(episode_nodes, raw_episodes(episode_nodes), {"0", "1", "3", "5", "6", "7"},
                                   {str(i): "2000-01" if i < 2 else "2000-02" for i in range(8)})
    return {"evaluator_version": VERSION, "purpose": "synthetic correctness; not research findings",
            "scope": "nine fabricated hourly slots; not a scientific full month or the 936-case design",
            "point_scores": points, "root_outcomes": roots, "case_accounting": case_accounting(roots),
            "root_strata": [{"root_id": r["root_id"], "summary": root_summary([r]),
                             "differences": paired_differences([r])} for r in roots],
            "raw_episodes": episodes, "burdens": burdens,
            "independent_cohort_example": point_scores(manual_units, manual_truth),
            "independent_episode_example": episode_worked,
            "independent_monthly_example": monthly_description([
                {"month": str(i+1), "numerator": n, "denominator": d}
                for i, (n, d) in enumerate(((0, 2), (1, 2), (2, 2), (0, 0)))])}


def export_points(folder, points):
    write_jsonl(folder/"point-scores.jsonl", points)
    with (folder/"point-scores.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(points[0]))
        writer.writeheader()
        for row in points:
            writer.writerow({k: json.dumps(v, allow_nan=False, separators=(",", ":")) for k, v in row.items()})
    # Full-month cohort IDs exceed csv's default 128 KiB field limit. Bound the
    # read limit by this trusted, just-written file and restore the global setting.
    previous_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous_limit, (folder/"point-scores.csv").stat().st_size))
        with (folder/"point-scores.csv").open(encoding="utf-8", newline="") as f:
            loaded = [{k: json.loads(v) for k, v in row.items()} for row in csv.DictReader(f)]
    finally:
        csv.field_size_limit(previous_limit)
    if loaded != points:
        raise IncompleteRun("Point export did not round-trip")
    lines = [json.loads(x) for x in (folder/"point-scores.jsonl").read_text().splitlines()]
    if lines != points:
        raise IncompleteRun("Incomplete point JSONL export")


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT/"evidence/evaluator"/("demo-"+stamp)
    folder.mkdir(parents=True, exist_ok=False)
    save_json(folder/"started.json", {"started_utc": utc_now(), "code": code_identity(), "purpose": "synthetic correctness"})
    try:
        slots, receipts, settings = tiny_public()
        save_json(folder/"public-inputs.json", [asdict(r) for r in receipts])
        save_json(folder/"schedule.json", slots)
        save_json(folder/"settings.json", {k: asdict(v) for k, v in settings.items()})
        public = [Receipt.from_public(r) for r in read_json(folder/"public-inputs.json")]
        parameters = {k: FixtureSettings(**v) for k, v in read_json(folder/"settings.json").items()}
        run = replay(public, slots, parameters, run_id="evaluator-tiny")
        if run != replay(public, slots, parameters, run_id="evaluator-tiny"):
            raise IncompleteRun("Monitoring repeat differed")
        export_ledger(folder, run["ledger"])
        for table in ("states", "summaries", "receipt_audit"):
            write_jsonl(folder/(table+".jsonl"), run[table])
        files = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in folder.iterdir() if p.is_file()}
        save_json(folder/"monitor-completed.json", {"run_status": "complete", "purpose": "synthetic correctness",
                  "finished_utc": utc_now(), "slots_closed": run["slots_closed"], "drain_until": run["drain_until"],
                  "files": files, "repeat_monitor_exact": True})
        saved, public_json, saved_slots = read_completed(folder)
        units = adapt(saved, public_json, saved_slots, case_id="tiny")
        ledger_hash_before = sha256(folder/"ledger.jsonl")
        # Private truth is created/read only AFTER completion and structural audit.
        private_path = ROOT/"data/synthetic-private"/("evaluator-"+stamp+".json")
        save_json(private_path, tiny_private())
        private = read_json(private_path)
        answers = analyse(units, private)
        if answers != analyse(copy.deepcopy(units), read_json(private_path)):
            raise IncompleteRun("Evaluation repeat differed")
        save_json(folder/"evaluation.json", answers)
        save_json(folder/"independent-expected.json", read_json(PROJECT/"evaluator_expected.json"))
        save_json(folder/"manual-cohort-inputs.json", {"units": cohort_fixture()[0]})
        save_json(folder/"manual-episode-inputs.json", hourly_nodes())
        save_json(ROOT/"data/synthetic-private"/("evaluator-cohort-"+stamp+".json"), cohort_fixture()[1])
        export_points(folder, answers["point_scores"])
        failures = {}
        for name in ("missing_ledger_row", "truncated_window", "execution_exception"):
            bad = copy.deepcopy(saved)
            if name == "missing_ledger_row":
                bad["ledger"].pop()
            elif name == "truncated_window":
                bad["slots_closed"] -= 1
            else:
                bad["run_status"] = "failed"
            failures[name] = run_health(bad, public_json, saved_slots, case_id="tiny")
            if failures[name]["valid"]:
                raise AssertionError("Constructed failure was accepted")
        save_json(folder/"constructed-failure-results.json", failures)
        # Existing selected replay is inspected without trying to invent richer truth.
        selected = ROOT/"evidence/synthetic-replay/demo-20261007T175801483715Z"
        old_run, old_public, old_slots = read_completed(selected)
        old_units = adapt(old_run, old_public, old_slots, case_id="previous-demo")
        save_json(folder/"existing-replay-adapter-audit.json", {"source": str(selected), "hashes_verified": True,
                  "slots": len(old_slots), "units": len(old_units), "evaluation_performed": False,
                  "reason": "Prior teaching annotations are not a complete Appendix C private truth contract"})
        if sha256(folder/"ledger.jsonl") != ledger_hash_before:
            raise AssertionError("Evaluator changed saved predictions")
        payload = (folder/"evaluation.json").read_bytes()
        # Re-serialize with the saved formatting to test payload equality, not merely counts.
        repeated = (json.dumps(analyse(units, private), indent=2, ensure_ascii=True, allow_nan=False)+"\n").encode()
        if repeated != payload:
            raise AssertionError("Repeated evaluation bytes differ")
        save_json(folder/"completed.json", {"run_status": "complete", "purpose": "synthetic evaluator correctness",
                  "finished_utc": utc_now(), "python": sys.version, "platform": platform.platform(), "code": code_identity(),
                  "private_truth_path": str(private_path), "private_truth_sha256": sha256(private_path),
                  "independent_expected_sha256": sha256(PROJECT/"evaluator_expected.json"),
                  "repeat_evaluation_payload_exact": True, "point_csv_jsonl_roundtrip_exact": True,
                  "ledger_unchanged_by_evaluation": True, "scientific_freeze": False,
                  "files": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()}})
        print(folder)
    except Exception as exc:
        save_json(folder/"failed.json", {"run_status": "failed", "finished_utc": utc_now(),
                  "error": str(exc), "traceback": traceback.format_exc(), "scientific_use_valid": False})
        raise


if __name__ == "__main__":
    main()
